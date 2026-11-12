"""CPU の表示名（候補からのランダム選択・重複回避・フォールバック）と isCpu のテスト。"""

from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from gameplatform import games, rooms
from games.neu import CPU_NAMES
from games.neu.adapter import NeuGame
from games.neu.view import build_view

HUMAN = {"id": "human-1", "name": "人間"}

_UNSET = object()


def make_room(players: list[dict[str, str]], game_key: str = "neu") -> rooms.Room:
    return rooms.Room(
        id="test-room",
        game_key=game_key,
        host_id=players[0]["id"] if players else "",
        players=list(players),
    )


def dummy_definition(cpu_names):
    return games.GameDefinition(
        key="dummy",
        title="ダミー",
        description="",
        min_players=1,
        max_players=7,
        create=lambda players, config: None,
        view=lambda game, player_id: {},
        act=lambda game, player_id, payload: None,
        normalize_config=lambda raw: {},
        cpu_names=cpu_names,
    )


def new_cpus(
    players: list[dict[str, str]],
    count: int,
    *,
    seed: int = 123,
    cpu_names=_UNSET,
) -> list[dict[str, str]]:
    """``_new_cpus`` を seed 固定で呼ぶ。``cpu_names`` 指定時はダミー定義を使う。"""
    room = make_room(players)
    rng_patch = patch.object(rooms, "_cpu_name_rng", random.Random(seed))
    with rng_patch:
        if cpu_names is _UNSET:
            return rooms._new_cpus(room, count)
        definition = dummy_definition(cpu_names)
        with patch.object(rooms.games, "get", return_value=definition):
            return rooms._new_cpus(room, count)


class CpuNameTest(unittest.TestCase):
    def test_names_come_from_candidates_without_duplicates(self):
        cpus = new_cpus([HUMAN], 7)
        names = [cpu["name"] for cpu in cpus]
        self.assertEqual(len(names), 7)
        self.assertEqual(len(set(names)), 7)
        self.assertTrue(set(names) <= set(CPU_NAMES))

    def test_same_seed_reproduces_names(self):
        first = [cpu["name"] for cpu in new_cpus([HUMAN], 5, seed=2026)]
        second = [cpu["name"] for cpu in new_cpus([HUMAN], 5, seed=2026)]
        other = [cpu["name"] for cpu in new_cpus([HUMAN], 5, seed=2027)]
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_human_name_is_not_chosen(self):
        names = [cpu["name"] for cpu in new_cpus([{"id": "h", "name": "はると"}], 5)]
        self.assertNotIn("はると", names)

    def test_human_name_comparison_ignores_case_and_spaces(self):
        names = [
            cpu["name"]
            for cpu in new_cpus(
                [
                    {"id": "h1", "name": " はると "},
                    {"id": "h2", "name": "sAkUrA"},
                ],
                2,
                cpu_names=("はると", "Sakura", "みさき"),
            )
        ]
        # 残った候補は「みさき」だけ。2人目は従来の CPU1 にフォールバックする。
        self.assertEqual(names, ["みさき", "CPU1"])

    def test_falls_back_to_cpu_number_when_candidates_run_out(self):
        names = [
            cpu["name"]
            for cpu in new_cpus(
                [HUMAN], 3, cpu_names=("はると",)
            )
        ]
        self.assertEqual(len(names), 3)
        self.assertEqual(names[0], "はると")
        self.assertEqual(sorted(names[1:]), ["CPU1", "CPU2"])
        self.assertEqual(len(set(names)), 3)

    def test_fallback_avoids_existing_cpu_named_humans(self):
        names = [
            cpu["name"]
            for cpu in new_cpus(
                [{"id": "h", "name": "cpu1"}], 2, cpu_names=None
            )
        ]
        self.assertEqual(names, ["CPU2", "CPU3"])

    def test_game_without_candidates_keeps_legacy_cpu_names(self):
        names = [cpu["name"] for cpu in new_cpus([HUMAN], 3, cpu_names=None)]
        self.assertEqual(names, ["CPU1", "CPU2", "CPU3"])


class IsCpuViewTest(unittest.TestCase):
    def test_is_cpu_depends_on_id_not_on_name(self):
        game = NeuGame(
            [
                ("human-1", "cpu"),
                ("human-2", "CPU1"),
                ("human-3", "cpu-1234abcd"),
                ("cpu-abcd1234", "CPU4"),
            ],
            seed=1,
        )
        game.start()
        players = {p["id"]: p for p in build_view(game, "human-1")["players"]}

        self.assertFalse(players["human-1"]["isCpu"])
        self.assertFalse(players["human-2"]["isCpu"])
        self.assertFalse(players["human-3"]["isCpu"])
        self.assertTrue(players["cpu-abcd1234"]["isCpu"])


if __name__ == "__main__":
    unittest.main()
