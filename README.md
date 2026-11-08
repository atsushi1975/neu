# ノイ (Neu)

場の数字の合計が **101 を超えないように** カードを出し合うカードゲーム
「ノイ (Neu)」のオンライン対戦実装です。

このリポジトリはフェーズ1として、**ゲームエンジン + テスト + 動作確認用 CLI** のみを
提供します。外部依存はなく、Python 3 の標準ライブラリだけで動きます。

## 必要なもの

- Python 3.10 以上（標準ライブラリのみ。pip install は不要）

## 遊んでみる（CLI）

```bash
python -m neu.cli --players 4 --seed 1
```

- 座席 0 が人間、それ以外は合法手からランダムに選ぶ簡易 Bot です。
- `--players` は 2〜7、`--seed` を固定すると山札の並びが再現されます。
- カード番号を入力して出します。SHOT のときは `番号 対象席` のように入力します。

## テスト

```bash
python -m unittest discover
```

## ディレクトリ構成

```
neu/
  engine.py    ゲームエンジン（純粋ロジック。print・入力・グローバル乱数なし）
  cli.py       動作確認用 CLI
tests/         unittest によるテスト
static/img/cards/   将来カード画像を置く場所（今は空）
init.md       開発メモ（ルール・設計・進捗の引き継ぎ用）
```

## フェーズ計画

1. ゲームエンジン + テスト + CLI（このリポジトリの現在地）
2. Flask + スマホ向け Web UI
3. `/health`・Docker・共通ロビー登録・VPS 展開
4. CPU プレイヤーの強化

詳細なルール・設計・進捗は [init.md](init.md) を参照してください。

## ライセンス

All rights reserved.（著作権者名は TODO）
