"""カード表示用のメタ情報（ルール判定は持たない）。"""

from __future__ import annotations

from typing import Any

from neu.engine import Card

CARD_LABELS = {
    "n1": "1",
    "n2": "2",
    "n3": "3",
    "n4": "4",
    "n5": "5",
    "n6": "6",
    "n7": "7",
    "n8": "8",
    "n9": "9",
    "n10": "10",
    "n50": "50",
    "nm1": "-1",
    "nm10": "-10",
    "n101": "101",
    "pass": "PASS",
    "turn": "TURN",
    "shot": "SHOT",
    "double": "DOUBLE",
}

CARD_DESCRIPTIONS = {
    "n101": "合計を101にする",
    "nm1": "合計を1減らす",
    "nm10": "合計を10減らす",
    "pass": "合計そのままで手番終了",
    "turn": "回る向きを反転",
    "shot": "自分以外の1人を次の手番に",
    "double": "次の手番の人は2枚出す",
}


def card_key(card: Card) -> str:
    """同種カードを区別する一意な文字列（例: ``n10#3``）。"""
    return f"{card.id}#{card.copy}"


def card_label(card: Card) -> str:
    return CARD_LABELS.get(card.id, card.id)


def card_description(card: Card) -> str:
    if card.id in CARD_DESCRIPTIONS:
        return CARD_DESCRIPTIONS[card.id]
    if card.kind in ("number", "hundred"):
        value = card.value
        if value > 0:
            return f"合計に +{value}"
        return f"合計に {value}"
    return ""


def card_to_view(card: Card | None) -> dict[str, Any] | None:
    if card is None:
        return None
    return {
        "id": card.id,
        "cardId": card_key(card),
        "kind": card.kind,
        "value": card.value,
        "copy": card.copy,
        "label": card_label(card),
        "description": card_description(card),
    }
