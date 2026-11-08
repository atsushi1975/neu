"""PASS / TURN / SHOT と手番進行のテスト。"""

import unittest

from neu.engine import (
    Action,
    CardNotInHand,
    IllegalAction,
    InvalidTarget,
    NotYourTurn,
    apply,
    legal_actions,
    new_game,
)

from tests.helpers import cards, make_state


class TurnOrderTest(unittest.TestCase):
    def test_normal_play_advances_clockwise(self):
        state = make_state([cards("n1", "pass", "pass")] * 4, current=0, direction=1)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.current_player, 1)
        self.assertEqual(after.direction, 1)
        self.assertEqual(after.cards_remaining, 1)

    def test_turn_wraps_around(self):
        state = make_state([cards("n1", "pass", "pass")] * 4, current=3, direction=1)
        after = apply(state, Action(3, 0))
        self.assertEqual(after.current_player, 0)

    def test_play_skips_eliminated_player(self):
        state = make_state(
            [cards("n1", "pass", "pass")] * 4,
            current=0,
            chips=[3, 0, 3, 3],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.current_player, 2)

    def test_pass_keeps_sum_and_advances(self):
        state = make_state(
            [cards("pass", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            total=42,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 42)
        self.assertEqual(after.current_player, 1)

    def test_turn_flips_direction_and_moves_backwards(self):
        state = make_state(
            [cards("n1", "n1", "n1"), cards("turn", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            current=1,
            direction=1,
        )
        after = apply(state, Action(1, 0))
        self.assertEqual(after.direction, -1)
        self.assertEqual(after.current_player, 0)

    def test_turn_twice_returns_clockwise(self):
        state = make_state(
            [cards("turn", "n1", "n2")] * 4,
            current=0,
            direction=1,
        )
        after = apply(state, Action(0, 0))
        self.assertEqual(after.direction, -1)
        after = apply(after, Action(3, 0))  # 反時計回りで 0 の次は 3
        self.assertEqual(after.direction, 1)
        self.assertEqual(after.current_player, 0)

    def test_shot_moves_turn_to_target(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 3,
            current=0,
            direction=1,
        )
        after = apply(state, Action(0, 0, target=2))
        self.assertEqual(after.current_player, 2)
        self.assertEqual(after.total, 0)

    def test_shot_legal_targets_exclude_self_and_dead(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 3,
            current=0,
            chips=[3, 3, 0, 3],
        )
        shot_actions = [a for a in legal_actions(state) if a.target is not None]
        self.assertEqual({a.target for a in shot_actions}, {1, 3})

    def test_shot_cannot_target_dead_player(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 3,
            current=0,
            chips=[3, 3, 0, 3],
        )
        with self.assertRaises(InvalidTarget):
            apply(state, Action(0, 0, target=2))

    def test_shot_cannot_target_self(self):
        state = make_state(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
        )
        with self.assertRaises(InvalidTarget):
            apply(state, Action(0, 0, target=0))

    def test_not_your_turn(self):
        state = make_state([cards("n1", "pass", "pass")] * 3, current=1)
        with self.assertRaises(NotYourTurn):
            apply(state, Action(0, 0))

    def test_card_not_in_hand(self):
        state = make_state([cards("n1", "pass", "pass")] * 3, current=0)
        with self.assertRaises(CardNotInHand):
            apply(state, Action(0, 99))

    def test_target_on_non_shot_is_rejected(self):
        state = make_state([cards("n1", "pass", "pass")] * 3, current=0)
        with self.assertRaises(IllegalAction):
            apply(state, Action(0, 0, target=1))

    def test_first_player_is_after_dealer(self):
        for players in range(2, 8):
            with self.subTest(players=players):
                state = new_game(players, seed=players)
                self.assertEqual(state.dealer, 0)
                self.assertEqual(state.direction, 1)
                self.assertEqual(state.current_player, (state.dealer + 1) % players)
                self.assertTrue(all(len(p.hand) == 3 for p in state.players))


if __name__ == "__main__":
    unittest.main()
