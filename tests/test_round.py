"""ラウンド開始・終了、ディーラー、脱落、ゲーム終了のテスト。"""

import unittest

from neu.engine import (
    Action,
    apply,
    is_game_over,
    legal_actions,
    new_game,
)

from tests.helpers import card, cards, filler, make_state, start_round_with_draws


class RoundStartTest(unittest.TestCase):
    def test_initial_field_skips_instructions_and_101(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("pass", "turn", "shot", "double", "n101", "n4")
        state = start_round_with_draws(draws, num_players=3, dealer=0)

        self.assertEqual(state.field_card.id, "n4")
        self.assertEqual(state.total, 4)
        self.assertEqual([c.id for c in state.discard], ["pass", "turn", "shot", "double", "n101"])
        self.assertEqual([len(p.hand) for p in state.players], [3, 3, 3])
        self.assertEqual(state.round_number, 1)
        self.assertEqual(state.direction, 1)
        self.assertEqual(state.current_player, 1)

    def test_initial_field_101_is_discarded(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("n101", "n3")
        state = start_round_with_draws(draws, num_players=3)
        self.assertEqual(state.field_card.id, "n3")
        self.assertEqual(state.total, 3)
        self.assertEqual([c.id for c in state.discard], ["n101"])

    def test_initial_field_can_start_negative(self):
        for card_id, expected in (("nm1", -1), ("nm10", -10)):
            with self.subTest(card_id=card_id):
                deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
                draws = deal + cards(card_id)
                state = start_round_with_draws(draws, num_players=3)
                self.assertEqual(state.field_card.id, card_id)
                self.assertEqual(state.total, expected)

    def test_dealing_starts_from_dealer_next(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("n4")
        for dealer, expected_first in ((0, 1), (1, 2), (2, 0)):
            with self.subTest(dealer=dealer):
                state = start_round_with_draws(draws, num_players=3, dealer=dealer)
                self.assertEqual(state.current_player, expected_first)


class RoundEndTest(unittest.TestCase):
    def _losing_state(self, **kwargs):
        hands = [cards("n5", "pass", "pass")] + [cards("n1", "n1", "n1")] * 2
        return make_state(hands, total=100, **kwargs)

    def test_loser_keeps_dealer_if_alive(self):
        state = self._losing_state(chips=[3, 3, 3], dealer=2)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.players[0].chips, 2)
        self.assertEqual(after.dealer, 0)
        self.assertEqual(after.round_number, 2)
        self.assertEqual(after.direction, 1)
        self.assertEqual(after.current_player, 1)

    def test_dealer_moves_clockwise_when_loser_eliminated(self):
        state = self._losing_state(chips=[1, 3, 3], dealer=0)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.players[0].chips, 0)
        self.assertFalse(after.players[0].alive)
        self.assertEqual(after.dealer, 1)
        self.assertEqual(after.direction, 1)
        self.assertEqual(after.current_player, 2)

    def test_dealer_moves_counterclockwise_when_direction_reversed(self):
        state = self._losing_state(chips=[1, 3, 3], dealer=0, direction=-1)
        after = apply(state, Action(0, 0))
        self.assertFalse(after.players[0].alive)
        self.assertEqual(after.dealer, 2)
        self.assertEqual(after.direction, 1)  # 新ラウンドで時計回りに戻る
        self.assertEqual(after.current_player, 1)

    def test_direction_resets_to_clockwise_on_new_round(self):
        state = self._losing_state(chips=[3, 3, 3], direction=-1)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.direction, 1)

    def test_game_over_when_one_survivor(self):
        hands = [cards("n5", "pass", "pass"), cards("n1", "n1", "n1")]
        state = make_state(hands, total=100, chips=[1, 3], current=0, round_number=1)
        after = apply(state, Action(0, 0))
        self.assertTrue(is_game_over(after))
        self.assertEqual(after.phase, "game_over")
        self.assertEqual(after.winner, 1)
        self.assertIsNone(after.current_player)
        self.assertEqual(legal_actions(after), [])
        self.assertEqual(after.round_number, 1)  # 新ラウンドは始まらない

    def test_game_over_has_exactly_one_alive_player(self):
        hands = [cards("n5", "pass", "pass"), cards("n1", "n1", "n1")]
        state = make_state(hands, total=100, chips=[1, 3])
        after = apply(state, Action(0, 0))
        self.assertEqual([p.seat for p in after.players if p.alive], [1])

    def test_new_round_collects_all_58_cards(self):
        state = new_game(4, seed=2)
        state.total = 101
        player = state.current_player
        state.players[player].hand[0] = card("n1")
        after = apply(state, Action(player, 0))

        self.assertEqual(after.players[player].chips, 2)
        self.assertEqual(after.round_number, 2)
        self._assert_58(after)
        self.assertTrue(all(len(p.hand) == 3 for p in after.players if p.alive))

    def test_new_round_gathers_field_card_too(self):
        state = new_game(3, seed=5)
        state.total = 101
        player = state.current_player
        state.players[player].hand[0] = card("n1")
        after = apply(state, Action(player, 0))
        self.assertIsNotNone(after.field_card)
        self._assert_58(after)

    @staticmethod
    def _assert_58(state):
        total = (
            sum(len(p.hand) for p in state.players)
            + len(state.deck)
            + len(state.discard)
            + (1 if state.field_card else 0)
        )
        assert total == 58, total


class RefillTest(unittest.TestCase):
    def test_refill_draws_from_deck(self):
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[card("n8"), card("n5")],  # pop で n5 が先に引かれる
            discard=[],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n5"])
        self.assertEqual([c.id for c in after.deck], ["n8"])
        self.assertEqual([c.id for c in after.discard], ["n1"])

    def test_refill_recycles_discard_when_deck_empty(self):
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            discard=[card("n8"), card("n9")],
        )
        after = apply(state, Action(0, 0))
        # 捨て札 [n8, n9, n1] が山札に戻り、n1 が引かれる
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n1"])
        self.assertEqual([c.id for c in after.deck], ["n8", "n9"])
        self.assertEqual(after.discard, [])

    def test_hand_stays_at_three_even_after_many_plays(self):
        state = make_state(
            [cards("pass", "pass", "pass"), cards("pass", "pass", "pass")],
            deck=filler(10),
        )
        for _ in range(5):
            player = state.current_player
            action = legal_actions(state)[0]
            state = apply(state, action)
            self.assertEqual(len(state.players[player].hand), 3)


if __name__ == "__main__":
    unittest.main()
