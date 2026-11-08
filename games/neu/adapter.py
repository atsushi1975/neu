"""ノイのエンジンを共通基盤に載せるための薄いアダプタ。"""

from __future__ import annotations

import random
from typing import Any

from neu.engine import GameState, new_game


class NeuGame:
    """``neu.engine`` の純粋な状態を、ルームが扱える形で包む。"""

    def __init__(
        self,
        players: list[tuple[str, str]],
        config: dict[str, Any] | None = None,
        *,
        seed: int | None = None,
    ) -> None:
        self.players = list(players)
        self.config = dict(config or {})
        self.state: GameState | None = None
        self.revision = 0
        self._seed = seed if seed is not None else random.SystemRandom().randrange(2**32)

    @property
    def phase(self) -> str:
        if self.state is None:
            return "lobby"
        return "finished" if self.state.phase == "game_over" else "playing"

    def start(self) -> None:
        if self.state is not None:
            raise ValueError("ゲームはすでに開始しています。")
        self.state = new_game(len(self.players), self._seed)
        self.revision += 1
