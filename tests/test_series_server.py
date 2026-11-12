"""ノイ連戦のサーバー側（共通基盤・API・アダプタ）のテスト。"""

from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from gameplatform import realtime, rooms
from web import create_app


class SeriesServerTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
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

    def start(self, client, room_id: str, token: str, *, series_length: int | None) -> dict:
        body: dict[str, int] = {"cpuCount": 1}
        if series_length is not None:
            body["seriesLength"] = series_length
        response = client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(token),
            json=body,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        state = self.state(client, room_id, token)
        return state

    def state(self, client, room_id: str, token: str) -> dict:
        response = client.get(f"/api/rooms/{room_id}/state", headers=self.headers(token))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def _start_host_with_cpu(self, *, series_length: int | None) -> dict:
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        start = self.start(host_client, room_id, host["token"], series_length=series_length)
        return {
            "roomId": room_id,
            "client": host_client,
            "host": host,
            "token": host["token"],
            "game": start["game"],
            "series": start["series"],
        }

    def _play_until(
        self,
        host_client,
        room_id: str,
        token: str,
        host_player_id: str,
        second_client=None,
        second_token: str | None = None,
        second_player_id: str | None = None,
    ) -> dict:
        rng = random.Random(7)
        steps = 0
        result = None
        while steps < 3000:
            state = self.state(host_client, room_id, token)
            game = state["game"]
            result = game
            if game["phase"] in ("finished", "waiting"):
                break
            if game["phase"] != "playing":
                break
            current_id = game["currentPlayerId"]
            if current_id == host_player_id:
                actor_client, actor_token, actor_id = host_client, token, host_player_id
            elif current_id is not None and second_client is not None and current_id == second_player_id:
                actor_client, actor_token, actor_id = second_client, second_token, second_player_id
            else:
                rooms.wait_for_cpus(room_id)
                continue
            actor_state = self.state(actor_client, room_id, actor_token)
            game = actor_state["game"]
            result = game
            if game["phase"] in ("finished", "waiting"):
                break
            if game["phase"] != "playing":
                break
            playable = [card for card in game["me"]["hand"] if card["playable"]]
            self.assertTrue(playable)
            card = rng.choice(playable)
            payload = {
                "type": "play",
                "cardId": card["cardId"],
                "expectedRevision": game["revision"],
            }
            if card["targets"]:
                payload["target"] = card["targets"][0]
            response = actor_client.post(
                f"/api/rooms/{room_id}/actions",
                headers=self.headers(actor_token),
                json=payload,
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            steps += 1
        self.assertNotEqual(steps, 3000)
        return result

    def _confirm_host(self, host_client, room_id: str, token: str) -> dict:
        response = host_client.post(
            f"/api/rooms/{room_id}/series/confirm",
            headers=self.headers(token),
            json={},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_start_rejects_invalid_series_length_and_defaults_to_one(self):
        host_client, host = self.create_room("ホスト")
        player_client, player_b = self.join_room(host["roomId"], "B")
        room_id = host["roomId"]

        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"seriesLength": 2.5, "cpuCount": 0},
        )
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertFalse(rooms.get_room(room_id).started)

        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"seriesLength": "2", "cpuCount": 0},
        )
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertFalse(rooms.get_room(room_id).started)

        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"seriesLength": 0, "cpuCount": 0},
        )
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertFalse(rooms.get_room(room_id).started)

        started = self._start_host_with_cpu(series_length=None)
        self.assertEqual(started["game"]["phase"], "playing")
        self.assertEqual(started["series"]["totalGames"], 1)
        self.assertFalse(started["series"]["waiting"])
        self.assertIsNone(started["series"]["result"])

    def test_three_series_finish_confirm_and_final_result(self):
        started = self._start_host_with_cpu(series_length=3)
        room_id = started["roomId"]
        host_client = started["client"]
        token = started["token"]
        host_player_id = started["host"]["playerId"]

        first = self._play_until(host_client, room_id, token, host_player_id)
        self.assertEqual(first["phase"], "waiting")
        self.assertEqual(first["series"]["waiting"], True)
        self.assertEqual(first["series"]["game"], 1)
        self.assertEqual(first["series"]["totalGames"], 3)
        self.assertFalse(first["series"]["isFinal"])
        self.assertEqual(first["series"]["result"]["game"], 1)
        self.assertFalse(first["series"]["result"]["isFinal"])

        second_started = self._confirm_host(host_client, room_id, token)
        self.assertEqual(second_started["series"]["waiting"], False)
        self.assertEqual(second_started["series"]["game"], 2)
        self.assertEqual(second_started["game"]["phase"], "playing")

        second = self._play_until(host_client, room_id, token, host_player_id)
        self.assertEqual(second["phase"], "waiting")
        self.assertEqual(second["series"]["waiting"], True)
        self.assertEqual(second["series"]["game"], 2)
        self.assertFalse(second["series"]["isFinal"])

        third_started = self._confirm_host(host_client, room_id, token)
        self.assertEqual(third_started["series"]["waiting"], False)
        self.assertEqual(third_started["series"]["game"], 3)
        self.assertEqual(third_started["game"]["phase"], "playing")

        final = self._play_until(host_client, room_id, token, host_player_id)
        self.assertEqual(final["phase"], "finished")
        self.assertFalse(final["series"]["waiting"])
        self.assertEqual(final["series"]["game"], 3)
        self.assertEqual(final["series"]["totalGames"], 3)
        self.assertTrue(final["series"]["isFinal"])
        final_series = final["series"]["result"]
        self.assertEqual(final_series["game"], 3)
        self.assertEqual(final_series["isFinal"], True)
        self.assertEqual(final_series["totalGames"], 3)
        self.assertEqual(len(final_series["seats"]), 2)
        self.assertIsNone(final_series["nextDealerSeat"])

    def test_exit_during_series_aborts_entire_series(self):
        host_client, host = self.create_room("ホスト")
        player_client, player_b = self.join_room(host["roomId"], "B")
        room_id = host["roomId"]
        response = host_client.post(
            f"/api/rooms/{room_id}/start",
            headers=self.headers(host["token"]),
            json={"seriesLength": 3, "cpuCount": 0},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        first = self._play_until(
            host_client,
            room_id,
            host["token"],
            host["playerId"],
            second_client=player_client,
            second_token=player_b["token"],
            second_player_id=player_b["playerId"],
        )
        self.assertEqual(first["phase"], "waiting")

        request = host_client.post(
            f"/api/rooms/{room_id}/exit-request",
            headers=self.headers(host["token"]),
            json={},
        )
        self.assertEqual(request.status_code, 200, request.get_json())
        vote = player_client.post(
            f"/api/rooms/{room_id}/exit-vote",
            headers=self.headers(player_b["token"]),
            json={"requestId": request.get_json()["id"], "approve": True},
        )
        self.assertEqual(vote.status_code, 200, vote.get_json())

        state = self.state(host_client, room_id, host["token"])
        self.assertIsNone(state["game"])
        self.assertIsNone(state["series"])
        self.assertFalse(state["room"]["started"])

    def test_timeout_starts_next_series_without_all_confirmed(self):
        started = self._start_host_with_cpu(series_length=3)
        room_id = started["roomId"]
        host_client = started["client"]
        token = started["token"]

        first = self._play_until(host_client, room_id, token, started["host"]["playerId"])
        self.assertEqual(first["phase"], "waiting")
        self.assertEqual(first["series"]["waiting"], True)

        game = rooms.get_room(room_id).game
        self.assertIsNotNone(game)
        deadline = float(game._series_deadline or 0)
        with patch.object(rooms, "_now", return_value=deadline + 10.5):
            state = self.state(host_client, room_id, token)
        self.assertFalse(state["series"]["waiting"])
        self.assertEqual(state["series"]["game"], 2)
        self.assertEqual(state["game"]["phase"], "playing")


if __name__ == "__main__":
    unittest.main()
