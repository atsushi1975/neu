"""連戦（シリーズ）の純粋ロジックのテスト。"""

from __future__ import annotations

import unittest

from neu.engine import finish_order
from neu.series import (
    SERIES_LENGTHS,
    add_scores,
    build_series_result,
    competition_ranks,
    last_place_seat,
    next_dealer,
    points_by_seat,
    points_for_rank,
    rank_changes,
    ranks_from_round_results,
    validate_series_length,
)


class PointsTest(unittest.TestCase):
    def test_points_for_each_player_count(self):
        expected = {
            2: [10, 0],
            3: [10, 7, 0],
            4: [10, 7, 5, 0],
            5: [10, 7, 5, 1, 0],
            6: [10, 7, 5, 1, 1, 0],
            7: [10, 7, 5, 1, 1, 1, 0],
        }
        for player_count, points in expected.items():
            with self.subTest(player_count=player_count):
                self.assertEqual(
                    [
                        points_for_rank(rank, player_count)
                        for rank in range(1, player_count + 1)
                    ],
                    points,
                )

    def test_invalid_rank_and_player_count_are_rejected(self):
        for rank, player_count in ((0, 4), (5, 4), (1, 1), ("1", 4)):
            with self.subTest(rank=rank, player_count=player_count):
                with self.assertRaises(ValueError):
                    points_for_rank(rank, player_count)  # type: ignore[arg-type]

    def test_points_by_seat_uses_rank_of_each_seat(self):
        self.assertEqual(
            points_by_seat({0: 2, 1: 1, 2: 3}), {0: 7, 1: 10, 2: 0}
        )

    def test_points_by_seat_rejects_missing_or_duplicate_ranks(self):
        for ranks in (
            {0: 1, 1: 1, 2: 3},
            {0: 1, 1: 2, 2: 4},
            {1: 1, 2: 2, 3: 3},
        ):
            with self.subTest(ranks=ranks):
                with self.assertRaises(ValueError):
                    points_by_seat(ranks)


class FinishOrderTest(unittest.TestCase):
    def test_finish_order_is_winner_then_reverse_elimination(self):
        round_results = [
            {"loser": 2, "eliminated": False},
            {"loser": 0, "eliminated": True},
            {"loser": 2, "eliminated": True},
            {"loser": 1, "eliminated": True},
        ]
        self.assertEqual(finish_order(round_results, 3, 4), [3, 1, 2, 0])

    def test_chained_eliminations_in_the_same_round_keep_order(self):
        round_results = [
            {"round": 5, "loser": 2, "eliminated": True},
            {"round": 5, "loser": 0, "eliminated": True},
            {"round": 5, "loser": 1, "eliminated": True},
        ]
        self.assertEqual(finish_order(round_results, 3, 4), [3, 1, 0, 2])

    def test_ranks_from_round_results_returns_seat_to_rank(self):
        round_results = [
            {"loser": 0, "eliminated": True},
            {"loser": 1, "eliminated": True},
        ]
        self.assertEqual(
            ranks_from_round_results(round_results, winner_seat=2, player_count=3),
            {2: 1, 1: 2, 0: 3},
        )


class AccumulationTest(unittest.TestCase):
    def test_first_game_uses_earned_points_as_total(self):
        earned = {0: 10, 1: 7, 2: 0}
        self.assertEqual(add_scores(None, earned), earned)
        self.assertEqual(add_scores({}, earned), earned)

    def test_later_game_adds_previous_total(self):
        self.assertEqual(
            add_scores(
                {0: 10, 1: 7, 2: 0},
                {0: 1, 1: 10, 2: 0},
            ),
            {0: 11, 1: 17, 2: 0},
        )

    def test_different_seats_are_rejected(self):
        with self.assertRaises(ValueError):
            add_scores({0: 10, 1: 0}, {0: 1, 2: 0})

    def test_inputs_are_not_modified(self):
        previous = {0: 10, 1: 7, 2: 0}
        earned = {0: 1, 1: 10, 2: 0}
        add_scores(previous, earned)
        self.assertEqual(previous, {0: 10, 1: 7, 2: 0})
        self.assertEqual(earned, {0: 1, 1: 10, 2: 0})


class CompetitionRanksTest(unittest.TestCase):
    def test_distinct_scores_get_sequential_ranks(self):
        self.assertEqual(
            competition_ranks({0: 20, 1: 15, 2: 7, 3: 0}),
            {0: 1, 1: 2, 2: 3, 3: 4},
        )

    def test_tied_top_ranks_skip_the_next_rank(self):
        self.assertEqual(
            competition_ranks({0: 17, 1: 17, 2: 10, 3: 0}),
            {0: 1, 1: 1, 2: 3, 3: 4},
        )

    def test_ties_in_the_middle_also_skip_ranks(self):
        self.assertEqual(
            competition_ranks({0: 20, 1: 10, 2: 10, 3: 0}),
            {0: 1, 1: 2, 2: 2, 3: 4},
        )

    def test_all_tied_are_first(self):
        self.assertEqual(
            competition_ranks({0: 5, 1: 5, 2: 5}), {0: 1, 1: 1, 2: 1}
        )

    def test_empty_scores(self):
        self.assertEqual(competition_ranks({}), {})

    def test_input_is_not_modified(self):
        scores = {0: 17, 1: 17, 2: 10}
        competition_ranks(scores)
        self.assertEqual(scores, {0: 17, 1: 17, 2: 10})


class RankChangeTest(unittest.TestCase):
    def test_first_game_has_no_comparison(self):
        self.assertEqual(
            rank_changes(None, {0: 1, 1: 2, 2: 3}),
            {0: None, 1: None, 2: None},
        )
        self.assertEqual(
            rank_changes({}, {0: 1, 1: 2, 2: 3}),
            {0: None, 1: None, 2: None},
        )

    def test_up_same_down_compare_lower_rank_as_better(self):
        self.assertEqual(
            rank_changes(
                {0: 1, 1: 2, 2: 3, 3: 4},
                {0: 2, 1: 1, 2: 3, 3: 4},
            ),
            {0: "down", 1: "up", 2: "same", 3: "same"},
        )

    def test_different_seats_are_rejected(self):
        with self.assertRaises(ValueError):
            rank_changes({0: 1, 1: 2}, {0: 1, 2: 2})


class NextDealerTest(unittest.TestCase):
    def test_last_place_becomes_next_dealer(self):
        ranks = {0: 2, 1: 1, 2: 3}
        self.assertEqual(last_place_seat(ranks), 2)
        self.assertEqual(next_dealer(ranks), 2)

    def test_last_place_must_be_unique(self):
        # 試合内順位は重複しないので、順位表が壊れている場合は拒否する。
        for ranks in ({0: 1, 1: 1, 2: 3}, {0: 1, 1: 2, 2: 2}):
            with self.subTest(ranks=ranks):
                with self.assertRaises(ValueError):
                    last_place_seat(ranks)


class SeriesLengthTest(unittest.TestCase):
    def test_allowed_lengths(self):
        self.assertEqual(SERIES_LENGTHS, (1, 3, 5, 10))
        for value in SERIES_LENGTHS:
            with self.subTest(value=value):
                self.assertEqual(validate_series_length(value), value)

    def test_other_lengths_are_rejected(self):
        for value in (0, 2, 4, 11, "3", True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_series_length(value)  # type: ignore[arg-type]


class SeriesResultTest(unittest.TestCase):
    def test_first_game_result(self):
        result = build_series_result(
            1,
            3,
            {0: 1, 1: 2, 2: 3, 3: 4},
        )
        self.assertEqual(result["game"], 1)
        self.assertEqual(result["totalGames"], 3)
        self.assertFalse(result["isFinal"])
        self.assertEqual(result["nextDealerSeat"], 3)
        self.assertEqual(
            result["seats"],
            [
                {
                    "seat": 0,
                    "rank": 1,
                    "points": 10,
                    "total": 10,
                    "seriesRank": 1,
                    "rankChange": None,
                },
                {
                    "seat": 1,
                    "rank": 2,
                    "points": 7,
                    "total": 7,
                    "seriesRank": 2,
                    "rankChange": None,
                },
                {
                    "seat": 2,
                    "rank": 3,
                    "points": 5,
                    "total": 5,
                    "seriesRank": 3,
                    "rankChange": None,
                },
                {
                    "seat": 3,
                    "rank": 4,
                    "points": 0,
                    "total": 0,
                    "seriesRank": 4,
                    "rankChange": None,
                },
            ],
        )

    def test_second_game_accumulates_scores_and_reports_changes(self):
        first = build_series_result(1, 3, {0: 1, 1: 2, 2: 3, 3: 4})
        previous_scores = {row["seat"]: row["total"] for row in first["seats"]}
        previous_ranks = {row["seat"]: row["seriesRank"] for row in first["seats"]}

        second = build_series_result(
            2,
            3,
            {0: 4, 1: 1, 2: 2, 3: 3},
            previous_scores,
            previous_ranks,
        )

        rows = {row["seat"]: row for row in second["seats"]}
        self.assertEqual(
            {seat: row["points"] for seat, row in rows.items()},
            {0: 0, 1: 10, 2: 7, 3: 5},
        )
        self.assertEqual(
            {seat: row["total"] for seat, row in rows.items()},
            {0: 10, 1: 17, 2: 12, 3: 5},
        )
        self.assertEqual(
            {seat: row["seriesRank"] for seat, row in rows.items()},
            {0: 3, 1: 1, 2: 2, 3: 4},
        )
        self.assertEqual(
            {seat: row["rankChange"] for seat, row in rows.items()},
            {0: "down", 1: "up", 2: "up", 3: "same"},
        )
        self.assertFalse(second["isFinal"])
        self.assertEqual(second["nextDealerSeat"], 0)

    def test_ties_use_competition_ranking(self):
        first = build_series_result(1, 3, {0: 1, 1: 2, 2: 3, 3: 4})
        previous_scores = {row["seat"]: row["total"] for row in first["seats"]}
        previous_ranks = {row["seat"]: row["seriesRank"] for row in first["seats"]}

        second = build_series_result(
            2,
            3,
            {0: 2, 1: 1, 2: 3, 3: 4},
            previous_scores,
            previous_ranks,
        )

        rows = {row["seat"]: row for row in second["seats"]}
        self.assertEqual(
            {seat: row["total"] for seat, row in rows.items()},
            {0: 17, 1: 17, 2: 10, 3: 0},
        )
        self.assertEqual(
            {seat: row["seriesRank"] for seat, row in rows.items()},
            {0: 1, 1: 1, 2: 3, 3: 4},
        )

    def test_final_game_has_no_next_dealer(self):
        result = build_series_result(3, 3, {0: 2, 1: 1, 2: 3})
        self.assertTrue(result["isFinal"])
        self.assertIsNone(result["nextDealerSeat"])

    def test_single_game_series_is_final_immediately(self):
        result = build_series_result(1, 1, {0: 1, 1: 2})
        self.assertTrue(result["isFinal"])
        self.assertIsNone(result["nextDealerSeat"])
        self.assertEqual(
            [(row["rank"], row["points"]) for row in result["seats"]],
            [(1, 10), (2, 0)],
        )

    def test_result_is_seat_based_and_has_no_medals(self):
        result = build_series_result(1, 1, {0: 1, 1: 2, 2: 3})
        self.assertEqual(
            [row["seat"] for row in result["seats"]], [0, 1, 2]
        )
        for row in result["seats"]:
            self.assertNotIn("playerId", row)
            self.assertIsInstance(row["seriesRank"], int)
        self.assertNotIn("medal", result)
        self.assertEqual(set(result), {
            "game",
            "totalGames",
            "isFinal",
            "nextDealerSeat",
            "seats",
        })

    def test_invalid_game_number_is_rejected(self):
        for game_number in (0, 4):
            with self.subTest(game_number=game_number):
                with self.assertRaises(ValueError):
                    build_series_result(game_number, 3, {0: 1, 1: 2})

    def test_inputs_are_not_modified(self):
        ranks = {0: 1, 1: 2, 2: 3}
        previous_scores = {0: 5, 1: 3, 2: 0}
        previous_ranks = {0: 1, 1: 2, 2: 3}
        build_series_result(2, 3, ranks, previous_scores, previous_ranks)
        self.assertEqual(ranks, {0: 1, 1: 2, 2: 3})
        self.assertEqual(previous_scores, {0: 5, 1: 3, 2: 0})
        self.assertEqual(previous_ranks, {0: 1, 1: 2, 2: 3})


if __name__ == "__main__":
    unittest.main()
