"""Flask アプリのファクトリ。

ゲームの登録は ``games`` パッケージの import で行う。
"""

from __future__ import annotations

import logging
import os
import secrets

from flask import Flask, jsonify, render_template

import games  # noqa: F401  （ゲーム登録のための副作用 import）
from .api import api, health_payload

logger = logging.getLogger("neu.web")

# カード画像・CSS・JS はリポジトリ直下の static/ に置く。
STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    logger.warning(
        "SECRET_KEY が未設定です。起動ごとのランダムな値を使います。"
        "公開時は環境変数 SECRET_KEY を設定してください。"
    )
    return secrets.token_hex(32)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config.from_mapping(SECRET_KEY=_secret_key())
    if test_config:
        app.config.update(test_config)
    app.register_blueprint(api)

    @app.get("/health")
    def health():
        """共通ヘルスチェック（/api/health と同じ内容を返す）。"""
        return jsonify(health_payload())

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/rooms/<room_id>")
    def room_page(room_id: str):
        return render_template("room.html", room_id=room_id)

    return app
