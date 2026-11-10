"""ラウンド開始・終了、ディーラー、脱落、ゲーム終了のテスト。"""

import unittest

from neu.engine import (
    Action,
    apply,
    is_game_over,
    legal_actions,
    new_game,
    next_alive,
    safe_actions,
)

from tests.helpers import card, cards, filler, make_state, start_round_with_draws


class RoundStartTest(unittest.TestCase):
    def test_initial_field_skips_instructions_and_101(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("pass", "turn", "shot", "double", "n101", "n4")
        state = start_round_with_draws(draws, num_players=3, dealer=0)

        # 指示カード・n101 は場札の山に積んでやり直し、最後の数字が一番上に来る。
        self.assertEqual([c.id for c in state.table], ["pass", "turn", "shot", "double", "n101", "n4"])
        self.assertEqual(state.table[-1].id, "n4")
        self.assertEqual(state.total, 4)
        self.assertEqual([len(p.hand) for p in state.players], [3, 3, 3])
        self.assertEqual(state.round_number, 1)
        self.assertEqual(state.direction, 1)
        # 最初の手番はディーラー本人（修正2）
        self.assertEqual(state.current_player, 0)

    def test_initial_field_101_is_skipped(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("n101", "n3")
        state = start_round_with_draws(draws, num_players=3)
        self.assertEqual(state.table[-1].id, "n3")
        self.assertEqual(state.total, 3)
        self.assertEqual([c.id for c in state.table], ["n101", "n3"])

    def test_initial_field_can_start_negative(self):
        for card_id, expected in (("nm1", -1), ("nm10", -10)):
            with self.subTest(card_id=card_id):
                deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
                draws = deal + cards(card_id)
                state = start_round_with_draws(draws, num_players=3)
                self.assertEqual(state.table[-1].id, card_id)
                self.assertEqual(state.total, expected)

    def test_first_player_is_the_dealer(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        draws = deal + cards("n4")
        for dealer, expected_first in ((0, 0), (1, 1), (2, 2)):
            with self.subTest(dealer=dealer):
                state = start_round_with_draws(draws, num_players=3, dealer=dealer)
                self.assertEqual(state.current_player, expected_first)


class RoundEndTest(unittest.TestCase):
    def _losing_state(self, **kwargs):
        # 席0 が安全な手（PASS）を持ち、席1 が安全なカードを持たない状態を作る。
        hands = [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")]
        return make_state(hands, total=100, **kwargs)

    def test_loser_keeps_dealer_if_alive(self):
        state = self._losing_state(chips=[3, 3, 3], dealer=1)
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ、席1 は出せない → 負け
        self.assertEqual(after.players[1].chips, 2)
        self.assertEqual(after.dealer, 1)
        self.assertEqual(after.round_number, 2)
        self.assertEqual(after.direction, 1)
        # 次のラウンドの最初の手番はディーラー本人（＝負けた人）
        self.assertEqual(after.current_player, 1)

    def test_dealer_moves_clockwise_when_loser_eliminated(self):
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10"), cards("n1", "n1", "n1")],
            total=100,
            current=0,
            chips=[3, 1, 3],
            dealer=1,
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 は出せない → 負け → 脱落
        self.assertFalse(after.players[1].alive)
        self.assertEqual(after.dealer, 2)  # 脱落者の次の生存者が新しいディーラー
        self.assertEqual(after.current_player, 2)
        self.assertEqual(after.phase, "playing")

    def test_dealer_moves_counterclockwise_when_direction_reversed(self):
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n1", "n1", "n1"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            direction=-1,
            chips=[3, 3, 1],
            dealer=2,
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 反時計回りで次は席2 → 出せない → 負け
        self.assertFalse(after.players[2].alive)
        self.assertEqual(after.dealer, 1)  # 反時計回り方向の隣が新しいディーラー
        self.assertEqual(after.direction, -1)  # 向きはリセットしない（引き継ぐ）
        self.assertEqual(after.current_player, 1)

    def test_direction_is_carried_over_on_new_round(self):
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10"), cards("n1", "n1", "n1")],
            total=100,
            current=0,
            direction=-1,
            chips=[3, 3, 3],
            dealer=1,
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番 → 負け
        self.assertEqual(after.direction, -1)  # 向きはリセットしない

    def test_game_over_when_one_survivor(self):
        hands = [cards("n50", "n50", "n10"), cards("pass", "pass", "pass")]
        state = make_state(hands, total=100, chips=[1, 3], current=1)
        after = apply(state, Action(1, 0))  # 席1 が PASS → 席0 は出せない → 負け → 脱落 → ゲーム終了
        self.assertTrue(is_game_over(after))
        self.assertEqual(after.phase, "game_over")
        self.assertEqual(after.winner, 1)
        self.assertEqual(after.players[0].chips, 0)
        self.assertEqual(legal_actions(after), [])
        self.assertEqual(after.round_number, 1)  # 新ラウンドは始まらない

    def test_game_over_has_exactly_one_alive_player(self):
        hands = [cards("n50", "n50", "n10"), cards("pass", "pass", "pass")]
        state = make_state(hands, total=100, chips=[1, 3], current=1)
        after = apply(state, Action(1, 0))
        self.assertEqual([p.seat for p in after.players if p.alive], [1])

    def test_new_round_does_not_collect_cards(self):
        state = new_game(4, seed=2)
        state.total = 100
        player = state.current_player
        state.players[player].hand[0] = card("n1")
        next_player = next_alive(state, player, state.direction)
        state.players[next_player].hand = [card("n50"), card("n50"), card("n10")]
        after = apply(state, Action(player, 0))

        self.assertEqual(after.round_number, 2)
        # 回収・シャッフル・再配布はしないので、カード総数は常に 58 枚のまま。
        self._assert_58(after)
        self.assertTrue(all(len(p.hand) == 3 for p in after.players if p.alive))

    def test_new_round_flips_initial_card_onto_the_table(self):
        state = new_game(3, seed=5)
        state.total = 100
        player = state.current_player
        state.players[player].hand[0] = card("n1")
        next_player = next_alive(state, player, state.direction)
        state.players[next_player].hand = [card("n50"), card("n50"), card("n10")]
        after = apply(state, Action(player, 0))

        # 前のラウンドの場札（初期場札 + 出したカード）が残り、新しい初期場札がその上にめくられる。
        self.assertIn("n1", [c.id for c in after.table])  # 出したカードが場札に積まれる
        self.assertEqual(after.table[-1].kind, "number")  # 新しい初期場札
        self.assertEqual(after.total, after.table[-1].value)

    @staticmethod
    def _assert_58(state):
        total = (
            sum(len(p.hand) for p in state.players)
            + len(state.deck)
            + len(state.table)
        )
        assert total == 58, total


class NoSafeCardTest(unittest.TestCase):
    """「出せるカードがすべて101超え」→ カードを出さずにラウンド負け（修正3）。"""

    def _waiting_state(self, loser_hand, chips=None):
        # 席0 が安全な手（PASS）を持ち、席1 が安全なカードを持たない状態を作る。
        if chips is None:
            chips = [3, 3, 3]
        return make_state(
            [cards("n5", "pass", "pass"), loser_hand],
            total=100,
            current=0,
            chips=chips,
        )

    def test_all_busting_cards_lose_without_playing(self):
        state = self._waiting_state(cards("n50", "n50", "n10"))
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ
        result = after.round_results[-1]
        self.assertEqual(result["reason"], "no_playable_card")
        self.assertEqual(result["loser"], 1)
        self.assertIsNone(result["card_id"])
        self.assertIsNone(result["sum_after"])
        self.assertEqual(result["sum"], 100)
        self.assertEqual(result["chips_left"], 2)
        self.assertEqual(after.players[1].chips, 2)
        self.assertTrue(after.players[1].alive)
        self.assertEqual(after.round_number, 2)
        self.assertIn("出せるカードがすべて101を超えるため", after.events[-2])

    def test_safe_n101_keeps_the_round_alive(self):
        state = self._waiting_state(cards("n101", "n50", "n50"))
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ
        self.assertEqual(after.round_results, [])
        self.assertEqual(after.phase, "playing")
        self.assertEqual(after.current_player, 1)

    def test_safe_pass_keeps_the_round_alive(self):
        state = self._waiting_state(cards("pass", "n50", "n50"))
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ（PASS は安全）
        self.assertEqual(after.round_results, [])
        self.assertEqual(after.current_player, 1)

    def test_safe_negative_keeps_the_round_alive(self):
        state = self._waiting_state(cards("nm1", "n50", "n50"))
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ（nm1 は安全）
        self.assertEqual(after.round_results, [])
        self.assertEqual(after.current_player, 1)

    def test_double_as_first_card_is_not_a_safe_card(self):
        state = make_state(
            [cards("double", "n50", "n50")] + [cards("n1", "n1", "n1")] * 2,
            total=90,
            remaining=2,
            current=0,
        )
        # DOUBLE 中（cards_remaining=2）の1枚目には DOUBLE を出せないので合法手にも入らない。
        # さらに n50 は合計 140 で 101 超えのため、合法手にも入らない → 出せるカードなし。
        self.assertEqual(legal_actions(state), [])
        self.assertEqual(safe_actions(state), [])

    def test_double_second_card_is_checked_before_playing(self):
        state = make_state(
            [cards("n50", "n50", "n50"), cards("n1", "n1", "n1")],
            total=40,
            remaining=2,
            current=0,
            deck=[card("n50"), card("n50")],
        )
        # 1枚目 n50 → 合計 90、残り1枚を出す義務 → 2枚目も n50 で 140 になり安全でない。
        after = apply(state, Action(0, 0))
        self.assertEqual(after.round_results[-1]["reason"], "no_playable_card")
        self.assertEqual(after.players[0].chips, 2)

    def test_no_playable_card_can_eliminate_and_end_the_game(self):
        # 手札がすべて101超えの人がチップ1枚しか持っていない → 脱落してゲーム終了。
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            chips=[3, 1],
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ
        self.assertTrue(is_game_over(after))
        self.assertEqual(after.phase, "game_over")
        self.assertEqual(after.winner, 0)
        result = after.round_results[-1]
        self.assertEqual(result["reason"], "no_playable_card")
        self.assertTrue(result["eliminated"])
        self.assertTrue(result["game_over"])
        self.assertEqual(result["winner"], 0)
        self.assertEqual(result["chips_left"], 0)


class RefillTest(unittest.TestCase):
    def test_refill_draws_from_deck(self):
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[card("n8"), card("n5")],  # pop で n5 が先に引かれる
            table=[],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n5"])
        self.assertEqual([c.id for c in after.deck], ["n8"])
        # 出したカードは場札の一番上に入る
        self.assertEqual([c.id for c in after.table], ["n1"])

    def test_refill_recycles_table_when_deck_empty(self):
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            table=[card("n8"), card("n9")],
        )
        after = apply(state, Action(0, 0))
        # 場札 [n8, n9, n1] のうち、一番上（n1）を残して残りをシャッフルし山札にする
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n9"])
        self.assertEqual([c.id for c in after.deck], ["n8"])
        self.assertEqual([c.id for c in after.table], ["n1"])

    def test_refill_shuffles_all_table_when_keep_top_is_false(self):
        from neu.engine import KEEP_TOP_ON_RESHUFFLE

        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            table=[card("n8"), card("n9")],
        )
        previous = KEEP_TOP_ON_RESHUFFLE
        import neu.engine as engine

        engine.KEEP_TOP_ON_RESHUFFLE = False
        try:
            after = apply(state, Action(0, 0))
        finally:
            engine.KEEP_TOP_ON_RESHUFFLE = previous
        # False なら場札の全部をシャッフルして山札にする
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n1"])
        self.assertEqual([c.id for c in after.deck], ["n8", "n9"])
        self.assertEqual(after.table, [])

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
