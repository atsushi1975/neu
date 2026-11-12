"""ノイ (Neu) ゲームエンジン。

ルール判定だけを持つ純粋なロジック。画面・通信・I/O には一切依存しない。
乱数は GameState が持つ ``random.Random`` に限定し、seed で再現できる。

設計の要点
----------
* 状態は ``GameState`` / ``PlayerState`` / ``Card`` の明示的なオブジェクト。
* 手番でできることは ``legal_actions`` で列挙し、``apply`` で適用する。
* ``apply`` は状態を破壊せず、新しい ``GameState`` を返す。
* 公開情報と非公開情報は ``view_for`` で分ける。
* ``to_dict`` / ``from_dict`` で JSON と相互変換できる（乱数状態込み）。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 定数（仮置きの決め事はここに集約する）
# ---------------------------------------------------------------------------

MIN_PLAYERS = 2
MAX_PLAYERS = 7
STARTING_CHIPS = 3
HAND_SIZE = 3
# 最初のディーラー。None なら全員（開始時は全員生存）からランダムに1人選ぶ。
# 整数が入っていればその席に固定する（テスト・CLI の再現用）。
INITIAL_DEALER: Optional[int] = None
LOSING_LIMIT = 101
# 最初の手番はディーラー本人にするか（True）。False なら進行方向の次の生存者の手番から。
FIRST_TURN_IS_DEALER = True
# 安全なカードが見つからずに連鎖でラウンド負けが続く場合の上限（保険）。
MAX_LOSS_CHAIN = 16
# 場札を山札へ戻すとき（山札が尽きたとき）、場札の一番上のカードを残すか（仮置き）。
# True なら「直前に出したカード」を残し、それ以外をシャッフルして新しい山札にする。
# False なら場札の全部をシャッフルする。
KEEP_TOP_ON_RESHUFFLE = True

# (カードID, kind, value, 枚数)
# kind: "number"（通常の数字）, "hundred"（n101）, "pass", "turn", "shot", "double"
CARD_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("n1", "number", 1, 3),
    ("n2", "number", 2, 3),
    ("n3", "number", 3, 3),
    ("n4", "number", 4, 3),
    ("n5", "number", 5, 3),
    ("n6", "number", 6, 3),
    ("n7", "number", 7, 3),
    ("n8", "number", 8, 3),
    ("n9", "number", 9, 3),
    ("n10", "number", 10, 6),
    ("n50", "number", 50, 2),
    ("nm10", "number", -10, 4),
    ("nm1", "number", -1, 2),
    ("n101", "hundred", 101, 5),
    ("pass", "pass", 0, 4),
    ("turn", "turn", 0, 4),
    ("shot", "shot", 0, 2),
    ("double", "double", 0, 2),
)

DECK_SIZE = sum(spec[3] for spec in CARD_SPECS)


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class NeuError(Exception):
    """エンジン共通の例外基底。"""


class InvalidSetup(NeuError):
    """人数・席番号などセットアップの不正。"""


class IllegalAction(NeuError):
    """不正な手。"""


class NotYourTurn(IllegalAction):
    """手番でないプレイヤーが操作した。"""


class CardNotInHand(IllegalAction):
    """手札に存在しないカードを指定した。"""


class DoubleNotAllowed(IllegalAction):
    """DOUBLE 中の1枚目に DOUBLE を出そうとした。"""


class InvalidTarget(IllegalAction):
    """SHOT の指名先が不正。"""


# ---------------------------------------------------------------------------
# カード
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """1枚のカード。同種カードは ``copy``（1始まり）で区別する。"""

    id: str
    kind: str  # number | hundred | pass | turn | shot | double
    value: int = 0
    copy: int = 1

    @property
    def is_number(self) -> bool:
        """合計を変化させる数字カードか（n101 を含む）。"""
        return self.kind in ("number", "hundred")

    @property
    def is_instruction(self) -> bool:
        return self.kind in ("pass", "turn", "shot", "double")


def build_deck() -> list[Card]:
    """58枚のデッキを生成する（シャッフルはしない）。"""
    deck: list[Card] = []
    for card_id, kind, value, count in CARD_SPECS:
        for copy_index in range(1, count + 1):
            deck.append(Card(id=card_id, kind=kind, value=value, copy=copy_index))
    return deck


def card_to_dict(card: Card) -> dict[str, Any]:
    return {"id": card.id, "kind": card.kind, "value": card.value, "copy": card.copy}


def card_from_dict(data: dict[str, Any]) -> Card:
    return Card(
        id=str(data["id"]),
        kind=str(data["kind"]),
        value=int(data.get("value", 0)),
        copy=int(data.get("copy", 1)),
    )


# ---------------------------------------------------------------------------
# 状態
# ---------------------------------------------------------------------------


@dataclass
class PlayerState:
    seat: int
    chips: int = STARTING_CHIPS
    hand: list[Card] = field(default_factory=list)
    alive: bool = True


@dataclass
class GameState:
    players: list[PlayerState]
    deck: list[Card]
    table: list[Card]  # 場札の山（積み上がる側が末尾 = 一番上）
    total: int  # 場の合計（API では "sum"）
    direction: int  # +1 = 時計回り（席番号増加）, -1 = 反時計回り
    dealer: int
    current_player: Optional[int]
    cards_remaining: int  # 今手番でまだ出す必要のある枚数（1 or 2）
    phase: str = "playing"  # playing | game_over
    winner: Optional[int] = None
    round_number: int = 0
    events: list[str] = field(default_factory=list)
    round_results: list[dict[str, Any]] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)


@dataclass(frozen=True)
class Action:
    """1回の操作。``card_index`` は手札リスト内の位置。"""

    player: int
    card_index: int
    target: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {"player": self.player, "card_index": self.card_index, "target": self.target}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Action":
        target = data.get("target")
        return Action(
            player=int(data["player"]),
            card_index=int(data["card_index"]),
            target=None if target is None else int(target),
        )


# ---------------------------------------------------------------------------
# 基本ヘルパ
# ---------------------------------------------------------------------------


def alive_players(state: GameState) -> list[PlayerState]:
    return [p for p in state.players if p.alive]


def next_alive(state: GameState, seat: int, direction: int) -> int:
    """``seat`` から ``direction`` 方向で次の生存プレイヤーの席番号を返す。"""
    count = len(state.players)
    for step in range(1, count + 1):
        candidate = (seat + direction * step) % count
        if state.players[candidate].alive:
            return candidate
    return seat


def _draw(state: GameState) -> Optional[Card]:
    """山札から1枚引く。山札が切れたら場札をシャッフルして再利用する。

    引けるカードがなければ ``None`` を返す（山札も場札も空のとき。
    場札が1枚だけのときは ``KEEP_TOP_ON_RESHUFFLE`` でその1枚を残すため引けない）。
    """
    if not state.deck:
        if not state.table:
            return None
        if KEEP_TOP_ON_RESHUFFLE:
            # 場札の一番上（直前に出したカード）は残し、それ以外をシャッフルして山札にする。
            state.deck = state.table[:-1]
            state.table = [state.table[-1]]
        else:
            state.deck = list(state.table)
            state.table = []
        state.rng.shuffle(state.deck)
        if not state.deck:
            # 場札をシャッフルしても山札が空のままのとき（場札が1枚など）は引けない。
            return None
    return state.deck.pop()


def _refill(state: GameState, player: PlayerState) -> None:
    """手札が HAND_SIZE 枚になるまで山札から補充する。"""
    while len(player.hand) < HAND_SIZE:
        card = _draw(state)
        if card is None:
            break
        player.hand.append(card)


# ---------------------------------------------------------------------------
# ゲーム・ラウンドの開始
# ---------------------------------------------------------------------------


def new_game(
    num_players: int, seed: Optional[int] = None, dealer: Optional[int] = None
) -> GameState:
    """新しいゲームを初期化する。

    Args:
        num_players: プレイヤー人数（2〜7）。
        seed: 乱数シード。同じ seed と同じ操作で同じ展開になる。
        dealer: 最初のディーラーの席番号（None なら :data:`INITIAL_DEALER` に従う。
            既定の :data:`INITIAL_DEALER` は None = 全員からランダムに1人選ぶ）。
            指定があればその席に固定する（テスト・CLI の再現用）。
    """
    if isinstance(num_players, bool) or not isinstance(num_players, int):
        raise InvalidSetup("プレイヤー人数は整数で指定してください")
    if not (MIN_PLAYERS <= num_players <= MAX_PLAYERS):
        raise InvalidSetup(
            f"プレイヤー人数は {MIN_PLAYERS}〜{MAX_PLAYERS} 人です（指定: {num_players}）"
        )

    rng = random.Random(seed)
    players = [PlayerState(seat=i) for i in range(num_players)]
    state = GameState(
        players=players,
        deck=build_deck(),
        table=[],
        total=0,
        direction=1,
        dealer=0,
        current_player=None,
        cards_remaining=1,
        phase="playing",
        winner=None,
        round_number=0,
        events=[],
        rng=rng,
    )
    # ディーラー選択 → ラウンド開始時のシャッフル・配布、の順で乱数を使う
    # （同じ seed なら同じ展開になる）。
    resolved_dealer = dealer if dealer is not None else INITIAL_DEALER
    if resolved_dealer is None:
        state.dealer = state.rng.randrange(num_players)
    else:
        state.dealer = resolved_dealer % num_players
    state.current_player = None
    state.cards_remaining = 1
    state.phase = "playing"
    state.winner = None
    state.round_number = 0
    state.events = []
    state.round_results = []
    _begin_round(state, deal=True)
    return state


def _begin_round(state: GameState, *, deal: bool) -> None:
    """ラウンド開始の共通処理。

    ``deal=True`` のときだけ全カードを回収してシャッフルし、生存者に3枚ずつ配る
    （ゲーム開始用。場札は空で始める）。``deal=False`` のときはカード（山札・場札・
    手札）には一切触らず、合計を 0 に戻すだけ（ラウンド継続用。ラウンド負け後の
    次ラウンドなど）。どちらの場合も、山札から場札をめくることはしない。
    """
    if state.phase != "playing":
        raise NeuError("ゲームは終了しています")

    survivors = alive_players(state)
    if not survivors:
        raise NeuError("生存プレイヤーがいません")

    if deal:
        # ゲーム開始時だけ: 全カードをシャッフルして生存者に3枚ずつ配る。
        # 場札は空のまま（山札から場札をめくらない）。
        gathered: list[Card] = []
        for player in state.players:
            gathered.extend(player.hand)
            player.hand = []
        gathered.extend(state.deck)
        gathered.extend(state.table)
        state.deck = gathered
        state.table = []
        state.rng.shuffle(state.deck)
        for player in survivors:
            for _ in range(HAND_SIZE):
                card = _draw(state)
                if card is None:  # 58枚あるので通常は起こらない
                    raise NeuError("配布できるカードが足りません")
                player.hand.append(card)

    # 合計を 0 に戻す。山札・場札・手札はここ（上の配布）以外では触らない。
    state.total = 0

    if deal:
        # ゲーム開始時は向きを時計回りにリセットする。
        state.direction = 1

    state.round_number += 1
    state.current_player = (
        state.dealer if FIRST_TURN_IS_DEALER else next_alive(state, state.dealer, state.direction)
    )
    state.cards_remaining = 1

    state.events.append(
        f"ラウンド{state.round_number} 開始: ディーラー席{state.dealer} / "
        f"合計 {state.total} / 手番 席{state.current_player}"
    )

    # 最初の手番の人も、「出せるカードが1枚もない」ならこのラウンドの負け。
    # 負けた場合は _lose_round → start_round が呼ばれるため、ここは while で連鎖を処理する。
    # チップが減るので必ず終了するが、保険として上限ガードを入れる。
    losses = 0
    while (
        state.phase == "playing"
        and state.current_player is not None
        and not legal_actions(state)
    ):
        if losses >= MAX_LOSS_CHAIN:
            raise NeuError("出せるカードがすべて101を超えるため、ラウンド負けの連鎖が続いています")
        losses += 1
        _lose_round(state, state.players[state.current_player], reason="no_playable_card")


def start_round(state: GameState) -> GameState:
    """次のラウンドを開始する（状態を直接更新する）。

    カードの回収・シャッフル・再配布・山札からのめくりは一切しない。
    変わるのは「合計を 0 に戻すこと」と「最初の手番（ディーラー本人 ＝ 前ラウンドの
    負けた人）を決めること」だけ。場札・山札・手札はそのまま積み上がった状態で
    引き継ぎ、回る向きもリセットしない。

    ゲーム開始時の配布（全カードをシャッフルして3枚ずつ配る）は ``new_game`` 側だけ。
    """
    _begin_round(state, deal=False)
    return state


# ---------------------------------------------------------------------------
# 合法手の列挙
# ---------------------------------------------------------------------------


def legal_actions(state: GameState) -> list[Action]:
    """現在の手番で選べる手をすべて返す。ゲーム終了時は空リスト。"""
    if state.phase != "playing" or state.current_player is None:
        return []

    player = state.players[state.current_player]
    actions: list[Action] = []

    for index, card in enumerate(player.hand):
        # DOUBLE 中の1枚目には DOUBLE を出せない
        if card.kind == "double" and state.cards_remaining >= 2:
            continue

        # 合計が 101 を超える数字カードは出せない（出せるカードが無ければ、この人は負け）。
        # n101 は合計が 101 になるだけなので、この判定を通る（101 は 101 を超えない）。
        if card.is_number and projected_total(state, card) > LOSING_LIMIT:
            continue

        if card.kind == "shot":
            for target in range(len(state.players)):
                if target == player.seat:
                    continue
                if state.players[target].alive:
                    actions.append(Action(player=player.seat, card_index=index, target=target))
        else:
            actions.append(Action(player=player.seat, card_index=index))

    return actions


def projected_total(state: GameState, card: Card) -> int:
    """カードを出した後の合計を計算する（合法手・安全な手の判定で共用する）。"""
    if card.kind == "hundred":
        return LOSING_LIMIT
    if card.kind == "number":
        return state.total + card.value
    return state.total


def safe_actions(state: GameState) -> list[Action]:
    """現在の手番で「出したとしても合計が 101 を超えない」安全な合法手を返す。

    ``legal_actions`` の結果のうち、カードを出しても合計が :data:`LOSING_LIMIT`
    を超えないものだけを安全な手として返す。PASS / TURN / SHOT / DOUBLE(法的に出せる
    場合)・n101・マイナスカードは常に安全。DOUBLE 中（``cards_remaining >= 2``）の
    1枚目に出せない DOUBLE は合法手に含まれないため、自動的に安全手の対象外になる。
    """
    if state.phase != "playing" or state.current_player is None:
        return []
    player = state.players[state.current_player]
    safe: list[Action] = []
    for action in legal_actions(state):
        card = player.hand[action.card_index]
        if projected_total(state, card) > LOSING_LIMIT:
            continue
        safe.append(action)
    return safe


def _has_safe_action(state: GameState) -> bool:
    """現在の手番の人が安全な手を1つ以上持っているか。"""
    return bool(safe_actions(state))


def _check_no_playable_card(state: GameState) -> None:
    """現在の手番の人が出せる合法手を1つも持っていない場合、そのラウンドの負けを宣言する。

    判定は「次に出す人が決まった直後」に行う。出せるカードが無ければ
    :func:`_lose_round`（``reason="no_playable_card"``）でラウンドを負ける。
    """
    if state.phase != "playing" or state.current_player is None:
        return
    if legal_actions(state):
        return
    _lose_round(state, state.players[state.current_player], reason="no_playable_card")


# ---------------------------------------------------------------------------
# 手の適用
# ---------------------------------------------------------------------------


def _validate(state: GameState, action: Action) -> Card:
    if state.phase != "playing" or state.current_player is None:
        raise IllegalAction("ゲームは終了しています")

    if action.player != state.current_player:
        raise NotYourTurn(
            f"手番は席{state.current_player} です（操作: 席{action.player}）"
        )

    player = state.players[action.player]
    if not (0 <= action.card_index < len(player.hand)):
        raise CardNotInHand(
            f"席{player.seat} の手札に index {action.card_index} はありません"
        )

    card = player.hand[action.card_index]

    if card.kind == "double" and state.cards_remaining >= 2:
        raise DoubleNotAllowed("DOUBLE 中の1枚目には DOUBLE を出せません")

    if card.is_number and projected_total(state, card) > LOSING_LIMIT:
        raise IllegalAction(
            f"合計 {projected_total(state, card)} は 101 を超えるため、このカードは出せません"
        )

    if card.kind == "shot":
        target = action.target
        if target is None:
            raise InvalidTarget("SHOT は指名先を指定してください")
        if not (0 <= target < len(state.players)):
            raise InvalidTarget(f"指名先の席番号が範囲外です: {target}")
        if target == player.seat:
            raise InvalidTarget("SHOT で自分を指名することはできません")
        if not state.players[target].alive:
            raise InvalidTarget(f"席{target} は脱落しているため指名できません")
    elif action.target is not None:
        raise IllegalAction("SHOT 以外のカードに指名先は指定できません")

    return card


def apply(state: GameState, action: Action) -> GameState:
    """手を適用した新しい ``GameState`` を返す。

    不正な手は :class:`IllegalAction` 系の例外で拒否する。
    元の ``state`` は変更しない。
    """
    _validate(state, action)
    new_state = copy.deepcopy(state)
    _apply_inplace(new_state, action)
    return new_state


def _apply_inplace(state: GameState, action: Action) -> None:
    player = state.players[action.player]
    card = player.hand.pop(action.card_index)
    # 出したカードは場札（table）の一番上に積む
    state.table.append(card)
    # 1枚出すたびに手札を3枚に戻す（DOUBLE の途中でも同様）
    _refill(state, player)

    remaining_before = state.cards_remaining

    # --- 数字カード -----------------------------------------------------
    if card.is_number:
        # 101 超えのカードは legal_actions / _validate で出せないため、ここでは必ず合計が LOSING_LIMIT 以下になる（バースト処理は不要）。
        if card.kind == "hundred":
            state.total = LOSING_LIMIT
        else:
            state.total += card.value

        state.events.append(f"席{player.seat} が {card.id} を出した (合計 {state.total})")

        state.cards_remaining -= 1
        if state.cards_remaining <= 0:
            # 手番終了。次の人の必要枚数は 1
            _end_turn(state, next_alive(state, player.seat, state.direction), 1)
        # 手番が次の人へ移った、または同じ人が残り枚数を出すべきになった直後なので、
        # 安全なカードが無いか判定する。
        _check_no_playable_card(state)
        return

    # --- 指示カード -----------------------------------------------------
    if card.kind == "pass":
        state.events.append(f"席{player.seat} が PASS (合計 {state.total} のまま)")
        # 必要枚数が残っている場合は、その義務が次の人へそのまま移る
        _end_turn(state, next_alive(state, player.seat, state.direction), remaining_before)
        _check_no_playable_card(state)
        return

    if card.kind == "turn":
        state.direction *= -1
        direction_label = "時計回り" if state.direction == 1 else "反時計回り"
        state.events.append(f"席{player.seat} が TURN (向き: {direction_label})")
        _end_turn(
            state,
            next_alive(state, player.seat, state.direction),
            remaining_before,
        )
        _check_no_playable_card(state)
        return

    if card.kind == "shot":
        target = action.target
        assert target is not None
        state.events.append(f"席{player.seat} が SHOT で席{target} を指名")
        _end_turn(state, target, remaining_before)
        _check_no_playable_card(state)
        return

    if card.kind == "double":
        state.events.append(f"席{player.seat} が DOUBLE (次の手番は2枚)")
        _end_turn(state, next_alive(state, player.seat, state.direction), 2)
        _check_no_playable_card(state)
        return

    raise IllegalAction(f"未知のカード種別です: {card.kind}")


def _lose_round(
    state: GameState,
    loser: PlayerState,
    *,
    reason: str = "no_playable_card",
    card: Optional[Card] = None,
    sum_after: Optional[int] = None,
) -> None:
    """ラウンドの負け処理。チップ減少・脱落・ゲーム終了・次ラウンド開始。

    ``reason`` は ``"no_playable_card"``（出せるカードがすべて 101 超えのため打てない）。
    カードを出した結果 101 超えで負けることはもうない（出せないカード負けにする）。

    次のラウンドへは、カードの回収・シャッフル・再配布をせずに継続処理
    （``start_round``）で進む。他のプレイヤーの手札・山札・場札はそのまま引き継ぐ。
    """
    lost_round = state.round_number
    loser.chips -= 1
    if loser.chips <= 0:
        loser.chips = 0
        loser.alive = False
        state.events.append(f"席{loser.seat} はチップを失って脱落した")
    else:
        state.events.append(f"席{loser.seat} はチップを1枚失った (残り {loser.chips}枚)")

    state.events.append(f"席{loser.seat} は出せるカードがすべて101を超えるためラウンド負け")

    if not loser.alive:
        # 脱落した手札は、場札（table）の一番下に入れる（仮置き: カード総数の保存用）。
        # 一番上のカードは変えない。定数・コメントで仮置きと分かるようにしている。
        for held_card in loser.hand:
            state.table.insert(0, held_card)
        loser.hand = []

    game_over = False
    winner_seat: Optional[int] = None
    if len(alive_players(state)) == 1:
        state.phase = "game_over"
        state.winner = alive_players(state)[0].seat
        state.current_player = None
        state.cards_remaining = 1
        game_over = True
        winner_seat = state.winner
        state.events.append(f"ゲーム終了。勝者は席{state.winner}!")

    state.round_results.append(
        {
            "round": lost_round,
            "loser": loser.seat,
            "reason": reason,
            "card_id": None,
            "sum_after": None,
            "sum": state.total,
            "chips_left": loser.chips,
            "eliminated": not loser.alive,
            "game_over": game_over,
            "winner": winner_seat if game_over else None,
        }
    )

    if not game_over:
        # 次のディーラー: 負けた人。脱落した場合はその時点の進行方向の隣の生存者。
        if loser.alive:
            state.dealer = loser.seat
        else:
            state.dealer = next_alive(state, loser.seat, state.direction)
        # 次のラウンドへは、カードを回収せずに継続処理で始める。
        start_round(state)


def _end_turn(state: GameState, next_player: int, required: int) -> None:
    state.current_player = next_player
    state.cards_remaining = required


# ---------------------------------------------------------------------------
# 参照・判定
# ---------------------------------------------------------------------------


def is_game_over(state: GameState) -> bool:
    return state.phase == "game_over"


def winner(state: GameState) -> Optional[int]:
    return state.winner


def finish_order(
    round_results: list[dict[str, Any]],
    winner_seat: int,
    num_players: int,
) -> list[int]:
    """ゲーム終了時の席順を返す（1位から順）。

    1位は勝者。2位以降は ``round_results`` の ``eliminated`` が真の要素を
    後ろからたどった「脱落の逆順」（最後に脱落した人が2位、最初に脱落した人が
    最下位）。同じ席の脱落記録が複数あっても最初の1件だけを使う。

    脱落記録に載っていない席は、席番号順で末尾に並べる。エンジンが正常に
    ゲームを終えた場合は全員の脱落が記録されるため、このフォールバックは
    保存データの欠損など想定外の入力向け。
    """
    if not (0 <= winner_seat < num_players):
        raise InvalidSetup(f"勝者の席番号が範囲外です: {winner_seat}")

    seats = [winner_seat]
    seen = {winner_seat}
    for result in reversed(round_results):
        if not result.get("eliminated"):
            continue
        loser = result.get("loser")
        if loser is None:
            continue
        seat = int(loser)
        if not (0 <= seat < num_players):
            continue
        if seat in seen:
            continue
        seen.add(seat)
        seats.append(seat)

    for seat in range(num_players):
        if seat not in seen:
            seen.add(seat)
            seats.append(seat)
    return seats


def view_for(state: GameState, seat: int) -> dict[str, Any]:
    """``seat`` 視点の公開情報を返す。

    他人の手札は枚数のみ、山札・場札は枚数のみ（場札は一番上だけ公開）。
    自分自身の手札だけ中身を返す。
    """
    if not (0 <= seat < len(state.players)):
        raise InvalidSetup(f"席番号が範囲外です: {seat}")

    players_view: list[dict[str, Any]] = []
    for player in state.players:
        entry: dict[str, Any] = {
            "seat": player.seat,
            "chips": player.chips,
            "alive": player.alive,
            "hand_count": len(player.hand),
        }
        if player.seat == seat:
            entry["hand"] = [card_to_dict(card) for card in player.hand]
        players_view.append(entry)

    table_top = state.table[-1] if state.table else None
    return {
        "viewer": seat,
        "phase": state.phase,
        "round": state.round_number,
        "sum": state.total,
        "direction": state.direction,
        "dealer": state.dealer,
        "current_player": state.current_player,
        "cards_remaining": state.cards_remaining,
        "tableTop": card_to_dict(table_top) if table_top else None,
        "table_top": card_to_dict(table_top) if table_top else None,
        "deckCount": len(state.deck),
        "deck_count": len(state.deck),
        "tableCount": len(state.table),
        "table_count": len(state.table),
        "winner": state.winner,
        "players": players_view,
        "events": list(state.events[-20:]),
    }


# ---------------------------------------------------------------------------
# JSON 変換
# ---------------------------------------------------------------------------


def _tuples_to_lists(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_tuples_to_lists(v) for v in value]
    if isinstance(value, list):
        return [_tuples_to_lists(v) for v in value]
    return value


def _lists_to_tuples(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_lists_to_tuples(v) for v in value)
    return value


def to_dict(state: GameState) -> dict[str, Any]:
    """JSON に流せる dict へ変換する（乱数状態も含む）。"""
    return {
        "players": [
            {
                "seat": player.seat,
                "chips": player.chips,
                "alive": player.alive,
                "hand": [card_to_dict(card) for card in player.hand],
            }
            for player in state.players
        ],
        "deck": [card_to_dict(card) for card in state.deck],
        "table": [card_to_dict(card) for card in state.table],
        "sum": state.total,
        "direction": state.direction,
        "dealer": state.dealer,
        "current_player": state.current_player,
        "cards_remaining": state.cards_remaining,
        "phase": state.phase,
        "winner": state.winner,
        "round": state.round_number,
        "events": list(state.events),
        "round_results": [dict(result) for result in state.round_results],
        "rng_state": _tuples_to_lists(state.rng.getstate()),
    }


def from_dict(data: dict[str, Any]) -> GameState:
    """``to_dict`` の出力から状態を復元する。"""
    players = [
        PlayerState(
            seat=int(player["seat"]),
            chips=int(player["chips"]),
            hand=[card_from_dict(card) for card in player.get("hand", [])],
            alive=bool(player["alive"]),
        )
        for player in data["players"]
    ]

    rng_state = data.get("rng_state")
    if rng_state is None:
        rng = random.Random()
    else:
        rng = random.Random()
        rng.setstate(_lists_to_tuples(rng_state))

    current_player = data.get("current_player")
    winner_seat = data.get("winner")

    # 旧形式（field_card + discard）の JSON も読めるようにする。
    # 場札は「捨て札（discard）＋ 初期場札（field_card。一番上）」として復元する。
    raw_table = data.get("table")
    if raw_table is None:
        raw_discard = data.get("discard") or []
        raw_field_card = data.get("field_card")
        table_cards = [card_from_dict(card) for card in raw_discard]
        if raw_field_card:
            table_cards.append(card_from_dict(raw_field_card))
        table = table_cards
    else:
        table = [card_from_dict(card) for card in raw_table]

    return GameState(
        players=players,
        deck=[card_from_dict(card) for card in data.get("deck", [])],
        table=table,
        total=int(data["sum"]),
        direction=int(data["direction"]),
        dealer=int(data["dealer"]),
        current_player=None if current_player is None else int(current_player),
        cards_remaining=int(data["cards_remaining"]),
        phase=str(data["phase"]),
        winner=None if winner_seat is None else int(winner_seat),
        round_number=int(data.get("round", 0)),
        events=list(data.get("events", [])),
        round_results=[
            # 旧形式の round_results（手札を含んでいた）も読み取れるように、hand は無視する。
            {
                key: value
                for key, value in dict(result).items()
                if key != "hand"
            }
            for result in (data.get("round_results") or [])
        ],
        rng=rng,
    )
