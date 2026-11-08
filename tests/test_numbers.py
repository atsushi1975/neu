"""数字カードと合計 (sum)、101 超えのテスト。"""

import unittest

from neu.engine import Action, apply, is_game_over

from tests.helpers import cards, make_state


class NumberCardTest(unittest.TestCase):
    def test_positive_number_adds(self):
        state = make_state([cards("n5", "pass", "pass"), cards("n1", "n1", "n1")], total=10)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 15)
        self.assertEqual(after.current_player, 1)

    def test_negative_numbers_subtract(self):
        for card_id, delta in (("nm1", -1), ("nm10", -10)):
            with self.subTest(card_id=card_id):
                state = make_state(
                    [cards(card_id, "pass", "pass"), cards("n1", "n1", "n1")],
                    total=5,
                )
                after = apply(state, Action(0, 0))
                self.assertEqual(after.total, 5 + delta)

    def test_negative_sum_is_allowed(self):
        state = make_state([cards("nm1", "pass", "pass"), cards("n1", "n1", "n1")], total=0)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, -1)
        self.assertFalse(is_game_over(after))
        self.assertEqual(after.players[0].chips, 3)

    def test_101_sets_sum_to_101(self):
        state = make_state([cards("n101", "pass", "pass"), cards("n1", "n1", "n1")], total=37)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 101)
        self.assertFalse(is_game_over(after))
        self.assertEqual(after.players[0].chips, 3)
        self.assertEqual(after.current_player, 1)

    def test_exactly_101_is_safe(self):
        state = make_state([cards("n9", "pass", "pass"), cards("n1", "n1", "n1")], total=92)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 101)
        self.assertEqual(after.players[0].chips, 3)

    def test_over_101_loses_round_and_chip(self):
        state = make_state(
            [cards("n5", "pass", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=100,
            chips=[3, 3, 3],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.players[0].chips, 2)
        self.assertTrue(after.players[0].alive)
        self.assertEqual(after.round_number, 2)  # 新しいラウンドが始まっている
        self.assertFalse(is_game_over(after))
        # 新ラウンドなので合計はリセットされている
        self.assertLessEqual(after.total, 101)

    def test_after_101_any_positive_loses(self):
        hands = [cards("n101", "pass", "pass"), cards("n1", "pass", "pass"), cards("pass", "pass", "pass")]
        state = make_state(hands, total=0)
        state = apply(state, Action(0, 0))
        self.assertEqual(state.total, 101)
        self.assertEqual(state.current_player, 1)

        state = apply(state, Action(1, 0))
        self.assertEqual(state.players[1].chips, 2)

    def test_loss_rotates_dealer_to_loser(self):
        state = make_state(
            [cards("n5", "pass", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=100,
            chips=[3, 3, 3],
            dealer=2,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.dealer, 0)


if __name__ == "__main__":
    unittest.main()
