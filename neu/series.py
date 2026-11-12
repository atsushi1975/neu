"""連戦（シリーズ）の純粋ロジック。

外部 I/O・時計・乱数には依存しない。席番号（0 始まり）だけを扱い、
プレイヤー id への変換は呼び出し側（アダプタ）で行う。

用語:
    * 試合内順位: 1試合の着順。勝者が1位、以降は脱落の逆順。
    * 得点: 試合内順位から決まる点。最下位は常に0点。
    * 積算点: 各試合の得点の合計。
    * 積算順位: 積算点の競技順位（同点は同順位、次を飛ばす）。
    * 順位変動: 前戦終了後の積算順位と比べた up / same / down。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .engine import finish_order

# 連戦数として選べる値（1 は通常戦）。
SERIES_LENGTHS = (1, 3, 5, 10)

# 上位の得点。最下位は人数にかかわらず 0 点、4位以下（最下位を除く）は 1 点。
_RANK_POINTS = {1: 10, 2: 7, 3: 5}
_LOW_RANK_POINTS = 1
_LAST_PLACE_POINTS = 0

_RANK_CHANGE_UP = "up"
_RANK_CHANGE_SAME = "same"
_RANK_CHANGE_DOWN = "down"


def validate_series_length(total_games: int) -> int:
    """連戦数が 1 / 3 / 5 / 10 のいずれかであることを確認して返す。"""
    if isinstance(total_games, bool) or not isinstance(total_games, int):
        raise ValueError("連戦数は整数で指定してください")
    if total_games not in SERIES_LENGTHS:
        raise ValueError(
            f"連戦数は {', '.join(str(value) for value in SERIES_LENGTHS)} のいずれかです"
            f"（指定: {total_games}）"
        )
    return total_games


def points_for_rank(rank: int, player_count: int) -> int:
    """試合内順位から得点を返す。

    最下位（``rank == player_count``）は常に 0 点。それ以外は
    1位=10、2位=7、3位=5、4位以下=1。
    """
    if isinstance(player_count, bool) or not isinstance(player_count, int):
        raise ValueError("プレイヤー人数は整数で指定してください")
    if player_count < 2:
        raise ValueError("プレイヤー人数は2人以上です")
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise ValueError("順位は整数で指定してください")
    if not (1 <= rank <= player_count):
        raise ValueError(f"順位は1〜{player_count}です（指定: {rank}）")

    if rank == player_count:
        return _LAST_PLACE_POINTS
    return _RANK_POINTS.get(rank, _LOW_RANK_POINTS)


def _validate_ranking(
    ranks_by_seat: Mapping[int, int], player_count: Optional[int] = None
) -> int:
    """試合内順位の対応表を検証し、プレイヤー人数を返す。"""
    if not isinstance(ranks_by_seat, Mapping):
        raise ValueError("順位は席番号をキーにした対応表で指定してください")
    if player_count is None:
        player_count = len(ranks_by_seat)
    if isinstance(player_count, bool) or not isinstance(player_count, int):
        raise ValueError("プレイヤー人数は整数で指定してください")
    if player_count < 2:
        raise ValueError("プレイヤー人数は2人以上です")
    if len(ranks_by_seat) != player_count:
        raise ValueError("席数と順位の数が一致しません")
    if set(ranks_by_seat.keys()) != set(range(player_count)):
        raise ValueError("席番号は0から連番で指定してください")
    if set(ranks_by_seat.values()) != set(range(1, player_count + 1)):
        raise ValueError("試合内順位は1から人数までの重複しない値で指定してください")
    return player_count


def points_by_seat(ranks_by_seat: Mapping[int, int]) -> dict[int, int]:
    """試合内順位の対応表から、席ごとの得点を返す。"""
    player_count = _validate_ranking(ranks_by_seat)
    return {
        seat: points_for_rank(ranks_by_seat[seat], player_count)
        for seat in sorted(ranks_by_seat)
    }


def add_scores(
    previous_scores_by_seat: Optional[Mapping[int, int]],
    earned_scores_by_seat: Mapping[int, int],
) -> dict[int, int]:
    """前戦までの積算点に、今回の得点を足した積算点を返す。

    1戦目は ``previous_scores_by_seat`` に ``None`` または空の対応表を渡す。
    """
    earned = {int(seat): int(points) for seat, points in earned_scores_by_seat.items()}
    if not previous_scores_by_seat:
        return {seat: earned[seat] for seat in sorted(earned)}

    previous = {
        int(seat): int(points) for seat, points in previous_scores_by_seat.items()
    }
    if set(previous) != set(earned):
        raise ValueError("前戦と今回の席が一致しません")
    return {seat: previous[seat] + earned[seat] for seat in sorted(earned)}


def competition_ranks(scores_by_seat: Mapping[int, int]) -> dict[int, int]:
    """積算点から競技順位を返す。

    同点は同順位で、次の順位は飛ばす（1位が2人なら次は3位）。
    """
    scores = {int(seat): int(score) for seat, score in scores_by_seat.items()}
    ordered_scores = sorted(scores.values(), reverse=True)
    ranks: dict[int, int] = {}
    for seat, score in scores.items():
        # 自分より高い点の人数 + 1 が競技順位。
        ranks[seat] = 1 + sum(1 for other in ordered_scores if other > score)
    return {seat: ranks[seat] for seat in sorted(ranks)}


def rank_changes(
    previous_ranks_by_seat: Optional[Mapping[int, int]],
    current_ranks_by_seat: Mapping[int, int],
) -> dict[int, Optional[str]]:
    """前戦終了後の積算順位と比べた順位変動を返す。

    順位は数字が小さいほど良い。``previous_ranks_by_seat`` が ``None`` または
    空の対応表の1戦目は比較対象がないため、すべて ``None`` を返す。
    """
    current = {int(seat): int(rank) for seat, rank in current_ranks_by_seat.items()}
    if not previous_ranks_by_seat:
        return {seat: None for seat in sorted(current)}

    previous = {
        int(seat): int(rank) for seat, rank in previous_ranks_by_seat.items()
    }
    if set(previous) != set(current):
        raise ValueError("前戦と今回の席が一致しません")

    changes: dict[int, Optional[str]] = {}
    for seat in sorted(current):
        if current[seat] < previous[seat]:
            changes[seat] = _RANK_CHANGE_UP
        elif current[seat] == previous[seat]:
            changes[seat] = _RANK_CHANGE_SAME
        else:
            changes[seat] = _RANK_CHANGE_DOWN
    return changes


def last_place_seat(
    ranks_by_seat: Mapping[int, int], player_count: Optional[int] = None
) -> int:
    """試合内順位が最下位の席を返す。最下位が一意でなければ ``ValueError``。"""
    player_count = _validate_ranking(ranks_by_seat, player_count)
    last_places = [
        seat for seat, rank in ranks_by_seat.items() if rank == player_count
    ]
    if len(last_places) != 1:
        raise ValueError("最下位の席が一意に決まりません")
    return int(last_places[0])


def next_dealer(
    ranks_by_seat: Mapping[int, int], player_count: Optional[int] = None
) -> int:
    """次戦の最初のディーラー（前戦のビリ）の席を返す。

    次戦の最初の手番も同じ席。1戦目だけは従来どおり呼び出し側がランダムに決める。
    """
    return last_place_seat(ranks_by_seat, player_count)


def ranks_from_round_results(
    round_results: Sequence[Mapping[str, Any]],
    winner_seat: int,
    player_count: int,
) -> dict[int, int]:
    """ゲーム終了時の ``round_results`` から、席ごとの試合内順位を返す。

    順位そのものは :func:`neu.engine.finish_order`（勝者→脱落の逆順）を再利用する。
    """
    seats = finish_order(list(round_results), winner_seat, player_count)
    return {seat: rank for rank, seat in enumerate(seats, start=1)}


def build_series_result(
    game_number: int,
    total_games: int,
    ranks_by_seat: Mapping[int, int],
    previous_scores_by_seat: Optional[Mapping[int, int]] = None,
    previous_ranks_by_seat: Optional[Mapping[int, int]] = None,
) -> dict[str, Any]:
    """1試合終了時の連戦結果を席番号ベースで組み立てる。

    Args:
        game_number: 今回の試合番号（1始まり）。
        total_games: 連戦数（1 / 3 / 5 / 10）。
        ranks_by_seat: 試合内順位。``{席: 順位}``。
        previous_scores_by_seat: 前戦終了後の積算点。1戦目は ``None``。
        previous_ranks_by_seat: 前戦終了後の積算順位。1戦目は ``None``。

    Returns:
        ``game`` / ``totalGames`` / ``isFinal`` / ``nextDealerSeat`` と、
        席ごとの ``rank``（試合内順位）・``points``（獲得点）・``total``（積算点）・
        ``seriesRank``（積算順位）・``rankChange``（up / same / down / None）を
        ``seats`` に持つ dict。メダルなどの表示は含めない。
    """
    total_games = validate_series_length(total_games)
    if isinstance(game_number, bool) or not isinstance(game_number, int):
        raise ValueError("試合番号は整数で指定してください")
    if not (1 <= game_number <= total_games):
        raise ValueError(f"試合番号は1〜{total_games}です（指定: {game_number}）")

    player_count = _validate_ranking(ranks_by_seat)
    points = points_by_seat(ranks_by_seat)
    totals = add_scores(previous_scores_by_seat, points)
    series_ranks = competition_ranks(totals)
    changes = rank_changes(previous_ranks_by_seat, series_ranks)
    is_final = game_number == total_games

    return {
        "game": game_number,
        "totalGames": total_games,
        "isFinal": is_final,
        "nextDealerSeat": (
            None if is_final else next_dealer(ranks_by_seat, player_count)
        ),
        "seats": [
            {
                "seat": seat,
                "rank": ranks_by_seat[seat],
                "points": points[seat],
                "total": totals[seat],
                "seriesRank": series_ranks[seat],
                "rankChange": changes[seat],
            }
            for seat in sorted(ranks_by_seat)
        ],
    }
