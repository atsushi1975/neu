"""Flask API の結合テスト（部屋作成→参加→開始→対戦→終了）。"""

from __future__ import annotations

import json
import random
import unittest
from unittest.mock import patch

from gameplatform import realtime, rooms
from games.neu import CPU_NAMES
from web import create_app


class ApiTest(unittest.TestCase):
    def setUp(self):
        rooms.reset()
        realtime.reset()
        patcher = patch.object(rooms, "CPU_TURN_DELAY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})

    def client(self):
        return self.app.test_client()

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {"X-Player-Token": token}

    def create_room(self, name: str):
        client = self.client()
        response = client.post("/api/rooms", json={"name": name})
        self.assertEqual(response.status_code, 201, response.get_json())
        return client, response.get_json()

    def join_room(self, room_id: str, name: str):
        client = self.client()
        response = client.post(f"/api/rooms/{room_id}/join", json={"name": name})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, response.get_json()

    def start(self, client, room_id: str, token: str):
        return client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(token),
            json={},
        )

    def state(self, client, room_id: str, token: str):
        return client.get(
            f"/api/rooms/{room_id}/state", headers=self.headers(token)
        )

    def three_players(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")
        client_c, player_c = self.join_room(room_id, "C")
        entries = [host, player_b, player_c]
        tokens = {entry["playerId"]: entry["token"] for entry in entries}
        clients = {
            host["playerId"]: host_client,
            player_b["playerId"]: client_b,
            player_c["playerId"]: client_c,
        }
        return room_id, host_client, host, tokens, clients

    def test_room_lifecycle_and_private_hands(self):
        room_id, host_client, host, tokens, clients = self.three_players()

        host_state = self.state(host_client, room_id, host["token"]).get_json()
        self.assertEqual(
            host_state["you"], {"id": host["playerId"], "isHost": True}
        )
        guest_id = next(pid for pid in tokens if pid != host["playerId"])
        guest_state = self.state(
            clients[guest_id], room_id, tokens[guest_id]
        ).get_json()
        self.assertEqual(guest_state["you"], {"id": guest_id, "isHost": False})

        response = self.start(host_client, room_id, host["token"])
        self.assertEqual(response.status_code, 200, response.get_json())

        for player_id, token in tokens.items():
            payload = self.state(clients[player_id], room_id, token).get_json()
            self.assertTrue(payload["room"]["started"])
            game = payload["game"]
            self.assertEqual(game["me"]["id"], player_id)
            self.assertEqual(game["phase"], "playing")
            self.assertEqual(len(game["players"]), 3)
            for entry in game["players"]:
                self.assertNotIn("hand", entry)
            self.assertEqual(sum(entry["handCount"] for entry in game["players"]), 9)
            self.assertEqual(len(game["me"]["hand"]), 3)
            # 他人の物理カード（id + copy）が自分の view に含まれないこと。
            my_seat = next(
                entry["seat"] for entry in game["players"] if entry["id"] == player_id
            )
            state = rooms.get_room(room_id).game.state
            for seat, player in enumerate(state.players):
                if seat == my_seat:
                    continue
                for card in player.hand:
                    self.assertNotIn(
                        f'"{card.id}#{card.copy}"',
                        json.dumps(game, ensure_ascii=False),
                    )

    def test_non_host_cannot_start_and_minimum_players_is_enforced(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")

        self.assertEqual(
            self.start(client_b, room_id, player_b["token"]).status_code, 403
        )

        # ノイは 2 人から。1 人だけの部屋は開始できない。
        alone_client, alone = self.create_room("ひとり")
        self.assertEqual(
            self.start(
                alone_client, alone["roomId"], alone["token"]
            ).status_code,
            400,
        )

    def test_join_after_start_is_rejected(self):
        room_id, host_client, host, _tokens, _clients = self.three_players()
        self.assertEqual(
            self.start(host_client, room_id, host["token"]).status_code, 200
        )
        late = self.client()
        response = late.post(f"/api/rooms/{room_id}/join", json={"name": "D"})
        self.assertEqual(response.status_code, 400)

    def test_bad_token_and_other_room_token_are_rejected(self):
        room_id, host_client, host, _tokens, _clients = self.three_players()
        self.assertEqual(
            self.state(host_client, room_id, "bad-token").status_code, 403
        )
        other_client, other = self.create_room("別室")
        self.assertEqual(
            self.state(other_client, room_id, other["token"]).status_code, 403
        )

    def test_stale_revision_is_rejected(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(
            self.start(host_client, room_id, host["token"]).status_code, 200
        )

        view = self.state(host_client, room_id, host["token"]).get_json()["game"]
        current_id = view["currentPlayerId"]
        current_view = self.state(
            clients[current_id], room_id, tokens[current_id]
        ).get_json()["game"]
        card = next(
            card for card in current_view["me"]["hand"] if card["playable"]
        )
        payload = {
            "type": "play",
            "cardId": card["cardId"],
            "expectedRevision": current_view["revision"],
        }
        if card["targets"]:
            payload["target"] = card["targets"][0]

        actor_client = clients[current_id]
        actor_token = tokens[current_id]
        response = actor_client.post(
            f"/api/rooms/{room_id}/actions",
            headers=self.headers(actor_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        stale = actor_client.post(
            f"/api/rooms/{room_id}/actions",
            headers=self.headers(actor_token),
            json=payload,
        )
        self.assertEqual(stale.status_code, 409)

    def test_invalid_card_is_a_bad_request(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(
            self.start(host_client, room_id, host["token"]).status_code, 200
        )
        view = self.state(host_client, room_id, host["token"]).get_json()["game"]
        current_id = view["currentPlayerId"]
        current_view = self.state(
            clients[current_id], room_id, tokens[current_id]
        ).get_json()["game"]
        response = clients[current_id].post(
            f"/api/rooms/{room_id}/actions",
            headers=self.headers(tokens[current_id]),
            json={
                "type": "play",
                "cardId": "does-not-exist#1",
                "expectedRevision": current_view["revision"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_blank_name_is_rejected(self):
        response = self.client().post("/api/rooms", json={"name": "   "})
        self.assertEqual(response.status_code, 400)

    def test_games_catalog(self):
        response = self.client().get("/api/games")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["default"], "neu")
        keys = [game["key"] for game in payload["games"]]
        self.assertIn("neu", keys)
        neu = next(game for game in payload["games"] if game["key"] == "neu")
        self.assertEqual(neu["title"], "ノイ")
        self.assertEqual(neu["minPlayers"], 2)
        self.assertEqual(neu["maxPlayers"], 7)
        self.assertEqual(neu["defaultConfig"], {})

    def test_unknown_game_and_invalid_config_are_rejected(self):
        response = self.client().post(
            "/api/rooms", json={"name": "A", "game": "unknown"}
        )
        self.assertEqual(response.status_code, 404)
        response = self.client().post(
            "/api/rooms",
            json={"name": "A", "game": "neu", "config": {"oops": True}},
        )
        self.assertEqual(response.status_code, 400)

    def test_leave_transfers_host(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")

        response = host_client.post(
            f"/api/rooms/{room_id}/leave",
            headers=self.headers(host["token"]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["hostChanged"])
        info = self.client().get(f"/api/rooms/{room_id}").get_json()
        self.assertEqual(info["hostId"], player_b["playerId"])
        self.assertEqual(
            self.state(host_client, room_id, host["token"]).status_code, 403
        )
        self.assertEqual(
            self.state(client_b, room_id, player_b["token"]).status_code, 200
        )

    def test_stream_ticket_requires_authentication(self):
        _client, host = self.create_room("ホスト")
        response = self.client().post(
            f"/api/rooms/{host['roomId']}/stream-ticket"
        )
        self.assertEqual(response.status_code, 403)

    def test_health(self):
        response = self.client().get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["title"], "ノイ")
        self.assertEqual(payload["version"], "dev")
        self.assertIn(payload["status"], {"idle", "waiting", "playing"})
        self.assertIn("playing_rooms", payload)
        self.assertIn("neu", payload["games"])

    def test_health_status_lifecycle(self):
        payload = self.client().get("/health").get_json()
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["playing_rooms"], 0)

        room_id, host_client, host, _tokens, _clients = self.three_players()
        payload = self.client().get("/health").get_json()
        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(payload["playing_rooms"], 0)

        self.assertEqual(
            self.start(host_client, room_id, host["token"]).status_code, 200
        )
        payload = self.client().get("/health").get_json()
        self.assertEqual(payload["status"], "playing")
        self.assertGreaterEqual(payload["playing_rooms"], 1)

        room = rooms.get_room(room_id)
        room.game.state.phase = "game_over"
        payload = self.client().get("/health").get_json()
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["playing_rooms"], 0)

    def test_health_and_api_health_return_the_same_content(self):
        root = self.client().get("/health")
        api = self.client().get("/api/health")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(root.get_json(), api.get_json())
        self.assertEqual(
            set(api.get_json()),
            {"ok", "games", "title", "version", "status", "playing_rooms"},
        )

    def test_start_with_cpu_adds_cpu_players_and_game_finishes(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"cpuCount": 2},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        names = [player["name"] for player in payload["room"]["players"]]
        cpu_names = [name for name in names if name != "ホスト"]
        self.assertEqual(len(cpu_names), 2)
        self.assertEqual(len(set(cpu_names)), 2)
        self.assertTrue(set(cpu_names) <= set(CPU_NAMES))
        self.assertEqual(len(payload["game"]["players"]), 3)
        cpu_players = [player for player in payload["game"]["players"] if player["isCpu"]]
        self.assertEqual(len(cpu_players), 2)
        host_player = next(
            player
            for player in payload["game"]["players"]
            if player["id"] == host["playerId"]
        )
        self.assertFalse(host_player["isCpu"])
        rooms.wait_for_cpus(room_id)

        rng = random.Random(123)
        steps = 0
        game = None
        while steps < 3000:
            state = self.state(host_client, room_id, host["token"]).get_json()
            game = state["game"]
            if game["phase"] == "finished":
                break
            if game["currentPlayerId"] != host["playerId"]:
                rooms.wait_for_cpus(room_id)
                continue
            playable = [card for card in game["me"]["hand"] if card["playable"]]
            self.assertTrue(playable)
            card = rng.choice(playable)
            action = {
                "type": "play",
                "cardId": card["cardId"],
                "expectedRevision": game["revision"],
            }
            if card["targets"]:
                action["target"] = rng.choice(card["targets"])
            played = host_client.post(
                f"/api/rooms/{room_id}/actions",
                headers=self.headers(host["token"]),
                json=action,
            )
            self.assertEqual(played.status_code, 200, played.get_json())
            steps += 1
        self.assertLess(steps, 3000)
        self.assertEqual(game["phase"], "finished")
        self.assertIsNotNone(game["result"])

    def test_cpu_count_is_limited_by_max_players(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"cpuCount": 7},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(rooms.get_room(room_id).started)

    def _current_view(self, room_id, tokens, clients):
        for player_id, token in tokens.items():
            payload = self.state(clients[player_id], room_id, token).get_json()
            game = payload["game"]
            if game and game.get("currentPlayerId") == player_id:
                return player_id, game
        return None, None

    def test_random_game_finishes_via_api(self):
        for game_index in range(3):
            with self.subTest(game_index=game_index):
                rooms.reset()
                realtime.reset()
                room_id, host_client, host, tokens, clients = self.three_players()
                self.assertEqual(
                    self.start(host_client, room_id, host["token"]).status_code,
                    200,
                )
                rng = random.Random(game_index)
                steps = 0
                final = None
                while steps < 3000:
                    player_id, game = self._current_view(room_id, tokens, clients)
                    if game is None:
                        # 終了後は currentPlayerId が null になる。
                        final = self.state(
                            host_client, room_id, host["token"]
                        ).get_json()["game"]
                        break
                    playable = [
                        card for card in game["me"]["hand"] if card["playable"]
                    ]
                    self.assertTrue(playable)
                    card = rng.choice(playable)
                    payload = {
                        "type": "play",
                        "cardId": card["cardId"],
                        "expectedRevision": game["revision"],
                    }
                    if card["targets"]:
                        payload["target"] = rng.choice(card["targets"])
                    response = clients[player_id].post(
                        f"/api/rooms/{room_id}/actions",
                        headers=self.headers(tokens[player_id]),
                        json=payload,
                    )
                    self.assertEqual(
                        response.status_code, 200, response.get_json()
                    )
                    steps += 1
                self.assertLess(steps, 3000)
                self.assertIsNotNone(final)
                self.assertEqual(final["phase"], "finished")
                self.assertIsNotNone(final["result"])
                self.assertIsNotNone(final["winnerId"])


if __name__ == "__main__":
    unittest.main()
