"""対戦画面の手動検証スクリプト（unittest の対象外・手動実行用）。

使い方:
    1. アプリを起動する: `python app.py --port 5001`
    2. `python tests/ui_check.py` を実行する。

- Playwright (Chromium) を使う。未インストール環境では `python -m playwright install chromium` を実行する。
- スマホ縦画面（390x844 / 360x640）のブラウザコンテキストで対戦画面を確認する。

実行:
    python tests/ui_check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:5001"
SCREENSHOT_DIR = Path("/tmp/opencode/neu_ui_check")
MOBILE = {"is_mobile": True, "has_touch": True, "device_scale_factor": 2}


# ------------------------------------------------------------------ API


def api_request(method: str, path: str, body: dict | None = None, token: str | None = None, base_url: str | None = None):
    url = f"{(base_url or BASE_URL)}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Player-Token", token)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def api_create_room(name: str) -> tuple[str, str, str]:
    created = api_request("POST", "/api/rooms", {"name": name})
    return created["roomId"], created["playerId"], created["token"]


def api_start(room_id: str, token: str, cpu_count: int = 0) -> None:
    api_request(
        "POST",
        f"/api/rooms/{room_id}/start",
        {"cpuCount": cpu_count},
        token=token,
    )


def api_state(room_id: str, token: str) -> dict:
    return api_request("GET", f"/api/rooms/{room_id}/state", token=token)


def port_in_use(port: int = 5001) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api_request("GET", "/api/health")
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("サーバーが起動しませんでした")


# ------------------------------------------------------------------ ブラウザ


class PlayerPage:
    def __init__(self, context, name: str) -> None:
        self.context = context
        self.page = context.new_page()
        self.name = name
        self.room_id = ""
        self.token = ""
        self.player_id = ""

    def join(self, room_id: str, display_name: str) -> None:
        self.room_id = room_id
        self.page.goto(f"{BASE_URL}/rooms/{room_id}")
        self.page.wait_for_load_state()
        self.page.fill("#join-name", display_name)
        self.page.click("#join-form button[type=submit]")
        self.page.wait_for_timeout(1500)

    def adopt_session(self, room_id: str, token: str, player_id: str, name: str) -> None:
        """API で作ったプレイヤーとしてブラウザを参加させる（席0をブラウザが使う）。"""
        self.room_id = room_id
        self.page.goto(f"{BASE_URL}/rooms/{room_id}")
        self.page.wait_for_load_state()
        self.page.evaluate(
            "([key, value]) => localStorage.setItem(key, JSON.stringify(value))",
            [f"neu:{room_id}", {"token": token, "playerId": player_id, "name": name}],
        )
        self.page.reload()
        self.page.wait_for_load_state()
        self.page.wait_for_timeout(1500)
        self.snapshot()

    def snapshot(self) -> None:
        value = self.page.evaluate(
            "() => JSON.parse(localStorage.getItem('neu:' + document.body.dataset.roomId) || 'null')"
        )
        self.token = value["token"]
        self.player_id = value["playerId"]


def wait_for_game(page) -> None:
    page.wait_for_selector("#game-panel", state="visible", timeout=15000)
    page.wait_for_timeout(1000)


def seat_boxes(page) -> dict:
    return page.evaluate(
        """() => {
          const tiles = [...document.querySelectorAll('#players .player')];
          const rect = (el) => {
            const r = el.getBoundingClientRect();
            return { x: r.x, y: r.y, width: r.width, height: r.height };
          };
          const table = document.getElementById('table');
          const tr = table ? table.getBoundingClientRect() : null;
          return {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            table: tr ? { x: tr.x, y: tr.y, width: tr.width, height: tr.height } : null,
            tiles: tiles.map((tile) => ({
              playerId: tile.dataset.playerId,
              styleLeft: tile.style.left,
              styleTop: tile.style.top,
              box: rect(tile),
            })),
          };
        }"""
    )


def center_boxes(page) -> list[dict]:
    return page.evaluate(
        """() => ['table-sum', 'table-pile', 'table-deck'].map((id) => {
             const el = document.getElementById(id);
             if (!el) return null;
             const r = el.getBoundingClientRect();
             return { id, x: r.x, y: r.y, width: r.width, height: r.height };
           }).filter(Boolean)"""
    )


def overlaps(a: dict, b: dict, pad: float = 0) -> bool:
    return not (
        a["x"] + a["width"] + pad < b["x"]
        or b["x"] + b["width"] + pad < a["x"]
        or a["y"] + a["height"] + pad < b["y"]
        or b["y"] + b["height"] + pad < a["y"]
    )


def check_seats(page, expected_count: int) -> None:
    data = seat_boxes(page)
    tiles = data["tiles"]
    assert len(tiles) == expected_count, f"席の数が {expected_count} でない: {len(tiles)}"
    table = data["table"]
    boxes = []
    for tile in tiles:
        left = float(str(tile["styleLeft"]).replace("px", ""))
        top = float(str(tile["styleTop"]).replace("px", ""))
        assert left == left and top == top, f"席の座標が NaN です: {tile}"
        assert top >= 0, f"席が画面外です: {tile}"
        box = tile["box"]
        assert box["x"] >= table["x"] - 1, f"席が卓からはみ出しました(左): {tile}"
        assert box["y"] >= table["y"] - 1, f"席が卓からはみ出しました(上): {tile}"
        assert box["x"] + box["width"] <= table["x"] + table["width"] + 1, f"席が卓からはみ出しました(右): {tile}"
        assert box["y"] + box["height"] <= table["y"] + table["height"] + 1, f"席が卓からはみ出しました(下): {tile}"
        assert box["x"] + box["width"] <= data["innerWidth"] + 1, f"席が画面外です(右): {tile}"
        assert box["y"] + box["height"] <= data["innerHeight"] + 1, f"席が画面外です(下): {tile}"
        boxes.append(box)
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            overlap = not (
                a["x"] + a["width"] < b["x"]
                or b["x"] + b["width"] < a["x"]
                or a["y"] + a["height"] < b["y"]
                or b["y"] + b["height"] < a["y"]
            )
            assert not overlap, f"席同士が重なっています: {tiles[i]} / {tiles[j]}"
    for center in center_boxes(page):
        for tile in tiles:
            assert not overlaps(tile["box"], center), (
                f"席が卓の中央の表示と重なっています: {tile['playerId']} / {center['id']}"
            )


def check_seat_structure(page, expected_cpu_count: int) -> None:
    """席が「名前 / バッジ行 / チップ」の3段になっていることを確認する。"""
    data = page.evaluate(
        """() => {
          const tiles = [...document.querySelectorAll('#players .player')];
          return tiles.map((tile) => {
            const name = tile.querySelector('.player-name');
            const badges = tile.querySelector('.player-badges');
            const chips = tile.querySelector('.player-chips');
            const dealer = tile.querySelector('.player-tag.dealer');
            const cpu = tile.querySelector('.player-tag.cpu');
            const badgeRect = badges ? badges.getBoundingClientRect() : null;
            return {
              playerId: tile.dataset.playerId,
              order: [...tile.children].map((child) => child.className),
              nameText: name ? name.textContent : null,
              nameClientWidth: name ? name.clientWidth : null,
              nameScrollWidth: name ? name.scrollWidth : null,
              badgeRowHeight: badgeRect ? badgeRect.height : null,
              dealerText: dealer ? dealer.textContent : null,
              dealerInBadges: Boolean(dealer && badges && badges.contains(dealer)),
              cpuText: cpu ? cpu.textContent : null,
              cpuInBadges: Boolean(cpu && badges && badges.contains(cpu)),
              hasEmoji: (tile.textContent || '').includes('🤖'),
              chipsText: chips ? chips.textContent.trim() : null,
            };
          });
        }"""
    )
    assert data, "席が1つも描画されていません"

    for seat in data:
        label = seat["playerId"]
        assert seat["order"][:3] == ["player-name-row", "player-badges", "player-chips"], (
            f"席の3段構造が不正です: {label} / {seat['order']}"
        )
        assert seat["chipsText"] is not None, f"チップ表示がありません: {label}"
        assert not seat["hasEmoji"], f"席に 🤖 が残っています: {label}"

    cpu_seats = [seat for seat in data if seat["cpuText"]]
    assert len(cpu_seats) == expected_cpu_count, (
        f"CPU バッジの数が {expected_cpu_count} でない: {len(cpu_seats)}"
    )
    for seat in cpu_seats:
        assert seat["cpuText"] == "CPU", f"CPU バッジの文字が不正です: {seat['cpuText']}"
        assert seat["cpuInBadges"], f"CPU バッジがバッジ行にありません: {seat['playerId']}"

    dealer_seats = [seat for seat in data if seat["dealerText"]]
    assert len(dealer_seats) == 1, f"「親」バッジが1個ではありません: {dealer_seats}"
    assert dealer_seats[0]["dealerText"] == "親", f"「親」バッジの文字が不正です: {dealer_seats[0]}"
    assert dealer_seats[0]["dealerInBadges"], "「親」バッジがバッジ行にありません"

    # バッジの有無にかかわらず、バッジ行の高さは全席で同じ。
    heights = [seat["badgeRowHeight"] for seat in data]
    assert all(height and height > 0 for height in heights), f"バッジ行の高さが取れません: {heights}"
    assert max(heights) - min(heights) < 0.5, f"バッジ行の高さが席ごとに違います: {heights}"

    # 短い名前（CPU 名を含む）は省略されずに全部見える。
    for seat in data:
        name = seat["nameText"] or ""
        if len(name) <= 4:
            assert seat["nameScrollWidth"] <= seat["nameClientWidth"], (
                f"名前が省略されています: {name!r} "
                f"(client={seat['nameClientWidth']} scroll={seat['nameScrollWidth']})"
            )


def check_badge_same_row(page) -> None:
    """CPU を親にしたときも「親」と「CPU」が同じ行に横並びになることを確認する。"""
    data = page.evaluate(
        """() => {
          const cpu = gameState.players.find((player) => player.isCpu);
          if (!cpu) return null;
          const previous = gameState.players.map((player) => player.isDealer);
          gameState.players.forEach((player) => { player.isDealer = player.id === cpu.id; });
          renderPlayers();
          const tile = [...document.querySelectorAll('#players .player')]
            .find((item) => item.dataset.playerId === cpu.id);
          const dealer = tile && tile.querySelector('.player-tag.dealer');
          const badge = tile && tile.querySelector('.player-tag.cpu');
          const row = tile && tile.querySelector('.player-badges');
          const tileRect = tile ? tile.getBoundingClientRect() : null;
          const dealerRect = dealer ? dealer.getBoundingClientRect() : null;
          const cpuRect = badge ? badge.getBoundingClientRect() : null;
          const rowRect = row ? row.getBoundingClientRect() : null;
          const result = {
            found: Boolean(dealer && badge && row),
            sameTop: dealerRect && cpuRect
              ? Math.abs(dealerRect.top - cpuRect.top) < 1
              : false,
            gap: dealerRect && cpuRect ? cpuRect.left - dealerRect.right : null,
            rowHeight: rowRect ? rowRect.height : null,
            insideTile: Boolean(
              tileRect && dealerRect && cpuRect &&
              dealerRect.left >= tileRect.left - 0.5 &&
              cpuRect.right <= tileRect.right + 0.5
            ),
          };
          gameState.players.forEach((player, index) => { player.isDealer = previous[index]; });
          renderPlayers();
          return result;
        }"""
    )
    assert data and data["found"], "CPU の席で「親」と「CPU」のバッジが見つかりません"
    assert data["sameTop"], "「親」と「CPU」が同じ行に並んでいません"
    assert data["gap"] is not None and data["gap"] >= 0, f"バッジのすき間が不正です: {data}"
    assert data["rowHeight"] and data["rowHeight"] < 24, (
        f"バッジ行が折り返して高くなっています: {data['rowHeight']}"
    )
    assert data["insideTile"], f"バッジが席からはみ出しています: {data}"


def check_dealer_move_stable(page) -> None:
    """親を別の席へ動かしても、席の大きさ・位置が変わらないことを確認する。"""
    def measure() -> dict:
        return {tile["playerId"]: tile for tile in seat_boxes(page)["tiles"]}

    before = measure()
    original = page.evaluate(
        """() => {
          const index = gameState.players.findIndex((player) => player.isDealer);
          const next = (index + 1) % gameState.players.length;
          gameState.players.forEach((player, i) => { player.isDealer = i === next; });
          renderPlayers();
          return index;
        }"""
    )
    page.wait_for_timeout(100)
    after = measure()
    try:
        for player_id, before_tile in before.items():
            after_tile = after[player_id]
            for key in ("width", "height"):
                assert abs(before_tile["box"][key] - after_tile["box"][key]) < 0.5, (
                    f"親の移動で席の {key} が変わりました: {player_id} "
                    f"{before_tile['box'][key]} -> {after_tile['box'][key]}"
                )
            for key in ("x", "y"):
                assert abs(before_tile["box"][key] - after_tile["box"][key]) < 0.5, (
                    f"親の移動で席の位置が変わりました: {player_id} "
                    f"{before_tile['box'][key]} -> {after_tile['box'][key]}"
                )
    finally:
        page.evaluate(
            """(index) => {
              gameState.players.forEach((player, i) => { player.isDealer = i === index; });
              renderPlayers();
            }""",
            original,
        )


def check_long_name(page) -> None:
    """16文字の名前でも席からはみ出さない（必要なら省略される）ことを確認する。"""
    data = page.evaluate(
        """() => {
          const player = gameState.players.find((item) => !item.isMe);
          if (!player) return null;
          const previous = player.name;
          player.name = 'あ'.repeat(16);
          renderPlayers();
          const tile = [...document.querySelectorAll('#players .player')]
            .find((item) => item.dataset.playerId === player.id);
          const name = tile.querySelector('.player-name');
          const tileRect = tile.getBoundingClientRect();
          const nameRect = name.getBoundingClientRect();
          const tableRect = document.getElementById('table').getBoundingClientRect();
          const result = {
            tileInsideTable:
              tileRect.left >= tableRect.left - 1 &&
              tileRect.right <= tableRect.right + 1 &&
              tileRect.top >= tableRect.top - 1 &&
              tileRect.bottom <= tableRect.bottom + 1,
            tileInsideScreen:
              tileRect.left >= -1 &&
              tileRect.right <= window.innerWidth + 1 &&
              tileRect.top >= -1 &&
              tileRect.bottom <= window.innerHeight + 1,
            nameWithinTile:
              nameRect.left >= tileRect.left - 0.5 &&
              nameRect.right <= tileRect.right + 0.5,
            truncated: name.scrollWidth > name.clientWidth,
          };
          player.name = previous;
          renderPlayers();
          return result;
        }"""
    )
    assert data, "長い名前の確認用プレイヤーが見つかりません"
    assert data["tileInsideTable"], f"16文字の名前で席が卓からはみ出しました: {data}"
    assert data["tileInsideScreen"], f"16文字の名前で席が画面からはみ出しました: {data}"
    assert data["nameWithinTile"], f"16文字の名前が席からはみ出しました: {data}"


def check_result_cpu_badge(page) -> None:
    """終了画面の順位表に CPU の文字バッジが出ることを、終了状態を差し替えて確認する。"""
    prepared = page.evaluate(
        """() => {
             const players = (gameState && gameState.players) || [];
             const cpu = players.find((player) => player.isCpu);
             const human = players.find((player) => player.isMe);
             if (!cpu || !human) return false;
             gameState.result = {
               winnerId: cpu.id,
               winnerName: cpu.name,
               message: `${cpu.name} さんの勝ち！`,
               standings: [
                 { playerId: cpu.id, rank: 1 },
                 { playerId: human.id, rank: 2 },
               ],
             };
             renderResult();
             return true;
           }"""
    )
    assert prepared, "終了画面の確認用に CPU と自分の席が見つかりません"
    badges = page.locator("#result-standings .result-row .player-tag.cpu")
    assert badges.count() == 1, f"終了画面の CPU バッジが1個ではありません: {badges.count()}"
    text = badges.first.inner_text()
    assert text == "CPU", f"終了画面の CPU バッジの文字が不正です: {text}"
    assert page.locator("#result-standings").get_by_text("🤖").count() == 0, (
        "終了画面に 🤖 が残っています"
    )
    name_order = page.evaluate(
        """() => {
             const row = document.querySelector('#result-standings .result-row');
             if (!row) return null;
             const name = row.querySelector('.result-name');
             const badge = row.querySelector('.result-name .player-tag.cpu');
             return { hasName: Boolean(name), badgeInName: Boolean(badge) };
           }"""
    )
    assert name_order and name_order["hasName"] and name_order["badgeInName"], (
        f"終了画面の CPU バッジが名前の横にありません: {name_order}"
    )


def check_cpu_toast(page) -> None:
    """CPU が負け、次も CPU のときのトーストを、状態を差し替えて確認する。"""
    data = page.evaluate(
        """() => {
          const previousRound = gameState.lastRound;
          const previousKey = lastRoundKey;
          const cpu = gameState.players.find((player) => player.isCpu);
          if (!cpu) return null;
          gameState.lastRound = {
            round: 999,
            loserId: cpu.id,
            loserName: cpu.name,
            message: `${cpu.name} さんは出せるカードがすべて101を超えるため負け`,
            chipsLeft: 2,
            eliminated: false,
            gameOver: false,
            nextRound: { round: 1000, firstPlayer: cpu.id, sum: 0 },
          };
          lastRoundKey = null;
          renderLastRound();
          const toast = document.getElementById('last-round-toast');
          const title = document.getElementById('toast-title');
          const body = document.getElementById('toast-body');
          const titleBadge = title.querySelector('.player-tag.cpu');
          const bodyBadge = body.querySelector('.player-tag.cpu');
          const badgeIndex = titleBadge
            ? [...title.childNodes].indexOf(titleBadge)
            : -1;
          const textBeforeBadge = [...title.childNodes]
            .slice(0, Math.max(0, badgeIndex))
            .map((node) => node.textContent)
            .join('');
          const rect = toast.getBoundingClientRect();
          const result = {
            visible: !toast.hidden && toast.classList.contains('show'),
            titleText: title.textContent,
            bodyText: body.textContent,
            titleBadge: Boolean(titleBadge),
            bodyBadge: Boolean(bodyBadge),
            titleBadgeAfterName: textBeforeBadge === `${cpu.name} `,
            titleTextOrder: title.textContent ===
              `${cpu.name} CPU さんは出せるカードがすべて101を超えるため負け`,
            bodyTextOrder: body.textContent.includes(`次は ${cpu.name} CPU さん`),
            insideViewport:
              rect.left >= -1 && rect.right <= window.innerWidth + 1 &&
              rect.top >= -1 && rect.bottom <= window.innerHeight + 1,
            noHands: toast.querySelectorAll('.small-card').length === 0,
          };
          gameState.lastRound = previousRound;
          lastRoundKey = previousKey;
          return result;
        }"""
    )
    assert data, "トースト確認用の CPU が見つかりません"
    assert data["visible"], f"CPU のトーストが表示されていません: {data}"
    assert data["titleBadge"], f"トーストのタイトルに CPU バッジがありません: {data}"
    assert data["bodyBadge"], f"トーストの本文に CPU バッジがありません: {data}"
    assert data["titleBadgeAfterName"], f"CPU バッジが名前の直後にありません: {data}"
    assert data["titleTextOrder"], f"トーストのタイトルが崩れています: {data['titleText']}"
    assert data["bodyTextOrder"], f"トーストの本文が崩れています: {data['bodyText']}"
    assert data["insideViewport"], f"トーストが画面からはみ出しています: {data}"
    assert data["noHands"], "トーストに手札が表示されています"
    print(f"[toast] タイトル: {data['titleText']} / 本文: {data['bodyText']}")


def check_series_start_select(page) -> None:
    """連戦選択のプルダウン（通常戦 / 3連戦 / 5連戦 / 10連戦）を確認する。"""
    data = page.evaluate(
        """() => {
          const select = document.getElementById('series-select');
          if (!select) return null;
          const options = [...select.options].map((option) => ({
            value: option.value,
            text: option.textContent,
          }));
          return { exists: Boolean(select), value: select.value, options };
        }"""
    )
    assert data, "連戦選択のプルダウン(#series-select)が見つかりません"
    assert data["exists"], "連戦選択のプルダウン(#series-select)が見つかりません"
    assert data["value"] == "1", f"連戦選択の既定値が不正です: {data['value']}"
    assert [option["value"] for option in data["options"]] == ["1", "3", "5", "10"], (
        f"連戦選択の選択肢が不正です: {data['options']}"
    )
    assert [option["text"] for option in data["options"]] == [
        "通常戦",
        "3連戦",
        "5連戦",
        "10連戦",
    ], f"連戦選択の文言が不正です: {data['options']}"


def check_series_toast(page) -> None:
    """連戦待機中のトースト（表示・OKで次戦開始確認・次戦開始で自動クローズ）を確認する。"""
    data = page.evaluate(
        """() => {
          const previousSeries = gameState.series;
          const previousRound = gameState.lastRound;
          const previousRoundKey = lastRoundKey;
          gameState.series = {
            game: 2,
            totalGames: 3,
            waiting: true,
            isFinal: false,
            result: {
              game: 2,
              totalGames: 3,
              isFinal: false,
              nextDealerSeat: 1,
              seats: [
                { seat: 0, rank: 1, points: 10, total: 20, seriesRank: 1, rankChange: 'up' },
                { seat: 1, rank: 2, points: 0, total: 10, seriesRank: 2, rankChange: 'down' },
              ],
            },
            confirmed: false,
            remainingSeconds: 10,
          };
          gameState.lastRound = {
            round: 1,
            loserId: 'p1',
            loserName: '相手',
            message: '相手 さんは出せるカードがすべて101を超えるため負け',
            chipsLeft: 0,
            eliminated: true,
            gameOver: true,
          };
          lastRoundKey = null;
          renderLastRound();
          const toast = document.getElementById('last-round-toast');
          const title = document.getElementById('toast-title');
          const body = document.getElementById('toast-body');
          const ok = document.getElementById('toast-ok');
          const rect = toast.getBoundingClientRect();
          const result = {
            visible: !toast.hidden && toast.classList.contains('show'),
            title: title.textContent,
            body: body.textContent,
            okVisible: !ok.hidden,
            insideViewport:
              rect.left >= -1 && rect.right <= window.innerWidth + 1 &&
              rect.top >= -1 && rect.bottom <= window.innerHeight + 1,
          };
          gameState.series = previousSeries;
          gameState.lastRound = previousRound;
          lastRoundKey = previousRoundKey;
          return result;
        }"""
    )
    assert data, "連戦トースト確認用の状態を組み立てられません"
    assert data["visible"], f"連戦待機トーストが表示されていません: {data}"
    assert data["title"] == "第2戦 / 全3戦", f"連戦トーストのタイトルが不正です: {data['title']}"
    assert "1位" in data["body"] and "累計" in data["body"], (
        f"連戦トーストの本文が不正です: {data['body']}"
    )
    assert data["okVisible"], f"連戦トーストに OK ボタンがありません: {data}"
    assert data["insideViewport"], f"連戦トーストが画面からはみ出しています: {data}"

    confirm_urls: list[str] = []

    def _record_confirm(request) -> None:
        if "/series/confirm" in request.url:
            confirm_urls.append(request.url)

    page.on("request", _record_confirm)
    try:
        page.locator("#toast-ok").click()
        page.wait_for_timeout(1500)
    finally:
        page.off("request", _record_confirm)
    assert confirm_urls, f"OK で次戦開始の確認を送りませんでした: {confirm_urls}"
    hidden_after_ok = page.locator("#last-round-toast").evaluate("el => el.hidden")
    page.wait_for_timeout(600)
    hidden_after_ok = page.locator("#last-round-toast").evaluate("el => el.hidden")
    assert hidden_after_ok, "OK を押してもトーストが閉じませんでした"

    # 次戦が始まった（トースト側の状態がリセットされた）場合は、自動で閉じる。
    data = page.evaluate(
        """() => {
          gameState.lastRound = null;
          lastRoundKey = '1:p1:0:0:true';
          renderLastRound();
          const toast = document.getElementById('last-round-toast');
          return { hidden: toast.hidden, shown: !toast.hidden && toast.classList.contains('show') };
        }"""
    )
    assert not data["shown"], "次戦開始後のトーストが自動で閉じませんでした"


def check_series_final_toast(page) -> None:
    """最終戦のトースト（メダル付き・OKで結果オーバーレイへ）を確認する。"""
    data = page.evaluate(
        """() => {
          const previousSeries = gameState.series;
          const previousRound = gameState.lastRound;
          const previousRoundKey = lastRoundKey;
          const previousFinalOpen = finalSeriesToastOpen;
          gameState.series = {
            game: 3,
            totalGames: 3,
            waiting: false,
            isFinal: true,
            result: {
              game: 3,
              totalGames: 3,
              isFinal: true,
              nextDealerSeat: null,
              seats: [
                { seat: 0, rank: 1, points: 10, total: 30, seriesRank: 1, rankChange: 'up' },
                { seat: 1, rank: 2, points: 7, total: 21, seriesRank: 2, rankChange: 'up' },
                { seat: 2, rank: 3, points: 5, total: 15, seriesRank: 3, rankChange: 'same' },
                { seat: 3, rank: 4, points: 1, total: 1, seriesRank: 4, rankChange: 'down' },
                { seat: 4, rank: 5, points: 1, total: 1, seriesRank: 5, rankChange: 'down' },
                { seat: 5, rank: 6, points: 1, total: 1, seriesRank: 6, rankChange: 'down' },
                { seat: 6, rank: 7, points: 0, total: 0, seriesRank: 7, rankChange: 'down' },
              ],
            },
            confirmed: true,
            remainingSeconds: 0,
          };
          gameState.result = {
            winnerId: 'p0',
            winnerName: 'あなた',
            message: 'あなた さんの勝ち！',
            standings: [
              { playerId: 'p0', rank: 1 },
              { playerId: 'p1', rank: 2 },
              { playerId: 'p2', rank: 3 },
              { playerId: 'p3', rank: 4 },
              { playerId: 'p4', rank: 5 },
              { playerId: 'p5', rank: 6 },
              { playerId: 'p6', rank: 7 },
            ],
          };
          gameState.lastRound = {
            round: 2,
            loserId: 'p1',
            loserName: '相手',
            message: '相手 さんは出せるカードがすべて101を超えるため負け',
            chipsLeft: 0,
            eliminated: true,
            gameOver: true,
          };
          lastRoundKey = null;
          renderLastRound();
          const toast = document.getElementById('last-round-toast');
          const title = document.getElementById('toast-title');
          const body = document.getElementById('toast-body');
          const overlay = document.getElementById('result-overlay');
          const rect = toast.getBoundingClientRect();
          const result = {
            visible: !toast.hidden && toast.classList.contains('show'),
            title: title.textContent,
            body: body.textContent,
            overlayHidden: overlay ? overlay.hidden : null,
            insideViewport:
              rect.left >= -1 && rect.right <= window.innerWidth + 1 &&
              rect.top >= -1 && rect.bottom <= window.innerHeight + 1,
            scrollHeight: toast.scrollHeight,
            clientHeight: toast.clientHeight,
          };
          gameState.series = previousSeries;
          gameState.lastRound = previousRound;
          lastRoundKey = previousRoundKey;
          finalSeriesToastOpen = previousFinalOpen;
          return result;
        }"""
    )
    assert data, "最終戦トースト確認用の状態を組み立てられません"
    assert data["visible"], f"最終戦トーストが表示されていません: {data}"
    assert data["title"] == "最終結果", f"最終戦トーストのタイトルが不正です: {data['title']}"
    assert "🥇" in data["body"], f"最終戦トーストに金メダルがありません: {data['body']}"
    assert data["overlayHidden"], f"最終戦トースト表示中に結果オーバーレイが出ています: {data}"
    assert data["insideViewport"], f"最終戦トーストが画面からはみ出しています: {data}"
    assert data["scrollHeight"] <= data["clientHeight"] + 1, (
        f"最終戦トーストが縦スクロールなしに収まりません: scrollHeight={data['scrollHeight']} clientHeight={data['clientHeight']}"
    )

    page.locator("#toast-ok").click()
    page.wait_for_timeout(1500)
    overlay_hidden = page.locator("#result-overlay").evaluate("el => el.hidden")
    assert not overlay_hidden, "最終戦トーストの OK 後に結果オーバーレイが出ません"


def check_cpu_prompt(page) -> None:
    """手番の案内に CPU の文字バッジが出ることを確認する。"""
    data = page.evaluate(
        """() => {
          const previous = gameState.currentPlayerId;
          const cpu = gameState.players.find((player) => player.isCpu);
          if (!cpu) return null;
          gameState.currentPlayerId = cpu.id;
          renderPrompt();
          const prompt = document.getElementById('prompt');
          const badge = prompt.querySelector('.player-tag.cpu');
          const badgeIndex = badge ? [...prompt.childNodes].indexOf(badge) : -1;
          const textBeforeBadge = [...prompt.childNodes]
            .slice(0, Math.max(0, badgeIndex))
            .map((node) => node.textContent)
            .join('');
          const lineHeight = parseFloat(getComputedStyle(prompt).lineHeight) || 20;
          const result = {
            badge: Boolean(badge),
            text: prompt.textContent,
            textOrder:
              prompt.textContent.includes(`${cpu.name} CPU さん`) &&
              prompt.textContent.includes('の番です'),
            badgeAfterName: textBeforeBadge === `${cpu.name} `,
            lines: Math.round(prompt.getBoundingClientRect().height / lineHeight),
          };
          gameState.currentPlayerId = previous;
          renderPrompt();
          return result;
        }"""
    )
    assert data, "プロンプト確認用の CPU が見つかりません"
    assert data["badge"], f"手番の案内に CPU バッジがありません: {data}"
    assert data["textOrder"], f"手番の案内の文言が崩れています: {data['text']}"
    assert data["badgeAfterName"], f"CPU バッジが名前の直後にありません: {data}"
    assert data["lines"] <= 2, f"手番の案内が {data['lines']} 行になっています"


def get_sum_elements(page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#table-sum, .summary, #summary-sum')]
             .filter((el) => el.offsetParent !== null)
             .map((el) => ({ id: el.id, cls: String(el.className), text: (el.textContent || '').trim() }))"""
    )


def check_screen_fit(page) -> None:
    data = page.evaluate(
        """() => {
             const panel = document.getElementById('game-panel');
             const r = panel ? panel.getBoundingClientRect() : null;
             return {
               scrollHeight: document.documentElement.scrollHeight,
               scrollWidth: document.documentElement.scrollWidth,
               innerHeight: window.innerHeight,
               innerWidth: window.innerWidth,
               panelBottom: r ? r.bottom : 0,
               panelRight: r ? r.right : 0,
             };
           }"""
    )
    assert data["scrollHeight"] <= data["innerHeight"], (
        f"画面が 1 画面に収まりません: scrollHeight={data['scrollHeight']} innerHeight={data['innerHeight']}"
    )
    assert data["scrollWidth"] <= data["innerWidth"], (
        f"横にはみ出しています: scrollWidth={data['scrollWidth']} innerWidth={data['innerWidth']}"
    )
    assert data["panelBottom"] <= data["innerHeight"] + 0.5, (
        f"対戦画面の下端が画面外です: panelBottom={data['panelBottom']} innerHeight={data['innerHeight']}"
    )
    assert data["panelRight"] <= data["innerWidth"] + 0.5, (
        f"対戦画面の右端が画面外です: panelRight={data['panelRight']} innerWidth={data['innerWidth']}"
    )


def check_absent(page, selector: str, label: str) -> None:
    assert page.locator(selector).count() == 0, f"画面に出るべきでない要素が残っています: {label} ({selector})"


def check_round_indicator(page) -> None:
    labels = page.evaluate(
        """() => [...document.querySelectorAll('body *')]
             .filter((el) => el.children.length === 0
               && /^ラウンド\\s*\\d+$/.test((el.textContent || '').trim()))
             .map((el) => el.id || String(el.className))"""
    )
    assert labels == ["round-indicator"], f"ラウンド番号の表示が1か所ではありません: {labels}"


def check_log_collapsed(page) -> None:
    count = page.locator("#log li").count()
    assert count <= 1, f"ログが既定で閉じていません: {count} 行表示"


def check_prompt_single_line(page) -> None:
    lines = page.evaluate(
        "() => { const el = document.getElementById('prompt');"
        " const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;"
        " return Math.round(el.getBoundingClientRect().height / lh); }"
    )
    assert lines <= 2, f"手番の案内が1行に収まっていません: 約 {lines} 行"


def check_toast(page) -> None:
    toast = page.locator("#last-round-toast")
    assert toast.count() == 1, "ラウンド結果トースト(#last-round-toast)が見つかりません"
    visible = toast.evaluate("el => !el.hidden && el.classList.contains('show')")
    assert visible, "ラウンド結果トーストが表示されていません"
    hands = page.locator("#last-round-toast .small-card").count()
    assert hands == 0, f"ラウンド結果トーストに手札のカードが表示されています: {hands} 枚"
    text = page.locator("#toast-body").inner_text()
    title = page.locator("#toast-title").inner_text()
    assert "負け" in f"{title} {text}", f"ラウンド負けのトーストの内容が不正です: {title} / {text}"
    # 人間だけの対戦では、トーストに CPU バッジは出ない。
    assert page.locator("#last-round-toast .player-tag.cpu").count() == 0, (
        "人間だけの対戦のトーストに CPU バッジが表示されています"
    )


def play_until_round_end(players: list[PlayerPage], room_id: str, max_steps: int = 400) -> dict:
    tokens = {p.player_id: p.token for p in players if p.token}
    first_token = players[0].token
    last_round_key = None
    game = api_state(room_id, first_token)["game"]
    for _ in range(max_steps):
        last_round = game.get("lastRound") or {}
        key = json.dumps(last_round, sort_keys=True, default=str)
        if last_round and key != last_round_key:
            last_round_key = key
            return game
        if game.get("phase") == "finished":
            return game
        current_id = game.get("currentPlayerId")
        if not current_id or current_id not in tokens:
            return game
        current = api_state(room_id, tokens[current_id])["game"]
        playable = [c for c in (current.get("me") or {}).get("hand") or [] if c.get("playable")]
        if not playable:
            # 出せるカードが無い → ラウンド負け。トーストを表示する状態。
            return current
        card = playable[0]
        payload = {"type": "play", "cardId": card["cardId"], "expectedRevision": current["revision"]}
        if card.get("targets"):
            payload["target"] = card["targets"][0]
        api_request(
            "POST",
            f"/api/rooms/{room_id}/actions",
            payload,
            token=tokens[current_id],
        )
        game = api_state(room_id, first_token)["game"]
    return game


def take_screenshot(page, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    return path


# ------------------------------------------------------------------ シナリオ


def new_context(browser, viewport: tuple[int, int]):
    # localStorage はコンテキスト単位で共有されるため、1人1コンテキストにする。
    return browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, **MOBILE)


def run_scenario(browser, players_count: int, viewport: tuple[int, int], tag: str) -> None:
    contexts = []
    host_context = new_context(browser, viewport)
    contexts.append(host_context)
    host = PlayerPage(host_context, f"ホスト{tag}")
    room_id, host_pid, host_token = api_create_room(f"ホスト{tag}")
    host.adopt_session(room_id, host_token, host_pid, host.name)

    others: list[PlayerPage] = []
    for index in range(1, players_count):
        other_context = new_context(browser, viewport)
        contexts.append(other_context)
        other = PlayerPage(other_context, f"相手{index}")
        other.join(room_id, f"相手{index}-{tag}")
        other.snapshot()
        others.append(other)

    api_start(room_id, host_token)

    wait_for_game(host.page)
    check_seats(host.page, players_count)
    check_seat_structure(host.page, 0)
    check_dealer_move_stable(host.page)
    check_long_name(host.page)

    game = api_state(room_id, host.token)["game"]
    sum_text = host.page.locator("#table-sum").inner_text()
    assert int(str(sum_text).strip()) == int(game["sum"]), f"場の合計が一致しません: UI={sum_text} game={game['sum']}"

    sum_elements = get_sum_elements(host.page)
    assert len(sum_elements) == 1, f"場の合計表示が {len(sum_elements)} か所あります: {sum_elements}"
    assert sum_elements[0]["id"] == "table-sum", f"場の合計表示が #table-sum 以外です: {sum_elements}"

    check_absent(host.page, ".summary", "上の大きな合計枠")
    check_absent(host.page, "#summary-sum", "summary の合計")
    check_absent(host.page, "#turn-arrow", "回る向きのリング矢印")
    check_absent(host.page, "#hand-count", "自分の手札の枚数表示")
    check_absent(host.page, ".mini-hand", "他のプレイヤーのミニ手札")
    check_absent(host.page, ".discard", "捨て札の表示")
    check_absent(host.page, ".toast-hand", "トーストの手札")
    check_round_indicator(host.page)
    check_log_collapsed(host.page)
    check_prompt_single_line(host.page)

    table_faces = host.page.locator("#table-card .card-face").count()
    # ゲーム開始時は場札は空（初期場札のめくりは廃止済み）。
    assert table_faces == 0, f"開始直後の場札が {table_faces} 枚表示されています: #table-card"
    assert host.page.locator("#table-card .pile-under").count() == 0, "場札の重なり(pile-under)があります"
    assert host.page.locator("#table-count").inner_text().startswith("場札"), host.page.locator("#table-count").inner_text()
    assert host.page.locator("#table-deck .deck-back").count() == 1, "山札の裏面がありません"
    deck_count_text = host.page.locator("#deck-count").inner_text()
    assert "山札" in deck_count_text and "枚" in deck_count_text, f"山札の枚数テキストが不正です: {deck_count_text}"
    hand_cards = host.page.locator("#hand .card").count()
    assert hand_cards == 3, f"自分の手札が {hand_cards} 枚表示されています: #hand"

    check_screen_fit(host.page)
    shot = take_screenshot(host.page, f"{tag}_{viewport[0]}x{viewport[1]}")
    print(f"[{tag}] スクリーンショット: {shot}")

    players = [host] + others
    final_game = play_until_round_end(players, room_id)
    assert final_game.get("lastRound") or final_game.get("phase") == "finished", (
        "ラウンド終了まで到達しませんでした"
    )
    host.page.wait_for_timeout(1500)

    latest = api_state(room_id, host.token)["game"]
    latest_sum = int(str(host.page.locator("#table-sum").inner_text()).strip())
    assert latest_sum == int(latest["sum"]), f"プレイ後の場の合計が一致しません: UI={latest_sum} game={latest['sum']}"
    assert host.page.locator("#table-card .card-face").count() <= 1, "場札が複数枚表示されています"
    if final_game.get("lastRound"):
        check_toast(host.page)
        print(f"[{tag}] ラウンド終了トースト確認: {host.page.locator('#toast-body').inner_text()}")
    for context in contexts:
        context.close()


def run_cpu_scenario(
    browser, cpu_count: int, viewport: tuple[int, int], tag: str
) -> None:
    """CPU の文字バッジ（席・終了画面）と席の3段構造を確認する。"""
    context = new_context(browser, viewport)
    try:
        host = PlayerPage(context, f"ホスト{tag}")
        room_id, host_pid, host_token = api_create_room(f"ホスト{tag}")
        host.adopt_session(room_id, host_token, host_pid, host.name)

        api_start(room_id, host_token, cpu_count=cpu_count)
        wait_for_game(host.page)
        expected_players = 1 + cpu_count
        check_seats(host.page, expected_players)

        game = api_state(room_id, host.token)["game"]
        cpu_players = [player for player in game["players"] if player["isCpu"]]
        human_players = [player for player in game["players"] if not player["isCpu"]]
        assert len(cpu_players) == cpu_count, f"CPU の人数が不正です: {cpu_players}"
        assert [player["id"] for player in human_players] == [host.player_id], (
            f"人間の isCpu が偽になっていません: {human_players}"
        )

        dealer = next(player for player in game["players"] if player.get("isDealer"))
        print(f"[{tag}] 親: {'CPU' if dealer['isCpu'] else '人間'} / {dealer['name']}")

        check_seat_structure(host.page, cpu_count)
        check_badge_same_row(host.page)
        check_dealer_move_stable(host.page)
        check_long_name(host.page)
        check_cpu_prompt(host.page)
        check_screen_fit(host.page)
        screenshot = take_screenshot(host.page, f"{tag}_{viewport[0]}x{viewport[1]}")
        print(f"[{tag}] スクリーンショット: {screenshot}")

        check_cpu_toast(host.page)
        toast_screenshot = take_screenshot(
            host.page, f"{tag}_toast_{viewport[0]}x{viewport[1]}"
        )
        print(f"[{tag}] トースト スクリーンショット: {toast_screenshot}")
        host.page.evaluate(
            """() => {
              const toast = document.getElementById('last-round-toast');
              toast.classList.remove('show');
              toast.hidden = true;
            }"""
        )

        check_result_cpu_badge(host.page)
        result_screenshot = take_screenshot(
            host.page, f"{tag}_result_{viewport[0]}x{viewport[1]}"
        )
        print(f"[{tag}] 終了画面スクリーンショット: {result_screenshot}")
    finally:
        context.close()


# ------------------------------------------------------------------ SHOT 指名シナリオ（手動実行用・in-process）


def _start_inprocess_server(port: int):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from web import create_app

    app = create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    wait_for_server_port(port)
    return app, thread


def wait_for_server_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    base = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            api_request("GET", "/api/health", base_url=base)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("サーバーが起動しませんでした")


def _setup_shot_room(base_url: str):
    created = api_request("POST", "/api/rooms", {"name": "host"}, base_url=base_url)
    room_id = created["roomId"]
    host_pid = created["playerId"]
    host_token = created["token"]
    for index in range(1, 4):
        joined = api_request(
            "POST", f"/api/rooms/{room_id}/join", {"name": f"p{index}"}, base_url=base_url
        )
    api_request("POST", f"/api/rooms/{room_id}/start", {}, token=host_token, base_url=base_url)

    # 現在の手番プレイヤーに SHOT を持たせる（in-process で状態を書き換える）。
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gameplatform import rooms

    room = rooms.get_room(room_id)
    game = room.game
    state = game.state
    shot_card = None
    for player in state.players:
        for card in player.hand:
            if card.kind == "shot":
                shot_card = card
                break
        if shot_card is not None:
            break
    if shot_card is None:
        for card in list(state.deck):
            if card.kind == "shot":
                shot_card = card
                state.deck.remove(card)
                break
    host_seat = game.seat_of(host_pid)
    if shot_card is not None and shot_card not in state.players[host_seat].hand:
        displaced = state.players[host_seat].hand[0]
        state.players[host_seat].hand[0] = shot_card
        state.deck.append(displaced)
    state.current_player = host_seat
    state.cards_remaining = 1
    game.revision += 1

    return room_id, host_pid, host_token


def _adopt_session(page, base_url: str, room_id: str, token: str, player_id: str) -> None:
    page.goto(f"{base_url}/rooms/{room_id}")
    page.wait_for_load_state()
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, JSON.stringify(value))",
        [f"neu:{room_id}", {"token": token, "playerId": player_id, "name": "host"}],
    )
    page.reload()
    page.wait_for_load_state()
    page.wait_for_timeout(1500)
    page.evaluate("() => window.refresh && window.refresh()")
    page.wait_for_timeout(1000)


def _tap_shot_card(page) -> None:
    cards = page.locator("#hand button.card")
    for i in range(cards.count()):
        card_id = cards.nth(i).get_attribute("data-card-id")
        if card_id and card_id.startswith("shot"):
            cards.nth(i).click()
            return
    raise AssertionError("SHOT カードが手札に見つかりません")


def run_shot_immediate_scenario(browser, base_url: str) -> None:
    room_id, host_pid, host_token = _setup_shot_room(base_url)
    context = browser.new_context(viewport={"width": 390, "height": 844}, **MOBILE)
    try:
        page = context.new_page()
        _adopt_session(page, base_url, room_id, host_token, host_pid)
        check_render_stability(page, 5.0)
        _tap_shot_card(page)
        page.wait_for_timeout(300)
        targetable = page.locator("#players .player.targetable")
        assert targetable.count() > 0, "指名できる席が光っていません"
        # SHOT をタップした直後に、有効な席をタップする。
        targetable.first.click()
        page.wait_for_timeout(1500)
        state = api_request("GET", f"/api/rooms/{room_id}/state", token=host_token, base_url=base_url)["game"]
        assert state["currentPlayerId"] != host_pid, "指名が成功していません（手番が進んでいません）"
        print(f"[shot-immediate] OK: turn advanced to {state['currentPlayerId']}")
    finally:
        context.close()


def run_shot_touch_scenario(browser, base_url: str) -> None:
    room_id, host_pid, host_token = _setup_shot_room(base_url)
    context = browser.new_context(viewport={"width": 390, "height": 844}, **MOBILE)
    try:
        page = context.new_page()
        _adopt_session(page, base_url, room_id, host_token, host_pid)
        _tap_shot_card(page)
        page.wait_for_timeout(300)
        target = page.locator("#players .player.targetable").first
        box = target.bounding_box()
        assert box is not None
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        cdp = context.new_cdp_session(page)
        # タップの最中に再描画を割り込ませる。
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx, "y": cy}]})
        page.evaluate("() => window.refresh && window.refresh()")
        time.sleep(0.2)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(1500)
        state = api_request("GET", f"/api/rooms/{room_id}/state", token=host_token, base_url=base_url)["game"]
        assert state["currentPlayerId"] != host_pid, "タップ中に再描画が割り込んでも指名が成功するべきです"
        print(f"[shot-touch] OK: turn advanced to {state['currentPlayerId']}")
    finally:
        context.close()


def run_shot_cancel_scenario(browser, base_url: str) -> None:
    room_id, host_pid, host_token = _setup_shot_room(base_url)
    context = browser.new_context(viewport={"width": 390, "height": 844}, **MOBILE)
    try:
        page = context.new_page()
        _adopt_session(page, base_url, room_id, host_token, host_pid)
        _tap_shot_card(page)
        page.wait_for_timeout(300)
        assert page.locator("#players .player.targetable").count() > 0, "指名モードに入っていません"
        # 指名できない席（targetable でない席）をタップすると取り消される。
        non_targetable = page.locator("#players .player:not(.targetable)").first
        if non_targetable.count() > 0:
            non_targetable.click()
        else:
            # 空き部分をタップ。
            page.locator("#players").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(500)
        assert page.locator("#players .player.targetable").count() == 0, "指名モードが取り消されていません"
        print("[shot-cancel] OK: naming cancelled")
    finally:
        context.close()


def check_render_stability(page, duration: float = 5.0) -> None:
    page.evaluate(
        """() => {
            window.__mutCount = 0;
            const target = document.getElementById('players');
            const obs = new MutationObserver((muts) => {
                for (const m of muts) {
                    window.__mutCount += m.addedNodes.length + m.removedNodes.length;
                }
            });
            obs.observe(target, { childList: true, subtree: true });
            window.__obs = obs;
        }"""
    )
    time.sleep(duration)
    count = page.evaluate("() => window.__mutCount")
    page.evaluate("() => { if (window.__obs) window.__obs.disconnect(); }")
    assert count == 0, f"#players の子要素が {count} 回作り直されています"
    print(f"[render-stability] OK: {count} changes over {duration}s")


def run_series_toast_scenario(browser, viewport: tuple[int, int], tag: str) -> None:
    """連戦の開始選択・待機トースト・最終戦トースト（メダル・OK）の手動確認。"""
    context = new_context(browser, viewport)
    try:
        page = context.new_page()
        room_id, host_pid, host_token = api_create_room(f"ホスト{tag}")
        host = PlayerPage(context, f"ホスト{tag}")
        host.adopt_session(room_id, host_token, host_pid, host.name)
        wait_for_game(page)
        check_series_start_select(page)
        check_series_toast(page)
        check_series_final_toast(page)
        series_screenshot = take_screenshot(
            page, f"{tag}_series_toast_{viewport[0]}x{viewport[1]}"
        )
        print(f"[{tag}] 連戦トースト確認用スクリーンショット: {series_screenshot}")
    finally:
        context.close()


def main() -> None:
    if port_in_use(5001):
        raise SystemExit(
            "ポート5001が既に使われています。古いサーバーを停止してから実行してください。"
        )
    server = subprocess.Popen(
        [sys.executable, "app.py", "--port", "5001"],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                run_scenario(browser, players_count=4, viewport=(390, 844), tag="four")
                run_scenario(browser, players_count=2, viewport=(360, 640), tag="two")
                run_scenario(browser, players_count=7, viewport=(390, 844), tag="seven")
                run_cpu_scenario(browser, cpu_count=1, viewport=(360, 640), tag="cpu-two")
                run_cpu_scenario(browser, cpu_count=6, viewport=(360, 640), tag="cpu-seven")
                run_series_toast_scenario(browser, viewport=(390, 844), tag="series")
                run_series_toast_scenario(browser, viewport=(360, 640), tag="series-small")
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    # SHOT 指名シナリオ（in-process サーバー）
    shot_port = 5002
    _app, _thread = _start_inprocess_server(shot_port)
    shot_base = f"http://127.0.0.1:{shot_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                run_shot_immediate_scenario(browser, shot_base)
                run_shot_touch_scenario(browser, shot_base)
                run_shot_cancel_scenario(browser, shot_base)
            finally:
                browser.close()
    finally:
        pass


if __name__ == "__main__":
    main()
