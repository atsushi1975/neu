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
- ゲーム開始時は、全カードをシャッフルして生存者に3枚ずつ配る**だけ**。
  山札から場札をめくらない。場札は空・合計は 0 から始まる。
  最初のディーラー（ランダム）が最初の手番で、手札から出して始める。
- 順番は座席順。初期の進行方向は時計回り（座席番号の増加方向 = `direction = +1`）。
- 初期ディーラーはランダムに1人（`neu.engine.INITIAL_DEALER = None`、状態の RNG から全員からシャッフルして選ぶ。
  整数を `new_game(dealer=...)` で指定すれば固定できる（テスト・CLI の再現用）。)

### 数字カードと合計 (sum)

- 数字カードは合計を変化させる。
  - `n1`〜`n9`, `n10`, `n50` は加算。
  - `nm1`, `nm10` は減算。
  - `n101` は「合計を 101 にする」（加算ではない）。
- 合計が 0 未満になることは許可する。
- 合計が **101 を超える数字カードは出せない**（`legal_actions` から除外し、`apply` では
  `IllegalAction` にする。カードを出した結果 101 超えになることは設計上ない）。
- 手番の人が「**次に出すカードがどれを出しても合計が 101 を超える**」状態に
  なったら、カードを出す前に、その人がそのラウンドの負けになる（**出せないカード負け**）。
  - 「安全なカード」= 合法手のうち、出しても合計が 101 を超えない手。
    PASS・TURN・SHOT・DOUBLE（合法なら）・`n101`・`-1`・`-10` は常に安全。
    `n101` は合計が 101 になるだけで安全（超えない）。
    DOUBLE 中（`cards_remaining >= 2`）の1枚目に出せない DOUBLE は手札に数えない
    （1枚目では出せないので安全カードに数えない）。
- 安全なカードが1枚も無ければ、カードを出さずにその人のラウンド負け
  （チップ減・脱落・ゲーム終了・次ラウンド開始の処理を行う）。負けが連鎖して起きることもある。

### 指示カード

- `PASS`: 合計そのまま手番終了。
- `TURN`: 回る向きを反転。
- `SHOT`: 自分以外の生存プレイヤー1人を指名し、次の手番をその人にする。
- `DOUBLE`: 次の手番の人は2枚出す。

### 補充と場札

- カードを1枚出すたびに、手札が3枚になるよう山札から自動補充する
  （リアルの「引き忘れ」は発生させない）。
- 山札が切れたら、場札をシャッフルして新しい山札にする（定数 `KEEP_TOP_ON_RESHUFFLE = True`。
  True なら場札の一番上（直前に出したカード）を残し、それ以外をシャッフルする。
  False なら場札の全部をシャッフルする。場札が1枚だけのときは True では残せるのがその1枚
  だけなので山札は空のままになり、引けない）。
- 出したカードはすべて場札の山（`table`、末尾が一番上）の一番上に積む。

### ラウンドの終了とチップ

- 手番の人が出せるカードがすべて 101 を超える場合は、カードを出さずにそのラウンドの負け
  （reason は `"no_playable_card"`）。
- 負けた人はチップ1枚を失う（戻さない）。
- チップが 0 になった人は脱落。
- 1人だけ残ったらゲーム終了、その人が勝者。

### 新しいラウンド

- **カードの回収・シャッフル・再配布・山札からのめくりはしない**。
  他のプレイヤーの手札・山札・場札はそのまま引き継ぐ（場札は積み上がったまま）。
- 変わるのは「**合計を 0 に戻すこと**」と「最初の手番が負けた人になること」だけ。
- チップを失った人（＝ディーラー）が次のラウンドの最初の手番を持つ。
- 負けた人が脱落した場合は、その人の次（**その時点の進行方向の隣**）の生存プレイヤーがディーラーで、その人が最初の手番。
- 脱落したプレイヤーの手札は、場札の一番下に入れる（仮置き。カードの総数を保存するため。一番上のカードは変えない）。
- 回る向きはリセットせず、そのまま引き継ぐ（TURN で逆回りになっていれば、逆回りのまま新ラウンドが始まる）。

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
- 最初の手番はディーラー本人（`neu.engine.FIRST_TURN_IS_DEALER = True`。False で変えられる）。
- 場札を山札に戻すとき（山札が尽きたとき）の「一番上を残す」は
  `neu.engine.KEEP_TOP_ON_RESHUFFLE = True`（False なら場札の全部をシャッフルする。仮置き）。
- これらの値は `neu/engine.py` 冒頭の定数に集約している。

---

## 7. エンジンの設計

### 方針

- 状態は明示的な状態オブジェクト（`GameState` / `PlayerState` / `Card`）。
- 「合法手の列挙 (`legal_actions`)」+「手の適用 (`apply`)」の純粋関数中心。
- `apply` は状態を破壊せず、**新しい `GameState` を返す**（内部で deepcopy）。
- 乱数は状態が持つ `random.Random` のみ。`new_game(..., seed=...)` で再現可能。
- 公開情報と非公開情報を分ける `view_for(player)` を用意。
  他人の手札は枚数のみ、山札・場札は枚数のみ（場札の一番上のみ公開）。
- `to_dict` / `from_dict` で JSON に変換できる（乱数状態も含めて完全復元）。

### 状態

```python
Card(id, kind, value, copy)
  kind: "number" | "hundred" | "pass" | "turn" | "shot" | "double"

PlayerState(seat, chips, hand, alive)

GameState(
    players, deck, table,
    total,          # 場の合計（API では "sum"）
    direction,      # +1=時計回り（席番号増加）, -1=反時計回り
    dealer, current_player, cards_remaining,
    phase,          # "playing" | "game_over"
    winner, round_number, events, round_results, rng,
)
```

- `round_results: list[dict]`: ラウンド結果の構造化リスト。ラウンド負け（`no_playable_card`）が起きるたび
  `_lose_round` の中で1件追加する。内容: `round`(ラウンド番号), `loser`(席番号), `reason`（`"no_playable_card"`）,
  `card_id`（負けても出したカードはないので常に None）, `sum_after`（常に None）,
  `sum`（負けた時点の合計。リセット前）, `chips_left`, `eliminated`, `game_over`, `winner`。
  負けた人の手札は含めない（`from_dict` は旧データの `hand` キーを無視して読む）。

### 公開関数・クラス一覧（`neu.engine`）

| 名前 | 説明 |
| --- | --- |
| `build_deck()` | 58枚のデッキを生成して返す |
| `new_game(num_players, seed=None, dealer=None)` | ゲームを初期化して `GameState` を返す（2〜7人）。`dealer` 未指定なら `INITIAL_DEALER`（None = ランダム）に従う |
| `start_round(state)` | 次のラウンドを開始する（カード回収・配布・山札からのめくりはしない。合計を0に戻し、向きを引き継ぎ、手番を決めるだけ） |
| `legal_actions(state)` | 現在の手番の合法手 `list[Action]` を返す。終了時は空 |
| `safe_actions(state)` | 現在の手番の「出したとしても合計が 101 を超えない」安全な合法手を返す |
| `apply(state, action)` | 手を適用した**新しい** `GameState` を返す。不正手は例外 |
| `view_for(state, seat)` | そのプレイヤー視点の公開情報 dict を返す |
| `to_dict(state)` / `from_dict(data)` | JSON 互換 dict への変換・復元 |
| `is_game_over(state)` / `winner(state)` | 終了判定・勝者 |
| `next_alive(state, seat, direction)` | 指定方向で次の生存プレイヤーの席番号 |
| `projected_total(state, card)` | カードを出した後の合計を計算する（合法手・安全な手の判定で共用） |
| `card_to_dict(card)` / `card_from_dict(data)` | カードの変換 |

### 安全なカードの判定

- 「合計が 101 を超えない」計算は `projected_total(state, card)` に集約し、
  合法手（`legal_actions`）と安全な手（`safe_actions`）の両方で使う（重複させない）。
- 手番が決まった直後（ラウンド開始時・手番が移ったとき・DOUBLE 中の2枚目を出す前）に
  `_check_no_playable_card` で「出せる合法手」が無いかを確認し、無ければ `reason="no_playable_card"` で負けとする。

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

現在 130 テスト（エンジン・アダプタ・API 結合）。

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
python3 app.py --port 5001
```

ブラウザで `http://localhost:5001/` を開く。カード画像は `static/img/cards/` から配信する。

### 構成

- `hannin-wa-odoru` と同じ共通基盤（`gameplatform/` + `games/<key>/`）を使う。
- `games/neu/__init__.py` で `GameDefinition(key="neu", title="ノイ", min_players=2, max_players=7)` を登録。
- `games/neu/adapter.py` の `NeuGame` が `GameState` を保持し、`revision` / `phase` / `start()` を提供。
- ルール判定はすべて `neu/engine.py` に委譲。API / UI 側で合法手や101超え判定を再実装しない。
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
  `reason`（`"no_playable_card"`）を持つ。負けた人の手札（旧 `revealedHand`）は返さない。
  イベントログの `round_end` も構造化結果から作る。

認証は `X-Player-Token`（または `Authorization: Bearer`）。トークンは localStorage の
`neu:<roomId>` に保存する。

### UI（スマホ縦画面優先）

- 画面に出すもの（対戦中）は次のとおり。それ以外の要素は足さない。
  - ヘッダ: 「ノイ」・ルームID・コピー・退出・ラウンド番号（ラウンド番号はここ1か所だけ）。
  - 楕円の卓: 各席は名前（長いときは省略）・チップ数・親の印・手番ハイライト・DOUBLE 中の手番の人だけ「あとN枚」。
    自分は下中央、他は席番号の増加＝画面上の時計回りに等間隔。脱落者は席を残して暗くする。
  - 回る向きは「↻ 時計回り」「↺ 反時計回り」の文字1か所だけ（ステータス帯）。矢印の図形・回転アニメーションは作らない。
  - 場の合計は卓の中央に1か所だけ（`gameState.sum` をそのまま常時更新）。
  - 場札は一番上の1枚だけを傾けず重ねずに表示し、下に「場札 N枚」。空のときは「なし」。
  - 山札は裏面1枚と「山札 N枚」。
  - 手番の案内は1行。
  - 自分の手札はカード画像。出せるカードは「出すと合計 XX」（`afterSum`）、101 超え（`busts`）は赤い「超過」バッジ。
  - ログは折りたたみ（既定は閉じる）。
  - ラウンド結果トーストは `lastRound.message` とチップ残り・脱落・次の最初の手番を1行で（手札は出さない）。
  - ゲーム終了時は勝者とチップ順位をオーバーレイ表示。
- 対戦中は `body.in-game` で `100dvh` の縦 flex にし、390×844 と 360×640 で縦スクロールなしに収める。
- 席は `static/table-layout.js` の `seatPosition(seat, mySeat, seatCount, {radiusX, radiusY, centerX, centerY})` で配置する。
  席の実寸を測って半径を決めるので、席が卓の枠からはみ出さない。`ResizeObserver` でサイズ変化時に再配置する。
- 手札は3枚を横並び。自分の手番で `playable` なカードをタップするとその場で出す（即出し）。アクションパネル（「出す」ボタン）は廃止した。
- SHOT は、手札の SHOT をタップしてから、円形卓上で `targets` に該当する席をタップして出す。
- DOUBLE 中の1枚目はサーバーが合法手から除外し、UI でも「1枚目に出せません」と表示する。
- 手番が回ってきたらプロンプト・ページタイトル・（対応端末では）振動で通知。
- ラウンド敗者はサーバーの `lastRound.message` をそのままトースト表示（負けた人の手札は表示しない）。
- 他人の手札の中身・枚数は表示しない。山札の裏面は CSS で描画する（`back.png` は未提供）。
- 画像が404でも CSS の仮カードで遊べる（`onerror` で画像を隠す）。

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
  - [x] スマホ縦画面の対戦 UI（即出し・SHOT 指名・警告・結果表示）
  - [x] 全 123 テスト通過
- [x] テストプレイ後 UI 改善（2026-11）
  - [x] 即出し（2タップ確認・「出す」ボタン・アクションパネル削除）
  - [x] 円形卓（`static/table-layout.js` の座標計算・回る向きの矢印・場札の山表示）
  - [x] SHOT 指名の卓タップ化
  - [x] ラウンド結果ダイアログ（サーバー `lastRound.message` をそのまま表示・手札は表示しない）
  - [x] 標準ポート 5001 / ヘルスチェック 5002 / カード表示サイズ調整
  - [x] node で円形卓の座標計算テスト追加（`static/table-layout.test.js`）
- [x] 対戦画面の修復・整理（2026-11）
  - [x] 席が全員重なる不具合を修正（`seatPosition` の呼び出し引数・実寸から半径を計算・`ResizeObserver` で再配置）
  - [x] 山札の枚数が消える不具合を修正（描画関数が枚数の要素を壊さないように）
  - [x] 場の合計を卓の中央1か所に統一・ラウンド番号をヘッダ1か所に統一・向きはステータス帯の文字1か所
  - [x] 余計な要素の削除（他人の手札枚数・ミニ裏面列・自分の手札枚数・リング矢印・上の合計枠・捨て札表示）
  - [x] 場札を一番上の1枚だけ表示（傾けず重ねない）
  - [x] 390×844 / 360×640 で縦スクロールなしに収める
  - [x] サーバーから負けた人の手札公開（`revealedHand` / `round_results[].hand`）を削除
  - [x] Playwright の手動検証スクリプト `tests/ui_check.py`（unittest 対象外）
- [x] ルール修正（テストプレイで見つかった点）
  - [x] 出せるカードがすべて 101 超えなら、カードを出さずにラウンド負け（`no_playable_card`）
  - [x] 次のラウンドは負けた人（ディーラー）から開始（最初の手番 = ディーラー本人）
  - [x] 最初のディーラーはランダム（`INITIAL_DEALER = None` / `new_game(dealer=...)`）
  - [x] 場札は「場札（field_card）＋ 捨て札（discard）」から場札の山1つ（`table`）に統合。
    山札が尽きたときだけ場札をシャッフル（`KEEP_TOP_ON_RESHUFFLE = True`、仮置き）。
  - [x] ラウンド終了後はカードを回収・シャッフル・再配布せず、継続処理で次のラウンドへ。
- [x] ルール修正: 山札からの場札めくりを廃止（2026-11、旧ルールは指示ミスだったため撤回）
  - [x] ゲーム開始は全カードをシャッフルして生存者に3枚ずつ配るだけ（場札は空・合計0）
  - [x] ラウンド負け後は合計だけ0に戻す。山札・場札・手札・向きはそのまま引き継ぐ
  - [x] 「初期場札をめくる」「n101・指示カードのやり直し」「nm1・nm10 でマイナススタート」を削除
  - [x] `_flip_initial_field_card`・`GAME_START_FLIPS_INITIAL_CARD` を削除。
    `_draw` が場札0枚・1枚でも安全に動くように修正
- [x] SHOT 指名のタップ不具合修正（2026-11）
  - 原因: enterShotNaming() が renderPrompt() だけ呼び、席の targetable/dimmed が次回再描画まで付かず、
    その間に席をタップすると cancelShotNaming() が走って指名モードが解除されていた
    （5秒の自動更新まで最大5秒の間だけ失敗するように見えた）。
  - 修正1: enterShotNaming()/cancelShotNaming() で renderPrompt() に加え renderPlayers()/renderHand() を即時実行
  - 修正2: 席クリックは DOM クラスではなく shotCard().targets に playerId が含まれるかで判定
  - 修正3: revision と UI 状態（指名モード・選んだカード・画面サイズ）が同じなら renderGame を省略。
    押している最中 (pointerdown/touchstart〜up) は再描画を遅延
  - 修正4: .player.targetable に cursor: pointer / touch-action: manipulation を追加。
    「やめる」ボタンの表示ラッパーも指名モードに合わせて表示/非表示
  - 検証: tests/ui_check.py に SHOT 即時タップ (a)・タップ中再描画 (b)・非指名席タップ (c)・
    MutationObserver による再描画0回確認を追加
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
