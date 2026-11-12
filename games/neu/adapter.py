"""ノイのエンジンを共通基盤に載せるための薄いアダプタ。

``neu.engine`` は「apply が新しい GameState を返す」純粋関数型。
共通基盤は ``revision`` / ``phase`` / ``start()`` を持つゲーム実体を期待する。
このクラスはその差分だけを埋め、ルール判定はすべてエンジンに任せる。
"""

from __future__ import annotations

import random
import re
from typing import Any

from neu.engine import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    Action,
    Card,
    GameState,
    NeuError,
    apply,
    finish_order,
    new_game,
)
from neu.series import (
    build_series_result,
    ranks_from_round_results,
    validate_series_length,
)
from gameplatform.errors import InvalidAction

from .cards import CARD_LABELS, card_key, card_to_view
from .view import build_view

_SEAT_PATTERN = re.compile(r"席(\d+)")

# 連戦の次戦開始待ちのタイムアウト（秒）。テストから差し替え可能にする。
SERIES_NEXT_MATCH_SECONDS = 10.0


class NeuGame:
    """``neu.engine.GameState`` をルームから扱える形で包む。"""

    def __init__(
        self,
        players: list[tuple[str, str]],
        config: dict[str, Any] | None = None,
        *,
        seed: int | None = None,
    ) -> None:
        if not (MIN_PLAYERS <= len(players) <= MAX_PLAYERS):
            raise ValueError(
                f"プレイヤーは {MIN_PLAYERS}〜{MAX_PLAYERS} 人で開始してください。"
            )
        ids = [player_id for player_id, _name in players]
        if len(set(ids)) != len(ids):
            raise ValueError("プレイヤー id が重複しています。")

        self.players = list(players)
        self.config = dict(config or {})
        self.player_ids = [player_id for player_id, _name in players]
        self.names = {player_id: name for player_id, name in players}
        self.seat_by_id = {
            player_id: seat for seat, player_id in enumerate(self.player_ids)
        }
        self.state: GameState | None = None
        self.revision = 0
        self.last_round: dict[str, Any] | None = None
        self._seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._consumed_events = 0
        # 連戦（series）の状態。1試合終了時に waiting / final を切り替える。
        self.total_games = validate_series_length((config or {}).get("seriesLength", 1))
        self.game_number = 1
        self._series_waiting = False
        self._series_deadline: float | None = None
        self.last_series_result: dict[str, Any] | None = None
        self._previous_scores_by_seat: dict[int, int] | None = None
        self._previous_series_ranks_by_seat: dict[int, int] | None = None
        self.series_confirmed: set[str] = set()

    # ------------------------------------------------------------------ 基本

    @property
    def phase(self) -> str:
        if self.state is None:
            return "lobby"
        if self._series_waiting:
            return "waiting"
        return "finished" if self.state.phase == "game_over" else "playing"

    @property
    def result(self) -> dict[str, Any] | None:
        if self.state is None or self.state.winner is None or self._series_waiting:
            return None
        series = self.last_series_result
        result = {
            "winnerId": self.player_id_at(self.state.winner),
            "winnerName": self.name_at(self.state.winner),
            "message": f"{self.name_at(self.state.winner)} さんの勝ち！",
            "standings": self._standings(self.state.winner),
        }
        if series is not None:
            result["series"] = dict(series)
            if bool(series.get("isFinal")):
                # 最終戦は「最終の積算結果」で standings を返し直す。
                result["standings"] = [
                    {
                        "playerId": self.player_id_at(int(row["seat"])),
                        "rank": int(row["seriesRank"]),
                    }
                    for row in sorted(
                        series.get("seats") or [],
                        key=lambda item: (int(item["seriesRank"]), int(item["seat"])),
                    )
                ]
        return result

    @property
    def series_length(self) -> int:
        return self.total_games

    @property
    def is_series_waiting(self) -> bool:
        return self._series_waiting

    @property
    def series_waiting_result(self) -> dict[str, Any] | None:
        return self.last_series_result

    @property
    def series_next_tick_at(self) -> float | None:
        if not self._series_waiting:
            return None
        return self._series_deadline

    def _now(self) -> float:
        # 共通基盤の既存時計をそのまま使う（テストは rooms._now を差し替える）。
        from gameplatform.rooms import _now

        return _now()

    def _all_confirmed(self) -> bool:
        human_ids = [
            player_id
            for player_id, _name in self.players
            if not str(player_id).startswith("cpu")
        ]
        return all(player_id in self.series_confirmed for player_id in human_ids)

    def _series_result_row(self, row: dict[str, Any]) -> dict[str, Any]:
        copied = dict(row)
        seat = int(copied["seat"])
        copied["playerId"] = self.player_id_at(seat)
        copied["playerName"] = self.name_at(seat)
        return copied

    def series_view(self, player_id: str) -> dict[str, Any]:
        """クライアント向けの連戦状態（シリーズ番号・確認・残り秒数・直近結果）。"""
        from gameplatform.rooms import is_cpu_id

        result = self.last_series_result
        seats: list[dict[str, Any]] = []
        if result is not None:
            seats = [
                self._series_result_row(row)
                for row in sorted(
                    result.get("seats") or [],
                    key=lambda item: int(item["seat"]),
                )
            ]
        remaining = 0
        if self._series_waiting and self._series_deadline is not None:
            remaining = max(0, int(self._series_deadline - self._now()))
        return {
            "game": self.game_number,
            "totalGames": self.total_games,
            "waiting": self._series_waiting,
            "isFinal": self.game_number >= self.total_games,
            "result": dict(result) if result is not None else None,
            "seats": seats,
            "confirmed": player_id in self.series_confirmed or is_cpu_id(player_id),
            "remainingSeconds": remaining,
        }

    def _standings(self, winner_seat: int) -> list[dict[str, Any]]:
        """勝者を1位、脱落者を最後に脱落した順（逆順）で並べる。"""
        seats = finish_order(
            self.state.round_results if self.state else [],
            winner_seat,
            len(self.player_ids),
        )
        return [
            {"playerId": self.player_id_at(seat), "rank": rank}
            for rank, seat in enumerate(seats, start=1)
        ]

    @property
    def current_player_id(self) -> str | None:
        if self.state is None or self.state.current_player is None:
            return None
        return self.player_id_at(self.state.current_player)

    def player_id_at(self, seat: int) -> str:
        return self.player_ids[seat]

    def name_at(self, seat: int) -> str:
        return self.names[self.player_ids[seat]]

    def seat_of(self, player_id: str) -> int:
        try:
            return self.seat_by_id[player_id]
        except KeyError:
            from gameplatform.errors import InvalidAction

            raise InvalidAction("このゲームのプレイヤーではありません。") from None

    def has_chips(self, player_id: str) -> bool:
        """そのプレイヤーにチップが1枚以上残っているか（共通基盤の待ち時間用）。"""
        if self.state is None:
            return False
        return self.state.players[self.seat_of(player_id)].chips > 0

    def start(self) -> None:
        if self.state is not None:
            raise ValueError("ゲームはすでに開始しています。")
        self.state = new_game(len(self.players), self._seed)
        self.game_number = 1
        self._series_waiting = False
        self._series_deadline = None
        self.last_series_result = None
        self._previous_scores_by_seat = None
        self._previous_series_ranks_by_seat = None
        self.series_confirmed = set()
        self.revision += 1
        self._consume_events()

    def _on_match_end(self, after: GameState) -> None:
        """1試合終了時に連戦結果を組み立て、最終戦か次戦待機へ遷移する。"""
        if after.phase != "game_over" or after.winner is None:
            return
        ranks = ranks_from_round_results(
            after.round_results, after.winner, len(self.players)
        )
        result = build_series_result(
            self.game_number,
            self.total_games,
            ranks,
            self._previous_scores_by_seat,
            self._previous_series_ranks_by_seat,
        )
        self.last_series_result = result
        self._previous_scores_by_seat = {
            int(row["seat"]): int(row["total"])
            for row in result.get("seats") or []
        }
        self._previous_series_ranks_by_seat = {
            int(row["seat"]): int(row["seriesRank"])
            for row in result.get("seats") or []
        }
        if bool(result.get("isFinal")):
            self._series_waiting = False
            self._series_deadline = None
            self.series_confirmed = set()
        else:
            self._series_waiting = True
            self._series_deadline = self._now() + SERIES_NEXT_MATCH_SECONDS
            self.series_confirmed = set()

    def _start_next_match(self) -> bool:
        """前戦のビリを最初のディーラーにして、席固定の新規ゲームを始める。"""
        if self.last_series_result is None:
            raise ValueError("次戦を開始するための前戦結果がありません。")
        next_dealer_seat = self.last_series_result.get("nextDealerSeat")
        if next_dealer_seat is None:
            raise ValueError("最終戦では次戦を開始できません。")
        self.game_number += 1
        self.state = new_game(
            len(self.players),
            self._seed + self.game_number,
            dealer=int(next_dealer_seat),
        )
        self._series_waiting = False
        self._series_deadline = None
        self.last_round = None
        self._events = []
        self._event_seq = 0
        self._consumed_events = 0
        self.revision += 1
        self._consume_events()
        return True

    def tick(self, now: float | None = None) -> bool:
        """連戦待機中の pending tick。"""
        if not self._series_waiting or self.state is None:
            return False
        current = self._now() if now is None else float(now)
        if self._all_confirmed() or current >= float(self._series_deadline or 0):
            return self._start_next_match()
        return False

    def confirm(self, player_id: str) -> bool:
        """人間プレイヤーの次戦開始確認。"""
        if not self._series_waiting or self.state is None:
            raise InvalidAction("次戦待ちではありません。")
        if str(player_id).startswith("cpu"):
            return False
        if self.player_id_at(self.seat_of(player_id)) != player_id:
            raise InvalidAction("このゲームのプレイヤーではありません。")
        self.series_confirmed.add(player_id)
        changed = True
        if self._all_confirmed():
            self._start_next_match()
            changed = True
        self.revision += 1
        return changed

    # ------------------------------------------------------------------ 操作

    def act(self, player_id: str, payload: dict[str, Any]) -> None:
        from gameplatform.errors import InvalidAction

        if self.state is None:
            raise InvalidAction("ゲームがまだ開始していません。")
        if self.state.phase != "playing":
            raise InvalidAction("ゲームは終了しています。")
        if not isinstance(payload, dict):
            raise InvalidAction("操作の形式が不正です。")

        action_type = payload.get("type")
        if action_type != "play":
            raise InvalidAction("type は play を指定してください。")

        card_id = payload.get("cardId")
        if not isinstance(card_id, str):
            raise InvalidAction("cardId が不正です。")

        seat = self.seat_of(player_id)
        card_index = next(
            (
                index
                for index, card in enumerate(self.state.players[seat].hand)
                if card_key(card) == card_id
            ),
            None,
        )
        if card_index is None:
            raise InvalidAction("手札にないカードです。")
        card = self.state.players[seat].hand[card_index]

        target = payload.get("target")
        target_seat: int | None = None
        if card.kind == "shot":
            if not isinstance(target, str):
                raise InvalidAction("SHOT は指名先を指定してください。")
            target_seat = self.seat_by_id.get(target)
            if target_seat is None:
                raise InvalidAction("SHOT の指名先が不正です。")
        elif target is not None:
            raise InvalidAction("SHOT 以外のカードに指名先は指定できません。")

        before = self.state
        action = Action(player=seat, card_index=card_index, target=target_seat)
        try:
            after = apply(before, action)
        except NeuError as exc:
            raise InvalidAction(str(exc)) from exc

        self.state = after
        self.revision += 1
        self._consume_events()
        round_end = self._detect_round_end(before, after, card)
        if round_end is not None:
            self.last_round = round_end
        self._on_match_end(after)

    def view(self, player_id: str) -> dict[str, Any]:
        return build_view(self, player_id)

    # ------------------------------------------------------------------ ログ

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events[-limit:]]

    def _consume_events(self) -> None:
        if self.state is None:
            return
        new_events = self.state.events[self._consumed_events :]
        self._consumed_events = len(self.state.events)
        for raw in new_events:
            self._log_event(str(raw))

    def _translate(self, text: str) -> str:
        return _SEAT_PATTERN.sub(
            lambda match: self._safe_name(int(match.group(1))), text
        )

    def _safe_name(self, seat: int) -> str:
        if 0 <= seat < len(self.player_ids):
            return self.name_at(seat)
        return f"席{seat}"

    def _add_event(self, kind: str, message: str, **fields: Any) -> None:
        self._event_seq += 1
        event = {"seq": self._event_seq, "kind": kind, "message": message}
        event.update({key: value for key, value in fields.items() if value is not None})
        self._events.append(event)

    def _log_event(self, raw: str) -> None:
        message = self._translate(raw)
        start = re.match(r"席(\d+)", raw)
        seat = int(start.group(1)) if start else None
        player_id = self.player_id_at(seat) if seat is not None else None
        player_name = self.name_at(seat) if seat is not None else None

        if raw.startswith("ラウンド") and "開始" in raw:
            round_match = re.search(r"ラウンド(\d+)", raw)
            self._add_event(
                "round_start",
                message,
                round=int(round_match.group(1)) if round_match else None,
            )
            return

        if raw.startswith("ゲーム終了"):
            winner_match = re.search(r"勝者は席(\d+)", raw)
            winner_seat = int(winner_match.group(1)) if winner_match else None
            self._add_event(
                "game_over",
                message,
                winnerId=(
                    self.player_id_at(winner_seat)
                    if winner_seat is not None
                    else None
                ),
                winnerName=(
                    self.name_at(winner_seat) if winner_seat is not None else None
                ),
            )
            return

        if "ラウンド負け" in raw:
            # ラウンド負けは構造化された round_results から作る。
            result = (
                self.state.round_results[-1]
                if self.state is not None and self.state.round_results
                else {}
            )
            reason = str(result.get("reason") or "no_playable_card")
            loser_seat = int(result.get("loser") or (start.group(1) if start else 0))
            loser_id = self.player_id_at(loser_seat)
            loser_name = self.name_at(loser_seat)
            card_id = str(result.get("card_id")) if result.get("card_id") else None
            sum_after = (
                int(result.get("sum_after"))
                if reason == "bust" and result.get("sum_after") is not None
                else None
            )
            self._add_event(
                "round_end",
                message,
                playerId=loser_id,
                playerName=loser_name,
                loserId=loser_id,
                loserName=loser_name,
                cardId=card_id,
                sumAfter=sum_after,
                reason=reason,
            )
            return

        if "チップを失って脱落" in raw:
            self._add_event(
                "eliminated",
                message,
                playerId=player_id,
                playerName=player_name,
                eliminated=True,
            )
            return

        if "チップを1枚失った" in raw:
            chips_match = re.search(r"残り (\d+)枚", raw)
            self._add_event(
                "chips",
                message,
                playerId=player_id,
                playerName=player_name,
                chips=int(chips_match.group(1)) if chips_match else None,
            )
            return

        if "が SHOT で席" in raw:
            target_match = re.search(r"SHOT で席(\d+) を指名", raw)
            target_seat = int(target_match.group(1)) if target_match else None
            self._add_event(
                "play",
                message,
                playerId=player_id,
                playerName=player_name,
                cardId="shot",
                targetId=(
                    self.player_id_at(target_seat)
                    if target_seat is not None
                    else None
                ),
                targetName=(
                    self.name_at(target_seat) if target_seat is not None else None
                ),
            )
            return

        for card_id in ("turn", "double", "pass"):
            if f"が {card_id.upper() if card_id != 'pass' else 'PASS'}" in raw:
                sum_match = re.search(r"合計 (-?\d+)", raw)
                self._add_event(
                    "play",
                    message,
                    playerId=player_id,
                    playerName=player_name,
                    cardId=card_id,
                    cardName=card_label_from_id(card_id),
                    sumAfter=int(sum_match.group(1)) if sum_match else None,
                )
                return

        play_match = re.search(r"が (\w+) を出した \(合計 (-?\d+)\)", raw)
        if play_match:
            self._add_event(
                "play",
                message,
                playerId=player_id,
                playerName=player_name,
                cardId=play_match.group(1),
                cardName=card_label_from_id(play_match.group(1)),
                sumAfter=int(play_match.group(2)),
            )
            return

        self._add_event(
            "info",
            message,
            playerId=player_id,
            playerName=player_name,
        )

    # ------------------------------------------------------------------ ラウンド結果

    def _detect_round_end(
        self, before: GameState, after: GameState, card: Card
    ) -> dict[str, Any] | None:
        # ラウンド結果が1件以上増えていれば、その操作でラウンド負けが起きたということ。
        if len(after.round_results) <= len(before.round_results):
            return None
        result = after.round_results[-1]
        # 101 超えでカードを出したらバーストするのではなく、
        # 出せるカードがすべて 101 超えならカードを出さずにラウンド負けする。
        reason = str(result.get("reason") or "no_playable_card")
        loser_seat = int(result.get("loser") or 0)
        loser_after = after.players[loser_seat]
        winner_seat = after.winner
        game_over = bool(result.get("game_over"))
        card_id = str(result.get("card_id")) if result.get("card_id") else None
        sum_after = (
            int(result.get("sum_after"))
            if reason == "bust" and result.get("sum_after") is not None
            else None
        )
        # 負けた時点の合計（リセット前の合計）を結果に残す。
        sum_value = result.get("sum")
        sum_pre = int(sum_value) if sum_value is not None else after.total
        round_number = int(result.get("round") or after.round_number)
        loser_id = self.player_id_at(loser_seat)
        loser_name = self.name_at(loser_seat)
        message = f"{loser_name} さんは出せるカードがすべて101を超えるため負け"
        return {
            "round": round_number,
            "loserId": loser_id,
            "loserName": loser_name,
            "cardId": card_id,
            "card": card_to_view(card) if reason == "bust" else None,
            "sumAfter": sum_after,
            "sum": sum_pre,
            "chipsLeft": int(result.get("chips_left") or loser_after.chips),
            "chips": int(result.get("chips_left") or loser_after.chips),
            "eliminated": bool(result.get("eliminated")),
            "alive": loser_after.alive,
            "gameOver": game_over,
            "winnerId": (
                self.player_id_at(winner_seat) if winner_seat is not None else None
            ),
            "winnerName": (
                self.name_at(winner_seat) if winner_seat is not None else None
            ),
            "nextRound": (
                None
                if game_over
                else {
                    "round": after.round_number,
                    "firstPlayer": (
                        self.player_id_at(after.current_player)
                        if after.current_player is not None
                        else None
                    ),
                    "sum": 0,
                }
            ),
            "message": message,
            "reason": reason,
        }


def card_label_from_id(card_id: str) -> str:
    """カード ID だけから表示名を引く（ログ用）。"""
    return CARD_LABELS.get(card_id, card_id)
