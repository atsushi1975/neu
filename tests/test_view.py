"""view_for と to_dict / from_dict のテスト。"""

import json
import unittest

from neu.engine import (
    InvalidSetup,
    apply,
    from_dict,
    legal_actions,
    new_game,
    to_dict,
    view_for,
)


class ViewTest(unittest.TestCase):
    def test_other_hands_are_hidden(self):
        state = new_game(4, seed=3)
        for seat in range(4):
            with self.subTest(seat=seat):
                view = view_for(state, seat)
                self.assertEqual(view["viewer"], seat)
                for entry in view["players"]:
                    self.assertIn("hand_count", entry)
                    self.assertEqual(
                        entry["hand_count"], len(state.players[entry["seat"]].hand)
                    )
                    if entry["seat"] == seat:
                        self.assertEqual(
                            [c["id"] for c in entry["hand"]],
                            [c.id for c in state.players[seat].hand],
                        )
                    else:
                        self.assertNotIn("hand", entry)

    def test_deck_and_table_are_counts_only(self):
        state = new_game(4, seed=4)
        view = view_for(state, 0)
        self.assertNotIn("deck", view)
        self.assertNotIn("table", view)
        self.assertNotIn("discard", view)
        self.assertNotIn("field_card", view)
        self.assertEqual(view["deck_count"], len(state.deck))
        self.assertEqual(view["deckCount"], len(state.deck))
        self.assertEqual(view["table_count"], len(state.table))
        self.assertEqual(view["tableCount"], len(state.table))

    def test_own_hand_is_visible_after_play(self):
        state = new_game(4, seed=5)
        state = apply(state, legal_actions(state)[0])
        current = state.current_player
        view = view_for(state, current)
        own = next(entry for entry in view["players"] if entry["seat"] == current)
        self.assertEqual([c["id"] for c in own["hand"]], [c.id for c in state.players[current].hand])
        self.assertEqual(view["sum"], state.total)
        self.assertEqual(view["cards_remaining"], state.cards_remaining)

    def test_invalid_seat(self):
        state = new_game(3, seed=1)
        with self.assertRaises(InvalidSetup):
            view_for(state, 3)
        with self.assertRaises(InvalidSetup):
            view_for(state, -1)

    def test_json_roundtrip(self):
        state = new_game(5, seed=9)
        data = to_dict(state)
        text = json.dumps(data, ensure_ascii=False)
        restored = from_dict(json.loads(text))
        self.assertEqual(to_dict(restored), data)

    def test_json_roundtrip_preserves_round_results(self):
        state = new_game(3, seed=3)
        state.round_results.append(
            {
                "round": 1,
                "loser": 0,
                "reason": "no_playable_card",
                "card_id": None,
                "sum_after": None,
                "chips_left": 2,
                "eliminated": False,
                "game_over": False,
                "winner": None,
                "hand": [
                    {"id": "n50", "kind": "number", "value": 50, "copy": 1},
                    {"id": "n50", "kind": "number", "value": 50, "copy": 2},
                    {"id": "n10", "kind": "number", "value": 10, "copy": 1},
                ],
            }
        )
        data = to_dict(state)
        restored = from_dict(json.loads(json.dumps(data)))
        self.assertEqual(to_dict(restored), data)
        self.assertEqual(len(restored.round_results), 1)
        self.assertEqual(restored.round_results[0]["reason"], "no_playable_card")

    def test_from_dict_accepts_old_format_without_round_results(self):
        data = to_dict(new_game(3, seed=3))
        self.assertIn("round_results", data)
        data.pop("round_results")
        state = from_dict(data)
        self.assertEqual(state.round_results, [])

    def test_from_dict_accepts_old_field_and_discard_format(self):
        # 旧形式（field_card + discard）の JSON も読めるようにする。
        data = to_dict(new_game(3, seed=3))
        data["table"] = None
        data.pop("table", None)
        data["field_card"] = {"id": "n4", "kind": "number", "value": 4, "copy": 1}
        data["discard"] = [
            {"id": "pass", "kind": "pass", "value": 0, "copy": 1},
        ]
        state = from_dict(data)
        self.assertEqual([c.id for c in state.table], ["pass", "n4"])
        self.assertEqual(state.table[-1].value, 4)

    def test_roundtrip_continues_identically(self):
        state = new_game(4, seed=11)
        restored = from_dict(json.loads(json.dumps(to_dict(state))))
        action = legal_actions(state)[0]
        self.assertEqual(
            to_dict(apply(state, action)),
            to_dict(apply(restored, action)),
        )

    def test_roundtrip_preserves_rng(self):
        state = new_game(3, seed=13)
        restored = from_dict(json.loads(json.dumps(to_dict(state))))
        # 状態の乱数を使ったシャッフル結果が一致する
        state.rng.shuffle(state.deck)
        restored.rng.shuffle(restored.deck)
        self.assertEqual(
            [c.id for c in state.deck],
            [c.id for c in restored.deck],
        )


if __name__ == "__main__":
    unittest.main()
