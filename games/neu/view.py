"""プレイヤーごとの公開ビュー。

他人の手札・山札の中身・順番は絶対に含めない。出せるかどうかや
出した後の合計は、必ずエンジンの ``legal_actions`` と ``apply`` を使って求める。
"""

from __future__ import annotations

from typing import Any

from neu.engine import (
    LOSING_LIMIT,
    Action,
    Card,
    GameState,
    apply,
    legal_actions,
    next_alive,
    projected_total,
)

from .cards import card_to_view

DIRECTION_LABELS = {1: "時計回り", -1: "反時計回り"}


def _static_after_sum(state: GameState, card: Card) -> int:
    """手番でないときなど、試し打ちできない場合の合計。"""
    return projected_total(state, card)


def _after_sum(state: GameState, card: Card, action: Action | None) -> int:
    """カードを出した後の合計。ラウンドが続く場合はその値、負けて次ラウンドになった場合は試し打ち用の値。"""
    if action is None:
        return _static_after_sum(state, card)
    try:
        after = apply(state, action)
    except Exception:
        return _static_after_sum(state, card)
    # 負けて次ラウンドが始まった場合も、リセット後の合計ではなく実際の合計を返す。
    if card.is_number and (
        after.round_number != state.round_number or after.phase == "game_over"
    ):
        return projected_total(state, card)
    return after.total


def _hand_view(
    game: Any, state: GameState, seat: int
) -> list[dict[str, Any]]:
    my_turn = state.phase == "playing" and state.current_player == seat
    actions = legal_actions(state) if my_turn else []
    by_index: dict[int, list[Action]] = {}
    for action in actions:
        by_index.setdefault(action.card_index, []).append(action)

    hand: list[dict[str, Any]] = []
    for index, card in enumerate(state.players[seat].hand):
        card_actions = by_index.get(index, [])
        playable = bool(card_actions)
        after_sum = _after_sum(state, card, card_actions[0] if card_actions else None)
        entry = card_to_view(card) or {}
        entry.update(
            {
                "playable": playable,
                "afterSum": after_sum,
                "after_sum": after_sum,
                "busts": card.is_number and after_sum > LOSING_LIMIT,
                "targets": [
                    game.player_id_at(action.target)
                    for action in card_actions
                    if action.target is not None
                ],
            }
        )
        hand.append(entry)
    return hand


def build_view(game: Any, player_id: str) -> dict[str, Any]:
    state = game.state
    if state is None:
        raise ValueError("ゲームがまだ開始していません。")
    seat = game.seat_of(player_id)
    me = state.players[seat]

    players: list[dict[str, Any]] = []
    for player in state.players:
        player_id_at_seat = game.player_id_at(player.seat)
        players.append(
            {
                "id": player_id_at_seat,
                "name": game.name_at(player.seat),
                "seat": player.seat,
                "chips": player.chips,
                "alive": player.alive,
                "handCount": len(player.hand),
                "hand_count": len(player.hand),
                "isCurrent": player.seat == state.current_player,
                "isMe": player.seat == seat,
                "isDealer": player.seat == state.dealer,
            }
        )

    current_player_id = (
        game.player_id_at(state.current_player)
        if state.current_player is not None
        else None
    )
    next_player_id = None
    if state.phase == "playing" and state.current_player is not None:
        next_player_id = game.player_id_at(
            next_alive(state, state.current_player, state.direction)
        )

    winner_id = (
        game.player_id_at(state.winner) if state.winner is not None else None
    )
    table_top = state.table[-1] if state.table else None

    result = game.result
    view: dict[str, Any] = {
        "revision": game.revision,
        "phase": game.phase,
        "round": state.round_number,
        "sum": state.total,
        "direction": state.direction,
        "directionLabel": DIRECTION_LABELS.get(state.direction, "時計回り"),
        "dealer": state.dealer,
        "dealerId": game.player_id_at(state.dealer),
        "currentPlayer": state.current_player,
        "currentPlayerId": current_player_id,
        "nextPlayerId": next_player_id,
        "cardsRemaining": state.cards_remaining,
        "cards_remaining": state.cards_remaining,
        "tableTop": card_to_view(table_top),
        "table_top": card_to_view(table_top),
        "deckCount": len(state.deck),
        "deck_count": len(state.deck),
        "tableCount": len(state.table),
        "table_count": len(state.table),
        "winner": state.winner,
        "winnerId": winner_id,
        "winnerName": game.name_at(state.winner) if state.winner is not None else None,
        "players": players,
        "me": {
            "id": player_id,
            "name": game.name_at(seat),
            "seat": seat,
            "chips": me.chips,
            "alive": me.alive,
            "hand": _hand_view(game, state, seat),
        },
        "lastRound": game.last_round,
        "last_round": game.last_round,
        "events": game.recent_events(),
        "eventMessages": [event["message"] for event in game.recent_events()],
        "result": result,
    }
    return view
