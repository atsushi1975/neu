"""デッキ構成のテスト。"""

import unittest
from collections import Counter

from neu.engine import DECK_SIZE, build_deck

EXPECTED_COUNTS = {
    **{f"n{i}": 3 for i in range(1, 10)},
    "n10": 6,
    "n50": 2,
    "nm10": 4,
    "nm1": 2,
    "n101": 5,
    "pass": 4,
    "turn": 4,
    "shot": 2,
    "double": 2,
}


class DeckTest(unittest.TestCase):
    def test_total_is_58(self):
        self.assertEqual(len(build_deck()), 58)
        self.assertEqual(DECK_SIZE, 58)

    def test_breakdown(self):
        counts = Counter(card.id for card in build_deck())
        self.assertEqual(dict(counts), EXPECTED_COUNTS)

    def test_copies_are_numbered(self):
        deck = build_deck()
        for card_id, expected in EXPECTED_COUNTS.items():
            copies = sorted(card.copy for card in deck if card.id == card_id)
            self.assertEqual(copies, list(range(1, expected + 1)), card_id)

    def test_kinds(self):
        deck = build_deck()
        by_id = {card.id: card for card in deck}
        self.assertEqual(by_id["n101"].kind, "hundred")
        for card_id in ("n1", "n9", "n10", "n50", "nm1", "nm10"):
            self.assertEqual(by_id[card_id].kind, "number", card_id)
        for card_id in ("pass", "turn", "shot", "double"):
            self.assertEqual(by_id[card_id].kind, card_id)

    def test_values(self):
        by_id = {card.id: card for card in build_deck()}
        self.assertEqual(by_id["n1"].value, 1)
        self.assertEqual(by_id["n9"].value, 9)
        self.assertEqual(by_id["n10"].value, 10)
        self.assertEqual(by_id["n50"].value, 50)
        self.assertEqual(by_id["nm1"].value, -1)
        self.assertEqual(by_id["nm10"].value, -10)
        self.assertEqual(by_id["n101"].value, 101)


if __name__ == "__main__":
    unittest.main()
