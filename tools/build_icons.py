#!/usr/bin/env python3
"""neu のアイコン(SVG/PNG/ICO)を生成する。

- 文字は Poppins ExtraBold (SIL Open Font License 1.1) のグリフを
  パスに変換して埋め込む（<text> は使わない）。
- PNG/ICO は Playwright(Chromium) で SVG を 2048px に描画し、
  LANCZOS で各サイズへ縮小する。

使い方:
    python3 tools/build_icons.py
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "tools" / "fonts" / "Poppins-ExtraBold.ttf"
OUT_DIR = ROOT / "static" / "img" / "icon"

SIZE = 1024
TEXT = "neu"
FILL_COLORS = {
    "n": "#1E9B4A",
    "e": "#FFD400",
    "u": "#1E5BC6",
}
BAND_COLORS = {
    "top": "#1E5BC6",
    "middle": "#E02020",
    "bottom": "#1E9B4A",
}
BAND_TOP = 204.8
BAND_BOTTOM = 819.2

# 白い縁取りは「塗りの外側に約24px見える」ようにする。
# stroke-width=48 を塗りの背後に描くと、外側に24pxが出る。
OUTLINE_VISIBLE = 24.0
OUTLINE_STROKE = OUTLINE_VISIBLE * 2
TARGET_OUTER_WIDTH = 880.0  # 縁取りを含む全体の横幅（1024中 約86%）
CENTER = 512.0


def svg_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def glyph_geometry(font: TTFont) -> list[dict]:
    """各文字のパス・送り幅・インク境界をフォント座標で返す。"""
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    glyphs = []
    x_cursor = 0.0
    for char in TEXT:
        name = cmap[ord(char)]
        bounds_pen = BoundsPen(glyph_set)
        glyph_set[name].draw(bounds_pen)
        if bounds_pen.bounds is None:
            raise RuntimeError(f"グリフ {char!r} の境界を取得できません")
        glyphs.append(
            {
                "char": char,
                "name": name,
                "x": x_cursor,
                "advance": float(hmtx[name][0]),
                "bounds": bounds_pen.bounds,  # (xMin, yMin, xMax, yMax)
            }
        )
        x_cursor += hmtx[name][0]
    return glyphs


def build_svg() -> tuple[str, dict]:
    font = TTFont(FONT_PATH)
    glyphs = glyph_geometry(font)
    glyph_set = font.getGlyphSet()

    ink_left = min(g["x"] + g["bounds"][0] for g in glyphs)
    ink_right = max(g["x"] + g["bounds"][2] for g in glyphs)
    ink_top = max(g["bounds"][3] for g in glyphs)
    ink_bottom = min(g["bounds"][1] for g in glyphs)

    # 縁取りを含む横幅を TARGET_OUTER_WIDTH に合わせる。
    scale = (TARGET_OUTER_WIDTH - OUTLINE_STROKE) / (ink_right - ink_left)

    ink_center_x = (ink_left + ink_right) / 2
    ink_center_y = (ink_top + ink_bottom) / 2

    def to_svg(x: float, y: float) -> tuple[float, float]:
        return (
            (x - ink_center_x) * scale + CENTER,
            -(y - ink_center_y) * scale + CENTER,
        )

    tx = CENTER - ink_center_x * scale
    ty = CENTER + ink_center_y * scale
    transform = (scale, 0, 0, -scale, tx, ty)

    outline_paths = []
    fill_paths = []
    for glyph in glyphs:
        pen = SVGPathPen(glyph_set, ntos=svg_number)
        glyph_transform = (
            transform[0],
            transform[1],
            transform[2],
            transform[3],
            transform[4] + glyph["x"] * scale,
            transform[5],
        )
        glyph_set[glyph["name"]].draw(TransformPen(pen, glyph_transform))
        path = pen.getCommands()
        outline_paths.append(path)
        fill_paths.append(
            f'    <path d="{path}" fill="{FILL_COLORS[glyph["char"]]}"/>'
        )

    outline_markup = "\n".join(
        f'    <path d="{path}"/>' for path in outline_paths
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">
  <title>neu</title>
  <!-- 背景: 上20% 青 / 中60% 赤 / 下20% 緑 -->
  <rect x="0" y="0" width="{SIZE}" height="{svg_number(BAND_TOP)}" fill="{BAND_COLORS["top"]}"/>
  <rect x="0" y="{svg_number(BAND_TOP)}" width="{SIZE}" height="{svg_number(BAND_BOTTOM - BAND_TOP)}" fill="{BAND_COLORS["middle"]}"/>
  <rect x="0" y="{svg_number(BAND_BOTTOM)}" width="{SIZE}" height="{svg_number(SIZE - BAND_BOTTOM)}" fill="{BAND_COLORS["bottom"]}"/>

  <!-- 白い縁取り（文字の背後） -->
  <g fill="none" stroke="#FFFFFF" stroke-width="{svg_number(OUTLINE_STROKE)}" stroke-linejoin="round" stroke-linecap="round">
{outline_markup}
  </g>

  <!-- 塗り -->
  <g>
{chr(10).join(fill_paths)}
  </g>
</svg>
"""

    outer_bounds = {
        "scale": scale,
        "text_top": to_svg(0, ink_top)[1] - OUTLINE_VISIBLE,
        "text_bottom": to_svg(0, ink_bottom)[1] + OUTLINE_VISIBLE,
        "outer_width": TARGET_OUTER_WIDTH,
        "outer_height": (ink_top - ink_bottom) * scale + OUTLINE_STROKE,
    }
    return svg, outer_bounds


def render_master(svg: str) -> Image.Image:
    """Chromium で SVG を 2048px に描画して Pillow 画像で返す。"""
    render_size = 2048
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;overflow:hidden;background:#fff}"
        f"svg{{display:block;width:{render_size}px;height:{render_size}px}}</style>"
        f"</head><body>{svg}</body></html>"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": render_size, "height": render_size},
            device_scale_factor=1,
        )
        page.set_content(html, wait_until="load")
        png = page.screenshot(type="png")
        browser.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


def save_icons(master: Image.Image) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def resized(size: int) -> Image.Image:
        return master.resize((size, size), Image.Resampling.LANCZOS)

    resized(32).save(OUT_DIR / "favicon-32.png")
    resized(180).save(OUT_DIR / "apple-touch-icon.png")
    resized(192).save(OUT_DIR / "icon-192.png")
    resized(512).save(OUT_DIR / "icon-512.png")

    ico_source = resized(48)
    ico_source.save(
        OUT_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )

    # 32px の見た目確認用（ニアレストネイバーで16倍に拡大）。
    preview = resized(32).resize((512, 512), Image.Resampling.NEAREST)
    preview.save(OUT_DIR / "favicon-32-preview.png")


def main() -> None:
    if not FONT_PATH.exists():
        raise SystemExit(f"フォントが見つかりません: {FONT_PATH}")

    svg, bounds = build_svg()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "neu-icon.svg").write_text(svg, encoding="utf-8")

    master = render_master(svg)
    save_icons(master)

    print(f"SVG: {OUT_DIR / 'neu-icon.svg'}")
    print(f"PNG/ICO: {OUT_DIR}")
    print(
        "文字＋縁取り: "
        f"横幅 {bounds['outer_width']:.1f}px / 高さ {bounds['outer_height']:.1f}px "
        f"(赤帯 {BAND_TOP:.1f}–{BAND_BOTTOM:.1f} 内)"
    )
    print(
        "文字＋縁取りの上下端: "
        f"y={bounds['text_top']:.1f}–{bounds['text_bottom']:.1f}px"
    )


if __name__ == "__main__":
    main()
