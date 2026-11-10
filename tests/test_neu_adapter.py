"""games/neu アダプタのテスト。

ルールそのものは neu/tests が担保する。ここでは「席・cardId の対応」
「公開情報の絞り込み」「UI 用のヒント」「ラウンド終了の記録」を確認する。
"""

from __future__ import annotations

import json
import random
import unittest

from gameplatform.errors import ConfigurationError, InvalidAction
from gameplatform import games as registry

from games.neu import GAME, normalize_config
from games.neu.adapter import NeuGame
from tests.helpers import card, cards, make_state

PLAYERS = [("p0", "Alice"), ("p1", "Bob"), ("p2", "Carol")]


def make_game(hands, **kwargs) -> NeuGame:
    game = NeuGame(PLAYERS, seed=1)
    game.state = make_state(hands, **kwargs)
    return game


class RegistrationTest(unittest.TestCase):
    def test_registered_definition(self):
        self.assertEqual(GAME.key, "neu")
        self.assertEqual(GAME.title, "ノイ")
        self.assertEqual((GAME.min_players, GAME.max_players), (2, 7))
        self.assertIs(registry.get("neu"), GAME)

    def test_config_is_empty_and_unknown_keys_are_rejected(self):
        self.assertEqual(normalize_config(None), {})
        self.assertEqual(normalize_config({}), {})
        for raw in ({"unknown": True}, ["not", "an", "object"], "x"):
            with self.subTest(raw=raw):
                with self.assertRaises(ConfigurationError):
                    normalize_config(raw)


class SeatAndCardIdTest(unittest.TestCase):
    def test_seats_follow_the_players_order(self):
        game = NeuGame([("a", "A"), ("b", "B"), ("c", "C")], seed=1)
        game.start()
        self.assertEqual(
            [game.player_id_at(seat) for seat in range(3)], ["a", "b", "c"]
        )
        self.assertEqual([game.seat_of(pid) for pid in ("a", "b", "c")], [0, 1, 2])
        self.assertEqual(game.name_at(1), "B")

    def test_card_ids_are_unique_even_for_duplicate_cards(self):
        game = make_game(
            [[card("n10", 1), card("n10", 2), card("n10", 3)]]
            + [cards("n1", "n1", "n1")] * 2,
            total=0,
        )
        hand = game.view("p0")["me"]["hand"]
        card_ids = [card["cardId"] for card in hand]
        self.assertEqual(len(card_ids), len(set(card_ids)))
        self.assertTrue(all("#" in card_id for card_id in card_ids))

    def test_play_uses_unique_card_id_and_moves_to_the_next_seat(self):
        game = make_game(
            [cards("n5", "n2", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=0,
            current=0,
        )
        game.act("p0", {"type": "play", "cardId": "n2#1"})
        self.assertEqual(game.state.total, 2)
        self.assertEqual(game.state.current_player, 1)
        self.assertEqual(game.revision, 1)


class ViewPrivacyTest(unittest.TestCase):
    def test_other_hands_and_deck_are_not_exposed(self):
        game = NeuGame(PLAYERS, seed=3)
        game.start()
        view = game.view("p0")
        self.assertEqual([p["id"] for p in view["players"]], ["p0", "p1", "p2"])
        for player in view["players"]:
            self.assertNotIn("hand", player)
            self.assertIn("handCount", player)
        self.assertEqual(view["me"]["id"], "p0")
        self.assertEqual(len(view["me"]["hand"]), 3)
        self.assertNotIn("deck", view)
        self.assertNotIn("table", view)
        self.assertNotIn("discard", view)
        self.assertIn("deckCount", view)
        self.assertIn("tableTop", view)
        self.assertIn("tableCount", view)
        # 自分以外のカード ID が view のどこにも出てこないこと。
        text = json.dumps(view, ensure_ascii=False)
        for seat in (1, 2):
            for card in game.state.players[seat].hand:
                self.assertNotIn(f'"{card.id}#{card.copy}"', text)

    def test_table_top_is_public(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=10,
            table=cards("n50"),
        )
        view = game.view("p0")
        self.assertEqual(view["tableTop"]["id"], "n50")
        self.assertEqual(view["tableTop"]["kind"], "number")
        self.assertEqual(view["tableCount"], 1)
        self.assertEqual(view["deckCount"] + view["tableCount"], len(game.state.deck) + 1)

    def test_events_use_player_names_not_seat_numbers(self):
        game = make_game(
            [cards("n5", "n2", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=0,
            current=0,
        )
        game.act("p0", {"type": "play", "cardId": "n5#1"})
        view = game.view("p0")
        self.assertEqual(view["events"][-1]["kind"], "play")
        self.assertEqual(view["events"][-1]["playerId"], "p0")
        self.assertEqual(view["events"][-1]["playerName"], "Alice")
        for event in view["events"]:
            self.assertNotIn("席0", event["message"])
            self.assertNotIn("席1", event["message"])
            self.assertNotIn("席2", event["message"])


class PlayHintTest(unittest.TestCase):
    def test_after_sum_and_bust(self):
        game = make_game(
            [cards("n1", "n50", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=100,
            current=0,
        )
        hand = {card["id"]: card for card in game.view("p0")["me"]["hand"]}
        self.assertTrue(hand["n1"]["playable"])
        self.assertEqual(hand["n1"]["afterSum"], 101)
        self.assertFalse(hand["n1"]["busts"])
        # n50（合計150）は101超えのため、出せない（playable=False / busts=True）。
        self.assertFalse(hand["n50"]["playable"])
        self.assertEqual(hand["n50"]["afterSum"], 150)
        self.assertTrue(hand["n50"]["busts"])
        self.assertTrue(hand["pass"]["playable"])
        self.assertEqual(hand["pass"]["afterSum"], 100)
        self.assertFalse(hand["pass"]["busts"])

    def test_n101_sets_the_sum_to_101_without_busting(self):
        game = make_game(
            [cards("n101", "n1", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
        )
        card = next(c for c in game.view("p0")["me"]["hand"] if c["id"] == "n101")
        self.assertEqual(card["afterSum"], 101)
        self.assertFalse(card["busts"])

    def test_shot_targets_exclude_myself_and_dead_players(self):
        game = make_game(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
            chips=[3, 3, 0],
        )
        shot = next(c for c in game.view("p0")["me"]["hand"] if c["id"] == "shot")
        self.assertTrue(shot["playable"])
        self.assertEqual(shot["targets"], ["p1"])

    def test_first_double_is_not_playable(self):
        game = make_game(
            [cards("double", "pass", "n1")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
            remaining=2,
        )
        hand = {card["id"]: card for card in game.view("p0")["me"]["hand"]}
        self.assertFalse(hand["double"]["playable"])
        self.assertTrue(hand["pass"]["playable"])

    def test_second_double_becomes_playable(self):
        game = make_game(
            [cards("n1", "double", "pass")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
            remaining=2,
        )
        game.act("p0", {"type": "play", "cardId": "n1#1"})
        self.assertEqual(game.state.current_player, 0)
        self.assertEqual(game.state.cards_remaining, 1)
        hand = {card["id"]: card for card in game.view("p0")["me"]["hand"]}
        self.assertTrue(hand["double"]["playable"])

    def test_not_my_turn_has_no_playable_card(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=10,
            current=1,
        )
        view = game.view("p0")
        self.assertTrue(all(not card["playable"] for card in view["me"]["hand"]))


class InvalidActionTest(unittest.TestCase):
    def test_not_your_turn_is_rejected(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=10,
            current=0,
        )
        with self.assertRaises(InvalidAction):
            game.act("p1", {"type": "play", "cardId": "n1#1"})

    def test_card_not_in_hand_is_rejected(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=10,
            current=0,
        )
        with self.assertRaises(InvalidAction):
            game.act("p0", {"type": "play", "cardId": "n50#1"})

    def test_shot_cannot_target_myself(self):
        game = make_game(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
        )
        with self.assertRaises(InvalidAction):
            game.act("p0", {"type": "play", "cardId": "shot#1", "target": "p0"})

    def test_shot_requires_a_target(self):
        game = make_game(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
        )
        with self.assertRaises(InvalidAction):
            game.act("p0", {"type": "play", "cardId": "shot#1"})

    def test_shot_moves_the_turn_to_the_target(self):
        game = make_game(
            [cards("shot", "n1", "n2")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
        )
        game.act("p0", {"type": "play", "cardId": "shot#1", "target": "p2"})
        self.assertEqual(game.state.current_player, 2)

    def test_first_double_is_rejected(self):
        game = make_game(
            [cards("double", "pass", "n1")] + [cards("n1", "n1", "n1")] * 2,
            total=10,
            current=0,
            remaining=2,
        )
        with self.assertRaises(InvalidAction):
            game.act("p0", {"type": "play", "cardId": "double#1"})

    def test_unknown_action_type_is_rejected(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=10,
            current=0,
        )
        with self.assertRaises(InvalidAction):
            game.act("p0", {"type": "select", "cardId": "n1#1"})


class RoundEndTest(unittest.TestCase):
    def test_no_playable_card_keeps_last_round_result(self):
        game = make_game(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            chips=[3, 3, 3],
        )
        game.act("p0", {"type": "play", "cardId": "pass#1"})
        last = game.last_round
        self.assertIsNotNone(last)
        self.assertEqual(last["round"], 1)
        self.assertEqual(last["loserId"], "p1")
        self.assertEqual(last["loserName"], "Bob")
        self.assertIsNone(last["cardId"])
        self.assertIsNone(last["sumAfter"])
        self.assertEqual(last["sum"], 100)
        self.assertEqual(last["chips"], 2)
        self.assertFalse(last["eliminated"])
        self.assertFalse(last["gameOver"])
        self.assertEqual(last["reason"], "no_playable_card")
        self.assertIsNone(last["card"])
        self.assertEqual(last["nextRound"], {"round": 2, "firstPlayer": "p1", "sum": 0})
        # no_playable_card ではカードを出していないが、負けた人の手札が見える。
        self.assertEqual(len(last["revealedHand"]), 3)
        self.assertEqual(game.state.round_results[-1]["hand"][0]["id"], "n50")

    def test_no_playable_card_revealed_hand_is_the_losers_hand(self):
        game = make_game(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            chips=[3, 3, 3],
        )
        game.act("p0", {"type": "play", "cardId": "pass#1"})
        last = game.last_round
        self.assertEqual(last["reason"], "no_playable_card")
        revealed = last["revealedHand"]
        self.assertEqual(len(revealed), 3)
        self.assertEqual(revealed[0]["id"], "n50")
        self.assertEqual(revealed[0]["cardId"], "n50#1")
        self.assertEqual(revealed[0]["kind"], "number")
        self.assertEqual(revealed[0]["value"], 50)
        self.assertEqual(revealed[1]["id"], "n50")
        self.assertEqual(revealed[2]["id"], "n10")

    def test_game_over_phase_and_winner(self):
        game = make_game(
            [cards("n50", "n50", "n10"), cards("pass", "pass", "pass")],
            total=100,
            current=1,
            chips=[1, 3],
        )
        game.act("p1", {"type": "play", "cardId": "pass#1"})
        self.assertEqual(game.phase, "finished")
        self.assertEqual(game.state.winner, 1)
        self.assertEqual(game.result["winnerId"], "p1")
        last = game.last_round
        self.assertTrue(last["gameOver"])
        self.assertTrue(last["eliminated"])
        self.assertEqual(last["chips"], 0)
        self.assertEqual(last["winnerId"], "p1")
        self.assertEqual(last["reason"], "no_playable_card")
        view = game.view("p0")
        self.assertEqual(view["phase"], "finished")
        self.assertEqual(view["winnerId"], "p1")
        self.assertEqual(view["result"]["winnerName"], "Bob")

    def test_start_uses_random_dealer_and_first_player_is_dealer(self):
        game = NeuGame(PLAYERS, seed=1)
        game.start()
        self.assertEqual(game.state.dealer, game.state.current_player)
        view = game.view("p0")
        self.assertEqual(view["dealer"], view["currentPlayer"])
        self.assertEqual(view["dealerId"], view["currentPlayerId"])

    def test_no_playable_card_event_is_logged_with_reason(self):
        game = make_game(
            [cards("n5", "pass", "pass"), cards("n50", "n50", "n10")],
            total=100,
            current=0,
            chips=[3, 3, 3],
        )
        game.act("p0", {"type": "play", "cardId": "pass#1"})
        events = game.recent_events()
        round_ends = [event for event in events if event.get("kind") == "round_end"]
        self.assertTrue(round_ends)
        self.assertEqual(round_ends[-1]["reason"], "no_playable_card")
        self.assertEqual(round_ends[-1]["loserName"], "Bob")
        self.assertIsNone(round_ends[-1].get("cardId"))
        self.assertIsNone(round_ends[-1].get("sumAfter"))


class RandomPlayTest(unittest.TestCase):
    def test_random_games_always_finish(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                game = NeuGame(
                    [(f"p{i}", f"P{i}") for i in range(4)], seed=seed
                )
                game.start()
                rng = random.Random(seed)
                steps = 0
                while game.phase == "playing" and steps < 3000:
                    player_id = game.current_player_id
                    self.assertIsNotNone(player_id)
                    view = game.view(player_id)
                    playable = [c for c in view["me"]["hand"] if c["playable"]]
                    self.assertTrue(playable)
                    card = rng.choice(playable)
                    payload = {"type": "play", "cardId": card["cardId"]}
                    if card["targets"]:
                        payload["target"] = rng.choice(card["targets"])
                    game.act(player_id, payload)
                    steps += 1
                self.assertEqual(game.phase, "finished")
                self.assertIsNotNone(game.result)


if __name__ == "__main__":
    unittest.main()
