"""ノイの CPU（カード選択・SHOT 指名・対戦の終了性）のテスト。"""

from __future__ import annotations

import random
import unittest

from gameplatform import rooms
from games.neu.adapter import NeuGame
from games.neu.cards import card_key
from games.neu.cpu import (
    RED_CARD_IDS,
    SHOT_HISTORY_WINDOW,
    CpuDriver,
    choose_action,
    choose_card_index,
    choose_shot_target,
    red_ratios_from_events,
)
from neu.engine import Action, legal_actions

from tests.helpers import card, cards, make_state


class ScriptedRandom(random.Random):
    """分岐テスト用。random() と choice() の結果を固定する。"""

    def __init__(self, value: float = 0.0, choice_index: int = 0) -> None:
        super().__init__(0)
        self.value = value
        self.choice_index = choice_index

    def random(self) -> float:
        return self.value

    def choice(self, seq):
        values = list(seq)
        return values[self.choice_index % len(values)]


def all_actions(hand) -> list[Action]:
    return [Action(player=0, card_index=index) for index in range(len(hand))]


class CardChoiceTest(unittest.TestCase):
    def test_red_only_chooses_max_red_most_of_the_time(self):
        hand = cards("n1", "n5", "n9")
        index = choose_card_index(hand, all_actions(hand), ScriptedRandom(0.0))
        self.assertEqual(hand[index].id, "n9")

    def test_red_only_can_choose_random_card(self):
        hand = cards("n1", "n5", "n9")
        index = choose_card_index(
            hand, all_actions(hand), ScriptedRandom(0.95, choice_index=0)
        )
        self.assertEqual(hand[index].id, "n1")

    def test_no_red_chooses_random_card(self):
        hand = cards("nm1", "nm10", "pass")
        index = choose_card_index(
            hand, all_actions(hand), ScriptedRandom(0.95, choice_index=1)
        )
        self.assertEqual(hand[index].id, "nm10")

    def test_mixed_chooses_max_red_most_of_the_time(self):
        hand = cards("n5", "nm10", "pass")
        index = choose_card_index(hand, all_actions(hand), ScriptedRandom(0.0))
        self.assertEqual(hand[index].id, "n5")

    def test_mixed_can_choose_non_red_card(self):
        hand = cards("n5", "nm10", "pass")
        index = choose_card_index(
            hand, all_actions(hand), ScriptedRandom(0.9, choice_index=0)
        )
        self.assertEqual(hand[index].id, "nm10")

    def test_max_red_tie_is_broken_randomly(self):
        hand = [card("n5", 1), card("n5", 2), card("pass", 1)]
        index = choose_card_index(
            hand, all_actions(hand), ScriptedRandom(0.0, choice_index=1)
        )
        self.assertEqual(index, 1)
        self.assertEqual(hand[index].value, 5)

    def test_red_card_ids_are_only_positive_number_cards(self):
        self.assertEqual(
            RED_CARD_IDS,
            frozenset(
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
            ),
        )


class ShotTargetTest(unittest.TestCase):
    def test_zero_ratio_is_not_chosen_when_another_ratio_is_positive(self):
        target = choose_shot_target(
            [1, 2, 3],
            {1: 0.0, 2: 0.5, 3: 0.5},
            ScriptedRandom(0.0),
        )
        self.assertEqual(target, 2)

    def test_probability_is_proportional_to_red_ratio(self):
        ratios = {1: 0.0, 2: 0.5, 3: 0.5}
        self.assertEqual(
            choose_shot_target([1, 2, 3], ratios, ScriptedRandom(0.25)), 2
        )
        self.assertEqual(
            choose_shot_target([1, 2, 3], ratios, ScriptedRandom(0.75)), 3
        )

    def test_all_zero_ratios_are_uniform(self):
        target = choose_shot_target(
            [1, 2, 3],
            {1: 0.0, 2: 0.0, 3: 0.0},
            ScriptedRandom(0.0, choice_index=2),
        )
        self.assertEqual(target, 3)

    def test_no_candidates_returns_none(self):
        self.assertIsNone(choose_shot_target([], {}, ScriptedRandom(0.0)))


class HistoryTest(unittest.TestCase):
    def test_red_ratio_uses_last_n_cards_per_player(self):
        events = [
            "席1 が nm1 を出した (合計 -1)",
            "席1 が n1 を出した (合計 0)",
            "席1 が n2 を出した (合計 2)",
            "席1 が n3 を出した (合計 5)",
            "席1 が n4 を出した (合計 9)",
            "席1 が n5 を出した (合計 14)",
        ]
        ratios = red_ratios_from_events(events, [1, 2], window=SHOT_HISTORY_WINDOW)
        # 直近5枚はすべて赤数字。最初の nm1 は窓の外に出る。
        self.assertEqual(ratios[1], 1.0)
        # 1枚も出していない人は 0。
        self.assertEqual(ratios[2], 0.0)

    def test_instruction_cards_count_as_non_red(self):
        events = [
            "席1 が n1 を出した (合計 1)",
            "席1 が PASS (合計 1 のまま)",
            "席1 が TURN (向き: 反時計回り)",
            "席1 が SHOT で席2 を指名",
            "席1 が DOUBLE (次の手番は2枚)",
        ]
        ratios = red_ratios_from_events(events, [1], window=5)
        self.assertEqual(ratios[1], 1 / 5)


class LegalActionsTest(unittest.TestCase):
    def test_double_is_not_chosen_as_first_card_of_double(self):
        state = make_state(
            [cards("double", "n1", "n2"), cards("n1", "n1", "n1")],
            remaining=2,
        )
        for seed in range(50):
            action = choose_action(state, random.Random(seed))
            self.assertIsNotNone(action)
            self.assertNotEqual(
                state.players[action.player].hand[action.card_index].id, "double"
            )

    def test_shot_target_is_always_in_legal_actions(self):
        state = make_state(
            [cards("shot", "n1", "n2"), cards("n1", "n1", "n1"), cards("n1", "n1", "n1")],
            current=0,
        )
        legal_targets = {
            action.target for action in legal_actions(state) if action.target is not None
        }
        for seed in range(50):
            action = choose_action(state, random.Random(seed))
            self.assertIsNotNone(action)
            if state.players[action.player].hand[action.card_index].kind == "shot":
                self.assertIn(action.target, legal_targets)


class CpuDelayTest(unittest.TestCase):
    def test_cpu_turn_delay_matches_hannin(self):
        # 犯人は踊ると同じく、共通基盤が1手ごとに1.5秒待つ。
        self.assertEqual(rooms.CPU_TURN_DELAY_SECONDS, 1.5)


class CpuGameTest(unittest.TestCase):
    def test_cpu_only_games_finish_for_multiple_seeds(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                ids = [f"cpu-{index}" for index in range(3)]
                game = NeuGame(
                    [(player_id, f"CPU{index}") for index, player_id in enumerate(ids)],
                    {},
                    seed=seed,
                )
                game.start()
                driver = CpuDriver(game, ids, {}, rng=random.Random(seed))
                steps = 0
                while driver.step(game):
                    steps += 1
                    self.assertLess(steps, 5000)
                self.assertEqual(game.state.phase, "game_over")
                self.assertIsNotNone(game.state.winner)

    def test_human_turn_is_not_advanced_by_cpu(self):
        game = NeuGame([("human", "人間"), ("cpu-0", "CPU1")], {}, seed=1)
        game.start()
        game.state.current_player = 0
        driver = CpuDriver(game, ["cpu-0"], {}, rng=random.Random(0))
        self.assertFalse(driver.step(game))
        self.assertEqual(game.state.current_player, 0)

    def test_human_and_cpu_game_finishes(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                game = NeuGame(
                    [("human", "人間"), ("cpu-0", "CPU1"), ("cpu-1", "CPU2")],
                    {},
                    seed=seed,
                )
                game.start()
                cpu_ids = ["cpu-0", "cpu-1"]
                driver = CpuDriver(game, cpu_ids, {}, rng=random.Random(seed))
                human_rng = random.Random(seed + 100)
                steps = 0
                while game.state.phase == "playing":
                    current_id = game.player_id_at(game.state.current_player)
                    if current_id in cpu_ids:
                        self.assertTrue(driver.step(game))
                    else:
                        actions = legal_actions(game.state)
                        self.assertTrue(actions)
                        action = human_rng.choice(actions)
                        hand = game.state.players[action.player].hand
                        payload = {
                            "type": "play",
                            "cardId": card_key(hand[action.card_index]),
                        }
                        if action.target is not None:
                            payload["target"] = game.player_id_at(action.target)
                        game.act(current_id, payload)
                    steps += 1
                    self.assertLess(steps, 5000)
                self.assertEqual(game.state.phase, "game_over")
                self.assertIsNotNone(game.state.winner)


if __name__ == "__main__":
    unittest.main()
