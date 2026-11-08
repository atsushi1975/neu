"""ノイ (Neu) の動作確認用 CLI。

座席0が人間、それ以外は合法手からランダムに選ぶ簡易 Bot。
本番 UI ではなく、エンジンの動作を目で確認するためのもの。

使い方::

    python -m neu.cli --players 4 --seed 1
"""

from __future__ import annotations

import argparse
import random
from typing import Optional

from .engine import (
    Action,
    Card,
    GameState,
    IllegalAction,
    InvalidSetup,
    apply,
    is_game_over,
    legal_actions,
    new_game,
)

HUMAN_SEAT = 0
DIRECTION_LABEL = {1: "時計回り", -1: "反時計回り"}


# ---------------------------------------------------------------------------
# 表示ヘルパ
# ---------------------------------------------------------------------------


def describe_card(card: Card) -> str:
    if card.kind == "number":
        return f"数字 {card.value:+d}"
    if card.kind == "hundred":
        return "101 (合計を101にする)"
    return {
        "pass": "PASS (手番終了)",
        "turn": "TURN (向き反転)",
        "shot": "SHOT (次の手番を指名)",
        "double": "DOUBLE (次の人は2枚)",
    }.get(card.kind, card.kind)


def projected_total(total: int, card: Card) -> int:
    if card.kind == "hundred":
        return 101
    if card.kind == "number":
        return total + card.value
    return total


def chips_line(state: GameState) -> str:
    parts = []
    for player in state.players:
        if not player.alive:
            parts.append(f"席{player.seat}:脱落")
        else:
            you = "(あなた)" if player.seat == HUMAN_SEAT else ""
            parts.append(f"席{player.seat}:{player.chips}枚{you}")
    return " / ".join(parts)


def flush_events(state: GameState, seen: int) -> int:
    """まだ表示していないイベントを出力し、表示済み件数を返す。"""
    for event in state.events[seen:]:
        print(f"  {event}")
    return len(state.events)


def print_status(state: GameState) -> None:
    player = state.players[state.current_player]
    print()
    print("-" * 64)
    print(
        f"ラウンド {state.round_number} / 手番: 席{player.seat} (あなた) / "
        f"向き: {DIRECTION_LABEL[state.direction]}"
    )
    field_label = state.field_card.id if state.field_card else "-"
    print(
        f"場の合計: {state.total}  (場札: {field_label})  "
        f"山札: {len(state.deck)}枚 / 捨て札: {len(state.discard)}枚"
    )
    print(f"チップ: {chips_line(state)}")

    if state.cards_remaining == 2:
        print("★ DOUBLE の義務: この手番は2枚出します（今は1枚目）")
    elif _is_second_of_double(state):
        print("★ DOUBLE の義務: この手番は2枚出します（今は2枚目）")

    print("手札:")
    shot_targets = [
        p.seat for p in state.players if p.alive and p.seat != player.seat
    ]
    for index, card in enumerate(player.hand):
        total = projected_total(state.total, card)
        note = describe_card(card)
        if card.kind == "double" and state.cards_remaining >= 2:
            note += "  ※DOUBLE中は出せません"
        elif total > 101:
            note += f"  ⚠ 出すと合計 {total} で101超え（ラウンド負け）"
        elif card.is_number:
            note += f"  → 合計 {total}"
        if card.kind == "shot":
            note += f"  (指名先: {', '.join(f'席{s}' for s in shot_targets)})"
        print(f"  [{index}] {card.id:<7} {note}")


def _is_second_of_double(state: GameState) -> bool:
    """同じ人が数字を1枚出した直後（= DOUBLE の2枚目）かどうか。"""
    if state.cards_remaining != 1 or not state.events:
        return False
    last = state.events[-1]
    return last.startswith(f"席{state.current_player} が ") and "を出した" in last


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


def ask_human_action(state: GameState) -> Optional[Action]:
    """人間の入力を1手ぶん受け取る。'q' で None を返す。"""
    player = state.current_player
    while True:
        try:
            raw = input("カード番号を入力 (SHOT は「番号 対象席」, q で終了): ").strip()
        except EOFError:
            print()
            return None
        except KeyboardInterrupt:
            print()
            return None

        if raw.lower() in ("q", "quit", "exit"):
            return None
        if not raw:
            continue

        parts = raw.split()
        try:
            card_index = int(parts[0])
            target = int(parts[1]) if len(parts) >= 2 else None
        except ValueError:
            print("!! 数字で入力してください（例: 0 / 2 1）")
            continue

        action = Action(player=player, card_index=card_index, target=target)
        try:
            # 検証だけ行う（apply は新しい状態を返すので元の state は変わらない）
            apply(state, action)
        except IllegalAction as error:
            print(f"!! {error}")
            continue

        return action


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m neu.cli",
        description="ノイ (Neu) の動作確認用 CLI",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=4,
        help="プレイヤー人数 (2〜7, 既定: 4)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="乱数シード（固定すると山札の並びが再現される）",
    )
    parser.add_argument(
        "--dealer",
        type=int,
        default=None,
        help="最初のディーラーの席番号（指定しない場合はランダム。テスト・CLI の再現用）",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        state = new_game(args.players, seed=args.seed, dealer=args.dealer)
    except InvalidSetup as error:
        print(f"エラー: {error}", flush=True)
        return 2

    bot_rng = random.Random(0 if args.seed is None else args.seed)

    dealer_label = f"席{args.dealer}" if args.dealer is not None else "ランダム"
    print("=" * 64)
    print("ノイ (Neu) - 動作確認用 CLI")
    print(f"プレイヤー: {args.players}人 / seed: {args.seed} / ディーラー: {dealer_label}")
    print(f"最初の手番: 席{state.current_player}（ディーラー席{state.dealer}）")
    print("あなたは席0です。カード番号を入力して出してください。")
    print("=" * 64)

    seen = flush_events(state, 0)
    human_alive = True

    while not is_game_over(state):
        current = state.current_player
        assert current is not None

        if current == HUMAN_SEAT and state.players[HUMAN_SEAT].alive:
            print_status(state)
            action = ask_human_action(state)
            if action is None:
                print("終了します。")
                return 0
        else:
            if human_alive and not state.players[HUMAN_SEAT].alive:
                human_alive = False
                print("あなたは脱落しました。以降は Bot 同士の対戦を表示します。")
            action = bot_rng.choice(legal_actions(state))

        state = apply(state, action)
        seen = flush_events(state, seen)

    print()
    print("=" * 64)
    if state.winner == HUMAN_SEAT:
        print("あなたの勝ちです！")
    else:
        print(f"ゲーム終了。勝者は席{state.winner} です。")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
