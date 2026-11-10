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


def api_start(room_id: str, token: str) -> None:
    api_request("POST", f"/api/rooms/{room_id}/start", {}, token=token)


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
