"""CPU の待ち時間（``rooms._cpu_delay``）と、人間が全滅したあとの進行のテスト。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from gameplatform import rooms
from games.neu.adapter import NeuGame


class StubGame:
    """``has_chips`` だけを持つゲームのダミー。"""

    def __init__(self, chips_by_id: dict[str, int]) -> None:
        self.chips_by_id = dict(chips_by_id)

    def has_chips(self, player_id: str) -> bool:
        return self.chips_by_id[player_id] > 0


class NoHookGame:
    """``has_chips`` を持たないゲームのダミー（他ゲーム相当）。"""


def make_room(players: list[dict[str, str]], game) -> rooms.Room:
    return rooms.Room(
        id="test-room",
        game_key="neu",
        host_id=players[0]["id"] if players else "",
        players=list(players),
        game=game,
    )


HUMANS = [
    {"id": "human-1", "name": "人間1"},
    {"id": "human-2", "name": "人間2"},
]
CPU = {"id": "cpu-1", "name": "CPU1"}


class CpuDelayTest(unittest.TestCase):
    def test_human_with_chips_keeps_default_delay(self):
        room = make_room(
            [*HUMANS, CPU],
            StubGame({"human-1": 3, "human-2": 0, "cpu-1": 3}),
        )
        self.assertEqual(rooms._cpu_delay(room), rooms.CPU_TURN_DELAY_SECONDS)

    def test_all_humans_without_chips_returns_zero(self):
        room = make_room(
            [*HUMANS, CPU],
            StubGame({"human-1": 0, "human-2": 0, "cpu-1": 3}),
        )
        self.assertEqual(rooms._cpu_delay(room), 0.0)

    def test_game_without_hook_keeps_default_delay(self):
        room = make_room([*HUMANS, CPU], NoHookGame())
        self.assertEqual(rooms._cpu_delay(room), rooms.CPU_TURN_DELAY_SECONDS)

    def test_cpu_chips_are_ignored_when_all_humans_are_out(self):
        room = make_room(
            [*HUMANS, CPU],
            StubGame({"human-1": 0, "human-2": 0, "cpu-1": 3}),
        )
        self.assertEqual(rooms._cpu_delay(room), 0.0)


class NeuGameHasChipsTest(unittest.TestCase):
    def test_reports_remaining_chips(self):
        game = NeuGame([("human", "人間"), ("cpu-1", "CPU1")], seed=1)
        game.start()
        self.assertTrue(game.has_chips("human"))
        game.state.players[game.seat_of("human")].chips = 0
        self.assertFalse(game.has_chips("human"))


class CpuFastFinishTest(unittest.TestCase):
    def setUp(self):
        rooms.reset()

    def tearDown(self):
        rooms.reset()

    def test_cpu_finishes_without_delay_after_all_humans_lose_chips(self):
        # start_game が起動する CPU スレッドは止め、状態を整えてから手動で開始する。
        with patch.object(rooms, "kick_cpus"):
            room, human_id, token = rooms.create_room("人間")
            rooms.start_game(room.id, token, cpu_count=2)

        game = room.game
        human_seat = game.seat_of(human_id)
        game.state.players[human_seat].chips = 0
        game.state.players[human_seat].alive = False

        cpu_seats = [
            seat
            for seat, player_id in enumerate(game.player_ids)
            if player_id.startswith("cpu")
        ]
        self.assertEqual(len(cpu_seats), 2)
        game.state.dealer = cpu_seats[0]
        game.state.current_player = cpu_seats[0]
        game.state.cards_remaining = 1

        delays: list[float] = []
        with patch.object(rooms.time, "sleep", side_effect=delays.append):
            rooms.kick_cpus(room)
            rooms.wait_for_cpus(room.id)

        self.assertTrue(delays)
        self.assertEqual(delays, [0.0] * len(delays))
        self.assertEqual(game.state.phase, "game_over")


if __name__ == "__main__":
    unittest.main()
