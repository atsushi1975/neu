"""数字カードと合計 (sum)、101 超えのテスト。"""

import unittest

from neu.engine import Action, IllegalAction, apply, is_game_over, legal_actions

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
        # 次の手番の人にも安全なカード（nm1）を用意しておかないと、101 超えで連鎖負けする。
        state = make_state([cards("n101", "pass", "pass"), cards("nm1", "pass", "pass")], total=37)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 101)
        self.assertFalse(is_game_over(after))
        self.assertEqual(after.players[0].chips, 3)
        self.assertEqual(after.current_player, 1)

    def test_exactly_101_is_safe(self):
        state = make_state([cards("n9", "pass", "pass"), cards("nm1", "pass", "pass")], total=92)
        after = apply(state, Action(0, 0))
        self.assertEqual(after.total, 101)
        self.assertEqual(after.players[0].chips, 3)
        self.assertFalse(is_game_over(after))
        self.assertEqual(after.current_player, 1)

    def test_over_101_card_is_not_playable(self):
        state = make_state(
            [cards("n5", "pass", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=100,
            chips=[3, 3, 3],
        )
        # 合計100で n5（→105）は101超えのため、出せるカードは PASS だけ。
        self.assertEqual([a.card_index for a in legal_actions(state)], [1, 2])
        with self.assertRaises(IllegalAction):
            apply(state, Action(0, 0))
        # 負けていない（出せないだけで、まだラウンドは続く）。
        self.assertEqual(state.round_results, [])
        self.assertEqual(state.players[0].chips, 3)

    def test_no_playable_card_loses_without_playing(self):
        # 合計100で手札が数字カードのみ3枚（すべて超える）→ カードを出さずに負け。
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            chips=[3, 3, 3],
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 の手番へ
        self.assertEqual(after.round_results[-1]["reason"], "no_playable_card")
        self.assertEqual(after.round_results[-1]["sum"], 100)  # 負けた時点の合計
        self.assertEqual(after.players[1].chips, 2)
        self.assertEqual(after.round_number, 2)

    def test_after_101_any_positive_loses(self):
        # n101 で合計を101にすると、次の人は正の数を持っていれば出せない → 負け。
        hands = [cards("n101", "pass", "pass"), cards("n50", "n50", "n10")]
        state = make_state(hands, total=0)
        state = apply(state, Action(0, 0))  # 席0 が n101 → 合計101
        self.assertEqual(state.round_results[-1]["reason"], "no_playable_card")
        self.assertEqual(state.round_results[-1]["sum"], 101)  # 負けた時点の合計
        self.assertEqual(state.current_player, 1)
        self.assertEqual(state.players[1].chips, 2)
        self.assertEqual(state.current_player, 1)  # 次のラウンドの最初の手番は負けた人

    def test_loss_rotates_dealer_to_loser(self):
        state = make_state(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            chips=[3, 3, 3],
            dealer=1,
        )
        after = apply(state, Action(0, 1))  # 席0 が PASS → 席1 が負け → ディーラーは負けた人へ
        self.assertEqual(after.round_results[-1]["loser"], 1)
        self.assertEqual(after.dealer, 1)  # 次ラウンドのディーラーは負けた人


if __name__ == "__main__":
    unittest.main()
