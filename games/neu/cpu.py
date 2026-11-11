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
from neu.engine import Action, Card, GameState, legal_actions, projected_total

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

# 黒カード（赤数字以外）の優先度順（上ほど優先）。1か所に集約する。
BLACK_CARD_PRIORITY = ["pass", "turn", "shot", "nm1", "n101", "nm10", "double"]
# 互換用の別名
BLACK_PRIORITY_ORDER = BLACK_CARD_PRIORITY
BLACK_CARD_PRIORITY_ORDER = BLACK_CARD_PRIORITY
BLACK_PRIORITY = BLACK_CARD_PRIORITY

# 確率定数（後から調整しやすいように名前付きで集約）。
EXACT_101_PROBABILITY = 0.9  # 101 ぴったり狙い
SURPRISE_101_PROBABILITY = 0.9  # 特殊 101 の奇襲
RED_ONLY_MAX_PROBABILITY = 0.9  # 赤数字のみ: 最大を選ぶ確率
MIXED_MAX_RED_PROBABILITY = 0.8  # 混在: 最大の赤数字を選ぶ確率
BLACK_ONLY_PRIORITY_PROBABILITY = 0.8  # 黒のみ: 優先度順で選ぶ確率
MIXED_BLACK_PRIORITY_PROBABILITY = 0.8  # 混在の黒分岐: 優先度順で選ぶ確率
PATTERN_A_MAX_PROBABILITY = RED_ONLY_MAX_PROBABILITY
PATTERN_B_PRIORITY_PROBABILITY = BLACK_ONLY_PRIORITY_PROBABILITY
PATTERN_C_MAX_RED_PROBABILITY = MIXED_MAX_RED_PROBABILITY
PATTERN_C_BLACK_PRIORITY_PROBABILITY = MIXED_BLACK_PRIORITY_PROBABILITY
AIM_101_PROBABILITY = EXACT_101_PROBABILITY
SPECIAL_101_PROBABILITY = SURPRISE_101_PROBABILITY

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


def _is_state(obj: Any) -> bool:
    return hasattr(obj, "players") and hasattr(obj, "total") and hasattr(obj, "current_player")


def _extract_context(*args: Any, **kwargs: Any) -> tuple[list[Card], list[Action], random.Random, GameState | None, int]:
    state = kwargs.get("state", None)
    hand = kwargs.get("hand", None)
    actions = kwargs.get("actions", None)
    rng = kwargs.get("rng", None)
    total = kwargs.get("total", None)

    if args:
        first = args[0]
        if _is_state(first):
            state = first
            # remaining positional args may be rng or actions
            for arg in args[1:]:
                if isinstance(arg, random.Random):
                    rng = arg
                elif isinstance(arg, (list, tuple)):
                    # distinguish actions vs hand by content
                    if actions is None:
                        actions = list(arg)
                    elif hand is None:
                        hand = list(arg)
            # also support (state, actions, rng) etc handled above
        else:
            if hand is None and len(args) > 0:
                hand = args[0]
            if actions is None and len(args) > 1:
                actions = args[1]
            if rng is None and len(args) > 2:
                rng = args[2]
            if total is None and len(args) > 3:
                total = args[3]

    if state is not None:
        if hand is None:
            cp = state.current_player
            if cp is None:
                cp = 0
            hand = list(state.players[cp].hand)
        if actions is None:
            try:
                actions = list(legal_actions(state))
            except Exception:
                actions = []
        if total is None:
            total = state.total

    if hand is None:
        hand = []
    if actions is None:
        actions = []
    if total is None:
        total = 0
    if rng is None:
        rng = random.Random()

    return list(hand), list(actions), rng, state, int(total)


def _projected_total(state: GameState | None, total: int, card: Card) -> int:
    if state is not None:
        try:
            return projected_total(state, card)
        except Exception:
            pass
    if card.kind == "hundred":
        return 101
    if card.kind == "number":
        return total + card.value
    return total


def _unique_indices(hand: list[Card], actions: list[Action]) -> list[int]:
    indices: list[int] = []
    seen: set[int] = set()
    for action in actions:
        if action.card_index not in seen:
            seen.add(action.card_index)
            indices.append(action.card_index)
    return indices


def _choose_max_red(red: list[tuple[int, Card]], rng: random.Random) -> int:
    """赤数字のうち値が最大のカードを選ぶ。同値が複数あればその中からランダム。"""
    maximum = max(card.value for _index, card in red)
    tied = [index for index, card in red if card.value == maximum]
    return rng.choice(tied)


def _black_priority(card_id: str) -> int:
    try:
        return BLACK_CARD_PRIORITY.index(card_id)
    except ValueError:
        return len(BLACK_CARD_PRIORITY)


def _choose_priority_black(non_red: list[tuple[int, Card]], rng: random.Random) -> int:
    best = min(_black_priority(card.id) for _index, card in non_red)
    tied = [index for index, card in non_red if _black_priority(card.id) == best]
    return rng.choice(tied)


def choose_card_index(*args: Any, **kwargs: Any) -> int | None:
    """合法手のカードから、手札のインデックスを1つ選ぶ。

    同じカード（SHOT は指名先ごとに合法手が複数ある）は1枚として数える。

    呼び出し形式:
      - choose_card_index(hand, actions, rng)
      - choose_card_index(state, rng)
      - choose_card_index(hand, actions, rng, total=...) / state=...
    """
    hand, actions, rng, state, total = _extract_context(*args, **kwargs)

    indices = _unique_indices(hand, actions)
    if not indices:
        return None

    playable = [(index, hand[index]) for index in indices]
    red = [(index, card) for index, card in playable if card.id in RED_CARD_IDS]
    non_red = [(index, card) for index, card in playable if card.id not in RED_CARD_IDS]

    # 1. 101 ぴったり狙い（n101 は除外: RED_CARD_IDS に含まれないため自動的に除外）
    exact_candidates = [
        (index, card)
        for index, card in red
        if _projected_total(state, total, card) == 101
    ]
    if exact_candidates and rng.random() < EXACT_101_PROBABILITY:
        return rng.choice([index for index, _card in exact_candidates])

    # 2. 特殊 101 の奇襲
    n101_indices = [index for index, card in playable if card.id == "n101"]
    has_other_black_in_hand = any(
        (c.id not in RED_CARD_IDS) and c.id != "n101" for c in hand
    )
    if (
        total <= 50
        and n101_indices
        and has_other_black_in_hand
        and rng.random() < SURPRISE_101_PROBABILITY
    ):
        return n101_indices[0]

    # 3. 通常判定
    if red and not non_red:
        # パターン A: 赤数字のみ
        if rng.random() < RED_ONLY_MAX_PROBABILITY:
            return _choose_max_red(red, rng)
        return rng.choice([index for index, _card in red])

    if non_red and not red:
        # パターン B: 黒カードのみ
        if rng.random() < BLACK_ONLY_PRIORITY_PROBABILITY:
            return _choose_priority_black(non_red, rng)
        return rng.choice([index for index, _card in non_red])

    if red and non_red:
        # パターン C: 混在
        if rng.random() < MIXED_MAX_RED_PROBABILITY:
            return _choose_max_red(red, rng)
        # 黒カードを出す
        if rng.random() < MIXED_BLACK_PRIORITY_PROBABILITY:
            return _choose_priority_black(non_red, rng)
        return rng.choice([index for index, _card in non_red])

    # フォールバック
    return rng.choice(indices)


# 互換用の別名
choose_card = choose_card_index
choose_play = choose_card_index
choose_play_card = choose_card_index
select_card = choose_card_index
pick_card = choose_card_index
choose_card_id = choose_card_index


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
    card_index = choose_card_index(state, rng)
    if card_index is None:
        # フォールバック: 合法手の先頭
        card_index = actions[0].card_index
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