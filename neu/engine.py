"""ノイ (Neu) ゲームエンジン。

ルール判定だけを持つ純粋なロジック。画面・通信・I/O には一切依存しない。
乱数は GameState が持つ ``random.Random`` に限定し、seed で再現できる。

設計の要点
----------
* 状態は ``GameState`` / ``PlayerState`` / ``Card`` の明示的なオブジェクト。
* 手番でできることは ``legal_actions`` で列挙し、``apply`` で適用する。
* ``apply`` は状態を破壊せず、新しい ``GameState`` を返す。
* 公開情報と非公開情報は ``view_for`` で分ける。
* ``to_dict`` / ``from_dict`` で JSON と相互変換できる（乱数状態込み）。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# 定数（仮置きの決め事はここに集約する）
# ---------------------------------------------------------------------------

MIN_PLAYERS = 2
MAX_PLAYERS = 7
STARTING_CHIPS = 3
HAND_SIZE = 3
INITIAL_DEALER = 0
LOSING_LIMIT = 101

# (カードID, kind, value, 枚数)
# kind: "number"（通常の数字）, "hundred"（n101）, "pass", "turn", "shot", "double"
CARD_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("n1", "number", 1, 3),
    ("n2", "number", 2, 3),
    ("n3", "number", 3, 3),
    ("n4", "number", 4, 3),
    ("n5", "number", 5, 3),
    ("n6", "number", 6, 3),
    ("n7", "number", 7, 3),
    ("n8", "number", 8, 3),
    ("n9", "number", 9, 3),
    ("n10", "number", 10, 6),
    ("n50", "number", 50, 2),
    ("nm10", "number", -10, 4),
    ("nm1", "number", -1, 2),
    ("n101", "hundred", 101, 5),
    ("pass", "pass", 0, 4),
    ("turn", "turn", 0, 4),
    ("shot", "shot", 0, 2),
    ("double", "double", 0, 2),
)

DECK_SIZE = sum(spec[3] for spec in CARD_SPECS)


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class NeuError(Exception):
    """エンジン共通の例外基底。"""


class InvalidSetup(NeuError):
    """人数・席番号などセットアップの不正。"""


class IllegalAction(NeuError):
    """不正な手。"""


class NotYourTurn(IllegalAction):
    """手番でないプレイヤーが操作した。"""


class CardNotInHand(IllegalAction):
    """手札に存在しないカードを指定した。"""


class DoubleNotAllowed(IllegalAction):
    """DOUBLE 中の1枚目に DOUBLE を出そうとした。"""


class InvalidTarget(IllegalAction):
    """SHOT の指名先が不正。"""


# ---------------------------------------------------------------------------
# カード
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """1枚のカード。同種カードは ``copy``（1始まり）で区別する。"""

    id: str
    kind: str  # number | hundred | pass | turn | shot | double
    value: int = 0
    copy: int = 1

    @property
    def is_number(self) -> bool:
        """合計を変化させる数字カードか（n101 を含む）。"""
        return self.kind in ("number", "hundred")

    @property
    def is_instruction(self) -> bool:
        return self.kind in ("pass", "turn", "shot", "double")


def build_deck() -> list[Card]:
    """58枚のデッキを生成する（シャッフルはしない）。"""
    deck: list[Card] = []
    for card_id, kind, value, count in CARD_SPECS:
        for copy_index in range(1, count + 1):
            deck.append(Card(id=card_id, kind=kind, value=value, copy=copy_index))
    return deck


def card_to_dict(card: Card) -> dict[str, Any]:
    return {"id": card.id, "kind": card.kind, "value": card.value, "copy": card.copy}


def card_from_dict(data: dict[str, Any]) -> Card:
    return Card(
        id=str(data["id"]),
        kind=str(data["kind"]),
        value=int(data.get("value", 0)),
        copy=int(data.get("copy", 1)),
    )


# ---------------------------------------------------------------------------
# 状態
# ---------------------------------------------------------------------------


@dataclass
class PlayerState:
    seat: int
    chips: int = STARTING_CHIPS
    hand: list[Card] = field(default_factory=list)
    alive: bool = True


@dataclass
class GameState:
    players: list[PlayerState]
    deck: list[Card]
    discard: list[Card]
    field_card: Optional[Card]
    total: int  # 場の合計（API では "sum"）
    direction: int  # +1 = 時計回り（席番号増加）, -1 = 反時計回り
    dealer: int
    current_player: Optional[int]
    cards_remaining: int  # 今手番でまだ出す必要のある枚数（1 or 2）
    phase: str = "playing"  # playing | game_over
    winner: Optional[int] = None
    round_number: int = 0
    events: list[str] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)


@dataclass(frozen=True)
class Action:
    """1回の操作。``card_index`` は手札リスト内の位置。"""

    player: int
    card_index: int
    target: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {"player": self.player, "card_index": self.card_index, "target": self.target}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Action":
        target = data.get("target")
        return Action(
            player=int(data["player"]),
            card_index=int(data["card_index"]),
            target=None if target is None else int(target),
        )


# ---------------------------------------------------------------------------
# 基本ヘルパ
# ---------------------------------------------------------------------------


def alive_players(state: GameState) -> list[PlayerState]:
    return [p for p in state.players if p.alive]


def next_alive(state: GameState, seat: int, direction: int) -> int:
    """``seat`` から ``direction`` 方向で次の生存プレイヤーの席番号を返す。"""
    count = len(state.players)
    for step in range(1, count + 1):
        candidate = (seat + direction * step) % count
        if state.players[candidate].alive:
            return candidate
    return seat


def _draw(state: GameState) -> Optional[Card]:
    """山札から1枚引く。山札が切れていたら捨て札をシャッフルして再利用する。"""
    if not state.deck:
        if not state.discard:
            return None
        state.deck = state.discard
        state.discard = []
        state.rng.shuffle(state.deck)
    return state.deck.pop()


def _refill(state: GameState, player: PlayerState) -> None:
    """手札が HAND_SIZE 枚になるまで山札から補充する。"""
    while len(player.hand) < HAND_SIZE:
        card = _draw(state)
        if card is None:
            break
        player.hand.append(card)


# ---------------------------------------------------------------------------
# ゲーム・ラウンドの開始
# ---------------------------------------------------------------------------


def new_game(num_players: int, seed: Optional[int] = None) -> GameState:
    """新しいゲームを初期化する。

    Args:
        num_players: プレイヤー人数（2〜7）。
        seed: 乱数シード。同じ seed と同じ操作で同じ展開になる。
    """
    if isinstance(num_players, bool) or not isinstance(num_players, int):
        raise InvalidSetup("プレイヤー人数は整数で指定してください")
    if not (MIN_PLAYERS <= num_players <= MAX_PLAYERS):
        raise InvalidSetup(
            f"プレイヤー人数は {MIN_PLAYERS}〜{MAX_PLAYERS} 人です（指定: {num_players}）"
        )

    rng = random.Random(seed)
    players = [PlayerState(seat=i) for i in range(num_players)]
    state = GameState(
        players=players,
        deck=build_deck(),
        discard=[],
        field_card=None,
        total=0,
        direction=1,
        dealer=INITIAL_DEALER % num_players,
        current_player=None,
        cards_remaining=1,
        phase="playing",
        winner=None,
        round_number=0,
        events=[],
        rng=rng,
    )
    start_round(state)
    return state


def start_round(state: GameState) -> GameState:
    """ラウンドを開始する（状態を直接更新する）。

    全カードを回収してシャッフルし、生存者に3枚ずつ配る。
    初期場札をめくり、最初の手番を決める。``new_game`` とラウンド終了時に呼ばれる。
    """
    if state.phase != "playing":
        raise NeuError("ゲームは終了しています")

    survivors = alive_players(state)
    if not survivors:
        raise NeuError("生存プレイヤーがいません")

    # 1. すべてのカードを回収する（脱落者の手札も含む）
    gathered: list[Card] = []
    for player in state.players:
        gathered.extend(player.hand)
        player.hand = []
    gathered.extend(state.deck)
    gathered.extend(state.discard)
    if state.field_card is not None:
        gathered.append(state.field_card)
    state.deck = gathered
    state.discard = []
    state.field_card = None

    # 2. シャッフルして配る
    state.rng.shuffle(state.deck)
    for player in survivors:
        for _ in range(HAND_SIZE):
            card = _draw(state)
            if card is None:  # 58枚あるので通常は起こらない
                raise NeuError("配布できるカードが足りません")
            player.hand.append(card)

    # 3. 初期場札: 数字カード（n101 を除く）が出るまでめくる
    while True:
        card = _draw(state)
        if card is None:
            raise NeuError("初期場札を引けませんでした")
        if card.kind == "number":
            state.field_card = card
            state.total = card.value
            break
        # n101 と指示カードは捨て札へ
        state.discard.append(card)

    # 4. 向きをリセットし、ディーラーの次の人から始める
    state.direction = 1
    state.round_number += 1
    state.current_player = next_alive(state, state.dealer, state.direction)
    state.cards_remaining = 1

    field_label = state.field_card.id if state.field_card else "-"
    state.events.append(
        f"ラウンド{state.round_number} 開始: ディーラー席{state.dealer} / "
        f"場札 {field_label} (合計 {state.total}) / 手番 席{state.current_player}"
    )
    return state


# ---------------------------------------------------------------------------
# 合法手の列挙
# ---------------------------------------------------------------------------


def legal_actions(state: GameState) -> list[Action]:
    """現在の手番で選べる手をすべて返す。ゲーム終了時は空リスト。"""
    if state.phase != "playing" or state.current_player is None:
        return []

    player = state.players[state.current_player]
    actions: list[Action] = []

    for index, card in enumerate(player.hand):
        # DOUBLE 中の1枚目には DOUBLE を出せない
        if card.kind == "double" and state.cards_remaining >= 2:
            continue

        if card.kind == "shot":
            for target in range(len(state.players)):
                if target == player.seat:
                    continue
                if state.players[target].alive:
                    actions.append(Action(player=player.seat, card_index=index, target=target))
        else:
            actions.append(Action(player=player.seat, card_index=index))

    return actions


# ---------------------------------------------------------------------------
# 手の適用
# ---------------------------------------------------------------------------


def _validate(state: GameState, action: Action) -> Card:
    if state.phase != "playing" or state.current_player is None:
        raise IllegalAction("ゲームは終了しています")

    if action.player != state.current_player:
        raise NotYourTurn(
            f"手番は席{state.current_player} です（操作: 席{action.player}）"
        )

    player = state.players[action.player]
    if not (0 <= action.card_index < len(player.hand)):
        raise CardNotInHand(
            f"席{player.seat} の手札に index {action.card_index} はありません"
        )

    card = player.hand[action.card_index]

    if card.kind == "double" and state.cards_remaining >= 2:
        raise DoubleNotAllowed("DOUBLE 中の1枚目には DOUBLE を出せません")

    if card.kind == "shot":
        target = action.target
        if target is None:
            raise InvalidTarget("SHOT は指名先を指定してください")
        if not (0 <= target < len(state.players)):
            raise InvalidTarget(f"指名先の席番号が範囲外です: {target}")
        if target == player.seat:
            raise InvalidTarget("SHOT で自分を指名することはできません")
        if not state.players[target].alive:
            raise InvalidTarget(f"席{target} は脱落しているため指名できません")
    elif action.target is not None:
        raise IllegalAction("SHOT 以外のカードに指名先は指定できません")

    return card


def apply(state: GameState, action: Action) -> GameState:
    """手を適用した新しい ``GameState`` を返す。

    不正な手は :class:`IllegalAction` 系の例外で拒否する。
    元の ``state`` は変更しない。
    """
    _validate(state, action)
    new_state = copy.deepcopy(state)
    _apply_inplace(new_state, action)
    return new_state


def _apply_inplace(state: GameState, action: Action) -> None:
    player = state.players[action.player]
    card = player.hand.pop(action.card_index)
    state.discard.append(card)
    # 1枚出すたびに手札を3枚に戻す（DOUBLE の途中でも同様）
    _refill(state, player)

    remaining_before = state.cards_remaining

    # --- 数字カード -----------------------------------------------------
    if card.is_number:
        if card.kind == "hundred":
            state.total = LOSING_LIMIT
        else:
            state.total += card.value

        if state.total > LOSING_LIMIT:
            state.events.append(
                f"席{player.seat} が {card.id} を出して合計 {state.total} "
                f"→ 101超えでラウンド負け"
            )
            _lose_round(state, player)
            return

        state.events.append(f"席{player.seat} が {card.id} を出した (合計 {state.total})")

        state.cards_remaining -= 1
        if state.cards_remaining <= 0:
            # 手番終了。次の人の必要枚数は 1
            _end_turn(state, next_alive(state, player.seat, state.direction), 1)
        return

    # --- 指示カード -----------------------------------------------------
    if card.kind == "pass":
        state.events.append(f"席{player.seat} が PASS (合計 {state.total} のまま)")
        # 必要枚数が残っている場合は、その義務が次の人へそのまま移る
        _end_turn(state, next_alive(state, player.seat, state.direction), remaining_before)
        return

    if card.kind == "turn":
        state.direction *= -1
        direction_label = "時計回り" if state.direction == 1 else "反時計回り"
        state.events.append(f"席{player.seat} が TURN (向き: {direction_label})")
        _end_turn(
            state,
            next_alive(state, player.seat, state.direction),
            remaining_before,
        )
        return

    if card.kind == "shot":
        target = action.target
        assert target is not None
        state.events.append(f"席{player.seat} が SHOT で席{target} を指名")
        _end_turn(state, target, remaining_before)
        return

    if card.kind == "double":
        state.events.append(f"席{player.seat} が DOUBLE (次の手番は2枚)")
        _end_turn(state, next_alive(state, player.seat, state.direction), 2)
        return

    raise IllegalAction(f"未知のカード種別です: {card.kind}")


def _end_turn(state: GameState, next_player: int, required: int) -> None:
    state.current_player = next_player
    state.cards_remaining = required


def _lose_round(state: GameState, loser: PlayerState) -> None:
    """ラウンドの負け処理。チップ減少・脱落・ゲーム終了・次ラウンド開始。"""
    loser.chips -= 1
    if loser.chips <= 0:
        loser.chips = 0
        loser.alive = False
        state.events.append(f"席{loser.seat} はチップを失って脱落した")
    else:
        state.events.append(f"席{loser.seat} はチップを1枚失った (残り {loser.chips}枚)")

    survivors = alive_players(state)
    if len(survivors) == 1:
        state.phase = "game_over"
        state.winner = survivors[0].seat
        state.current_player = None
        state.cards_remaining = 1
        state.events.append(f"ゲーム終了。勝者は席{state.winner}!")
        return

    # 次のディーラー: 負けた人。脱落した場合はその時点の進行方向の隣の生存者。
    if loser.alive:
        state.dealer = loser.seat
    else:
        state.dealer = next_alive(state, loser.seat, state.direction)

    start_round(state)


# ---------------------------------------------------------------------------
# 参照・判定
# ---------------------------------------------------------------------------


def is_game_over(state: GameState) -> bool:
    return state.phase == "game_over"


def winner(state: GameState) -> Optional[int]:
    return state.winner


def view_for(state: GameState, seat: int) -> dict[str, Any]:
    """``seat`` 視点の公開情報を返す。

    他人の手札は枚数のみ、山札・捨て札は枚数のみ（捨て札は一番上だけ公開）。
    自分自身の手札だけ中身を返す。
    """
    if not (0 <= seat < len(state.players)):
        raise InvalidSetup(f"席番号が範囲外です: {seat}")

    players_view: list[dict[str, Any]] = []
    for player in state.players:
        entry: dict[str, Any] = {
            "seat": player.seat,
            "chips": player.chips,
            "alive": player.alive,
            "hand_count": len(player.hand),
        }
        if player.seat == seat:
            entry["hand"] = [card_to_dict(card) for card in player.hand]
        players_view.append(entry)

    return {
        "viewer": seat,
        "phase": state.phase,
        "round": state.round_number,
        "sum": state.total,
        "direction": state.direction,
        "dealer": state.dealer,
        "current_player": state.current_player,
        "cards_remaining": state.cards_remaining,
        "field_card": card_to_dict(state.field_card) if state.field_card else None,
        "deck_count": len(state.deck),
        "discard_count": len(state.discard),
        "discard_top": card_to_dict(state.discard[-1]) if state.discard else None,
        "winner": state.winner,
        "players": players_view,
        "events": list(state.events[-20:]),
    }


# ---------------------------------------------------------------------------
# JSON 変換
# ---------------------------------------------------------------------------


def _tuples_to_lists(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_tuples_to_lists(v) for v in value]
    if isinstance(value, list):
        return [_tuples_to_lists(v) for v in value]
    return value


def _lists_to_tuples(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_lists_to_tuples(v) for v in value)
    return value


def to_dict(state: GameState) -> dict[str, Any]:
    """JSON に流せる dict へ変換する（乱数状態も含む）。"""
    return {
        "players": [
            {
                "seat": player.seat,
                "chips": player.chips,
                "alive": player.alive,
                "hand": [card_to_dict(card) for card in player.hand],
            }
            for player in state.players
        ],
        "deck": [card_to_dict(card) for card in state.deck],
        "discard": [card_to_dict(card) for card in state.discard],
        "field_card": card_to_dict(state.field_card) if state.field_card else None,
        "sum": state.total,
        "direction": state.direction,
        "dealer": state.dealer,
        "current_player": state.current_player,
        "cards_remaining": state.cards_remaining,
        "phase": state.phase,
        "winner": state.winner,
        "round": state.round_number,
        "events": list(state.events),
        "rng_state": _tuples_to_lists(state.rng.getstate()),
    }


def from_dict(data: dict[str, Any]) -> GameState:
    """``to_dict`` の出力から状態を復元する。"""
    players = [
        PlayerState(
            seat=int(player["seat"]),
            chips=int(player["chips"]),
            hand=[card_from_dict(card) for card in player.get("hand", [])],
            alive=bool(player["alive"]),
        )
        for player in data["players"]
    ]

    rng_state = data.get("rng_state")
    if rng_state is None:
        rng = random.Random()
    else:
        rng = random.Random()
        rng.setstate(_lists_to_tuples(rng_state))

    current_player = data.get("current_player")
    winner_seat = data.get("winner")

    return GameState(
        players=players,
        deck=[card_from_dict(card) for card in data.get("deck", [])],
        discard=[card_from_dict(card) for card in data.get("discard", [])],
        field_card=card_from_dict(data["field_card"]) if data.get("field_card") else None,
        total=int(data["sum"]),
        direction=int(data["direction"]),
        dealer=int(data["dealer"]),
        current_player=None if current_player is None else int(current_player),
        cards_remaining=int(data["cards_remaining"]),
        phase=str(data["phase"]),
        winner=None if winner_seat is None else int(winner_seat),
        round_number=int(data.get("round", 0)),
        events=list(data.get("events", [])),
        rng=rng,
    )
