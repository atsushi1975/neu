#!/usr/bin/env python3
"""neu アイコンの検証（数値チェック）。

使い方:
    python3 tools/verify_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "static" / "img" / "icon"

BLUE = (30, 91, 198)   # #1E5BC6
RED = (224, 32, 32)    # #E02020
GREEN = (30, 155, 74)  # #1E9B4A
YELLOW = (255, 212, 0) # #FFD400
WHITE = (255, 255, 255)


def distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def classify_band(pixel: tuple[int, int, int]) -> str:
    return min(
        (("blue", BLUE), ("red", RED), ("green", GREEN)),
        key=lambda item: distance(pixel, item[1]),
    )[0]


def check_bands() -> None:
    image = Image.open(ICON_DIR / "icon-512.png").convert("RGB")
    width, height = image.size
    column = [classify_band(image.getpixel((0, y))) for y in range(height)]

    red_start = column.index("red")
    red_end = len(column) - 1 - column[::-1].index("red")
    green_start = column.index("green")

    expected_top = height * 0.20
    expected_bottom = height * 0.80
    top_error = (red_start - expected_top) / height * 100
    bottom_error = (green_start - expected_bottom) / height * 100

    print("1) 512px PNG 左端1列の帯境界")
    print(
        f"   青→赤: y={red_start}px ({red_start / height * 100:.2f}%) "
        f"期待 {expected_top:.1f}px (20.00%) / 誤差 {top_error:+.2f}pt"
    )
    print(
        f"   赤→緑: y={green_start}px ({green_start / height * 100:.2f}%) "
        f"期待 {expected_bottom:.1f}px (80.00%) / 誤差 {bottom_error:+.2f}pt"
    )
    print(f"   赤帯の範囲: y={red_start}–{red_end}px")
    if abs(top_error) > 1.0 or abs(bottom_error) > 1.0:
        raise SystemExit("   失敗: 帯境界の誤差が ±1% を超えています")
    print("   OK: 誤差は ±1% 以内")


def check_text_inside_red_band() -> None:
    image = Image.open(ICON_DIR / "icon-512.png").convert("RGB")
    width, height = image.size
    band_top = height * 0.20
    band_bottom = height * 0.80

    xs: list[int] = []
    ys: list[int] = []
    # 帯境界のアンチエイリアスを避けるため 1px 内側だけを見る。
    for y in range(math.ceil(band_top) + 1, math.floor(band_bottom) - 1):
        for x in range(width):
            if distance(image.getpixel((x, y)), RED) > 40:
                xs.append(x)
                ys.append(y)

    if not xs:
        raise SystemExit("   失敗: 赤帯内に文字ピクセルが見つかりません")

    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    # 512px 座標を 1024px の設計座標へ換算する。
    top_margin = (top - band_top) * 2
    bottom_margin = (band_bottom - bottom) * 2
    text_width = (right - left + 1) * 2
    text_height = (bottom - top + 1) * 2

    print("2) 文字＋縁取りの範囲（512px から1024px換算）")
    print(
        f"   x={left * 2}–{(right + 1) * 2}px / "
        f"y={top * 2}–{(bottom + 1) * 2}px"
    )
    print(f"   横幅 {text_width}px / 高さ {text_height}px")
    print(
        f"   赤帯 y=204.8–819.2px に対する上余白 {top_margin:.1f}px / "
        f"下余白 {bottom_margin:.1f}px"
    )
    if top_margin <= 0 or bottom_margin <= 0:
        raise SystemExit("   失敗: 文字が赤帯からはみ出しています")
    print("   OK: 文字＋縁取りは赤帯の内側")


def check_32px() -> None:
    image = Image.open(ICON_DIR / "favicon-32.png").convert("RGB")
    width, height = image.size
    band_top = int(height * 0.20)
    band_bottom = int(height * 0.80)

    counts = {"白": 0, "n(緑)": 0, "e(黄)": 0, "u(青)": 0}
    colored_pixels: list[tuple[int, int, tuple[int, int, int]]] = []
    for y in range(band_top, band_bottom):
        for x in range(width):
            pixel = image.getpixel((x, y))
            if min(pixel) > 170 and distance(pixel, WHITE) < 120:
                counts["白"] += 1
            if distance(pixel, GREEN) < 45:
                counts["n(緑)"] += 1
                colored_pixels.append((x, y, GREEN))
            if distance(pixel, YELLOW) < 60:
                counts["e(黄)"] += 1
                colored_pixels.append((x, y, YELLOW))
            if distance(pixel, BLUE) < 45:
                counts["u(青)"] += 1
                colored_pixels.append((x, y, BLUE))

    print("3) 32px PNG（赤帯内）の文字ピクセル")
    print(
        "   " + " / ".join(f"{label}={count}px" for label, count in counts.items())
    )
    if any(counts[label] == 0 for label in ("n(緑)", "e(黄)", "u(青)")):
        raise SystemExit("   失敗: 32px でいずれかの文字の塗りが消えています")
    if counts["白"] == 0:
        raise SystemExit("   失敗: 32px で白い縁取りが消えています")
    print(f"   拡大プレビュー: {ICON_DIR / 'favicon-32-preview.png'} (512x512, 16倍)")


def check_ico() -> None:
    image = Image.open(ICON_DIR / "favicon.ico")
    sizes = sorted(image.info.get("sizes", set()))
    print("4) favicon.ico の収録サイズ")
    print("   " + ", ".join(f"{w}x{h}" for w, h in sizes))
    expected = {(16, 16), (32, 32), (48, 48)}
    if set(sizes) != expected:
        raise SystemExit(f"   失敗: 期待 {sorted(expected)} / 実際 {sizes}")
    print("   OK: 16・32・48 を収録")


def main() -> None:
    check_bands()
    print()
    check_text_inside_red_band()
    print()
    check_32px()
    print()
    check_ico()


if __name__ == "__main__":
    main()
