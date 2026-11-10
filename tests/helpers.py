"""テスト共通のヘルパ。"""

from __future__ import annotations

import random
from typing import Optional

from neu.engine import CARD_SPECS, Card, GameState, PlayerState, STARTING_CHIPS

_SPEC = {card_id: (kind, value) for card_id, kind, value, _count in CARD_SPECS}


class NoShuffleRandom(random.Random):
    """シャッフルしない乱数。デッキの並びをテストで固定するために使う。"""

    def shuffle(self, x):  # noqa: D401 - random.Random の差し替え
        return None


def card(card_id: str, copy: int = 1) -> Card:
    kind, value = _SPEC[card_id]
    return Card(id=card_id, kind=kind, value=value, copy=copy)


def cards(*card_ids: str) -> list[Card]:
    return [card(card_id) for card_id in card_ids]


def filler(count: int = 20) -> list[Card]:
    """補充用のダミーカード（すべて数字）。"""
    return [Card(id="n2", kind="number", value=2, copy=i + 1) for i in range(count)]


def make_state(
    hands: list[list[Card]],
    *,
    total: int = 0,
    current: Optional[int] = 0,
    remaining: int = 1,
    direction: int = 1,
    chips: Optional[list[int]] = None,
    deck: Optional[list[Card]] = None,
    table: Optional[list[Card]] = None,
    dealer: int = 0,
    round_number: int = 1,
    events: Optional[list[str]] = None,
) -> GameState:
    """テスト用に状態を直接組み立てる。"""
    count = len(hands)
    if chips is None:
        chips = [STARTING_CHIPS] * count
    players = [
        PlayerState(
            seat=seat,
            chips=chips[seat],
            hand=list(hands[seat]),
            alive=chips[seat] > 0,
        )
        for seat in range(count)
    ]
    return GameState(
        players=players,
        deck=list(deck) if deck is not None else filler(),
        table=list(table) if table is not None else [],
        total=total,
        direction=direction,
        dealer=dealer,
        current_player=current,
        cards_remaining=remaining,
        phase="playing",
        winner=None,
        round_number=round_number,
        events=list(events or []),
        rng=NoShuffleRandom(0),
    )


def start_round_with_draws(draws: list[Card], num_players: int = 3, dealer: int = 0) -> GameState:
    """``draws`` の順に手札を3枚ずつ配って、ラウンドを開始した状態を作る。

    山札は末尾から pop されるので、逆順にして渡す。手札に使わなかった残りは
    そのまま山札として残る。ラウンド開始時に山札から場札をめくることはないため、
    場札は空・合計は 0 で始まる。
    """
    players = [PlayerState(seat=i, chips=STARTING_CHIPS, hand=[], alive=True) for i in range(num_players)]
    deck = list(reversed(draws))
    for player in players:
        for _ in range(3):
            player.hand.append(deck.pop())
    from neu.engine import _begin_round

    state = GameState(
        players=players,
        deck=deck,
        table=[],
        total=0,
        direction=1,
        dealer=dealer,
        current_player=None,
        cards_remaining=1,
        phase="playing",
        winner=None,
        round_number=0,
        events=[],
        rng=NoShuffleRandom(0),
    )
    # ラウンド開始（配布なし）: 合計 0・場札は空のまま・手番はディーラー本人。
    _begin_round(state, deal=False)
    return state


def card_ids(card_list: list[Card]) -> list[str]:
    return [c.id for c in card_list]
