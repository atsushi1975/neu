"""ランダムプレイによる終了性・保存則・再現性のテスト。"""

import random
import unittest

from neu.engine import (
    apply,
    is_game_over,
    legal_actions,
    new_game,
    to_dict,
)

MAX_MOVES = 20000


class RandomPlayTest(unittest.TestCase):
    def _assert_invariants(self, state):
        total_cards = (
            sum(len(p.hand) for p in state.players)
            + len(state.deck)
            + len(state.discard)
            + (1 if state.field_card else 0)
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

    def test_random_play_never_has_empty_legal_actions(self):
        # _assert_invariants 内で毎手番確認しているが、代表例を明示的にも確認する
        state = new_game(7, seed=77)
        chooser = random.Random(77)
        while not is_game_over(state):
            actions = legal_actions(state)
            self.assertTrue(actions)
            state = apply(state, chooser.choice(actions))


if __name__ == "__main__":
    unittest.main()
