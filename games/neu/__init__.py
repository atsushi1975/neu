"""「ノイ (Neu)」を共通基盤に載せるための定義。

共通基盤（gameplatform）から見えるのは ``GAME`` だけ。
ルールの実装は ``neu.engine`` に閉じている。
"""

from __future__ import annotations

from typing import Any

from gameplatform.errors import ConfigurationError
from gameplatform.games import GameDefinition, register

from .adapter import NeuGame
from .cpu import CpuDriver

# CPU の表示名の候補（重複なしでランダムに選ぶ）。追加・変更はこの1か所で行う。
CPU_NAMES = (
    "はると",
    "そうた",
    "れん",
    "たくみ",
    "りく",
    "かいと",
    "ゆうと",
    "しょうた",
    "だいち",
    "こうき",
    "まさと",
    "ひろし",
    "たろう",
    "じろう",
    "さくら",
    "ひなた",
    "みさき",
    "あおい",
    "はるか",
    "なつみ",
    "めぐみ",
    "ゆきこ",
    "はなこ",
    "まりこ",
    "ともこ",
    "みどり",
    "あかり",
    "りこ",
    "ことね",
    "ななみ",
)


def normalize_config(raw: Any) -> dict[str, Any]:
    """外部入力の設定を検証する。現在、ノイに設定項目はない。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("設定はオブジェクトで指定してください。")
    allowed = {"seriesLength"}
    for key in raw:
        if key not in allowed:
            raise ConfigurationError(f"不明な設定です: {key}")
    if "seriesLength" not in raw:
        return {}
    from neu.series import validate_series_length

    value = raw["seriesLength"]
    try:
        value = validate_series_length(value)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    return {"seriesLength": value}


def create_game(players: list[tuple[str, str]], config: dict[str, Any]) -> NeuGame:
    return NeuGame(players, config)


def player_view(game: NeuGame, player_id: str) -> dict[str, Any]:
    return game.view(player_id)


def act(game: NeuGame, player_id: str, payload: dict[str, Any]) -> None:
    game.act(player_id, payload)


GAME = GameDefinition(
    key="neu",
    title="ノイ",
    description="場の合計が101を超えないようにカードを出し合うカードゲーム。",
    min_players=2,
    max_players=7,
    create=create_game,
    view=player_view,
    act=act,
    normalize_config=normalize_config,
    cpu_driver=CpuDriver,
    cpu_names=CPU_NAMES,
)

register(GAME)
