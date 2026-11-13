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
| 3 | Docker 化・共通ロビー (game-lobby) 登録・VPS 展開 | 完了 |
| 4 | CPU プレイヤーの追加 | 完了 |

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
  series.py         連戦の純粋ロジック（得点・積算順位・次戦ディーラー）
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
  cpu.py            CPU プレイヤー（カード選択・SHOT 指名・step）
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
  test_neu_cpu.py   CPU のカード選択・SHOT 指名・対戦の終了性
  test_cpu_names.py CPU の表示名（候補から選択・重複回避・フォールバック）と isCpu
  test_cpu_delay.py CPU の待ち時間（人間全員チップ0で0）の単体・結合テスト
  test_series.py    連戦の得点・競技順位・順位変動・次戦ディーラー
  test_series_server.py 連戦のサーバー側開始・待機・確認・最終結果テスト
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

現在 207 テスト（エンジン・アダプタ・CPU・CPU の名前・待ち時間・API 結合）。

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
  CPU の表示名の候補 `CPU_NAMES` を `cpu_names` に渡す（共通基盤の任意項目。指定しないゲームは従来どおり `CPU1`…）。
- `games/neu/adapter.py` の `NeuGame` が `GameState` を保持し、`revision` / `phase` / `start()` を提供。
- ルール判定はすべて `neu/engine.py` に委譲。API / UI 側で合法手や101超え判定を再実装しない。
- カードの物理的な一意性は `cardId = "<カードID>#<copy>"`（例: `n10#3`）。画像名・ログには `Card.id` を使う。
- 公開 view は他人の手札の中身を一切含まず、`me.hand` だけが中身を持つ。
  `players` の各要素には `isCpu`（真偽値）があり、id が `"cpu"` で始まるかで判定する（名前は見ない）。
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
| `POST` | `/api/rooms/<id>/start` | ゲーム開始（ホストのみ、`cpuCount` でCPU人数を指定） |
| `POST` | `/api/rooms/<id>/actions` | カードを出す（`type: "play"`, `cardId`, `expectedRevision`, SHOT は `target`） |
| `POST` | `/api/rooms/<id>/stream-ticket` | SSE 用の短命チケット |
| `GET` | `/api/rooms/<id>/events` | SSE（変更通知） |

- `lastRound` は構造化されたラウンド結果（`GameState.round_results` の最新件）から作る。
  既存キー（`round`, `loserId`, `loserName`, `cardId`, `card`, `sumAfter`, `sum`, `chipsLeft`, `chips`,
  `eliminated`, `alive`, `gameOver`, `winnerId`, `winnerName`, `nextRound`, `message`）に加え、
  `reason`（`"no_playable_card"`）を持つ。負けた人の手札（旧 `revealedHand`）は返さない。
  イベントログの `round_end` も構造化結果から作る。

- `result` はゲーム終了時だけ `{winnerId, winnerName, message, standings}` を返す（終了前は `null`）。
  `standings` は1位から順に `{"playerId", "rank"}` を持つ。1位は勝者、2位以降は
  `GameState.round_results` の `eliminated` が真の要素を後ろからたどった「脱落の逆順」
  （最後に脱落した人が2位、最初に脱落した人が最下位）。脱落記録に載っていない人がいた場合は
  席番号順で末尾に並べる。

認証は `X-Player-Token`（または `Authorization: Bearer`）。トークンは localStorage の
`neu:<roomId>` に保存する。

### UI（スマホ縦画面優先）

- 画面に出すもの（対戦中）は次のとおり。それ以外の要素は足さない。
  - ヘッダ: 「ノイ」・ルームID・コピー・退出・ラウンド番号（ラウンド番号はここ1か所だけ）。
  - 楕円の卓: 各席は「名前（長いときは省略） / バッジ行（親・CPU） / チップ数」の3段。
    バッジ行はバッジが無い席でも同じ高さを確保し、親が移動しても席の大きさ・位置が変わらない。
    CPU の席はバッジ行に文字「CPU」のバッジ（`.player-tag.cpu`。色だけ「親」と区別）を出す。
    手番ハイライト・DOUBLE 中の手番の人だけ「あとN枚」も従来どおり。自分は下中央、他は席番号の増加＝画面上の時計回りに等間隔。
    脱落者は席を残して暗くする。
  - 回る向きは「↻ 時計回り」「↺ 反時計回り」の文字1か所だけ（ステータス帯）。矢印の図形・回転アニメーションは作らない。
  - 場の合計は卓の中央に1か所だけ（`gameState.sum` をそのまま常時更新）。
  - 場札は一番上の1枚だけを傾けず重ねずに表示し、下に「場札 N枚」。空のときは「なし」。
  - 山札は裏面1枚と「山札 N枚」。
  - 手番の案内は1行。
  - 自分の手札はカード画像。出せるカードは「出すと合計 XX」（`afterSum`）、101 超え（`busts`）は赤い「超過」バッジ。
  - ログは折りたたみ（既定は閉じる）。
  - ラウンド結果トーストは `lastRound.message` とチップ残り・脱落・次の最初の手番を1行で（手札は出さない）。
    負けた人・次の最初の手番が CPU のときは、名前の直後に文字「CPU」のバッジをインライン表示する
    （`message` は変更せず、`loserName` で始まるときだけクライアントで名前部分と残りに分ける）。
  - ゲーム終了時は勝者と順位をオーバーレイ表示。順位は1位=勝者、2位以降=脱落の逆順で、
    各行に「1位」「2位」…を表示し、上位3人には 🥇🥈🥉 を添える（4位以降は順位の数字のみ）。
    名前・チップ数・脱落/勝者タグ・自分の行の強調は従来どおり。CPU の行は名前の横に文字「CPU」のバッジを付ける。
    360×640 で収まらない場合はカード内でスクロールする。
- 対戦中は `body.in-game` で `100dvh` の縦 flex にし、390×844 と 360×640 で縦スクロールなしに収める。
- 席は `static/table-layout.js` の `seatPosition(seat, mySeat, seatCount, {radiusX, radiusY, centerX, centerY})` で配置する。
  席の実寸を測って半径を決めるので、席が卓の枠からはみ出さない。`ResizeObserver` でサイズ変化時に再配置する。
- 手札は3枚を横並び。自分の手番で `playable` なカードをタップするとその場で出す（即出し）。アクションパネル（「出す」ボタン）は廃止した。
- SHOT は、手札の SHOT をタップしてから、円形卓上で `targets` に該当する席をタップして出す。
- DOUBLE 中の1枚目はサーバーが合法手から除外し、UI でも「1枚目に出せません」と表示する。
- 手番が回ってきたらプロンプト・ページタイトル・（対応端末では）振動で通知。
  プロンプトで手番の人が CPU のときは、名前の直後に文字「CPU」のバッジをインライン表示する。
- ラウンド敗者はサーバーの `lastRound.message` をそのままトースト表示（負けた人の手札は表示しない）。
  CPU が負けたときは名前の直後に、次が CPU のときは本文の名前に文字「CPU」のバッジを付ける。
- 他人の手札の中身・枚数は表示しない。山札の裏面は CSS で描画する（`back.png` は未提供）。
- 画像が404でも CSS の仮カードで遊べる（`onerror` で画像を隠す）。

### 未対応（フェーズ3以降）

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
- [x] フェーズ4: CPU プレイヤーの追加（2026-11）
  - [x] `games/neu/cpu.py` の `CpuDriver`（`step(game) -> bool`）を実装
  - [x] `games/neu/__init__.py` の `cpu_driver` に登録
  - [x] 開始UIに「ゲーム開始 ▾」のCPU人数選択を追加（人間＋CPUで2〜7人）
  - [x] CPU のカード選択・SHOT 指名・DOUBLE・ラウンド負けを含むテスト
  - [x] CPU混じりの API 対戦が最後まで終わることを確認
- [x] CPU の名前とバッジ（2026-11）
  - [x] `GameDefinition.cpu_names`（任意）を追加。未指定のゲームは従来どおり `CPU1`…
  - [x] ノイの `CPU_NAMES` から重複なしでランダムに選択（人間の名前は大文字小文字・空白を無視して除外）
  - [x] 候補不足時は `CPU{n}` にフォールバック
  - [x] 公開 view の `players[].isCpu` を追加（id 判定を `rooms.is_cpu_id` に集約）
  - [x] 席（バッジ行）と終了画面の順位表に文字「CPU」のバッジを表示（席は名前 / バッジ行 / チップの3段）
- [x] フェーズ3: Docker 化 / 共通ロビー登録 / VPS 展開（RELEASE 1.0 で実施済み）

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
- `VERSION` はリポジトリで管理する（.gitignore からは外す）。無い場合 `/health` の version は `"dev"`。
- `SECRET_KEY` は環境変数で設定する。未設定時は起動ごとのランダム値になる。
- 共通ロビーへ戻るリンクは、同じホストのルート（既定ポート80）を指す。
- ルームはメモリ上に保持する（`gameplatform.rooms`）。再起動で消えるため、永続化はフェーズ3以降の課題。

### フェーズ4（CPU）の実装メモ — 2026-11 完了

- `games/neu/cpu.py` の `CpuDriver` を `games/neu/__init__.py` の `cpu_driver` に登録した。
- 共通基盤は `cpu_driver(game, cpu_ids, config)` が `step(game) -> bool` を持つオブジェクトを期待する
  （1手進めたら `True`、人間の番・打てない・終了なら `False`）。
- CPU も必ず `legal_actions` / `apply` を通す（`NeuGame.act` 経由）。UI や API にルールを足さない。
- 開始 UI は「ゲーム開始 ▾」のメニューで、人間＋CPUの合計が2〜7人になる選択肢を出す。
  人間は必ず1人以上（ルーム作成者がホストとして残る）。

#### 赤数字の定義
- 赤数字 = `n1`〜`n9`, `n10`, `n50`（`RED_CARD_IDS`）。
- それ以外（`nm1`, `nm10`, `n101`, `pass`, `turn`, `shot`, `double`）は赤数字ではない。

#### カードの選び方（`legal_actions` の合法手のみ）
判定は次の順で行い、条件に当てはまって確率に当たったらそのカードを出して終了する。
当てはまらない、または確率に外れたら次の判定へ進む。

1. 101 ぴったり狙い（最優先）:
   - 条件: 出せる赤数字のうち、出すと場の合計がちょうど 101 になるカードがある（n101 は除外）。
   - 90%: そのカードを出す（`EXACT_101_PROBABILITY = 0.9`）。
   - 10%: スキップして 2 へ。
2. 特殊 101 の奇襲:
   - 条件: (a) 現在の場の合計が 50 以下、(b) 手札に n101 があり出せる、(c) 手札に n101 以外の黒カードが 1 枚以上。
   - 90%: n101 を出す（`SURPRISE_101_PROBABILITY = 0.9`）。
   - 10%: スキップして 3 へ。
3. 手札構成による通常判定（黒カードの優先度順:
   `pass` ＞ `turn` ＞ `shot` ＞ `nm1` ＞ `n101` ＞ `nm10` ＞ `double`、定数 `BLACK_CARD_PRIORITY`）。
   - パターン A: 出せるカードが赤数字のみ
     - 90%: 出せる赤数字のうち値が最大のもの（同値ならその中からランダム）
       （`RED_ONLY_MAX_PROBABILITY = 0.9`）。
     - 10%: 出せる赤数字の中からランダム。
   - パターン B: 出せるカードが黒カードのみ
     - 80%: 優先度順で最も優先度の高いもの（`BLACK_ONLY_PRIORITY_PROBABILITY = 0.8`）。
     - 20%: 出せる黒カードの中からランダム。
   - パターン C: 赤数字と黒カードが混在
     - 80%: 出せる赤数字のうち値が最大のもの（同値ならその中からランダム）
       （`MIXED_MAX_RED_PROBABILITY = 0.8`）。
     - 20%: 黒カードを出す。その中身は 80% で優先度順の最優先、20% で出せる黒カードからランダム
       （`MIXED_BLACK_PRIORITY_PROBABILITY = 0.8`）。
   - 最大値が同値で複数あるときは、その中からランダム。

#### SHOT の指名先
- 合法手が許す対象（自分以外の生存者）だけから選ぶ。
- 各対象の「直近にその人が出したカード `SHOT_HISTORY_WINDOW = 5` 枚のうち赤数字だった比率」を
  重みにして、比率に比例する確率で選ぶ。全員 0 なら均等ランダム。
- 履歴はエンジンの `GameState.events`（`席N が <card> を出した` / PASS / TURN / SHOT / DOUBLE）から
  都度作る。エンジンの状態や API の形は変えていない。

#### 待ち時間・乱数・名前
- 待ち時間は犯人は踊ると同じく、共通基盤 `gameplatform.rooms.CPU_TURN_DELAY_SECONDS = 1.5` 秒。
  CPU スレッドが1手ごとに待つ方式で、テストではこの定数を 0 に差し替える。
- 人間プレイヤー（id が `"cpu"` で始まらない人）が全員チップ0になると、CPU の待ち時間は 0 になる。
  ゲーム側のフック `NeuGame.has_chips(player_id)`（エンジンの `PlayerState.chips > 0` を返す）を
  共通基盤の `rooms._cpu_delay(room)` がロック内で確認し、`sleep` はロックの外で行う。
  フックが無いゲーム（他リポジトリへの共通基盤コピー先）は従来どおり `CPU_TURN_DELAY_SECONDS` で待つ。
- CPU は自前の `random.Random` を持ち、`CpuDriver(..., rng=random.Random(seed))` で再現できる。
- 表示名は共通基盤の `_new_cpus` が付ける。ゲームの `GameDefinition.cpu_names` に候補があれば、
  その中から重複なしでランダムに選ぶ（人間の名前と同じ候補は、大文字小文字・前後の空白を無視して除外）。
  候補が足りない場合は `CPU1`, `CPU2`, ... にフォールバックする。候補を渡さないゲームは従来どおり `CPU1`…。
  ノイの候補は `games/neu/__init__.py` の `CPU_NAMES` に集約している。
  名前の選択はモジュールレベルの `rooms._cpu_name_rng` を使うため、テストでは seed を固定できる。
- CPU かどうかは名前ではなく id で判定する。共通基盤の `rooms.is_cpu_id(player_id)`（id の先頭が `"cpu"`）を
  待ち時間・退出・中止・公開 view の `isCpu` で共用し、判定を重複させない。

---

## 11. 退出システム（承認制）— 2026-11 追加

共通基盤 `gameplatform/rooms.py` に実装。ゲーム非依存。

### 定数
- `EXIT_VOTE_SECONDS = 30`
- `EXIT_COOLDOWN_SECONDS = 120`
- `EXIT_NOTICE_SECONDS = 60`（catan の `NOTICE_SECONDS` 相当）

### 仕様
- 進行中のゲーム（開始済みで未終了）でのみ「退出の申請」が可能。他の**全員**の許可が必要。
- 全員承認でゲーム中止 → 全員がその部屋のロビーに戻る。部屋・プレイヤー・ホストは残る。通知「ゲームは中止されました（◯◯さんの退出）」。
- 1人でも拒否、または30秒以内に全員が答えなければ却下。申請者は120秒再申請不可（クールダウン）。申請者本人の取り消しはクールダウンなし。
- 他人の投票内容は誰にも見せない。見えるのは自分の投票と残り秒数のみ。
- 申請・投票・取り消しは座席のある人（認証トークンを持つ人）だけ。
- 同時に1部屋1件。申請中にゲームが終わったら申請は自動で消える。
- CPU プレイヤー（`id` が `"cpu"` で始まる人）は自動承認。人間が申請者だけで他が全部 CPU なら即承認。
- ゲーム終了後は承認なしで即退出可能。`leave_room` の判定は「進行中（未終了）」かどうか（`_is_finished(game)`）。

### サーバー側
- `Room` に `exit_request`, `exit_cooldown`, `notice`, `exit_seq`, `notice_seq`, `exit_timer` を追加。
- 判定は `_evaluate_exit_request(room)` に集約。すべての API 呼び出し冒頭（遅延評価）と期限到来時（`threading.Timer`）に呼ぶ。
- 中止処理: `game=None`, `cpu_driver=None`, CPU スレッド停止, CPU プレイヤーを `players` から除外, `version` を進める。人間プレイヤーとホストは残す。
- API: `POST /api/rooms/<id>/exit-request`, `exit-vote`, `exit-cancel`（いずれも `X-Player-Token` 認証必須）。
- 状態 API / SSE: `exitRequest`（`id`, `by`, `mine`, `myVote`, `remaining`）と `notice`（`id`, `text`）。

### 他リポジトリに共通基盤をコピーする際の注意
- `gameplatform/rooms.py` の `_now()` はテストから差し替え可能な時計。`EXIT_*` 定数もモジュール属性として差し替え可能。
- 中止後も `/health` の `playing_rooms` が正しくなるよう、`_is_finished` を使う。
- CPU プレイヤーの id は `"cpu"` で始める必要がある（`_new_cpus` が `"cpu-..."` を生成）。
  判定は `rooms.is_cpu_id(player_id)` に集約している（名前ではなく id）。
- `notice` / `exit_seq` / `notice_seq` はリセット後も残す（通知が消えないように）。

---

## 12. 連戦システム — 2026-11 追加

### 仕様

- 連戦数: 1（通常戦。従来どおり）、3、5、10 のいずれか。
- 試合内順位: 勝者が1位、脱落の逆順で2位、3位…。最初に脱落した人がビリ。
- 得点: ビリ（最下位）は常に0点。それ以外は 1位=10、2位=7、3位=5、4位以下=1。
  - 人数別の例: 2人は10/0。3人は10/7/0。4人は10/7/5/0。5人は10/7/5/1/0。7人は10/7/5/1/1/1/0。
- 積算点: 各戦の得点の合計。
- 積算順位: 競技順位方式。同点は同順位で、次の順位は飛ばす（1位が2人なら次は3位）。試合内順位は同順位にならない。
- 順位変動: 前戦終了後の積算順位と比べる。良くなったら up、同じなら same、悪くなったら down。1戦目は比較対象がないので「なし」。
- 次戦: 席は固定（同じ人が同じ席）。全カードを再配布し、チップは3に戻す（新規ゲーム相当）。最初のディーラーと最初の手番は前戦のビリ。1戦目だけ従来どおりランダム。
- メダルは最終戦終了後の積算順位でのみつける。途中の試合順位・途中の積算順位にはつけない。ロジックは順位の数値だけ返し、絵文字はUI側で決める。
- 各戦終了時に返す結果: 試合番号、全試合数、最終戦かどうか、試合内順位と獲得点（席ごと）、積算点・積算順位・順位変動（席ごと）。

### 純粋ロジック `neu/series.py`

外部 I/O・時計・乱数なし。席番号（0始まり）だけを扱い、`playerId` への変換は
アダプタ側（後続の依頼）で行う。試合内順位は `neu.engine.finish_order`
（勝者→脱落の逆順。旧 `NeuGame._standings` の処理を抽出）を再利用し、重複実装しない。

- `SERIES_LENGTHS = (1, 3, 5, 10)`
- `validate_series_length(total_games)`: 連戦数を検証して返す。
- `points_for_rank(rank, player_count)`: 試合内順位から得点を返す。
- `points_by_seat(ranks_by_seat)`: `{席: 順位}` から `{席: 得点}` を返す。
- `add_scores(previous_scores_by_seat, earned_scores_by_seat)`: 積算点を返す。1戦目は `None` または `{}`。
- `competition_ranks(scores_by_seat)`: 積算点から競技順位を返す（同点は同順位、次を飛ばす）。
- `rank_changes(previous_ranks_by_seat, current_ranks_by_seat)`: `up` / `same` / `down` を返す。1戦目は全席 `None`。
- `last_place_seat(ranks_by_seat)`: 最下位の席を返す。一意でなければ `ValueError`。
- `next_dealer(ranks_by_seat)`: 次戦の最初のディーラー（前戦のビリ）の席を返す。
- `ranks_from_round_results(round_results, winner_seat, player_count)`: `GameState.round_results` から `{席: 試合内順位}` を返す。
- `build_series_result(game_number, total_games, ranks_by_seat, previous_scores_by_seat=None, previous_ranks_by_seat=None)`:
  1試合分の結果を返す。

`build_series_result` の戻り値:

```python
{
    "game": 2,                 # 試合番号（1始まり）
    "totalGames": 3,           # 連戦数
    "isFinal": False,          # 最終戦かどうか
    "nextDealerSeat": 0,       # 次戦の最初のディーラー（席）。最終戦は None
    "seats": [
        {
            "seat": 0,
            "rank": 4,         # この試合の順位
            "points": 0,       # この試合の獲得点
            "total": 10,       # 積算点
            "seriesRank": 3,   # 積算順位
            "rankChange": "down",  # None / "up" / "same" / "down"
        },
        # ... 席ごとに続く
    ],
}
```

- メダル・絵文字・`playerId` はこの戻り値に含めない。最終戦かどうかは `isFinal` で判断し、
  メダルはUI側が最終戦の `seriesRank` だけを見て決める。
- 脱落記録に載っていない席は、既存挙動を踏襲して席番号順で末尾に並べる。
  エンジンが正常にゲームを終えた場合は全員の脱落が記録されるため、通常は発生しない。

### サーバー側・API（依頼B の実装メモ）

- 開始: `POST /api/rooms/<room_id>/start` は `seriesLength` を受け取る。許可値は
  `1` / `3` / `5` / `10`、未指定は `1`。不正値は既存形式の 400 エラー。
  ホストのみ開始可能。ゲーム共通の開始設定として `game_config` 経由でゲームへ渡す。
- 連戦状態: `games/neu/adapter.py` が `game_number` / `totalGames` / `series_waiting` /
  `last_series_result` / 確認済み集合 / 次の遷移 deadline を持つ。
- 状態 API / SSE: ゲーム view の `series` に
  `game` / `totalGames` / `waiting` / `isFinal` / `result` / `seats` / `confirmed` / `remainingSeconds`
  を返す。リロード・再接続でも直近結果を復元できる。
- 確認: `POST /api/rooms/<room_id>/series/confirm`（認証は既存トークン）。人間が全員
  確認した、または 10 秒経過したら次戦へ。CPU は確認済み扱い。
- 次戦待機: `game.phase = "waiting"`、ゲーム view の `phase` も `waiting`。最終戦だけ
  `phase = "finished"` で終了扱い（次戦待機なし）。
- 共通基盤: `gameplatform.rooms` に pending tick 用タイマー
  （`next_tick_at` / `tick(now)`）と `evaluate_room` / `evaluate_game` を追加。
  CPU スレッド・SSE・退出判定から同じ遅延評価を通る。最終戦・中止時はタイマーを止める。
- 次戦開始: アダプタは前戦ビリを最初のディーラーにして `new_game` を再生成。
  席固定は `NeuGame.players` の seat 順で維持。チップは新規ゲームと同じ `STARTING_CHIPS`。

---

## バージョン管理

- バージョンはプロジェクト直下の `VERSION` を正とする。GET `/health` の `version` はその値。
- 最初のリリース = `RELEASE1.0`。以降の小さな修正は `RELEASE1.0-01`, `RELEASE1.0-02`, …（2桁の連番）。
- 大きな変更（遊び方や互換性が変わるもの）は `RELEASE1.1` のようにサブバージョンを上げる。
- ビルド時に `python3 tools/build_version.py --write` を実行し、git タグ `RELEASE1.0` からの
  コミット数で自動採番する（0件 -> `RELEASE1.0`、n件 -> `RELEASE1.0-nn`）。
- git 履歴/タグが無いビルド環境では既存の `VERSION` をそのまま使い、`VERSION` も無ければ `dev`。
- 番号は手作業で書き換えない（採番の基準タグは `RELEASE1.0`）。

## バージョン履歴

- `RELEASE1.0`: 最初のリリース。フェーズ2（Web UI / API）、退出システム（承認制）、CPU 対戦、
  順位表示、連戦システム（通常戦 / 3・5・10 連戦）を含む。
- `RELEASE1.0-01`: アプリアイコン（`static/img/icon/`）と favicon の指定を追加。
