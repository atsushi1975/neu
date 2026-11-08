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
    new_game,
)

from .cards import CARD_LABELS, card_key, card_to_view
from .view import build_view

_SEAT_PATTERN = re.compile(r"席(\d+)")


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

    # ------------------------------------------------------------------ 基本

    @property
    def phase(self) -> str:
        if self.state is None:
            return "lobby"
        return "finished" if self.state.phase == "game_over" else "playing"

    @property
    def result(self) -> dict[str, Any] | None:
        if self.state is None or self.state.winner is None:
            return None
        winner_id = self.player_id_at(self.state.winner)
        return {
            "winnerId": winner_id,
            "winnerName": self.name_at(self.state.winner),
            "message": f"{self.name_at(self.state.winner)} さんの勝ち！",
        }

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

    def start(self) -> None:
        if self.state is not None:
            raise ValueError("ゲームはすでに開始しています。")
        self.state = new_game(len(self.players), self._seed)
        self.revision += 1
        self._consume_events()

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
            # ラウンド負け（バースト・安全なカード無し）は構造化された round_results から作る。
            result = (
                self.state.round_results[-1]
                if self.state is not None and self.state.round_results
                else {}
            )
            reason = str(result.get("reason") or "bust")
            loser_seat = int(result.get("loser") or (start.group(1) if start else 0))
            loser_id = self.player_id_at(loser_seat)
            loser_name = self.name_at(loser_seat)
            card_id = str(result.get("card_id")) if reason == "bust" and result.get("card_id") else None
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
        reason = str(result.get("reason") or "bust")
        loser_seat = int(result.get("loser") or 0)
        loser_after = after.players[loser_seat]
        winner_seat = after.winner
        game_over = bool(result.get("game_over"))
        card_id = str(result.get("card_id")) if reason == "bust" and result.get("card_id") else None
        sum_after = (
            int(result.get("sum_after"))
            if reason == "bust" and result.get("sum_after") is not None
            else None
        )
        round_number = int(result.get("round") or after.round_number)
        loser_id = self.player_id_at(loser_seat)
        loser_name = self.name_at(loser_seat)
        message = (
            f"{loser_name} さんが {card_id} でバースト（合計 {sum_after}）"
            if reason == "bust"
            else f"{loser_name} さんは出せるカードがすべて101を超えるため負け"
        )
        revealed_hand: list[dict[str, Any]] | None = None
        if reason == "no_safe_card":
            revealed_hand = [
                card_to_view(
                    Card(
                        id=str(entry["id"]),
                        kind=str(entry.get("kind") or "number"),
                        value=int(entry.get("value") or 0),
                        copy=int(entry.get("copy") or 1),
                    )
                )
                for entry in (result.get("hand") or [])
            ]
        return {
            "round": round_number,
            "loserId": loser_id,
            "loserName": loser_name,
            "cardId": card_id,
            "card": card_to_view(card) if reason == "bust" else None,
            "sumAfter": sum_after,
            "sum": sum_after,
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
            "nextRound": None if game_over else after.round_number,
            "message": message,
            "reason": reason,
            "revealedHand": revealed_hand,
        }


def card_label_from_id(card_id: str) -> str:
    """カード ID だけから表示名を引く（ログ用）。"""
    return CARD_LABELS.get(card_id, card_id)
