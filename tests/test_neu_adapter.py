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
from neu.engine import from_dict, to_dict
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
        # 手札の公開は廃止したため、ラウンド結果に手札は含まれない。
        self.assertNotIn("hand", game.state.round_results[-1])
        self.assertNotIn("revealedHand", last)

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


class StandingsTest(unittest.TestCase):
    def make_finished_game(
        self,
        count: int,
        winner: int,
        round_results: list[dict],
    ) -> NeuGame:
        players = [(f"p{seat}", f"P{seat}") for seat in range(count)]
        game = NeuGame(players, seed=1)
        chips = [0] * count
        chips[winner] = 1
        game.state = make_state(
            [cards("n1", "n2", "n3") for _ in range(count)],
            chips=chips,
            current=None,
        )
        game.state.phase = "game_over"
        game.state.winner = winner
        game.state.round_results = []
        for index, entry in enumerate(round_results, start=1):
            game.state.round_results.append(
                {
                    "round": entry.get("round", index),
                    "loser": entry["loser"],
                    "reason": "no_playable_card",
                    "card_id": None,
                    "sum_after": None,
                    "sum": 0,
                    "chips_left": 0 if entry.get("eliminated") else 1,
                    "eliminated": bool(entry.get("eliminated")),
                    "game_over": entry.get("loser") == winner,
                    "winner": winner if entry.get("loser") == winner else None,
                }
            )
        return game

    def test_standings_follow_reverse_elimination_order(self):
        game = self.make_finished_game(
            4,
            winner=3,
            round_results=[
                {"loser": 2, "eliminated": False},
                {"loser": 0, "eliminated": True},
                {"loser": 2, "eliminated": True},
                {"loser": 1, "eliminated": True},
            ],
        )
        result = game.result
        self.assertEqual(result["winnerId"], "p3")
        self.assertEqual(result["winnerName"], "P3")
        self.assertEqual(result["message"], "P3 さんの勝ち！")
        self.assertEqual(
            result["standings"],
            [
                {"playerId": "p3", "rank": 1},
                {"playerId": "p1", "rank": 2},
                {"playerId": "p2", "rank": 3},
                {"playerId": "p0", "rank": 4},
            ],
        )
        # 公開ビューにもそのまま載る。
        self.assertEqual(game.view("p0")["result"]["standings"], result["standings"])

    def test_chained_eliminations_still_have_unique_ranks(self):
        # 同じラウンドで席2 → 席0 → 席1 の順に脱落した場合。
        game = self.make_finished_game(
            4,
            winner=3,
            round_results=[
                {"round": 5, "loser": 2, "eliminated": True},
                {"round": 5, "loser": 0, "eliminated": True},
                {"round": 5, "loser": 1, "eliminated": True},
            ],
        )
        standings = game.result["standings"]
        self.assertEqual(
            [entry["playerId"] for entry in standings], ["p3", "p1", "p0", "p2"]
        )
        self.assertEqual([entry["rank"] for entry in standings], [1, 2, 3, 4])
        self.assertEqual(len({entry["playerId"] for entry in standings}), 4)

    def test_two_and_seven_players(self):
        for count in (2, 7):
            with self.subTest(count=count):
                # 席番号と逆順（最後の席から順に脱落）させる。
                eliminated = list(range(count - 1))  # 0, 1, ..., count-2
                game = self.make_finished_game(
                    count,
                    winner=count - 1,
                    round_results=[
                        {"loser": seat, "eliminated": True}
                        for seat in eliminated
                    ],
                )
                standings = game.result["standings"]
                expected_ids = [f"p{count - 1}"] + [
                    f"p{seat}" for seat in reversed(eliminated)
                ]
                self.assertEqual(
                    [entry["playerId"] for entry in standings], expected_ids
                )
                self.assertEqual(
                    [entry["rank"] for entry in standings],
                    list(range(1, count + 1)),
                )

    def test_unrecorded_players_are_appended_by_seat(self):
        game = self.make_finished_game(
            4,
            winner=3,
            round_results=[{"loser": 1, "eliminated": True}],
        )
        self.assertEqual(
            [entry["playerId"] for entry in game.result["standings"]],
            ["p3", "p1", "p0", "p2"],
        )

    def test_standings_survive_dict_roundtrip(self):
        game = self.make_finished_game(
            5,
            winner=4,
            round_results=[
                {"loser": 3, "eliminated": True},
                {"loser": 0, "eliminated": True},
                {"loser": 2, "eliminated": True},
                {"loser": 1, "eliminated": True},
            ],
        )
        before = game.result["standings"]
        data = json.loads(json.dumps(to_dict(game.state), ensure_ascii=False))
        restored = NeuGame(game.players, seed=1)
        restored.state = from_dict(data)
        self.assertEqual(restored.result["standings"], before)

    def test_result_is_none_before_game_over(self):
        game = make_game(
            [cards("n1", "n2", "n3")] * 3,
            total=0,
        )
        self.assertIsNone(game.result)
        self.assertIsNone(game.view("p0")["result"])


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
