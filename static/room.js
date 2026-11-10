const roomId = document.body.dataset.roomId;
const storageKey = `neu:${roomId}`;

const els = {
  error: document.getElementById("error"),
  joinPanel: document.getElementById("join-panel"),
  joinForm: document.getElementById("join-form"),
  joinName: document.getElementById("join-name"),
  lobbyPanel: document.getElementById("lobby-panel"),
  lobbyGame: document.getElementById("lobby-game"),
  lobbyHost: document.getElementById("lobby-host"),
  lobbyRule: document.getElementById("lobby-rule"),
  lobbyStatus: document.getElementById("lobby-status"),
  lobbyPlayers: document.getElementById("lobby-players"),
  startButton: document.getElementById("start-button"),
  startWrap: document.getElementById("start-wrap"),
  takeHost: document.getElementById("take-host"),
  gamePanel: document.getElementById("game-panel"),
  summaryRound: document.getElementById("summary-round"),
  summaryDirection: document.getElementById("summary-direction"),
  summaryRemaining: document.getElementById("summary-remaining"),
  summarySum: document.getElementById("summary-sum"),
  summaryNote: document.getElementById("summary-note"),
  roundIndicator: document.getElementById("round-indicator"),
  players: document.getElementById("players"),
  tableSum: document.getElementById("table-sum"),
  tableCard: document.getElementById("table-card"),
  tableCount: document.getElementById("table-count"),
  tableDeck: document.getElementById("table-deck"),
  turnArrow: document.getElementById("turn-arrow"),
  deckCount: document.getElementById("deck-count"),
  prompt: document.getElementById("prompt"),
  hand: document.getElementById("hand"),
  handCount: document.getElementById("hand-count"),
  handHelp: document.getElementById("hand-help"),
  cancelShot: document.getElementById("cancel-shot"),
  log: document.getElementById("log"),
  logToggle: document.getElementById("log-toggle"),
  toast: document.getElementById("last-round-toast"),
  toastTitle: document.getElementById("toast-title"),
  toastBody: document.getElementById("toast-body"),
  toastHand: document.getElementById("toast-hand"),
  resultOverlay: document.getElementById("result-overlay"),
  resultWinner: document.getElementById("result-winner"),
  resultMessage: document.getElementById("result-message"),
  resultStandings: document.getElementById("result-standings"),
  copyRoom: document.getElementById("copy-room"),
  leaveRoom: document.getElementById("leave-room"),
};

const LOG_ICONS = {
  round_start: "🎲",
  play: "🃏",
  chips: "🪙",
  eliminated: "💀",
  round_end: "💥",
  game_over: "🏆",
  info: "・",
};

let session = readSession();
let roomState = null;
let youState = null;
let gameState = null;
let targetPlayerId = null;
let shotNaming = false;
let pendingShotCardId = null;
let busy = false;
let errorTimer = null;
let eventSource = null;
let streamRetry = null;
let toastTimer = null;
let lastRoundKey = null;
let wasMyTurn = false;
let logCollapsed = true;
let lastArrowDir = null;

function readSession() {
  try {
    return JSON.parse(localStorage.getItem(storageKey) || "null");
  } catch {
    return null;
  }
}

function saveSession(value) {
  session = value;
  localStorage.setItem(storageKey, JSON.stringify(value));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function showError(message) {
  els.error.textContent = message;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => {
    els.error.textContent = "";
  }, 5000);
}

function setDocumentTitle(myTurn) {
  document.title = myTurn ? "【あなたの番】ノイ" : "ノイ | ルーム";
}

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  if (session) headers["X-Player-Token"] = session.token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function apply(data) {
  roomState = data.room;
  youState = data.you || youState;
  gameState = data.game;
  if (gameState && shotNaming && !gameState.me.hand.some((card) => card.cardId === pendingShotCardId)) {
    cancelShotNaming();
  }
  els.error.textContent = "";
  els.joinPanel.hidden = true;

  if (!gameState) {
    els.lobbyPanel.hidden = false;
    els.gamePanel.hidden = true;
    els.resultOverlay.hidden = true;
    setDocumentTitle(false);
    renderLobby();
  } else {
    els.lobbyPanel.hidden = true;
    els.gamePanel.hidden = false;
    renderGame();
  }
}

/* ------------------------------------------------------------------ ロビー */

function renderLobby() {
  const players = roomState.players;
  const minPlayers = roomState.minPlayers || 2;
  const maxPlayers = roomState.maxPlayers || 7;
  const host = players.find((player) => player.id === roomState.hostId);
  const hostName = host ? host.name : "?";
  const isHost = youState
    ? Boolean(youState.isHost)
    : Boolean(session && roomState.hostId === session.playerId);
  const myId = youState ? youState.id : session && session.playerId;
  const canStart = players.length >= minPlayers;

  els.lobbyGame.textContent = roomState.gameTitle
    ? `${roomState.gameTitle}（${players.length} / ${maxPlayers} 人）`
    : "";
  els.lobbyHost.textContent = `ホスト: ${hostName}`;
  els.lobbyRule.textContent = `${minPlayers}〜${maxPlayers}人で開始できます。`;

  els.lobbyPlayers.replaceChildren();
  for (const player of players) {
    const item = el("li", "lobby-player");
    item.dataset.playerId = player.id;
    item.append(el("span", "dot"));
    item.append(el("span", "name", player.name));
    if (player.id === roomState.hostId) item.append(el("span", "badge", "ホスト"));
    if (player.id === myId) item.append(el("span", "badge me", "あなた"));
    els.lobbyPlayers.append(item);
  }

  els.startWrap.hidden = !isHost;
  els.startButton.disabled = !canStart;
  els.takeHost.hidden = isHost;

  if (!isHost) {
    els.lobbyStatus.textContent =
      `${hostName} さんが「ゲームを開始」を押すと始まります。` +
      "操作できない場合は「ホストを引き継ぐ」を押してください。";
  } else if (!canStart) {
    els.lobbyStatus.textContent =
      `あと ${minPlayers - players.length} 人そろうと開始できます。`;
  } else {
    els.lobbyStatus.textContent =
      "全員そろいました。「ゲームを開始」を押してください。";
  }
}

/* ------------------------------------------------------------------ ゲーム */

function isMyTurn() {
  return Boolean(
    gameState &&
      gameState.phase === "playing" &&
      gameState.currentPlayerId === gameState.me.id
  );
}

function playerName(playerId) {
  if (!gameState) return "?";
  const player = gameState.players.find((candidate) => candidate.id === playerId);
  return player ? player.name : "?";
}

function cardImagePath(cardId) {
  return `/static/img/cards/${encodeURIComponent(cardId)}.png`;
}

function makeCardImage(card, alt) {
  const img = el("img", "card-img");
  img.alt = alt || card.label || card.id || "カード";
  img.loading = "lazy";
  img.decoding = "async";
  img.src = cardImagePath(card.id);
  img.addEventListener("error", () => {
    // 画像が無くても CSS のフォールバック表示で遊べるようにする。
    img.classList.add("missing");
    img.removeAttribute("src");
  });
  return img;
}

function makeCardFace(card) {
  const face = el("div", `card-face card-kind-${card.kind} card-${card.id}`);
  const fallback = el("div", "card-fallback");
  fallback.append(el("span", "card-fallback-label", card.label || card.id));
  fallback.append(el("span", "card-fallback-desc", card.description || ""));
  face.append(fallback);
  face.append(makeCardImage(card));
  return face;
}

function renderGame() {
  renderSummary();
  renderPlayers();
  renderField();
  renderPrompt();
  renderHand();
  renderLog();
  renderLastRound();
  renderResult();
  renderTurnArrow();

  const myTurn = isMyTurn();
  if (myTurn && !wasMyTurn && navigator.vibrate) {
    navigator.vibrate([70, 40, 70]);
  }
  wasMyTurn = myTurn;
  setDocumentTitle(myTurn);
}

function renderSummary() {
  els.summarySum.textContent = String(gameState.sum);
  els.summaryDirection.textContent =
    `${gameState.direction === -1 ? "↺" : "↻"} ${gameState.directionLabel || "時計回り"}`;
  els.summaryRemaining.textContent =
    gameState.cardsRemaining >= 2 ? "2枚出し" : "1枚";
  els.summaryRemaining.classList.toggle("double", gameState.cardsRemaining >= 2);

  const nearLimit = gameState.sum >= 80;
  els.summarySum.classList.toggle("near-limit", nearLimit);

  const notes = [];
  if (gameState.phase === "finished") {
    notes.push("ゲーム終了");
  } else if (gameState.cardsRemaining >= 2) {
    notes.push("DOUBLE の効果で、手番の人は2枚出します");
  }
  if (gameState.nextPlayerId) {
    notes.push(`次は ${playerName(gameState.nextPlayerId)} さん`);
  }
  els.summaryNote.textContent = notes.join(" ／ ");
  if (els.roundIndicator) els.roundIndicator.textContent = `ラウンド ${gameState.round}`;
}

function renderPlayers() {
  const container = els.players;
  const meSeat = gameState.me.seat;
  const seatCount = gameState.players.length;
  const rect = container.getBoundingClientRect();
  const radiusX = Math.max(34, rect.width / 2 - 30);
  const radiusY = Math.max(24, rect.height / 2 - 26);
  const centerX = rect.width / 2;
  const centerY = rect.height / 2;

  container.replaceChildren();
  const naming = isShotNaming();
  const namingTargets = naming ? shotCard().targets || [] : [];
  for (const player of gameState.players) {
    const position = TableLayout.seatPosition({
      seat: player.seat,
      mySeat: meSeat,
      seatCount,
      radiusX,
      radiusY,
      centerX,
      centerY,
    });
    const classes = ["player"];
    if (player.isCurrent && gameState.phase !== "finished") classes.push("current");
    if (player.isMe) classes.push("mine");
    if (!player.alive) classes.push("dead");
    if (naming) {
      if (namingTargets.includes(player.id)) classes.push("targetable");
      else classes.push("dimmed");
    }
    const tile = el("div", classes.join(" "));
    tile.dataset.playerId = player.id;
    tile.style.left = `${position.x}px`;
    tile.style.top = `${position.y}px`;

    const nameRow = el("div", "player-name-row");
    nameRow.append(el("span", "player-name", player.isMe ? "あなた" : player.name));
    if (player.isDealer) nameRow.append(el("span", "player-tag dealer", "親"));
    tile.append(nameRow);

    const chips = el("p", "player-chips");
    chips.append(el("span", "chip-mark", "●"));
    chips.append(el("span", "", ` ${player.chips}`));
    tile.append(chips);

    const backs = el("div", "mini-hand");
    const count = Math.max(0, Number(player.handCount) || 0);
    for (let index = 0; index < count; index += 1) {
      backs.append(el("span", "mini-card"));
    }
    backs.append(el("span", "mini-count", `${count}枚`));
    tile.append(backs);

    const isTurn = player.isCurrent && gameState.phase !== "finished";
    if (isTurn) {
      if (gameState.cardsRemaining >= 2) {
        tile.append(el("span", "player-tag turn double", `あと${gameState.cardsRemaining}枚`));
      } else {
        tile.append(el("span", "player-tag turn", "手番"));
      }
    }
    if (!player.alive) tile.append(el("span", "player-tag out", "脱落"));

    container.append(tile);
  }
}

function fillPile(container, card, count, emptyText) {
  container.replaceChildren();
  if (!card) {
    container.append(el("p", "pile-empty", emptyText));
    return;
  }
  const face = makeCardFace(card);
  const copies = Math.min(2, Math.max(0, Number(count) - 1));
  for (let index = 0; index < copies; index += 1) {
    const clone = face.cloneNode(true);
    clone.classList.add("pile-under");
    clone.style.transform = `translate(${index === 0 ? -3 : 3}px, ${
      index * 3
    }px) rotate(${index === 0 ? -6 : 6}deg)`;
    container.append(clone);
  }
  container.append(face);
  container.append(el("span", "pile-label", card.label || card.id));
}

function fillDeck(container) {
  container.replaceChildren();
  container.append(el("div", "deck-back"));
}

function renderField() {
  fillPile(els.tableCard, gameState.tableTop, gameState.tableCount, "なし");
  els.tableCount.textContent = `場札 ${gameState.tableCount}枚`;
  fillDeck(els.tableDeck);
  els.deckCount.textContent = `山札 ${gameState.deckCount}枚`;
}

function renderPrompt() {
  els.prompt.className = "prompt";

  if (gameState.phase === "finished") {
    els.prompt.textContent = gameState.result
      ? gameState.result.message
      : "ゲーム終了";
    els.prompt.classList.add("finished");
    return;
  }

  const myTurn = isMyTurn();
  if (isShotNaming()) {
    const shot = shotCard();
    els.prompt.textContent = shot
      ? `「${shot.label}」で、誰を次の手番にしますか？`
      : "誰を次の手番にしますか？";
    els.prompt.classList.add("action");
    if (els.cancelShot) els.cancelShot.hidden = false;
    return;
  }

  if (els.cancelShot) els.cancelShot.hidden = true;
  if (myTurn) {
    els.prompt.textContent = gameState.cardsRemaining >= 2
      ? "あなたの番です（2枚出し）。手札をタップして出してください。"
      : "あなたの番です。手札をタップして出してください。";
    els.prompt.classList.add("action");
    return;
  }

  const current = gameState.currentPlayerId
    ? playerName(gameState.currentPlayerId)
    : "?";
  const waitText =
    gameState.cardsRemaining >= 2
      ? `${current} さんの番です（2枚出し）。`
      : `${current} さんの番です。`;
  els.prompt.textContent = waitText;
}

function handCardNote(card, myTurn) {
  if (!myTurn) return null;
  if (card.busts) {
    return { text: `超過 ${card.afterSum}`, danger: true };
  }
  if (card.kind === "number" || card.kind === "hundred") {
    if (!card.playable) {
      return { text: `出せない（合計 ${card.afterSum}）`, danger: false };
    }
    return { text: `出すと合計 ${card.afterSum}`, danger: false };
  }
  if (card.kind === "double" && gameState.cardsRemaining >= 2) {
    return { text: "DOUBLE中は1枚目に出せません", danger: true };
  }
  if (!card.playable) {
    return { text: "いまは出せません", danger: false };
  }
  return null;
}

function renderHand() {
  const hand = gameState.me.hand;
  const myTurn = isMyTurn();
  els.hand.replaceChildren();
  els.handCount.textContent = `(${hand.length}枚)`;

  if (hand.length === 0) {
    els.hand.append(el("p", "muted", "手札がありません。"));
    return;
  }

  for (const card of hand) {
    const selectable = myTurn && card.playable;
    const classes = ["card", `card-kind-${card.kind}`, `card-${card.id}`];
    if (!selectable) classes.push("unplayable");

    const button = el("button", classes.join(" "));
    button.type = "button";
    button.dataset.cardId = card.cardId;
    button.setAttribute(
      "aria-label",
      `${card.label}${card.description ? `（${card.description}）` : ""}`
    );
    button.append(makeCardFace(card));

    const note = handCardNote(card, myTurn);
    if (note) {
      button.append(
        el("span", `card-note${note.danger ? " danger" : ""}`, note.text)
      );
    }

    if (selectable) {
      button.addEventListener("click", () => {
        void handleCardTap(card);
      });
    } else {
      button.disabled = true;
    }
    els.hand.append(button);
  }
}

function shotCard() {
  if (!pendingShotCardId || !gameState) return null;
  return gameState.me.hand.find((card) => card.cardId === pendingShotCardId) || null;
}

function isShotNaming() {
  const card = shotCard();
  return Boolean(shotNaming && card && card.kind === "shot");
}

function enterShotNaming(cardId) {
  shotNaming = true;
  pendingShotCardId = cardId;
  targetPlayerId = null;
  renderPrompt();
}

function cancelShotNaming() {
  shotNaming = false;
  pendingShotCardId = null;
  targetPlayerId = null;
  renderPrompt();
}

async function handleCardTap(card) {
  if (busy) return;
  if (!isMyTurn()) return;
  if (!card.playable) return;
  if (isShotNaming()) {
    // 指名モード中は、SHOT をもう一度タップでやめる。それ以外をタップしたら即出す。
    if (card.kind === "shot") {
      cancelShotNaming();
      return;
    }
    await submitPlay(card.cardId, null);
    return;
  }
  if (card.kind === "shot") {
    enterShotNaming(card.cardId);
    return;
  }
  await submitPlay(card.cardId, null);
}

async function submitPlay(cardId, target) {
  if (busy) return;
  busy = true;
  const payload = {
    type: "play",
    cardId,
    expectedRevision: gameState.revision,
  };
  if (target) payload.target = target;

  try {
    const data = await request(`/api/rooms/${roomId}/actions`, {
      method: "POST",
      body: payload,
    });
    apply(data);
  } catch (error) {
    if (error.status === 409 && error.data && error.data.game) {
      // 状態が古くなっていたら、最新の状態で再描画する。
      apply({ room: roomState, you: youState, game: error.data.game });
      showError("状態が更新されていたため、最新の状態で再読み込みしました。");
    } else {
      showError(error.message);
    }
  } finally {
    busy = false;
  }
}

function renderLog() {
  const previous = els.log.dataset.count ? Number(els.log.dataset.count) : 0;
  els.log.replaceChildren();
  const events = logCollapsed ? gameState.events.slice(-1) : gameState.events;
  for (const event of events) {
    const item = el("li", `log-${event.kind}`);
    const icon = LOG_ICONS[event.kind] || LOG_ICONS.info;
    item.append(el("span", "log-icon", icon));
    item.append(el("span", "log-message", event.message));
    els.log.append(item);
  }
  els.log.dataset.count = String(gameState.events.length);
  if (gameState.events.length !== previous) {
    els.log.scrollTop = els.log.scrollHeight;
  }
}

function hideToast() {
  els.toast.classList.remove("show");
  setTimeout(() => {
    if (!els.toast.classList.contains("show")) els.toast.hidden = true;
  }, 250);
}

function renderToastHand(revealed) {
  const box = els.toastHand;
  if (!box) return;
  box.replaceChildren();
  const cards = Array.isArray(revealed) ? revealed : [];
  for (const entry of cards) {
    const cardEl = el("div", `small-card card-kind-${entry.kind} card-${entry.id}`);
    cardEl.append(makeCardFace(entry));
    box.append(cardEl);
  }
}

function renderLastRound() {
  const lastRound = gameState.lastRound;
  if (!lastRound) return;

  const key = [
    lastRound.round,
    lastRound.loserId,
    lastRound.sum,
    lastRound.chipsLeft,
    lastRound.gameOver,
  ].join(":");
  if (key === lastRoundKey) return;
  lastRoundKey = key;

  // ゲーム終了時は、勝者発表（renderResult）を優先する。
  if (lastRound.gameOver) return;

  els.toastTitle.textContent = lastRound.message || "";
  const bits = [];
  const sumValue = lastRound.sumAfter ?? lastRound.sum;
  if (sumValue !== null && sumValue !== undefined) bits.push(`合計 ${sumValue}`);
  if (lastRound.chipsLeft !== null && lastRound.chipsLeft !== undefined) {
    bits.push(`チップ残り ${lastRound.chipsLeft}`);
  }
  if (lastRound.eliminated) bits.push("脱落");
  if (lastRound.nextRound && lastRound.nextRound.firstPlayer) {
    bits.push(`次は ${playerName(lastRound.nextRound.firstPlayer)} さん`);
  }
  els.toastBody.textContent = bits.join(" ／ ");
  els.toast.classList.toggle("danger", Boolean(lastRound.eliminated));
  els.toast.hidden = false;
  els.toast.classList.remove("show");
  // いったん reflow してアニメーションをやり直す。
  void els.toast.offsetWidth;
  els.toast.classList.add("show");
  renderToastHand(lastRound.revealedHand);

  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    hideToast();
  }, 6000);
}

function renderResult() {
  const result = gameState.result;
  if (!result) {
    els.resultOverlay.hidden = true;
    return;
  }

  const winner = gameState.players.find(
    (player) => player.id === (result.winnerId || gameState.winnerId)
  );
  const winnerName = result.winnerName || (winner && winner.name) || "?";
  const isMe = winner && winner.isMe;
  els.resultWinner.textContent = isMe
    ? "あなたの勝ち！"
    : `${winnerName} さんの勝ち`;
  els.resultMessage.textContent = result.message || "ゲーム終了";

  els.resultStandings.replaceChildren();
  const standings = [...gameState.players].sort((a, b) => {
    if (Boolean(a.alive) !== Boolean(b.alive)) return a.alive ? -1 : 1;
    if (b.chips !== a.chips) return b.chips - a.chips;
    return a.seat - b.seat;
  });
  for (const player of standings) {
    const item = el(
      "li",
      "result-row" +
        (player.isMe ? " me" : "") +
        (player.id === (result.winnerId || gameState.winnerId) ? " winner" : "")
    );
    item.append(el("span", "result-name", player.isMe ? "あなた" : player.name));
    item.append(el("span", "result-chips", `チップ ${player.chips}`));
    if (!player.alive) item.append(el("span", "result-tag", "脱落"));
    if (player.id === (result.winnerId || gameState.winnerId)) {
      item.append(el("span", "result-tag win", "勝者"));
    }
    els.resultStandings.append(item);
  }

  els.resultOverlay.hidden = false;
}

function renderTurnArrow() {
  const arrow = els.turnArrow;
  if (!arrow) return;
  const arc = arrow.querySelector(".turn-hand") || arrow;
  const direction = gameState.direction;
  arc.classList.toggle("reverse", direction === -1);
  if (lastArrowDir !== direction) {
    lastArrowDir = direction;
    arc.classList.remove("turn-anim");
    void arc.offsetWidth;
    arc.classList.add("turn-anim");
  }
}

/* ------------------------------------------------------------------ 通信 */

async function refresh() {
  if (!session) return;
  try {
    const data = await request(`/api/rooms/${roomId}/state`);
    apply(data);
  } catch (error) {
    if (error.status === 403 || error.status === 404) {
      session = null;
      localStorage.removeItem(storageKey);
      showJoinPanel(error.message);
    } else {
      showError(error.message);
    }
  }
}

function showJoinPanel(message) {
  stopEvents();
  els.joinPanel.hidden = false;
  els.lobbyPanel.hidden = true;
  els.gamePanel.hidden = true;
  els.resultOverlay.hidden = true;
  setDocumentTitle(false);
  if (message) showError(message);
}

function stopEvents() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearTimeout(streamRetry);
  streamRetry = null;
}

async function connectEvents() {
  if (!session) return;
  stopEvents();
  let ticket;
  try {
    const data = await request(`/api/rooms/${roomId}/stream-ticket`, {
      method: "POST",
      body: {},
    });
    ticket = data.ticket;
  } catch (error) {
    if (error.status === 403 || error.status === 404) {
      session = null;
      localStorage.removeItem(storageKey);
      showJoinPanel(error.message);
      return;
    }
    streamRetry = setTimeout(connectEvents, 5000);
    return;
  }

  const source = new EventSource(
    `/api/rooms/${roomId}/events?ticket=${encodeURIComponent(ticket)}`
  );
  eventSource = source;
  source.addEventListener("state", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!busy) apply(payload);
  });
  source.addEventListener("closed", () => {
    source.close();
    if (eventSource === source) eventSource = null;
    showJoinPanel("ルームが終了しました。");
  });
  source.onerror = () => {
    source.close();
    if (eventSource === source) eventSource = null;
    // チケットは 1 回限りなので、取り直してから再接続する。
    streamRetry = setTimeout(connectEvents, 3000);
  };
}

/* ------------------------------------------------------------------ イベント */

els.leaveRoom.addEventListener("click", async () => {
  if (!session) return;
  if (!window.confirm("ルームから退出しますか？")) return;
  try {
    await request(`/api/rooms/${roomId}/leave`, { method: "POST", body: {} });
  } catch (error) {
    showError(error.message);
    return;
  }
  stopEvents();
  session = null;
  localStorage.removeItem(storageKey);
  location.href = "/";
});

els.joinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = els.joinName.value.trim();
  if (!name) return;
  try {
    const data = await request(`/api/rooms/${roomId}/join`, {
      method: "POST",
      body: { name },
    });
    saveSession({ token: data.token, playerId: data.playerId, name });
    els.joinPanel.hidden = true;
    await refresh();
    connectEvents();
  } catch (error) {
    showError(error.message);
  }
});

els.startButton.addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  els.startButton.disabled = true;
  try {
    const data = await request(`/api/rooms/${roomId}/start`, {
      method: "POST",
      body: {},
    });
    apply(data);
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
    if (roomState && !gameState) renderLobby();
  }
});

els.takeHost.addEventListener("click", async () => {
  if (!window.confirm("このルームのホスト権を引き継ぎますか？")) return;
  try {
    const data = await request(`/api/rooms/${roomId}/host`, {
      method: "POST",
      body: {},
    });
    apply(data);
  } catch (error) {
    showError(error.message);
  }
});

els.logToggle.addEventListener("click", () => {
  logCollapsed = !logCollapsed;
  renderLog();
});

els.toast.addEventListener("click", () => {
  clearTimeout(toastTimer);
  hideToast();
});

els.cancelShot.addEventListener("click", () => {
  cancelShotNaming();
});

// 指名モード中は、卓上の目立った席（targets）をタップして指名して出す。
// 空き部分や目立たない席をタップした場合は、指名モードをやめる。
els.players.addEventListener("click", (event) => {
  if (!shotNaming) return;
  const target = event.target instanceof Element ? event.target : null;
  const tile = target ? target.closest(".player") : null;
  const seatId = tile ? tile.dataset.playerId : null;
  if (tile && tile.classList.contains("targetable")) {
    void submitPlay(pendingShotCardId, seatId);
    return;
  }
  cancelShotNaming();
});

els.copyRoom.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(roomId);
    els.copyRoom.textContent = "コピー済み";
    setTimeout(() => {
      els.copyRoom.textContent = "コピー";
    }, 1500);
  } catch {
    window.prompt("このルーム ID を共有してください。", roomId);
  }
});

if (session) {
  refresh();
  connectEvents();
  // SSE が切れた場合の保険。通常は SSE で即時更新される。
  setInterval(() => {
    if (!busy && !document.hidden) refresh();
  }, 5000);
} else {
  showJoinPanel();
}
