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


def normalize_config(raw: Any) -> dict[str, Any]:
    """外部入力の設定を検証する。現在、ノイに設定項目はない。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("設定はオブジェクトで指定してください。")
    for key in raw:
        raise ConfigurationError(f"不明な設定です: {key}")
    return {}


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
)

register(GAME)
