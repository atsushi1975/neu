"""ゲーム非依存のルーム管理。

ルームは「どのゲームか」「設定」「参加者」「開始後のゲーム実体」だけを持つ。
ゲーム固有の処理は ``GameDefinition`` に委譲する。
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import games, realtime, sessions
from .errors import RoomError

logger = logging.getLogger("gameplatform.rooms")

LOCK = threading.RLock()

# CPU が1手打つ前に待つ時間（秒）。人間が直前の手を見られるように空ける。
CPU_TURN_DELAY_SECONDS = 1.5

# 退出システムの定数（catan と同じ）。
EXIT_VOTE_SECONDS = 30
EXIT_COOLDOWN_SECONDS = 120
EXIT_NOTICE_SECONDS = 60


def _now() -> float:
    """時計。テストから差し替えられるように関数化する。"""
    return time.time()

_rooms: dict[str, "Room"] = {}
_cpu_threads: dict[str, threading.Thread] = {}
_room_rng = random.Random()
# CPU の表示名を選ぶ乱数。テストから seed を固定・差し替えできるように分けている。
_cpu_name_rng = random.Random()


def is_cpu_id(player_id: Any) -> bool:
    """CPU プレイヤーの id かどうか。名前ではなく id の先頭で判定する。"""
    return str(player_id).startswith("cpu")


@dataclass(slots=True)
class Room:
    id: str
    game_key: str
    host_id: str
    players: list[dict[str, str]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    game: Any | None = None
    # ロビーの変化（入室・退出・開始）を検知するための版番号。
    version: int = 0
    # CPU の手番を進める駆動器（CPU 参加つきで開始したときだけ入る）。
    cpu_driver: Any | None = None
    # CPU の手番を進めるスレッドが動いているか（LOCK 内で更新する）。
    cpu_running: bool = False
    # 退出申請の状態。
    exit_request: Any | None = None
    exit_cooldown: dict[str, float] = field(default_factory=dict)
    notice: Any | None = None
    exit_seq: int = 0
    notice_seq: int = 0
    exit_timer: Any | None = None

    @property
    def started(self) -> bool:
        return self.game is not None

    @property
    def definition(self) -> games.GameDefinition:
        return games.get(self.game_key)

    def player(self, player_id: str) -> dict[str, str] | None:
        return next((player for player in self.players if player["id"] == player_id), None)

    def public(self) -> dict[str, Any]:
        definition = self.definition
        return {
            "id": self.id,
            "gameKey": self.game_key,
            "gameTitle": definition.title,
            "gameDescription": definition.description,
            "started": self.started,
            "hostId": self.host_id,
            "players": [{"id": p["id"], "name": p["name"]} for p in self.players],
            "minPlayers": definition.min_players,
            "maxPlayers": definition.max_players,
            "config": dict(self.config),
        }


def create_room(
    name: str, *, game_key: str | None = None, config: Any = None
) -> tuple[Room, str, str]:
    with LOCK:
        key = game_key or games.default_key()
        definition = games.get(key)
        normalized = definition.normalize_config(config)
        room = Room(id=uuid.uuid4().hex[:8], game_key=key, host_id="", config=normalized)
        player_id, token = _add_player(room, name)
        room.host_id = player_id
        room.version = 1
        _rooms[room.id] = room
        return room, player_id, token


def join_room(room_id: str, name: str) -> tuple[Room, str, str]:
    room = get_room(room_id)
    with LOCK:
        if room.started:
            raise RoomError("このルームはすでに開始しています。")
        if len(room.players) >= room.definition.max_players:
            raise RoomError("このルームは満員です。")
        player_id, token = _add_player(room, name)
        room.version += 1
    return room, player_id, token


def leave_room(room_id: str, token: str) -> tuple[Room | None, str, bool]:
    """退出する。戻り値は (ルーム or 消滅なら None, プレイヤー id, ホスト交代の有無)。"""
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        if room.started and not _is_finished(room.game):
            raise RoomError("ゲーム中は退出できません。")
        sessions.revoke_player(room_id, player_id)
        room.players = [player for player in room.players if player["id"] != player_id]
        room.version += 1
        if not room.players:
            del _rooms[room.id]
            return None, player_id, False
        host_changed = room.host_id == player_id
        if host_changed:
            room.host_id = room.players[0]["id"]
        return room, player_id, host_changed


def take_host(room_id: str, token: str) -> tuple[Room, str]:
    """ホスト権を引き継ぐ。

    ブラウザの保存情報が消えた／別プレイヤーで入り直した等でホストが
    操作できなくなると、誰も開始できずルームが詰む。開始前なら
    参加者なら誰でも引き継げるようにして復帰できるようにする。
    """
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        if room.started:
            raise RoomError("ゲーム中はホストを変更できません。")
        room.host_id = player_id
        room.version += 1
        return room, player_id


def start_game(room_id: str, token: str, cpu_count: int = 0) -> tuple[Room, str]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        definition = room.definition
        if player_id != room.host_id:
            raise RoomError("開始できるのはホストだけです。", status=403)
        if room.started:
            raise RoomError("すでに開始しています。")
        if cpu_count and definition.cpu_driver is None:
            raise RoomError("このゲームはCPUに対応していません。")
        total = len(room.players) + cpu_count
        if total > definition.max_players:
            raise RoomError(f"CPUを含めて{definition.max_players}人までです。")
        if total < definition.min_players:
            raise RoomError(f"{definition.min_players}人以上で開始できます。")
        cpus = _new_cpus(room, cpu_count)
        # 席順はサーバー側で決める。ゲームの players 配列がそのまま席順
        # （手番の回る順）になり、クライアントは結果だけを受け取る。
        seats = _seat_order([*room.players, *cpus])
        game = definition.create(
            [(player["id"], player["name"]) for player in seats],
            dict(room.config),
        )
        try:
            game.start()
        except ValueError as exc:
            raise RoomError(str(exc)) from exc
        room.players.extend(cpus)
        room.game = game
        if cpus:
            room.cpu_driver = definition.cpu_driver(game, [cpu["id"] for cpu in cpus], dict(room.config))
        room.version += 1
        kick_cpus(room)
        return room, player_id


def kick_cpus(room: Room) -> None:
    """CPU の手番を別スレッドで進め始める（LOCK 内で呼ぶ）。動いていれば何もしない。

    人間の操作のあとや開始直後に呼ぶ。1手ごとに ``CPU_TURN_DELAY_SECONDS`` 待ち、
    待ちの間はロックを持たない。CPU が打てなくなったらスレッドは終わる。
    """
    if room.cpu_driver is None or room.cpu_running:
        return
    room.cpu_running = True
    thread = threading.Thread(target=_run_cpus, args=(room.id,), daemon=True)
    _cpu_threads[room.id] = thread
    thread.start()


def _cpu_delay(room: Room) -> float:
    """CPU が次の1手を打つ前に待つ秒数を返す。

    ゲームが ``has_chips(player_id)`` を実装していて、人間プレイヤーが
    全員チップを失っている場合は 0 にして、CPU だけの進行を速く終わらせる。
    フックが無いゲームや、チップが残っている人間がいるときは従来どおり
    ``CPU_TURN_DELAY_SECONDS`` を返す。判定はロック内で行う。
    """
    with LOCK:
        game = room.game
        has_chips = getattr(game, "has_chips", None)
        if not callable(has_chips):
            return CPU_TURN_DELAY_SECONDS
        humans = [
            player
            for player in room.players
            if not is_cpu_id(player.get("id", ""))
        ]
        try:
            if humans and all(not has_chips(player["id"]) for player in humans):
                return 0.0
        except Exception:
            # フックが不正でも CPU スレッドを止めず、従来どおり待つ。
            logger.exception(
                "CPU の待ち時間判定に失敗したため既定値を使います。 room=%s",
                room.id,
            )
        return CPU_TURN_DELAY_SECONDS


def _run_cpus(room_id: str) -> None:
    while True:
        with LOCK:
            room = _rooms.get(room_id)
            if room is None or room.cpu_driver is None or room.game is None:
                return
            delay = _cpu_delay(room)
        time.sleep(delay)
        with LOCK:
            room = _rooms.get(room_id)
            if room is None or room.cpu_driver is None or room.game is None:
                return
            try:
                acted = room.cpu_driver.step(room.game)
            except Exception:
                logger.exception("CPU の手番処理で例外が起きたため止めました。 room=%s", room_id)
                acted = False
            if not acted:
                room.cpu_running = False
                return
        realtime.publish(room_id)


def wait_for_cpus(room_id: str, timeout: float = 10.0) -> None:
    """テスト用。CPU の手番スレッドが終わるまで待つ。"""
    thread = _cpu_threads.get(room_id)
    if thread is not None:
        thread.join(timeout)


def get_room(room_id: str) -> Room:
    room = _rooms.get(room_id)
    if room is None:
        raise RoomError("ルームが見つかりません。", status=404)
    return room


def find_room(room_id: str) -> Room | None:
    return _rooms.get(room_id)


def list_rooms() -> list[Room]:
    with LOCK:
        return list(_rooms.values())


_TERMINAL_PHASES = {"finished"}


def _is_finished(game: Any) -> bool:
    """ゲームが終了（勝敗が決いた）状態かどうか。

    ``games/hannin/engine.py`` の ``FINISHED``（``Game.phase == "finished"``、
    または勝敗が ``Game.result`` に入った時点）が終了の判定。
    終了済みルームは ``rooms._rooms`` に残るため、進行中から除外する。
    """
    if getattr(game, "result", None) is not None:
        return True
    return getattr(game, "phase", None) in _TERMINAL_PHASES


def count_rooms() -> tuple[int, int]:
    """「開始前」と「進行中」のルーム数を数える。

    戻り値は ``(waiting_count, playing_count)``。

    - waiting: 開始前（ゲーム未開始）のルームの数。
    - playing: 開始済みで、かつ未終了（勝敗が決まっていない）のルームの数。

    終了済み（``Game.phase == FINISHED``）のルームは ``rooms._rooms`` に残るため、
    ここでは進行中から除外する。
    """
    with LOCK:
        waiting = 0
        playing = 0
        for room in _rooms.values():
            if not room.started:
                waiting += 1
                continue
            if _is_finished(room.game):
                continue
            playing += 1
        return waiting, playing


def _cancel_exit_timer(room: Room) -> None:
    timer = getattr(room, "exit_timer", None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass
    room.exit_timer = None


def _schedule_exit_timer(room: Room) -> None:
    req = room.exit_request
    if req is None:
        return
    _cancel_exit_timer(room)

    def _fire() -> None:
        with LOCK:
            r = _rooms.get(room.id)
            if r is None or r.exit_request is None:
                return
            if r.exit_request.get("id") != req["id"]:
                return
            _evaluate_exit_request(r)
        try:
            realtime.publish(room.id)
        except Exception:
            pass

    delay = max(0.0, req["expires"] - _now()) + 1.0
    timer = threading.Timer(delay, _fire)
    timer.daemon = True
    room.exit_timer = timer
    timer.start()


def _post_notice(room: Room, text: str) -> None:
    room.notice_seq += 1
    room.notice = {"id": room.notice_seq, "time": _now(), "text": text}


def _notice_view(room: Room) -> dict[str, Any] | None:
    n = room.notice
    if n is not None and _now() - n["time"] <= EXIT_NOTICE_SECONDS:
        return {"id": n["id"], "text": n["text"]}
    return None


def _exit_request_view(room: Room, player_id: str) -> dict[str, Any] | None:
    req = room.exit_request
    if req is None:
        return None
    if room.player(player_id) is None:
        return None
    mine = player_id == req["requester"]
    asker = room.player(req["requester"])
    by_name = asker["name"] if asker is not None else req["requester"]
    if mine:
        my_vote = None
    else:
        my_vote = req["votes"].get(player_id, None)
    remaining = max(0, int(__import__("math").ceil(req["expires"] - _now())))
    return {
        "id": req["id"],
        "by": by_name,
        "mine": mine,
        "myVote": my_vote,
        "remaining": remaining,
    }


def _abort_game(room: Room) -> None:
    room.game = None
    room.cpu_driver = None
    room.cpu_running = False
    # CPU プレイヤーを部屋から外す。
    room.players = [p for p in room.players if not is_cpu_id(p.get("id", ""))]
    _cancel_exit_timer(room)
    room.version += 1


def _evaluate_exit_request(room: Room) -> None:
    """退出申請の判定を1つにまとめた関数。LOCK 保持下で呼ぶ。"""
    req = room.exit_request
    if req is None:
        return
    game = room.game
    if game is None or _is_finished(game):
        room.exit_request = None
        _cancel_exit_timer(room)
        return
    players = room.players
    requester_id = req["requester"]
    asker = next((p for p in players if p["id"] == requester_id), None)
    # 申請者が部屋にいなければ消す。
    if asker is None:
        room.exit_request = None
        _cancel_exit_timer(room)
        return
    others = [p for p in players if p["id"] != requester_id]

    def approved(pid: str) -> bool:
        if is_cpu_id(pid):
            return True
        return req["votes"].get(pid) is True

    now = _now()
    # 全員承認 -> 中止。
    if all(approved(p["id"]) for p in others):
        name = asker["name"] if asker is not None else requester_id
        _post_notice(room, f"ゲームは中止されました（{name}さんの退出）")
        room.exit_request = None
        _abort_game(room)
        return
    # 拒否 or 期限切れ -> 却下。
    if any((not is_cpu_id(p["id"])) and req["votes"].get(p["id"]) is False for p in others) or now >= req["expires"]:
        room.exit_request = None
        cooldown_until = min(now, req["expires"]) + EXIT_COOLDOWN_SECONDS
        room.exit_cooldown[requester_id] = cooldown_until
        _cancel_exit_timer(room)
        return
    # まだ pending。


def evaluate_exit_request(room: Room) -> None:
    """公開ラッパ（LOCK 保持下で呼ぶ想定）。"""
    _evaluate_exit_request(room)


def request_exit(room_id: str, token: str) -> tuple[Room, str, int]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        game = room.game
        if game is None or _is_finished(game):
            raise RoomError("進行中のゲームがありません。")
        if room.exit_request is not None:
            raise RoomError("すでに退出要求があります。")
        wait = room.exit_cooldown.get(player_id, 0) - _now()
        if wait > 0:
            raise RoomError(f"退出要求はあと{int(__import__('math').ceil(wait))}秒たってから出し直してください。")
        room.exit_seq += 1
        req = {
            "id": room.exit_seq,
            "requester": player_id,
            "votes": {},
            "expires": _now() + EXIT_VOTE_SECONDS,
        }
        room.exit_request = req
        _cancel_exit_timer(room)
        _schedule_exit_timer(room)
        _evaluate_exit_request(room)
        return room, player_id, req["id"]


def vote_exit(room_id: str, token: str, request_id: Any, approve: bool) -> tuple[Room, str]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        req = room.exit_request
        if req is None:
            raise RoomError("退出要求はありません。")
        if player_id == req["requester"]:
            raise RoomError("自分の退出要求には投票できません。")
        if request_id != req["id"]:
            raise RoomError("その退出要求はもう有効ではありません。")
        if player_id in req["votes"]:
            raise RoomError("投票済みです。")
        if not isinstance(approve, bool):
            raise RoomError("approve は true / false で指定してください。")
        req["votes"][player_id] = approve
        _evaluate_exit_request(room)
        return room, player_id


def cancel_exit(room_id: str, token: str) -> tuple[Room, str]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        _evaluate_exit_request(room)
        req = room.exit_request
        if req is None:
            raise RoomError("退出要求はありません。")
        if player_id != req["requester"]:
            raise RoomError("取り消せるのは要求した本人だけです。")
        room.exit_request = None
        _cancel_exit_timer(room)
        return room, player_id


def exit_request_view(room: Room, player_id: str) -> dict[str, Any] | None:
    return _exit_request_view(room, player_id)


def notice_view(room: Room) -> dict[str, Any] | None:
    return _notice_view(room)


def authenticate(room_id: str, token: str) -> tuple[Room, str]:
    room = get_room(room_id)
    session = sessions.resolve_for_room(token, room.id)
    if session is None or room.player(session.player_id) is None:
        raise RoomError("認証に失敗しました。", status=403)
    return room, session.player_id


def reset() -> None:
    """テスト用。"""
    with LOCK:
        for room in _rooms.values():
            _cancel_exit_timer(room)
        _rooms.clear()
        _cpu_threads.clear()
        sessions.reset()


def _seat_order(players: list[dict[str, str]]) -> list[dict[str, str]]:
    """ゲーム開始時の席順を決める（サーバー側）。

    参加順やホストを固定せず、毎回ランダムに並べ替える。クライアントには
    並べ替え後の ``Game.players`` だけを返し、抽選の過程は送らない。
    """
    seats = list(players)
    _room_rng.shuffle(seats)
    return seats


def _name_key(name: str) -> str:
    """名前の重複判定用のキー（前後の空白を除き、大文字小文字を無視する）。"""
    return (name or "").strip().casefold()


def _new_cpus(room: Room, count: int) -> list[dict[str, str]]:
    """CPU 用のプレイヤー（トークンは持たない）。名前は既存の名前と重ならないように付ける。

    ゲームの ``GameDefinition.cpu_names`` に候補があれば、その中から重複なしで
    ランダムに選ぶ。候補が無いゲームや、候補だけでは足りない場合は従来どおり
    ``CPU1``, ``CPU2``, ... を付ける（例外にはしない）。
    """
    taken = {_name_key(player["name"]) for player in room.players}
    pool: list[str] = []
    pool_keys: set[str] = set()
    for candidate in room.definition.cpu_names or ():
        cleaned = (candidate or "").strip()
        key = cleaned.casefold()
        if not key or key in taken or key in pool_keys:
            continue
        pool.append(cleaned)
        pool_keys.add(key)

    cpus = []
    number = 1
    for _ in range(count):
        if pool:
            name = pool.pop(_cpu_name_rng.randrange(len(pool)))
        else:
            while f"cpu{number}" in taken:
                number += 1
            name = f"CPU{number}"
            number += 1
        taken.add(_name_key(name))
        cpus.append({"id": f"cpu-{uuid.uuid4().hex[:8]}", "name": name})
    return cpus


def _add_player(room: Room, name: str) -> tuple[str, str]:
    cleaned = (name or "").strip()
    if not cleaned:
        raise RoomError("名前を入力してください。")
    if len(cleaned) > 16:
        raise RoomError("名前は16文字までです。")
    if any(player["name"].casefold() == cleaned.casefold() for player in room.players):
        raise RoomError("同じ名前のプレイヤーがいます。")
    player_id = uuid.uuid4().hex[:8]
    room.players.append({"id": player_id, "name": cleaned})
    token = sessions.issue(room.id, player_id)
    return player_id, token
