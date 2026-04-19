"use strict";

// ========== 全局状态 ==========
let socket = null;
let myPlayerId = null;
let myRoomId = null;
let gameState = null;
let currentPhase = "waiting";
let myRole = null;
let isMyTurn = false;
let nightTarget = null;
let nightPoisonTarget = null;
let myVoteTarget = null;
let selectedPKTarget = null;
let timerInterval = null;
let voteTimerInterval = null;

// ========== 初始化 ==========
window.addEventListener("DOMContentLoaded", function () {
  const params = new URLSearchParams(location.search);
  myRoomId = params.get("room");
  myPlayerId = params.get("pid");

  if (!myRoomId || !myPlayerId) {
    alert("参数错误，请从首页进入");
    location.href = "/";
    return;
  }

  document.getElementById("disp-room-id").textContent = myRoomId;

  initSocket();
  initEventListeners();
});

function initSocket() {
  socket = io();

  socket.on("connect", () => {
    const el = document.getElementById("conn-status");
    if (el) { el.textContent = "🟢 已连接"; el.style.color = "#4caf50"; }
    socket.emit("join_room", { room_id: myRoomId, player_id: myPlayerId });
  });

  socket.on("disconnect", () => {
    const el = document.getElementById("conn-status");
    if (el) { el.textContent = "🔴 断开了"; el.style.color = "#e74c3c"; }
  });

  socket.on("connect_error", () => {
    const el = document.getElementById("conn-status");
    if (el) { el.textContent = "🔴 连接失败"; el.style.color = "#e74c3c"; }
  });

  // 游戏状态
  socket.on("room_state", onRoomState);
  socket.on("player_joined", onRoomState);
  socket.on("player_left", onRoomState);
  socket.on("player_online", onRoomState);

  // 游戏流程
  socket.on("game_started", onGameStarted);
  socket.on("night_start", onNightStart);
  socket.on("role_turn", onRoleTurn);
  socket.on("action_confirmed", onActionConfirmed);
  socket.on("player_action_done", onPlayerActionDone);
  socket.on("witch_decision", onWitchDecision);
  socket.on("night_result", onNightResult);
  socket.on("day_start", onDayStart);
  socket.on("speaking_start", onSpeakingStart);
  socket.on("ai_speech", onAISpeech);
  socket.on("speaking_end", onSpeakingEnd);
  socket.on("player_speech", onPlayerSpeech);
  socket.on("vote_start", onVoteStart);
  socket.on("vote_cast", onVoteCast);
  socket.on("vote_result", onVoteResult);
  socket.on("pk_vote_start", onPKVoteStart);
  socket.on("pk_result", onPKResult);
  socket.on("game_end", onGameEnd);
  socket.on("seer_result_private", onSeerResultPrivate);

  // 错误
  socket.on("error", d => showToast("错误: " + (d.message || "未知错误")));

  // 心跳保活
  setInterval(() => { if (socket && socket.connected) socket.emit("ping"); }, 30000);
}

function initEventListeners() {
  // 空实现，后续可扩展
}

// ========== 页面切换 ==========
function showPage(id) {
  document.querySelectorAll(".page").forEach(p => p.style.display = "none");
  const el = document.getElementById("page-" + id);
  if (el) el.style.display = "flex";
}

// ========== Socket 事件处理 ==========
function onRoomState(data) {
  gameState = data;
  if (data.phase === "waiting") {
    renderWaitingRoom(data);
  }
}

function onGameStarted(data) {
  gameState = data;
  showPage("night");
  startNightPhase(data);
}

function onNightStart(data) {
  gameState = data;
  startNightPhase(data);
}

function onRoleTurn(data) {
  gameState = data;
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  myRole = myPlayer ? myPlayer.role : null;

  if (data.role === "werewolf") {
    showWolfTurn(data);
  } else if (data.role === "seer") {
    showSeerTurn(data);
  } else if (data.role === "witch") {
    showWitchTurn(data);
  }
}

// 处理后端 action_confirmed 事件（deploy 后端使用此事件名）
function onActionConfirmed(data) {
  if (data.action === "check" && data.result) {
    showSeerResultModal(data.result);
  }
  if (data.action === "heal") {
    showToast("💧 解药已用！");
  }
  if (data.action === "kill") {
    showToast("🐺 击杀目标已确认");
  }
  if (data.action === "witch_done") {
    showToast("🧪 女巫决策已确认");
  }
}

function onPlayerActionDone(data) {
  if (data.state) gameState = data.state;

  if (data.action === "check" && data.result) {
    showSeerResultModal(data.result);
  }
  if (data.action === "heal") {
    showToast("💧 解药已用！");
  }
  if (data.action === "poison") {
    showToast("☠️ 毒药已用！");
  }
  if (data.action === "kill") {
    const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
    if (myPlayer && myPlayer.role === "werewolf") {
      showToast("🐺 击杀目标已确认");
    }
  }
  if (data.action === "witch_done") {
    showToast("女巫决策已记录");
  }
}

function onSeerResultPrivate(data) {
  if (data.state) gameState = data.state;
  if (data.seer_result) {
    showSeerResultModal(data.seer_result);
  }
}

// ========== 预言家查验弹窗 ==========
function showSeerResultModal(result) {
  // 移除已有的
  const existing = document.getElementById("seer-result-modal");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "seer-result-modal";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;display:flex;align-items:center;justify-content:center;";
  overlay.innerHTML = `
    <div style="background:#1a1a2e;border:2px solid #f39c12;border-radius:16px;padding:32px 40px;text-align:center;max-width:320px;">
      <div style="font-size:48px;margin-bottom:12px;">🔮</div>
      <div style="color:#f39c12;font-size:14px;margin-bottom:8px;font-weight:bold;">预言家查验结果</div>
      <div style="color:white;font-size:20px;font-weight:bold;margin-bottom:8px;">${result}</div>
      <div style="color:#888;font-size:12px;">仅你可见</div>
    </div>
  `;
  document.body.appendChild(overlay);

  // 点击任意处关闭
  overlay.addEventListener("click", function(e) {
    if (e.target === overlay) overlay.remove();
  });

  // 3秒后自动消失
  setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 3000);
}

function onWitchDecision(data) {
  if (data.state) gameState = data.state;
  showWitchPoisonTurn(data);
}

function onNightResult(data) {
  gameState = data.state;
  showNightResultPage(data);
}

function onDayStart(data) {
  gameState = data.state;
  startDayPhase(data);
}

function onSpeakingStart(data) {
  gameState = data.state;
  showSpeaking(data);
}

function onAISpeech(data) {
  if (data.state) gameState = data.state;
  appendMessage({
    speaker_id: null,
    name: data.player_name,
    role: data.role,
    role_color: data.role_color,
    content: data.speech,
    type: "speech",
  });
}

function onSpeakingEnd(data) {
  if (data.state) gameState = data.state;
  clearInterval(timerInterval);
  document.getElementById("speaking-area").style.display = "none";
  isMyTurn = false;
}

function onPlayerSpeech(data) {
  if (data.state) gameState = data.state;
  appendMessage({
    speaker_id: data.player_id,
    name: data.player_name,
    role: null,
    role_color: data.role_color,
    content: data.content,
    type: "speech",
  });
}

function onVoteStart(data) {
  gameState = data.state;
  startVotePhase(data);
}

function onVoteCast(data) {
  if (data.state) gameState = data.state;
  updateVoteUI(data.votes);
}

function onVoteResult(data) {
  gameState = data.state;
  showVoteResult(data);
}

function onPKVoteStart(data) {
  gameState = data.state;
  showPKPhase(data);
}

function onPKResult(data) {
  gameState = data.state;
  showPKResult(data);
}

function onGameEnd(data) {
  gameState = data.state;
  showEndGame(data);
}

// ========== 等待房间渲染 ==========
function renderWaitingRoom(state) {
  const grid = document.getElementById("waiting-players");
  if (!grid) return;
  grid.innerHTML = "";

  (state.players || []).forEach(p => {
    const div = document.createElement("div");
    div.className = "player-slot" + (p.alive ? "" : " dead");
    div.innerHTML = `
      <div class="player-avatar" style="background:${getRoleColor(p.role)}">${(p.name || "?").slice(0, 1)}</div>
      <div class="player-name">${p.name}${p.is_me ? " (我)" : ""}</div>
      <div class="player-badge">${p.is_ai ? "🤖" : "👤"}</div>
    `;
    grid.appendChild(div);
  });

  const startBtn = document.getElementById("btn-start");
  const addAIBtn = document.getElementById("btn-add-ai");
  const myPlayer = (state.players || []).find(p => p.is_me);
  const isOwner = myPlayer && state.players[0] && state.players[0].id === myPlayerId;

  if ((state.players || []).length >= 4) {
    startBtn.disabled = false;
    startBtn.classList.remove("disabled");
  } else {
    startBtn.disabled = true;
    startBtn.classList.add("disabled");
  }

  if (addAIBtn) {
    addAIBtn.style.display = (state.players || []).length < 8 && isOwner ? "inline-block" : "none";
  }

  showPage("waiting");
}

// ========== 夜间阶段 ==========
function startNightPhase(state) {
  showPage("night");
  nightTarget = null;
  nightPoisonTarget = null;
  clearInterval(timerInterval);
  clearInterval(voteTimerInterval);

  const overlay = document.getElementById("night-overlay");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  const instruction = document.getElementById("night-instruction");

  if (overlay) overlay.style.display = "flex";
  if (targetSection) targetSection.style.display = "none";
  if (closedEyes) closedEyes.style.display = "flex";
  if (instruction) instruction.textContent = "天黑请闭眼...";
  if (document.getElementById("night-phase-label")) {
    document.getElementById("night-phase-label").textContent = "🌙 夜间阶段";
  }
}

function showWolfTurn(data) {
  if (!gameState) return;
  const myPlayer = gameState.players.find(p => p.id === myPlayerId);
  const isWolf = myPlayer && myPlayer.role === "werewolf" && myPlayer.alive;

  document.getElementById("night-phase-label").textContent = "🌙 狼人请睁眼";

  const instrEl = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");

  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";
  if (instrEl) {
    if (isWolf && data.teammates && data.teammates.length > 0) {
      const teammateNames = data.teammates.map(t => t.name).join("、");
      instrEl.innerHTML = `<span style="color:#ff8888">🐺 队友：${teammateNames}</span><br><small>点击下方玩家选择击杀目标</small>`;
    } else {
      instrEl.textContent = data.instruction || "狼人请选择今晚击杀的目标";
    }
  }
  if (document.getElementById("night-prompt-text")) {
    document.getElementById("night-prompt-text").textContent = "狼人请选择今晚击杀的目标：";
  }

  if (isWolf) {
    renderNightTargets(data.targets || [], "kill", data.teammates || []);
  } else {
    const container = document.getElementById("night-targets");
    if (container) container.innerHTML = '<p class="waiting-msg">狼人正在选择...</p>';
  }
}

function showSeerTurn(data) {
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const isSeer = myPlayer && myPlayer.role === "seer" && myPlayer.alive;

  document.getElementById("night-phase-label").textContent = "⭐ 预言家请睁眼";
  const instrEl = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");

  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";
  if (instrEl) instrEl.textContent = data.instruction || "预言家请选择要查验的目标";
  if (document.getElementById("night-prompt-text")) {
    document.getElementById("night-prompt-text").textContent = "查验谁的身份？";
  }

  if (isSeer) {
    renderNightTargets(data.targets || [], "check");
  } else {
    const container = document.getElementById("night-targets");
    if (container) container.innerHTML = '<p class="waiting-msg">预言家正在查验...</p>';
  }
}

function showWitchTurn(data) {
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const isWitch = myPlayer && myPlayer.role === "witch" && myPlayer.alive;

  document.getElementById("night-phase-label").textContent = "🧪 女巫请睁眼";
  const killInfo = data.kill_target ? `狼人杀的是 ${data.kill_target}！` : "今晚无人死亡";
  const instrEl = document.getElementById("night-instruction");
  if (instrEl) instrEl.innerHTML = killInfo + "<br><small>选择用药或跳过</small>";
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";

  if (document.getElementById("night-prompt-text")) {
    document.getElementById("night-prompt-text").textContent = killInfo;
  }

  const container = document.getElementById("night-targets");
  if (!container) return;

  if (isWitch) {
    const alivePlayers = (gameState.players || []).filter(p => p.alive && p.id !== myPlayerId);
    let html = `<p class="witch-option">`;

    if (data.kill_target && data.can_heal) {
      html += `<button class="btn-witch" id="witch-heal-btn" onclick="witchHeal('${data.kill_target}')">💧 救人（${data.kill_target}）</button> `;
    }
    html += `</p>`;
    html += `<p class="witch-option-label">选择毒杀目标（可选）：</p>`;
    html += `<div class="players-row-small" style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:16px;">`;
    alivePlayers.forEach(t => {
      html += `<div class="player-chip ${nightPoisonTarget === t.name ? 'selected' : ''}" id="poison-${t.id}" onclick="selectPoisonTarget('${t.name}', '${t.id}')">${t.name}</div>`;
    });
    html += `</div>`;
    html += `<button class="btn-primary" id="witch-confirm-btn" onclick="confirmWitchAction()">确认</button>`;
    container.innerHTML = html;
  } else {
    container.innerHTML = '<p class="waiting-msg">女巫正在决策...</p>';
  }
}

function showWitchPoisonTurn(data) {
  // 单独的毒药选择轮次（如果后端拆分了）
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  if (myPlayer && myPlayer.role === "witch" && myPlayer.alive) {
    const instrEl = document.getElementById("night-instruction");
    if (instrEl) instrEl.textContent = "是否对某人使用毒药？";
  }
}

function renderNightTargets(targets, actionType, teammates) {
  const container = document.getElementById("night-targets");
  if (!container) return;
  if (typeof targets === "string") return; // 女巫场景跳过
  container.innerHTML = "";
  nightTarget = null;
  nightPoisonTarget = null;

  const confirmBtn = document.getElementById("night-confirm-btn");
  if (confirmBtn) confirmBtn.disabled = true;

  const teammateIds = (teammates || []).map(t => t.id);

  targets.forEach(t => {
    const div = document.createElement("div");
    const isTeammate = teammateIds.includes(t.id);
    div.className = "player-chip" + (isTeammate ? " wolf-teammate" : "");
    div.textContent = (isTeammate ? "🐺 " : "") + t.name;
    div.addEventListener("click", () => selectNightTarget(t.name, div, actionType));
    container.appendChild(div);
  });
}

function selectNightTarget(name, el, type) {
  nightTarget = name;
  document.querySelectorAll("#night-targets .player-chip").forEach(c => c.classList.remove("selected"));
  el.classList.add("selected");
  const btn = document.getElementById("night-confirm-btn");
  if (btn) btn.disabled = false;
}

function selectPoisonTarget(name, id) {
  nightPoisonTarget = name;
  document.querySelectorAll(".player-chip[id^='poison-']").forEach(c => c.classList.remove("selected"));
  const el = document.getElementById("poison-" + id);
  if (el) el.classList.add("selected");
}

function confirmNightAction() {
  if (!nightTarget) return;
  socket.emit("night_action", { action: "kill", target: nightTarget });
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  const instrEl = document.getElementById("night-instruction");
  if (targetSection) targetSection.style.display = "none";
  if (closedEyes) closedEyes.style.display = "flex";
  if (instrEl) instrEl.textContent = "等待其他人...";
  nightTarget = null;
}

function witchHeal(target) {
  socket.emit("night_action", { action: "heal", target: target });
  showToast("💧 解药已使用！");
  const btn = document.getElementById("witch-heal-btn");
  if (btn) btn.disabled = true;
}

function confirmWitchAction() {
  socket.emit("night_action", {
    action: "poison",
    target: nightPoisonTarget || null,
  });
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  const instrEl = document.getElementById("night-instruction");
  if (targetSection) targetSection.style.display = "none";
  if (closedEyes) closedEyes.style.display = "flex";
  if (instrEl) instrEl.textContent = "等待结算...";
  nightPoisonTarget = null;
  nightTarget = null;
  showToast("🧪 女巫决策已确认");
}

// ========== 夜间结果页 ==========
function showNightResultPage(data) {
  showPage("night-result");
  const container = document.getElementById("night-result-content");
  if (!container) return;

  let html = `<div class="night-result-card">`;
  html += `<h2 class="result-title">🌅 天亮了</h2>`;

  if (data.dead && data.dead.length > 0) {
    html += `<p class="dead-label">昨夜死亡：</p>`;
    data.dead.forEach((name, idx) => {
      const roleName = (data.dead_roles && data.dead_roles[name]) || "";
      html += `<div class="dead-reveal-card" data-role="${roleName}" id="night-card-${idx}">
        <div class="card-front">${name}</div>
        <div class="card-back ${getGlowClass(roleName)}">${roleName}</div>
      </div>`;
    });
  } else {
    html += `<p class="no-dead">今夜平安夜，无人死亡</p>`;
  }

  html += `</div>`;
  container.innerHTML = html;

  // 用 requestAnimationFrame 触发动画（不用 script 注入）
  requestAnimationFrame(() => {
    document.querySelectorAll(".dead-reveal-card").forEach((card, i) => {
      setTimeout(() => card.classList.add("flipped"), 300 + i * 600);
    });
  });
}

// ========== 白天阶段 ==========
function startDayPhase(data) {
  showPage("day");
  document.getElementById("day-num").textContent = `第${data.day}天`;
  document.getElementById("day-dead-reveal").style.display = "none";
  document.getElementById("speaking-area").style.display = "none";
  document.getElementById("vote-area").style.display = "none";
  document.getElementById("pk-area").style.display = "none";
  clearInterval(timerInterval);
  clearInterval(voteTimerInterval);
  renderMessages(gameState ? gameState.messages : []);
  renderPlayersMini(gameState ? gameState.players : []);
}

function showSpeaking(data) {
  isMyTurn = (data.speaker_id === myPlayerId);
  document.getElementById("phase-badge").textContent = isMyTurn ? "⏳ 轮到你发言！" : "💬 发言中";

  document.getElementById("speaking-area").style.display = "block";
  document.getElementById("vote-area").style.display = "none";
  document.getElementById("pk-area").style.display = "none";

  const card = document.getElementById("speaker-card");
  if (card) {
    card.innerHTML = `
      <div class="speaker-name" style="color:${data.role_color || '#333'}">${data.speaker_name || "???"}</div>
      <div class="speaker-role">${data.role_name || ""}</div>
    `;
    card.className = "speaker-card" + (data.role_color ? " speaker-card-glow" : "");
  }

  const inputWrap = document.getElementById("speech-input-wrap");
  if (inputWrap) inputWrap.style.display = isMyTurn ? "flex" : "none";
  if (isMyTurn) {
    const input = document.getElementById("speech-input");
    if (input) { input.value = ""; input.focus(); }
  }

  startSpeakingTimer(data.timer || 60);
  renderPlayersMini(gameState ? gameState.players : []);
}

function startSpeakingTimer(seconds) {
  clearInterval(timerInterval);
  const bar = document.getElementById("timer-bar");
  const num = document.getElementById("timer-num");
  if (!bar || !num) return;
  const total = seconds;
  bar.style.width = "100%";
  bar.style.background = "#4caf50";
  num.textContent = total;

  timerInterval = setInterval(() => {
    const cur = parseInt(num.textContent);
    if (cur <= 1) {
      clearInterval(timerInterval);
      if (isMyTurn) sendSpeech();
      return;
    }
    const next = cur - 1;
    num.textContent = next;
    bar.style.width = ((next / total) * 100) + "%";
    if (next <= 10) {
      bar.style.background = "#e74c3c";
      num.style.color = "#e74c3c";
    }
  }, 1000);
}

function sendSpeech() {
  clearInterval(timerInterval);
  const input = document.getElementById("speech-input");
  const content = input ? input.value.trim() : "";
  socket.emit("speech", { content: content || "（过）" });
  if (input) input.value = "";
  document.getElementById("speaking-area").style.display = "none";
  isMyTurn = false;
}

// ========== 投票阶段 ==========
function startVotePhase(data) {
  document.getElementById("phase-badge").textContent = "🗳️ 投票阶段";
  document.getElementById("speaking-area").style.display = "none";
  document.getElementById("vote-area").style.display = "block";
  document.getElementById("pk-area").style.display = "none";
  document.getElementById("vote-confirm-wrap").style.display = "none";
  myVoteTarget = null;

  const container = document.getElementById("vote-players");
  if (!container) return;
  container.innerHTML = "";

  const alive = (gameState ? gameState.players : []).filter(p => p.alive && p.id !== myPlayerId);
  alive.forEach(p => {
    const div = document.createElement("div");
    div.className = "vote-player-chip";
    div.id = "vote-" + p.id;
    div.innerHTML = `<div class="vp-name">${p.name}</div>`;
    div.addEventListener("click", () => selectVote(p.id, p.name));
    container.appendChild(div);
  });

  startVoteTimer(data.timer || 30);
  renderPlayersMini(gameState ? gameState.players : []);
}

function selectVote(playerId, name) {
  myVoteTarget = name;
  document.querySelectorAll(".vote-player-chip").forEach(c => c.classList.remove("selected"));
  const el = document.getElementById("vote-" + playerId);
  if (el) el.classList.add("selected");
  const confirmWrap = document.getElementById("vote-confirm-wrap");
  if (confirmWrap) confirmWrap.style.display = "block";
}

function sendVote() {
  if (!myVoteTarget) return;
  socket.emit("vote", { target: myVoteTarget });
  const confirmWrap = document.getElementById("vote-confirm-wrap");
  if (confirmWrap) confirmWrap.innerHTML = `<p class="voted-confirm">已投给 ${myVoteTarget}</p>`;
}

function updateVoteUI(votes) {
  if (!votes) return;
  // 清除旧的票数显示
  document.querySelectorAll(".vote-player-chip .vp-tally").forEach(el => el.remove());

  const counts = {};
  Object.values(votes).forEach(n => { counts[n] = (counts[n] || 0) + 1; });

  for (const [voterId, votedName] of Object.entries(votes)) {
    const el = document.querySelector(`[data-voted="${votedName}"]`);
    if (el) {
      const span = document.createElement("span");
      span.className = "vp-tally";
      span.textContent = " " + (counts[votedName] || 0) + "票";
      el.querySelector(".vp-name").appendChild(span);
    }
  }
}

function startVoteTimer(seconds) {
  clearInterval(voteTimerInterval);
  const bar = document.getElementById("vote-timer-bar");
  const num = document.getElementById("vote-timer-num");
  if (!bar || !num) return;
  const total = seconds;
  bar.style.width = "100%";
  bar.style.background = "#e74c3c";
  num.textContent = total;

  voteTimerInterval = setInterval(() => {
    const cur = parseInt(num.textContent);
    if (cur <= 1) { clearInterval(voteTimerInterval); return; }
    const next = cur - 1;
    num.textContent = next;
    bar.style.width = ((next / total) * 100) + "%";
  }, 1000);
}

function showVoteResult(data) {
  document.getElementById("speaking-area").style.display = "none";
  document.getElementById("vote-area").style.display = "none";
  document.getElementById("pk-area").style.display = "none";
  clearInterval(voteTimerInterval);

  if (data.dead && data.dead.length > 0) {
    const html = `<div class="result-card">
      <h3>${data.result || "投票结果"}</h3>
      ${(data.dead || []).map(n => `<div class="dead-badge">${n}</div>`).join("")}
    </div>`;
    appendToDayArea(html);
  } else if (data.pk_candidates && data.pk_candidates.length > 0) {
    appendToDayArea(`<p class="pk-hint">⚔️ 平票！进入 PK 投票：${data.pk_candidates.join(" vs ")}</p>`);
  }

  renderMessages(gameState ? gameState.messages : []);
}

// ========== PK 投票 ==========
function showPKPhase(data) {
  document.getElementById("phase-badge").textContent = "⚔️ PK投票";
  document.getElementById("vote-area").style.display = "none";
  document.getElementById("speaking-area").style.display = "none";
  document.getElementById("pk-area").style.display = "block";
  selectedPKTarget = null;
  document.getElementById("pk-vote-confirm-wrap").style.display = "none";

  const container = document.getElementById("pk-players");
  if (!container) return;
  container.innerHTML = "";
  (data.candidates || []).forEach(name => {
    const div = document.createElement("div");
    div.className = "pk-chip";
    div.id = "pk-" + name;
    div.textContent = name;
    div.addEventListener("click", () => selectPKTarget(name));
    container.appendChild(div);
  });

  renderPlayersMini(gameState ? gameState.players : []);
}

function selectPKTarget(name) {
  selectedPKTarget = name;
  document.querySelectorAll(".pk-chip").forEach(c => c.classList.remove("selected"));
  const el = document.getElementById("pk-" + name);
  if (el) el.classList.add("selected");
  const confirmWrap = document.getElementById("pk-vote-confirm-wrap");
  if (confirmWrap) confirmWrap.style.display = "block";
}

function sendPKVote() {
  if (!selectedPKTarget) return;
  socket.emit("vote", { target: selectedPKTarget });
  const confirmWrap = document.getElementById("pk-vote-confirm-wrap");
  if (confirmWrap) confirmWrap.innerHTML = `<p class="voted-confirm">已投给 ${selectedPKTarget}</p>`;
}

function showPKResult(data) {
  document.getElementById("pk-area").style.display = "none";
  if (data.dead && data.dead.length > 0) {
    appendToDayArea(`<p class="pk-result">⚔️ ${data.dead.join("、")} 在PK投票中被出局！</p>`);
  }
  renderMessages(gameState ? gameState.messages : []);
}

// ========== 游戏结束 ==========
function showEndGame(data) {
  showPage("end");
  const container = document.getElementById("end-content");
  if (!container) return;
  const isGood = data.winner === "good";
  const players = data.state ? data.state.players : [];

  container.innerHTML = `
    <div class="end-card">
      <h1 class="end-title ${isGood ? "good-win" : "wolf-win"}">
        ${isGood ? "🎉 好人大获全胜！" : "🐺 狼人胜利！"}
      </h1>
      <p class="end-sub">${data.winner_name || ""}</p>
      <div class="all-roles">
        ${players.map(p => `
          <div class="role-reveal-card ${p.alive ? "alive" : "dead"}">
            <div class="rrc-avatar" style="background:${p.role_color || "#999"}">${(p.name || "?").slice(0, 1)}</div>
            <div class="rrc-name">${p.name}</div>
            <div class="rrc-role" style="color:${p.role_color || "#999"}">${p.role_name || p.role || ""}</div>
            <div class="rrc-status">${p.alive ? "存活" : "死亡"}</div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ========== 消息渲染 ==========
function renderMessages(messages) {
  const container = document.getElementById("messages-area");
  if (!container) return;
  container.innerHTML = "";
  (messages || []).forEach(m => {
    if (m.type === "speech" || m.type === "system") {
      appendMessage(m, false);
    }
  });
  container.scrollTop = container.scrollHeight;
}

function appendMessage(msg, scroll = true) {
  const container = document.getElementById("messages-area");
  if (!container) return;
  const div = document.createElement("div");
  const isMe = msg.speaker_id === myPlayerId;

  if (msg.type === "system") {
    div.className = "msg-system";
    div.textContent = msg.content;
  } else {
    div.className = `msg-bubble-wrap ${isMe ? "msg-me" : "msg-other"}`;
    div.innerHTML = `
      <div class="msg-name" style="color:${msg.role_color || "#999"}">${msg.name || "???"}</div>
      <div class="msg-bubble" style="${isMe ? "background:#667eea" : "background:rgba(255,255,255,0.1)"}">
        ${escapeHtml(msg.content || "")}
      </div>
    `;
  }

  container.appendChild(div);
  if (scroll) container.scrollTop = container.scrollHeight;
}

function appendToDayArea(html) {
  const dayArea = document.getElementById("day-dead-reveal");
  if (dayArea) {
    dayArea.style.display = "block";
    dayArea.innerHTML += html;
  }
}

// ========== 浮动玩家列表 ==========
function renderPlayersMini(players) {
  const container = document.getElementById("player-list-float");
  if (!container) return;
  container.innerHTML = "";
  (players || []).forEach(p => {
    const div = document.createElement("div");
    div.className = "pm-chip" + (p.alive ? "" : " dead") + (p.id === myPlayerId ? " me" : "");
    div.innerHTML = `<span class="pm-dot" style="background:${p.role_color || "#999"}"></span>${p.name}`;
    container.appendChild(div);
  });
}

// ========== 工具函数 ==========
function getRoleColor(role) {
  const colors = { werewolf: "#e74c3c", seer: "#f39c12", witch: "#9b59b6", villager: "#3498db" };
  return colors[role] || "#95a5a6";
}

function getGlowClass(roleName) {
  if (roleName === "狼人") return "glow-red";
  if (roleName === "预言家") return "glow-gold";
  if (roleName === "女巫") return "glow-purple";
  return "glow-blue";
}

function showToast(msg) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2500);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ========== API 辅助 ==========
async function addAI() {
  try {
    await fetch(`/api/room/${myRoomId}/add-ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: null }),
    });
    showToast("AI已添加");
  } catch(e) {
    showToast("添加AI失败");
  }
}

async function startGame() {
  try {
    const res = await fetch(`/api/room/${myRoomId}/start`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) showToast(data.error || "无法开始");
  } catch(e) {
    showToast("开始游戏失败");
  }
}

function copyRoomId() {
  navigator.clipboard.writeText(myRoomId).then(() => showToast("房间号已复制！"));
}

function copyLink() {
  navigator.clipboard.writeText(location.origin + `/?room=${myRoomId}`).then(() => showToast("链接已复制！"));
}

// ========== 规则弹窗 ==========
function openRules() {
  document.getElementById("modal-rules").classList.add("open");
}
function closeRules() {
  document.getElementById("modal-rules").classList.remove("open");
}
