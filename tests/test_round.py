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
    start_round,
)

from tests.helpers import card, cards, filler, make_state, start_round_with_draws


class RoundStartTest(unittest.TestCase):
    def test_game_start_deals_cards_and_keeps_the_table_empty(self):
        for num_players in range(2, 8):
            with self.subTest(num_players=num_players):
                state = new_game(num_players, seed=num_players)
                # ゲーム開始は配るだけ。場札は空・合計 0・山札は 58 − 配った枚数。
                self.assertEqual(state.table, [])
                self.assertEqual(state.total, 0)
                self.assertEqual(len(state.deck), 58 - 3 * num_players)
                self.assertTrue(all(len(p.hand) == 3 for p in state.players))
                self.assertEqual(state.round_number, 1)
                # 最初の手番は最初のディーラー本人。
                self.assertEqual(state.current_player, state.dealer)

    def test_round_start_does_not_flip_any_card(self):
        # ラウンド開始では山札から場札をめくらない。手札に使わなかったカードは山札のまま。
        draws = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        state = start_round_with_draws(draws + cards("n10"), num_players=3)
        self.assertEqual(state.table, [])
        self.assertEqual(state.total, 0)
        self.assertEqual([c.id for c in state.deck], ["n10"])

    def test_first_player_is_the_dealer(self):
        deal = cards("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9")
        for dealer, expected_first in ((0, 0), (1, 1), (2, 2)):
            with self.subTest(dealer=dealer):
                state = start_round_with_draws(deal, num_players=3, dealer=dealer)
                self.assertEqual(state.current_player, expected_first)

    def test_round_start_event_mentions_no_field_card(self):
        state = new_game(3, seed=1)
        self.assertEqual(
            state.events[0],
            f"ラウンド1 開始: ディーラー席{state.dealer} / 合計 0 / 手番 席{state.dealer}",
        )
        self.assertNotIn("場札", state.events[0])


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

    def test_round_loss_only_resets_sum_and_turn(self):
        # ラウンド負けのあとに変わるのは「合計が0に戻ること」「最初の手番が負けた人に
        # なること」だけ。山札・場札・手札は、席0 が出した PASS 1枚ぶん（場札に積む・
        # 補充で山札から1枚引く）を除いて変わらない。
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n1", "n2", "n3"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            direction=-1,
            chips=[3, 3, 3],
            dealer=2,
            deck=[card("n7", 1), card("n8", 1)],
            table=[card("n9", 1), card("n2", 2)],
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 反時計回りで席2 の手番 → 出せず負け

        # (a) 合計が 0 に戻る
        self.assertEqual(after.total, 0)
        # (b) 山札は席0 の補充に使った1枚を引かれただけ
        self.assertEqual([c.id for c in after.deck], ["n7"])
        # (c) 場札は出した PASS が積まれただけ（負け処理では場札に触らない）
        self.assertEqual([c.id for c in after.table], ["n9", "n2", "pass"])
        # (d) 場札の一番上は出したカードのまま（負け処理では積み増しされない）
        self.assertEqual(after.table[-1].id, "pass")
        # (e) 全員の手札は負ける前のまま（席0 は PASS を出して補充したぶんだけ変化）
        self.assertEqual([c.id for c in after.players[0].hand], ["n5", "pass", "n8"])
        self.assertEqual([c.id for c in after.players[1].hand], ["n1", "n2", "n3"])
        self.assertEqual([c.id for c in after.players[2].hand], ["n50", "n50", "n10"])
        # (f) 最初の手番は負けた人
        self.assertEqual(after.current_player, 2)
        self.assertEqual(after.round_number, 2)
        # (g) 向きは引き継ぐ
        self.assertEqual(after.direction, -1)

    def test_start_round_keeps_deck_table_hands_and_direction(self):
        # ラウンド継続（start_round）は合計を 0 に戻すだけで、カード・向きには触らない。
        state = make_state(
            [cards("n1", "n2", "n3")] * 3,
            total=77,
            direction=-1,
            chips=[3, 3, 3],
            dealer=2,
            deck=[card("n4", 1), card("n5", 1)],
            table=[card("n6", 1), card("n7", 1)],
            round_number=3,
        )
        start_round(state)

        self.assertEqual(state.total, 0)
        self.assertEqual([c.id for c in state.deck], ["n4", "n5"])
        self.assertEqual([c.id for c in state.table], ["n6", "n7"])
        self.assertEqual(state.table[-1].id, "n7")  # 一番上も変わらない
        self.assertEqual(
            [[c.id for c in p.hand] for p in state.players],
            [["n1", "n2", "n3"]] * 3,
        )
        self.assertEqual(state.direction, -1)
        self.assertEqual(state.round_number, 4)
        self.assertEqual(state.current_player, 2)  # ディーラー（負けた人）から

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

    def test_refill_cannot_recycle_when_table_has_only_the_played_card(self):
        # 山札が尽きて場札が空の状態で出すと、場札は出したカード1枚だけ。
        # KEEP_TOP_ON_RESHUFFLE ではその1枚を残すため引けるカードがなく、
        # 補充は行われない（エラーにならない）。
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            table=[],
        )
        after = apply(state, Action(0, 0))
        self.assertEqual([c.id for c in after.table], ["n1"])
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3"])  # 補充できず
        self.assertEqual(after.deck, [])

    def test_refill_recycles_once_table_has_two_cards(self):
        # 場札が2枚（元からあった1枚 + 出したカード）になれば、上以外を回収して補充できる。
        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            table=[card("n9", 1)],
        )
        after = apply(state, Action(0, 0))
        # 場札 [n9] に n1 が積まれ、n1 を残して n9 が山札へ → 補充で n9 が引かれる
        self.assertEqual([c.id for c in after.table], ["n1"])
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n9"])
        self.assertEqual([c.id for c in after.deck], [])

    def test_refill_with_keep_top_false_recycles_the_played_card(self):
        from neu.engine import KEEP_TOP_ON_RESHUFFLE

        state = make_state(
            [cards("n1", "n2", "n3"), cards("n1", "n1", "n1")],
            deck=[],
            table=[],
        )
        previous = KEEP_TOP_ON_RESHUFFLE
        import neu.engine as engine

        engine.KEEP_TOP_ON_RESHUFFLE = False
        try:
            after = apply(state, Action(0, 0))
        finally:
            engine.KEEP_TOP_ON_RESHUFFLE = previous
        # False なら場札の全部（出したカード1枚だけ）をシャッフルして山札にする
        self.assertEqual(after.table, [])
        self.assertEqual([c.id for c in after.players[0].hand], ["n2", "n3", "n1"])
        self.assertEqual([c.id for c in after.deck], [])

    def test_game_survives_when_deck_and_table_run_dry(self):
        # 山札・場札ともに空の状態から始めても、エラーにならず対戦が続く。
        state = make_state(
            [cards("pass", "pass", "pass")] * 2,
            deck=[],
            table=[],
        )
        for _ in range(6):
            action = legal_actions(state)[0]
            state = apply(state, action)
            self.assertEqual(state.phase, "playing")
            # カードの総数は保存される
            total_cards = (
                sum(len(p.hand) for p in state.players)
                + len(state.deck)
                + len(state.table)
            )
            self.assertEqual(total_cards, 6)

    def test_empty_hand_with_no_cards_loses_the_round_without_error(self):
        # 山札も場札もなく手札も空なら、出せるカードがないためラウンド負けになる。
        state = make_state(
            [cards("pass"), [], cards("n1", "n2", "n3")],
            deck=[],
            table=[],
            current=0,
            chips=[3, 3, 3],
        )
        after = apply(state, Action(0, 0))  # 席0 が PASS → 席1 は手札が空で出せない → 負け
        # 席1 は手札が空のままチップが尽きるまで連鎖で負け、脱落する。
        self.assertEqual([result["loser"] for result in after.round_results], [1, 1, 1])
        self.assertTrue(all(result["reason"] == "no_playable_card" for result in after.round_results))
        self.assertFalse(after.players[1].alive)
        self.assertEqual(after.current_player, 2)
        self.assertEqual(after.total, 0)

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
