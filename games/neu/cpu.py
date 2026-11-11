"""ノイの CPU プレイヤー。

ルール判定は持たない。``neu.engine.legal_actions`` が返した合法手のうちから
カードと SHOT の指名先を選び、``NeuGame.act``（内部で ``neu.engine.apply``）に
渡すだけにする。

乱数はこのモジュールが持つ ``random.Random`` だけを使い、テストから差し替えられる。
待ち時間は共通基盤 ``gameplatform.rooms`` の ``CPU_TURN_DELAY_SECONDS`` が管理する
（犯人は踊ると同じ方式・同じ値）。
"""

from __future__ import annotations

import logging
import random
import re
from collections import deque
from typing import Any, Iterable

from gameplatform.errors import InvalidAction
from neu.engine import Action, Card, GameState, legal_actions

from .cards import card_key

logger = logging.getLogger("neu.cpu")

# 赤数字（合計をプラスする数字）のカード ID。ここだけを「赤数字」として扱う。
# nm1 / nm10 / n101 / pass / turn / shot / double は赤数字ではない。
RED_CARD_IDS = frozenset(
    {
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
        "n7",
        "n8",
        "n9",
        "n10",
        "n50",
    }
)

# 出せるカードが赤数字だけのとき、最大の赤数字を選ぶ確率。
RED_ONLY_MAX_PROBABILITY = 0.9
# 赤数字と赤数字以外が混在するとき、最大の赤数字を選ぶ確率。
MIXED_MAX_RED_PROBABILITY = 0.8
# SHOT の指名先を決めるときに見る「その人が直近に出したカード」の枚数。
SHOT_HISTORY_WINDOW = 5

# 万一の無限ループ防止。1 ゲームの手数より十分大きい。
MAX_CPU_STEPS = 5000

_PLAY_PATTERNS = (
    re.compile(r"^席(\d+) が (\w+) を出した"),
    re.compile(r"^席(\d+) が (PASS|TURN|SHOT|DOUBLE) "),
)


def _parse_play_event(event: str) -> tuple[int, str] | None:
    """エンジンのイベント文字列から「席番号と出したカード ID」を取り出す。

    数字カードは ``席0 が n5 を出した (合計 5)``、指示カードは
    ``席0 が PASS (...)`` / ``席0 が SHOT で席1 を指名`` などの形で記録される。
    出札以外のイベント（ラウンド開始・チップ・脱落など）は None を返す。
    """
    for pattern in _PLAY_PATTERNS:
        match = pattern.match(event)
        if match:
            return int(match.group(1)), match.group(2)
    return None


def red_ratios_from_events(
    events: Iterable[str],
    seats: Iterable[int],
    window: int = SHOT_HISTORY_WINDOW,
) -> dict[int, float]:
    """各席の「直近 window 枚のうち赤数字だった比率」を返す。

    その人がまだ1枚も出していなければ比率は 0.0。``window`` は人ごとの
    直近カード枚数で、他の人の出札では窓を消費しない。
    """
    if window < 1:
        raise ValueError("window は 1 以上で指定してください")
    seat_list = list(seats)
    history: dict[int, deque[str]] = {
        seat: deque(maxlen=window) for seat in seat_list
    }
    for event in events:
        parsed = _parse_play_event(str(event))
        if parsed is None:
            continue
        seat, card_id = parsed
        if seat in history:
            history[seat].append(card_id)
    ratios: dict[int, float] = {}
    for seat in seat_list:
        cards = history[seat]
        if not cards:
            ratios[seat] = 0.0
        else:
            red = sum(1 for card_id in cards if card_id in RED_CARD_IDS)
            ratios[seat] = red / len(cards)
    return ratios


def choose_card_index(
    hand: list[Card],
    actions: list[Action],
    rng: random.Random,
) -> int | None:
    """合法手のカードから、手札のインデックスを1つ選ぶ。

    同じカード（SHOT は指名先ごとに合法手が複数ある）は1枚として数える。
    """
    indices: list[int] = []
    seen: set[int] = set()
    for action in actions:
        if action.card_index not in seen:
            seen.add(action.card_index)
            indices.append(action.card_index)
    if not indices:
        return None

    playable = [(index, hand[index]) for index in indices]
    red = [(index, card) for index, card in playable if card.id in RED_CARD_IDS]
    non_red = [(index, card) for index, card in playable if card.id not in RED_CARD_IDS]

    if red and not non_red:
        if rng.random() < RED_ONLY_MAX_PROBABILITY:
            return _choose_max_red(red, rng)
        return rng.choice(indices)

    if red and non_red:
        if rng.random() < MIXED_MAX_RED_PROBABILITY:
            return _choose_max_red(red, rng)
        return rng.choice([index for index, _card in non_red])

    # 赤数字が1枚も出せないとき（または合法手が空でないが赤がないとき）は全体からランダム。
    return rng.choice(indices)


def _choose_max_red(red: list[tuple[int, Card]], rng: random.Random) -> int:
    """赤数字のうち値が最大のカードを選ぶ。同値が複数あればその中からランダム。"""
    maximum = max(card.value for _index, card in red)
    tied = [index for index, card in red if card.value == maximum]
    return rng.choice(tied)


def choose_shot_target(
    candidates: Iterable[int],
    red_ratios: dict[int, float],
    rng: random.Random,
) -> int | None:
    """SHOT の指名先を、赤数字だった比率に比例する確率で選ぶ。

    全員の比率が 0 のときは均等ランダム。比率が 0 の人は、他に比率が
    正の人がいる限り選ばれない。
    """
    target_list = list(candidates)
    if not target_list:
        return None
    weights = [max(0.0, float(red_ratios.get(seat, 0.0))) for seat in target_list]
    total = sum(weights)
    if total <= 0.0:
        return rng.choice(target_list)

    pick = rng.random() * total
    cumulative = 0.0
    for seat, weight in zip(target_list, weights):
        cumulative += weight
        if pick < cumulative:
            return seat
    return target_list[-1]


def choose_action(
    state: GameState,
    rng: random.Random,
    *,
    history_window: int = SHOT_HISTORY_WINDOW,
) -> Action | None:
    """現在の手番で CPU が選ぶ合法手を返す。合法手がなければ None。"""
    if state.phase != "playing" or state.current_player is None:
        return None
    actions = legal_actions(state)
    if not actions:
        return None

    player = state.players[state.current_player]
    card_index = choose_card_index(player.hand, actions, rng)
    if card_index is None:
        return None
    card = player.hand[card_index]
    same_card = [action for action in actions if action.card_index == card_index]

    if card.kind == "shot":
        target_seats = [action.target for action in same_card if action.target is not None]
        ratios = red_ratios_from_events(state.events, target_seats, history_window)
        target = choose_shot_target(target_seats, ratios, rng)
        return next(
            (action for action in same_card if action.target == target),
            same_card[0],
        )
    return same_card[0]


class CpuDriver:
    """CPU プレイヤーの手番を1手ずつ進める。

    ``step(game)`` は CPU が1手打てば True、打てなければ False を返す
    （人間の番・ゲーム終了・打てる手がない）。待ち時間は呼び出し側
    （``gameplatform.rooms``）が管理する。
    """

    def __init__(
        self,
        game: Any,
        cpu_ids: list[str],
        config: dict[str, Any] | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.cpu_ids = list(cpu_ids)
        self.config = dict(config or {})
        self.rng = rng if rng is not None else random.Random()
        self.steps = 0

    def step(self, game: Any) -> bool:
        if self.steps >= MAX_CPU_STEPS:
            logger.warning("CPU の手数が上限 %d に達したため止めました。", MAX_CPU_STEPS)
            return False
        state = getattr(game, "state", None)
        if state is None or state.phase != "playing" or state.current_player is None:
            return False

        player_id = game.player_id_at(state.current_player)
        if player_id not in self.cpu_ids:
            return False

        action = choose_action(state, self.rng)
        if action is None:
            return False

        card = state.players[action.player].hand[action.card_index]
        payload: dict[str, Any] = {
            "type": "play",
            "cardId": card_key(card),
        }
        if action.target is not None:
            payload["target"] = game.player_id_at(action.target)
        try:
            game.act(player_id, payload)
        except InvalidAction:
            logger.exception("CPU %s の行動が拒否されました: %s", player_id, payload)
            return False
        self.steps += 1
        return True
