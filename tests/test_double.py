"""DOUBLE の全パターンのテスト。"""

import unittest

from neu.engine import (
    Action,
    DoubleNotAllowed,
    InvalidTarget,
    apply,
    legal_actions,
)

from tests.helpers import cards, make_state


class DoubleTest(unittest.TestCase):
    def test_normal_two_numbers(self):
        state = make_state(
            [cards("n5", "n6", "pass"), cards("n1", "n1", "n1")],
            total=0,
            remaining=2,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.current_player, 0)
        self.assertEqual(after.cards_remaining, 1)
        self.assertEqual(after.total, 5)

        after = apply(after, Action(0, 0))  # 手札は [n6, pass, 補充]
        self.assertEqual(after.total, 11)
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.cards_remaining, 1)

    def test_first_card_over_101_loses_immediately(self):
        state = make_state(
            [cards("n5", "n6", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=100,
            remaining=2,
            chips=[3, 3, 3],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.players[0].chips, 2)
        self.assertTrue(after.players[0].alive)
        # 2枚目 (n6) は出されていない
        self.assertFalse(any("n6" in event for event in after.events))

    def test_first_pass_transfers_double_duty(self):
        state = make_state(
            [cards("pass", "n1", "n2"), cards("n1", "n1", "n1")],
            total=10,
            remaining=2,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 10)
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.cards_remaining, 2)

    def test_first_turn_transfers_double_duty_and_flips(self):
        state = make_state(
            [cards("turn", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            direction=1,
            remaining=2,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.direction, -1)
        self.assertEqual(after.current_player, 2)
        self.assertEqual(after.cards_remaining, 2)

    def test_first_shot_transfers_double_duty_to_target(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 3,
            remaining=2,
        )
        after = apply(state, Action(0, 0, target=3))
        self.assertEqual(after.current_player, 3)
        self.assertEqual(after.cards_remaining, 2)

    def test_first_card_double_is_forbidden(self):
        state = make_state(
            [cards("double", "n1", "pass"), cards("n1", "n1", "n1")],
            remaining=2,
        )
        indices = [action.card_index for action in legal_actions(state)]
        self.assertNotIn(0, indices)
        self.assertTrue(indices)  # 詰みではない
        with self.assertRaises(DoubleNotAllowed):
            apply(state, Action(0, 0))

    def test_second_card_double_is_allowed(self):
        state = make_state(
            [cards("n1", "double", "pass"), cards("n1", "n1", "n1")],
            remaining=2,
        )
        after = apply(state, Action(0, 0))  # n1
        self.assertEqual(after.cards_remaining, 1)
        # 手札は [double, pass, 補充]
        self.assertIn(0, [action.card_index for action in legal_actions(after)])
        after = apply(after, Action(0, 0))  # double
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.cards_remaining, 2)

    def test_second_card_pass_gives_next_one(self):
        state = make_state(
            [cards("n1", "pass", "pass"), cards("n1", "n1", "n1")],
            remaining=2,
        )
        after = apply(state, Action(0, 0))  # n1
        self.assertEqual(after.cards_remaining, 1)
        after = apply(after, Action(0, 0))  # pass
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.cards_remaining, 1)

    def test_second_card_turn_gives_next_one(self):
        state = make_state(
            [cards("n1", "turn", "pass")] + [cards("n1", "n1", "n1")] * 3,
            direction=1,
            remaining=2,
        )
        after = apply(state, Action(0, 0))  # n1
        after = apply(after, Action(0, 0))  # turn
        self.assertEqual(after.direction, -1)
        self.assertEqual(after.current_player, 3)
        self.assertEqual(after.cards_remaining, 1)

    def test_101_counts_as_number_for_double(self):
        state = make_state(
            [cards("n101", "n1", "pass"), cards("n1", "n1", "n1")],
            total=0,
            remaining=2,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 101)
        self.assertEqual(after.current_player, 0)
        self.assertEqual(after.cards_remaining, 1)

    def test_double_on_normal_turn_sets_next_to_two(self):
        state = make_state(
            [cards("double", "n1", "pass"), cards("n1", "n1", "n1")],
            remaining=1,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.cards_remaining, 2)

    def test_two_doubles_and_a_pass_is_not_deadlocked(self):
        state = make_state(
            [cards("double", "double", "pass"), cards("n1", "n1", "n1")],
            remaining=2,
        )
        actions = legal_actions(state)
        self.assertTrue(actions)
        self.assertTrue(all(action.card_index == 2 for action in actions))

    def test_shot_cannot_target_self(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
        )
        with self.assertRaises(InvalidTarget):
            apply(state, Action(0, 0, target=0))


if __name__ == "__main__":
    unittest.main()
