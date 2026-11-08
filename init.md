# ノイ (Neu) 開発メモ

次回以降、別トピックからでも引き継げるように、ルール・設計・進捗をここに集約する。

---

## 1. プロジェクト概要

「ノイ (Neu)」は、**場の数字の合計が 101 を超えないように**カードを出し合うカードゲーム。
101 を超えてしまったプレイヤーがラウンドの負けとなり、チップを1枚失う。
チップが尽きて脱落し、最後に1人だけ残った人が勝者。

オンライン対戦版を新規リポジトリ `atsushi1975/neu` で開発する。

### フェーズ計画

| フェーズ | 内容 | 状態 |
| --- | --- | --- |
| 1 | ゲームエンジン + テスト + CLI（標準ライブラリのみ） | 完了 |
| 2 | Flask + スマホ向け Web UI（素の JS、複数人オンライン対戦） | 完了 |
| 3 | Docker 化・共通ロビー (game-lobby) 登録・VPS 展開 | 未着手 |
| 4 | CPU プレイヤーの強化 | 未着手 |

### 技術方針

- Python 3（エンジンは標準ライブラリのみ）
- フェーズ2で Flask を追加（`requirements.txt`）。フロントは素の HTML / CSS / JavaScript。
- ルール判定は画面や通信から切り離した**純粋なエンジン**にし、テストで固める
  （過去の別ゲームで修正がデグレした反省から）
- Web / API 層はルールを再実装せず、`legal_actions` と `apply` の結果だけを表示する
- エンジン内では `print`・`input`・乱数のグローバル利用を禁止。
  乱数は状態が持つ `random.Random` に限定し、`seed` で再現可能にする

---

## 2. ディレクトリ構成

```
neu/
  __init__.py       公開 API の再エクスポート
  engine.py         ゲームエンジン本体（UI・I/O 非依存）
  cli.py            動作確認用 CLI（人間1人 + ランダム Bot）
gameplatform/
  games.py          GameDefinition とゲーム登録
  rooms.py          ルーム管理・開始・CPU 駆動（共通基盤）
  realtime.py       SSE の購読・チケット
  sessions.py       プレイヤートークン
  errors.py         共通例外
games/neu/
  __init__.py       GameDefinition(key="neu", title="ノイ") の登録
  adapter.py        GameState と共通基盤をつなぐアダプタ
  cards.py         カード表示用メタ情報・一意な cardId
  view.py           プレイヤーごとの公開ビュー
web/
  __init__.py       Flask アプリ生成・static_folder 設定
  api.py            ルーム / 対戦 / SSE / health の JSON API
  templates/        index.html, room.html
static/
  room.js           ルーム画面の素の JavaScript
  style.css         スマホ縦画面優先のスタイル
  img/cards/        カード画像18枚
app.py              開発サーバー起動
health_app.py       ヘルスチェック単体起動
tests/
  helpers.py        テスト用の状態生成ヘルパ
  test_deck.py      デッキ構成
  test_numbers.py   数字カードと合計、101 超え
  test_double.py    DOUBLE の全パターン
  test_turn.py      PASS / TURN / SHOT / 手番進行
  test_round.py     ラウンド開始・終了、ディーラー、脱落、ゲーム終了
  test_random.py    ランダムプレイの終了性・保存則・再現性
  test_view.py      view_for / to_dict / from_dict
  test_neu_adapter.py アダプタ・公開 view・構造化ログ・ラウンド結果
  test_api.py       部屋作成→参加→開始→対戦→終了の API 結合
init.md             このファイル
README.md
LICENSE             All rights reserved
```

---

## 3. デッキ（全58枚）

| カードID | 種類 | 効果 | 枚数 |
| --- | --- | --- | --- |
| `n1`〜`n9` | 数字 | 合計に +1〜+9 | 各3枚（計27） |
| `n10` | 数字 | 合計に +10 | 6 |
| `n50` | 数字 | 合計に +50 | 2 |
| `nm1` | 数字 | 合計に -1 | 2 |
| `nm10` | 数字 | 合計に -10 | 4 |
| `n101` | 数字 | 合計を 101 にする | 5 |
| `pass` | 指示 | 合計そのまま手番終了 | 4 |
| `turn` | 指示 | 回る向きを反転 | 4 |
| `shot` | 指示 | 自分以外の生存者1人を指名し、次の手番にする | 2 |
| `double` | 指示 | 次の手番の人は2枚出す | 2 |

- 合計 58 枚。
- 同種カードの区別用に、カードは `copy`（1始まりの連番）を別途持つ。
- カードIDは画像ファイル名に対応させる（`n1.png` など）。

---

## 4. ルール（確定事項）

### プレイヤーと開始状態

- プレイヤーは 2〜7 人。
- 各自チップ3枚 + 手札3枚。
- 順番は座席順。初期の進行方向は時計回り（座席番号の増加方向 = `direction = +1`）。
- 初期ディーラーはランダムに1人（`neu.engine.INITIAL_DEALER = None`、状態の RNG から全員からシャッフルして選ぶ。
  整数を `new_game(dealer=...)` で指定すれば固定できる（テスト・CLI の再現用）。)

### 数字カードと合計 (sum)

- 数字カードは合計を変化させる。
  - `n1`〜`n9`, `n10`, `n50` は加算。
  - `nm1`, `nm10` は減算。
  - `n101` は「合計を 101 にする」（加算ではない）。
- 合計が 0 未満になることは許可する。
- 合計が **101 を超えたら**、出したプレイヤーがそのラウンドの負け（回避手段なし）。
- また、手番の人が「**次に出すカードがどれを出しても合計が 101 を超える**」状態に
  なったら、カードを出す前に、その人がそのラウンドの負けになる（**安全なカード無し負け**）。
  - 「安全なカード」= 合法手のうち、出しても合計が 101 を超えない手。
    PASS・TURN・SHOT・DOUBLE（合法なら）・`n101`・`-1`・`-10` は常に安全。
    `n101` は合計が 101 になるだけで安全（超えない）。
    DOUBLE 中（`cards_remaining >= 2`）の1枚目に出せない DOUBLE は手札に数えない
    （1枚目では出せないので安全カードに数えない）。
- 安全なカードが1枚も無ければ、カードを出さずにその人のラウンド負け（バースト時と同じ
  チップ減・脱落・ゲーム終了・次ラウンド開始の処理を行う）。負けが連鎖して起きることもある。

### 指示カード

- `PASS`: 合計そのまま手番終了。
- `TURN`: 回る向きを反転。
- `SHOT`: 自分以外の生存プレイヤー1人を指名し、次の手番をその人にする。
- `DOUBLE`: 次の手番の人は2枚出す。

### 補充と捨て札

- カードを1枚出すたびに、手札が3枚になるよう山札から自動補充する
  （リアルの「引き忘れ」は発生させない）。
- 山札が切れたら、捨て札を全部シャッフルして新しい山札にする。
- 出したカードは捨て札へ送る。
- ラウンド開始時に場に置いた初期場札は、そのラウンド中は場に残す。

### ラウンドの終了とチップ

- 合計が 101 を超えたプレイヤーがラウンドの負け（バースト）。
- 手番の人が出せるカードがすべて 101 を超える場合は、カードを出さずにラウンドの負け（安全なカード無し）。
- 負けた人はチップ1枚を場に置く（戻らない）。
- チップが 0 になった人は脱落。
- 1人だけ残ったらゲーム終了、その人が勝者。

### 新しいラウンド

- 全カード（手札・捨て札・山札・初期場札）を集めてシャッフルし、
  残りのプレイヤーに3枚ずつ配る。
- ディーラーは前ラウンドの負け。
- 負けた人が脱落した場合は、その人の次（**その時点の進行方向の隣**）の生存プレイヤー。
- 回る向きは新ラウンドで時計回り（`+1`）にリセット。
- 最初の手番は**ディーラー本人**（＝前ラウンドの負けた人。最初のラウンドでは最初のディーラー）。

### ラウンド開始時の初期場札

- 山札の上から、**数字カード（`n1`〜`n9`, `n10`, `n50`, `nm1`, `nm10`）が出るまで**めくる。
- `n101` と指示カード（`pass`, `turn`, `shot`, `double`）が出たら捨て札へ送ってやり直す。
- 出た数字カードで合計を初期化する（`nm1`・`nm10` なら合計は -1・-10 からのマイナススタート）。
- 最初の手番は**ディーラー本人**（＝前ラウンドの負けた人。最初のラウンドでは最初のディーラー）。

---

## 5. DOUBLE の処理（重要・1枚ずつ処理する）

エンジンでは「**今手番で出す必要のある残り枚数**」を `cards_remaining`（1 or 2）として持つ。
1枚出して効果を解決し、残っていれば同じ人が続けて出す。

- DOUBLE の効果は、**次の手番の人の必要枚数を 2 にするだけ**。
- 数字カード（`n101` を含む）を出した場合: 残り枚数を1減らす。
  - 0 になったら手番終了。
  - まだ残っていれば同じ人が続けて出す。
- 必要枚数2の人が、**1枚目に PASS・TURN・SHOT** を出した場合:
  - その時点で手番終了。
  - 2枚出す義務が次の手番の人にそのまま移る（`cards_remaining` は 2 のまま）。
  - SHOT なら指名された人、TURN なら反転後の隣の人へ。
- 必要枚数2の人が、**1枚目に DOUBLE を出すことは禁止**（合法手に含めない）。
- 必要枚数2の人が、**2枚目に DOUBLE を出すのは OK**。
  通常の DOUBLE 同様、次の人の必要枚数が 2 になる。
- 必要枚数2の人が、1枚目に数字カードを出し、**2枚目に PASS・TURN・SHOT** を出した場合:
  - 通常の指示カードと同じ（次の人の必要枚数は 1）。
- 手札3枚で DOUBLE は全体で2枚のため、1枚目に出せるカードが必ず残る（詰みは発生しない）。

### 実装上のかみ合わせ

指示カード（PASS/TURN/SHOT）で手番が終わるとき、
次の人の必要枚数は **その時点の `cards_remaining`** とする。

- 必要枚数2の1枚目で指示カード → 次の人も 2（義務が移る）。
- 必要枚数1で指示カード → 次の人も 1。
- 必要枚数2で数字を1枚出した後の指示カード → その時点で残り1なので次の人も 1。

DOUBLE は常に手番を終了させ、次の人の必要枚数を 2 にする。

---

## 6. 仮置きの決め事（定数・フラグで後から変えやすくする）

- SHOT の指名対象は自分以外の生存プレイヤー（座席順に関係なく選べる）。
- 初期ディーラーはランダムに1人（`neu.engine.INITIAL_DEALER = None`、未指定なら
  状態の RNG（seed 再現用）で全員からシャッフルして選ぶ。`new_game(dealer=...)` で固定も可能）。
- チップ初期枚数 3（`STARTING_CHIPS`）、手札枚数 3（`HAND_SIZE`）、
  101 の上限（`LOSING_LIMIT = 101`）。
- これらの値は `neu/engine.py` 冒頭の定数に集約している。

---

## 7. エンジンの設計

### 方針

- 状態は明示的な状態オブジェクト（`GameState` / `PlayerState` / `Card`）。
- 「合法手の列挙 (`legal_actions`)」+「手の適用 (`apply`)」の純粋関数中心。
- `apply` は状態を破壊せず、**新しい `GameState` を返す**（内部で deepcopy）。
- 乱数は状態が持つ `random.Random` のみ。`new_game(..., seed=...)` で再現可能。
- 公開情報と非公開情報を分ける `view_for(player)` を用意。
  他人の手札は枚数のみ、山札・捨て札は枚数のみ（捨て札の一番上のみ公開）。
- `to_dict` / `from_dict` で JSON に変換できる（乱数状態も含めて完全復元）。

### 状態

```python
Card(id, kind, value, copy)
  kind: "number" | "hundred" | "pass" | "turn" | "shot" | "double"

PlayerState(seat, chips, hand, alive)

GameState(
    players, deck, discard, field_card,
    total,          # 場の合計（API では "sum"）
    direction,      # +1=時計回り（席番号増加）, -1=反時計回り
    dealer, current_player, cards_remaining,
    phase,          # "playing" | "game_over"
    winner, round_number, events, round_results, rng,
)
```

- `round_results: list[dict]`: ラウンド結果の構造化リスト。ラウンド負け（バースト・安全なカード無し）が起きるたび
  `_lose_round` の中で1件追加する。内容: `round`(ラウンド番号), `loser`(席番号), `reason`（"bust" | "no_safe_card"）,
  `card_id`(bust のときのみ。出したカードの id), `sum_after`(bust のときのみ。出した後の合計),
  `chips_left`, `eliminated`, `game_over`, `winner`, `hand`(no_safe_card のときのみ。負けた人の手札を
  `card_to_dict` と同じ形で。bust のとき None)。

### 公開関数・クラス一覧（`neu.engine`）

| 名前 | 説明 |
| --- | --- |
| `build_deck()` | 58枚のデッキを生成して返す |
| `new_game(num_players, seed=None, dealer=None)` | ゲームを初期化して `GameState` を返す（2〜7人）。`dealer` 未指定なら `INITIAL_DEALER`（None = ランダム）に従う |
| `start_round(state)` | ラウンドを開始する（カード回収・配布・初期場札・手番決定） |
| `legal_actions(state)` | 現在の手番の合法手 `list[Action]` を返す。終了時は空 |
| `safe_actions(state)` | 現在の手番の「出したとしても合計が 101 を超えない」安全な合法手を返す |
| `apply(state, action)` | 手を適用した**新しい** `GameState` を返す。不正手は例外 |
| `view_for(state, seat)` | そのプレイヤー視点の公開情報 dict を返す |
| `to_dict(state)` / `from_dict(data)` | JSON 互換 dict への変換・復元 |
| `is_game_over(state)` / `winner(state)` | 終了判定・勝者 |
| `next_alive(state, seat, direction)` | 指定方向で次の生存プレイヤーの席番号 |
| `card_to_dict(card)` / `card_from_dict(data)` | カードの変換 |

### 安全なカードの判定

- 「合計が 101 を超えない」計算は `_projected_total(state, card)` に集約し、
  バースト判定（`apply`）と安全な手の判定（`safe_actions`）の両方で使う（重複させない）。
- 手番が決まった直後（ラウンド開始時・手番が移ったとき・DOUBLE 中の2枚目を出す前）に
  `_check_no_safe_card` で安全な手が無いかを確認し、無ければ `reason="no_safe_card"` で負けとする。

### アクション

```python
Action(player, card_index, target=None)
```

- `player`: 手番の席番号（`current_player` と一致しないと `NotYourTurn`）。
- `card_index`: そのプレイヤーの手札リスト内のインデックス。
- `target`: SHOT の指名先の席番号。SHOT 以外では `None`。

### 例外

| 例外 | 意味 |
| --- | --- |
| `NeuError` | 基底 |
| `InvalidSetup` | 人数・席番号などのセットアップ不正 |
| `IllegalAction` | 不正な手の基底 |
| `NotYourTurn` | 手番でない人の操作 |
| `CardNotInHand` | 手札にないカード |
| `DoubleNotAllowed` | DOUBLE 中の1枚目に DOUBLE を出した |
| `InvalidTarget` | SHOT の対象が自分・死亡者・範囲外 |

### テスト実行

```bash
python3 -m unittest discover
```

現在 121 テスト（エンジン・アダプタ・API 結合）。

### CLI

```bash
python3 -m neu.cli --players 4 --seed 1
python3 -m neu.cli --players 4 --seed 1 --dealer 2   # 最初のディーラーを固定する場合
```

- 座席0が人間、他は合法手からランダムに選ぶ簡易 Bot。
- 場の合計・手札・各人のチップ・直前の出来事を表示。
- DOUBLE 中は残り枚数、101 を超える手は警告を表示する。
- 最初の手番はディーラー本人（`--dealer` で指定可能、未指定ならランダム）。

---

## 8. フェーズ2: Web UI / API

### 起動

```bash
python3 -m pip install -r requirements.txt
python3 app.py --port 5000
```

ブラウザで `http://localhost:5000/` を開く。カード画像は `static/img/cards/` から配信する。

### 構成

- `hannin-wa-odoru` と同じ共通基盤（`gameplatform/` + `games/<key>/`）を使う。
- `games/neu/__init__.py` で `GameDefinition(key="neu", title="ノイ", min_players=2, max_players=7)` を登録。
- `games/neu/adapter.py` の `NeuGame` が `GameState` を保持し、`revision` / `phase` / `start()` を提供。
- ルール判定はすべて `neu/engine.py` に委譲。API / UI 側で合法手やバースト判定を再実装しない。
- カードの物理的な一意性は `cardId = "<カードID>#<copy>"`（例: `n10#3`）。画像名・ログには `Card.id` を使う。
- 公開 view は他人の手札の中身を一切含まず、`me.hand` だけが中身を持つ。
- `events` は UI 用の構造化ログ（`seq` / `kind` / `playerId` / `cardId` / `sumAfter` / `targetId` など）。
- `lastRound` はラウンド遷移を前後比較して生成し、敗者・合計・残りチップ・脱落・ゲーム終了を返す。

### 主な API

| メソッド | パス | 内容 |
| --- | --- | --- |
| `GET` | `/health`, `/api/health` | タイトル・バージョン・状態・進行中ルーム数 |
| `GET` | `/api/games` | 登録ゲーム一覧 |
| `GET` | `/api/rooms` | ルーム一覧 |
| `POST` | `/api/rooms` | ルーム作成 |
| `POST` | `/api/rooms/<id>/join` | 参加 |
| `POST` | `/api/rooms/<id>/leave` | 退出（開始前のみ） |
| `POST` | `/api/rooms/<id>/host` | ホスト引き継ぎ（開始前のみ） |
| `GET` | `/api/rooms/<id>/state` | 自分の視点の状態 |
| `POST` | `/api/rooms/<id>/start` | ゲーム開始（ホストのみ） |
| `POST` | `/api/rooms/<id>/actions` | カードを出す（`type: "play"`, `cardId`, `expectedRevision`, SHOT は `target`） |
| `POST` | `/api/rooms/<id>/stream-ticket` | SSE 用の短命チケット |
| `GET` | `/api/rooms/<id>/events` | SSE（変更通知） |

- `lastRound` は構造化されたラウンド結果（`GameState.round_results` の最新件）から作る。
  既存キー（`round`, `loserId`, `loserName`, `cardId`, `card`, `sumAfter`, `sum`, `chipsLeft`, `chips`,
  `eliminated`, `alive`, `gameOver`, `winnerId`, `winnerName`, `nextRound`, `message`）に加え、
  `reason`（"bust" | "no_safe_card"）と `revealedHand`（no_safe_card のときのみ、負けた人の手札）を持つ。
  イベントログの `round_end` も構造化結果から作る（バースト・安全なカード無しを区別する）。

認証は `X-Player-Token`（または `Authorization: Bearer`）。トークンは localStorage の
`neu:<roomId>` に保存する。

### UI（スマホ縦画面優先）

- 場の合計を特大表示。ラウンド・向き・残り枚数・山札/捨て札・場札をまとめて表示。
- 手札は3枚を横並び。カードをタップして選択し、「出す」で確定する2タップ操作。
- 各カードに「出すと合計 XX」を表示。101 を超える場合は赤で警告し、確認ボタンも警告文に変える。
- SHOT は指名先を選ぶまで確定できない。
- DOUBLE 中の1枚目はサーバーが合法手から除外し、UI でも「1枚目に出せません」と表示する。
- 手番が回ってきたらプロンプト・ページタイトル・（対応端末では）振動で通知。
- ラウンド敗者が出たらトーストで「バースト／合計／残りチップ／脱落」を演出。
  - バースト: 「◯◯さんが△△でバースト（合計▽▽）」。
  - 安全なカード無し: 「◯◯さんは出せるカードがすべて101を超えるため負け」。
    このとき `revealedHand`（負けた人の手札）を UI が使えるように見せる。
- 他人の手札は枚数のみ。裏面は CSS で描画する（`back.png` は未提供）。
- 画像が404でも CSS の仮カードで遊べる（`onerror` で画像を隠す）。
- ゲーム終了時は勝者とチップ順位をオーバーレイ表示。

### 未対応（フェーズ3以降）

- Docker 化、共通ロビー登録、VPS 展開。
- CPU 対戦。現在 `cpu_driver=None` のため、`cpuCount > 0` での開始は API が拒否する。
- `back.png` / `chip.png` は未提供。裏面・チップは CSS で代替している。

---

## 9. 現在の進捗

- [x] リポジトリ初期化（README / LICENSE / init.md / ディレクトリ）
- [x] エンジン本体（デッキ・状態・合法手・適用・ラウンド進行・view・JSON 変換）
- [x] テスト一式（デッキ / 数字 / DOUBLE / 手番 / ラウンド / ランダム / view）
- [x] CLI（人間1人 + ランダム Bot）
- [x] フェーズ2: 共通基盤 + Flask API + スマホ向け Web UI
  - [x] `gameplatform/` のコピーと `/health`
  - [x] `NeuGame` アダプタ・公開 view・構造化ログ・lastRound
  - [x] 部屋作成→参加→開始→ランダム対戦→終了の API 結合テスト
  - [x] スマホ縦画面の対戦 UI（2タップ操作・SHOT 指名・警告・結果表示）
  - [x] 全 121 テスト通過
- [x] ルール修正（テストプレイで見つかった点）
  - [x] 出せるカードがすべて 101 超えなら、カードを出さずにラウンド負け（`no_safe_card`）
  - [x] 次のラウンドは負けた人（ディーラー）から開始（最初の手番 = ディーラー本人）
  - [x] 最初のディーラーはランダム（`INITIAL_DEALER = None` / `new_game(dealer=...)`）
- [ ] フェーズ3: Docker 化 / 共通ロビー登録 / VPS 展開
- [ ] フェーズ4: CPU 強化

---

## 10. 将来のメモ

### カード画像

`static/img/cards/` に、カードIDと同名の PNG を置く（18枚追加済み）。

- 数字: `n1.png` 〜 `n9.png`, `n10.png`, `n50.png`, `n101.png`, `nm1.png`, `nm10.png`
- 指示: `pass.png`, `turn.png`, `shot.png`, `double.png`
- 未提供: `back.png`（裏面）, `chip.png`（チップ）

裏面とチップは CSS で描画している。画像がないカードも CSS の仮カードで表示できる。

### フェーズ3への引き継ぎ

- Docker 化する場合は `app.py` を gunicorn から起動する（`requirements.txt` に gunicorn あり）。
- 共通ロビー (game-lobby) へは `GET /health` の
  `title` / `version` / `status` / `playing_rooms` をそのまま登録できる。
- `VERSION` はビルド時に生成する想定で `.gitignore` 済み。無い場合 `/health` の version は `"dev"`。
- `SECRET_KEY` は環境変数で設定する。未設定時は起動ごとのランダム値になる。
- 共通ロビーへ戻るリンクは、同じホストのルート（既定ポート80）を指す。
- ルームはメモリ上に保持する（`gameplatform.rooms`）。再起動で消えるため、永続化はフェーズ3以降の課題。

### フェーズ4（CPU）への引き継ぎ

- `games/neu/__init__.py` の `cpu_driver=None` を、`games/neu/cpu.py` の実装に差し替える。
- 共通基盤側は `cpu_driver(game, cpu_ids, config)` が `step(game) -> bool` を持つオブジェクトを期待する
  （1手進めたら `True`、打てない/終了なら `False`）。
- CPU も必ず `legal_actions` / `apply` を通す。UI や API にルールを足さない。
- 開始 UI は現在 CPU 人数を出していない。`cpu_driver` を登録したら
  `startOptions` 相当の人数選択を戻す。
