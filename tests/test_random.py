"""ランダムプレイによる終了性・保存則・再現性のテスト。"""

import random
import unittest

from neu.engine import (
    INITIAL_DEALER,
    apply,
    is_game_over,
    legal_actions,
    new_game,
    safe_actions,
    to_dict,
)

MAX_MOVES = 20000


class RandomPlayTest(unittest.TestCase):
    def _assert_invariants(self, state):
        total_cards = (
            sum(len(p.hand) for p in state.players)
            + len(state.deck)
            + len(state.table)
        )
        self.assertEqual(total_cards, 58)

        for player in state.players:
            if player.alive:
                self.assertEqual(len(player.hand), 3, f"seat {player.seat}")

        if not is_game_over(state):
            self.assertIsNotNone(state.current_player)
            self.assertTrue(state.players[state.current_player].alive)
            self.assertIn(state.cards_remaining, (1, 2))
            self.assertTrue(legal_actions(state))
            # 手番の人は必ず「出しても101を超えない」安全な手を1つ以上持っている（修正1）。
            self.assertTrue(
                safe_actions(state),
                f"seat {state.current_player} に安全な手がありません",
            )
    def _play(self, players, seed, max_moves=MAX_MOVES):
        state = new_game(players, seed=seed)
        chooser = random.Random(seed * 1000 + players)
        moves = 0
        while not is_game_over(state):
            self._assert_invariants(state)
            actions = legal_actions(state)
            self.assertTrue(actions)
            state = apply(state, chooser.choice(actions))
            moves += 1
            self.assertLess(moves, max_moves, f"players={players} seed={seed} が終わらない")
        self._assert_invariants(state)
        return state, moves

    def test_random_games_always_terminate_and_conserve_cards(self):
        for players in range(2, 8):
            for seed in range(1, 11):
                with self.subTest(players=players, seed=seed):
                    state, _moves = self._play(players, seed)
                    self.assertEqual(state.phase, "game_over")
                    survivors = [p.seat for p in state.players if p.alive]
                    self.assertEqual(len(survivors), 1)
                    self.assertEqual(state.winner, survivors[0])
                    self.assertEqual(legal_actions(state), [])

    def test_same_seed_reproduces_the_same_game(self):
        first, moves_a = self._play(5, 123)
        second, moves_b = self._play(5, 123)
        self.assertEqual(moves_a, moves_b)
        self.assertEqual(to_dict(first), to_dict(second))

    def test_different_seeds_produce_different_games(self):
        first, _ = self._play(4, 1)
        second, _ = self._play(4, 2)
        self.assertNotEqual(to_dict(first), to_dict(second))

    def test_initial_dealer_is_random_and_first_player_is_the_dealer(self):
        self.assertIsNone(INITIAL_DEALER)
        for seed in range(200):
            with self.subTest(seed=seed):
                state = new_game(3, seed=seed)
                # 最初の手番はディーラー本人（修正2・4）
                self.assertEqual(state.current_player, state.dealer)

    def test_same_seed_gives_the_same_dealer(self):
        self.assertEqual(new_game(4, seed=7).dealer, new_game(4, seed=7).dealer)
        self.assertEqual(new_game(4, seed=7, dealer=2).dealer, 2)

    def test_random_dealer_covers_all_seats(self):
        dealers = {new_game(3, seed=seed).dealer for seed in range(200)}
        self.assertEqual(dealers, {0, 1, 2})

    def test_random_play_never_has_empty_legal_actions(self):
        # _assert_invariants 内で毎手番確認しているが、代表例を明示的にも確認する
        state = new_game(7, seed=77)
        chooser = random.Random(77)
        while not is_game_over(state):
            actions = legal_actions(state)
            self.assertTrue(actions)
            state = apply(state, chooser.choice(actions))


class RoundContinuationInvariantTest(unittest.TestCase):
    """ランダムプレイ中のラウンド継続（ラウンド負け → 次ラウンド）の不変条件。

    ``start_round`` を差し替えて、ラウンド負けの直前と直後の状態を比較する。
    """

    def _play(self, players, seed):
        import neu.engine as engine

        original = engine.start_round

        def checked(state):
            before_results = len(state.round_results)
            before_deck = list(state.deck)
            before_table = list(state.table)
            before_hands = [list(p.hand) for p in state.players]
            before_direction = state.direction
            result = original(state)

            context = f"players={players} seed={seed} round={result.round_number}"
            # 2ラウンド目以降の開始時は、合計は必ず 0。
            self.assertEqual(result.total, 0, context)
            # 向きはリセットせず引き継ぐ。
            self.assertEqual(result.direction, before_direction, context)
            eliminated = any(
                entry["eliminated"]
                for entry in state.round_results[before_results:]
            )
            if not eliminated:
                # ラウンド負けの前後で、山札・場札・全員の手札は変わらない。
                self.assertEqual(result.deck, before_deck, context)
                self.assertEqual(result.table, before_table, context)
                self.assertEqual(
                    [p.hand for p in result.players], before_hands, context
                )
            else:
                # 脱落した人の手札は場札の一番下に入るだけ。山札と場札の一番上は変わらない。
                self.assertEqual(result.deck, before_deck, context)
                if before_table:
                    self.assertEqual(result.table[-1], before_table[-1], context)
            if result.phase == "playing":
                # 最初の手番はディーラー（＝負けた人）で、出せるカードを持っている。
                self.assertEqual(result.current_player, result.dealer, context)
                self.assertTrue(legal_actions(result), context)
            return result

        engine.start_round = checked
        try:
            # ゲーム開始時は場札が空（山札から場札をめくらない）でも問題なく始まる。
            state = new_game(players, seed=seed)
            self.assertEqual(state.table, [])
            self.assertEqual(state.total, 0)
            chooser = random.Random(seed * 1000 + players)
            moves = 0
            while not is_game_over(state):
                actions = legal_actions(state)
                self.assertTrue(actions)
                state = apply(state, chooser.choice(actions))
                moves += 1
                self.assertLess(moves, MAX_MOVES, f"players={players} seed={seed} が終わらない")
            self.assertEqual(state.phase, "game_over")
        finally:
            engine.start_round = original

    def test_round_continuation_keeps_cards_and_sum_zero(self):
        for players in range(2, 8):
            for seed in range(1, 6):
                with self.subTest(players=players, seed=seed):
                    self._play(players, seed)


if __name__ == "__main__":
    unittest.main()
