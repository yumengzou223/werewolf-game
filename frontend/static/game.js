"use strict";

// ========== 全局状态 ==========
let socket = null;
let myPlayerId = null;
let myRoomId = null;
let gameState = null;
let myRole = null;
let isMyTurn = false;
let roleBadgeShown = false;
let nightTarget = null;
let nightPoisonTarget = null;
let witchHealDone = false;   // 本夜是否已用解药
let myVoteTarget = null;
let selectedPKTarget = null;
let timerInterval = null;
let voteTimerInterval = null;
const wolfTeammateIds = new Set(); // 狼队友 id 集合，游戏开始后填充

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

  // 房间/等待
  socket.on("room_state", onRoomState);
  socket.on("player_joined", onRoomState);
  socket.on("player_left", onRoomState);
  socket.on("player_online", onRoomState);

  // 游戏主流程
  socket.on("game_started", onGameStarted);
  socket.on("night_start", onNightStart);
  socket.on("role_turn", onRoleTurn);
  socket.on("action_confirmed", onActionConfirmed);
  socket.on("seer_result_private", onSeerResultPrivate);
  socket.on("night_result", onNightResult);
  socket.on("last_words_start", onLastWordsStart);
  socket.on("your_last_words", onYourLastWords);
  socket.on("day_start", onDayStart);
  socket.on("discussion_start", onDiscussionStart);
  socket.on("speaking_start", onSpeakingStart);
  socket.on("speaking_end", onSpeakingEnd);
  socket.on("player_speech", onPlayerSpeech);
  socket.on("vote_start", onVoteStart);
  socket.on("vote_cast", onVoteCast);
  socket.on("vote_result", onVoteResult);
  socket.on("pk_discussion_start", onPKDiscussionStart);
  socket.on("pk_vote_start", onPKVoteStart);
  socket.on("game_end", onGameEnd);
  socket.on("wolf_teammate_action", onWolfTeammateAction);
  socket.on("wolf_chat", onWolfChat);

  socket.on("error", d => showToast("错误: " + (d.message || "未知错误")));

  // 心跳
  setInterval(() => { if (socket && socket.connected) socket.emit("ping"); }, 25000);
}

// ========== 页面切换 ==========
function showPage(id) {
  document.querySelectorAll(".page").forEach(p => p.style.display = "none");
  const el = document.getElementById("page-" + id);
  if (el) el.style.display = "flex";
}

// ========== Socket 事件处理 ==========
function onRoomState(data) {
  const state = data.state || data;
  gameState = state;
  if (state.phase === "waiting") {
    renderWaitingRoom(state);
  }
}

function onGameStarted(data) {
  gameState = data.state || data;
  updateMyRole();
  roleBadgeShown = false;
  showPage("night");
  renderNightIdle("游戏开始，天黑请闭眼...");
  showRoleBadgeOnce();
}

function onNightStart(data) {
  gameState = data.state || data;
  showPage("night");
  renderNightIdle("🌙 天黑请闭眼...");
  nightTarget = null;
  nightPoisonTarget = null;
  witchHealDone = false;
  const wolfChat = document.getElementById("wolf-chat");
  if (wolfChat) wolfChat.style.display = "none";
}

function onRoleTurn(data) {
  gameState = data.state || data;
  updateMyRole();
  const role = data.role;

  if (role === "werewolf") {
    // 记录狼队友 id，用于投票阶段识别
    (data.teammates || []).forEach(t => wolfTeammateIds.add(t.id));
    showWolfTurn(data);
  } else if (role === "seer") {
    showSeerTurn(data);
  } else if (role === "witch") {
    showWitchTurn(data);
  }
}

function onActionConfirmed(data) {
  if (data.action === "kill") showToast("🐺 击杀目标已确认，等待结算...");
  if (data.action === "heal") showToast("💧 解药已使用！");
  if (data.action === "poison") showToast("☠️ 毒药已使用！");
  // 女巫操作后恢复等待状态
  if (data.action === "heal" || data.action === "poison") {
    renderNightIdle("等待夜间结算...");
  }
}

function onSeerResultPrivate(data) {
  if (data.seer_result) showSeerResultModal(data.seer_result);
}

function onNightResult(data) {
  gameState = data.state || data;
  // 只有当前在夜间页时才切换到结算页，避免白天阶段被意外跳回
  const nightPage = document.getElementById("page-night");
  const isOnNightPage = nightPage && nightPage.style.display !== "none";
  if (isOnNightPage) {
    showNightResultPage(data);
  } else {
    console.warn("[onNightResult] 忽略：当前不在夜间页，phase=", gameState.phase);
  }
}

function onLastWordsStart(data) {
  gameState = data.state || data;
  // 切换到白天发言页（如果还在夜晚页就先切换）
  const dayPage = document.getElementById("page-day");
  if (dayPage && dayPage.style.display === "none") {
    showPage("day");
    document.getElementById("day-num").textContent = `第${gameState.day || 1}天`;
  }

  const isMe = data.player_id === myPlayerId;
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = `💬 ${data.player_name} 发表遗言`;

  renderMessages(gameState.messages || []);
  renderPlayersMini(gameState.players || []);

  if (!isMe) {
    // 展示等待遗言的提示
    const speakArea = document.getElementById("speaking-area");
    if (speakArea) {
      speakArea.style.display = "block";
      const card = document.getElementById("speaker-card");
      if (card) {
        card.innerHTML = `
          <div class="speaker-name" style="color:#ccc">${data.player_name}</div>
          <div class="speaker-role">【遗言】</div>
        `;
      }
      const inputWrap = document.getElementById("speech-input-wrap");
      if (inputWrap) inputWrap.style.display = "none";
      startSpeakTimer(data.timer || 30, false);
    }
  }
}

function onYourLastWords(data) {
  // 我死亡了，可以发遗言
  showPage("day");
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = "⚰️ 你已死亡 - 发表遗言";

  const speakArea = document.getElementById("speaking-area");
  if (speakArea) {
    speakArea.style.display = "block";
    const card = document.getElementById("speaker-card");
    if (card) {
      card.innerHTML = `<div class="speaker-name" style="color:#e74c3c">你已死亡</div>
        <div class="speaker-role">发表你的遗言（${data.timer||30}秒）</div>`;
    }
    const inputWrap = document.getElementById("speech-input-wrap");
    if (inputWrap) {
      inputWrap.style.display = "flex";
      const input = document.getElementById("speech-input");
      if (input) { input.value = ""; input.placeholder = "输入遗言..."; input.focus(); }
    }
    isMyTurn = true;
    startSpeakTimer(data.timer || 30, true);
  }
}

function onDayStart(data) {
  gameState = data.state || data;
  showPage("day");
  document.getElementById("day-num").textContent = `第${data.day || gameState.day}天`;
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = "☀️ 白天阶段";
  hideAllDayAreas();
  clearTimers();
  renderMessages(gameState.messages || []);
  renderPlayersMini(gameState.players || []);
}

function onDiscussionStart(data) {
  gameState = data.state || data;
  renderMessages(gameState.messages || []);
  renderPlayersMini(gameState.players || []);
}

function onSpeakingStart(data) {
  gameState = data.state || data;
  isMyTurn = (data.speaker_id === myPlayerId);

  // 确保在白天页，如果不在就先切过去
  const dayPage = document.getElementById("page-day");
  const isOnDayPage = dayPage && dayPage.style.display !== "none";
  if (!isOnDayPage) {
    showPage("day");
    document.getElementById("day-num").textContent = `第${gameState.day || 1}天`;
  }

  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = data.is_pk ? "⚔️ PK发言" : (isMyTurn ? "⏳ 轮到你发言！" : "💬 发言中");

  const speakArea = document.getElementById("speaking-area");
  const voteArea = document.getElementById("vote-area");
  const pkArea = document.getElementById("pk-area");
  if (speakArea) speakArea.style.display = "block";
  if (voteArea) voteArea.style.display = "none";
  if (pkArea) pkArea.style.display = "none";

  const card = document.getElementById("speaker-card");
  if (card) {
    card.innerHTML = `<div class="speaker-name" style="color:#fff">${data.speaker_name||"???"}</div>`;
  }

  const inputWrap = document.getElementById("speech-input-wrap");
  if (inputWrap) {
    inputWrap.style.display = isMyTurn ? "flex" : "none";
    if (isMyTurn) {
      const input = document.getElementById("speech-input");
      if (input) { input.value = ""; input.focus(); }
    }
  }

  startSpeakTimer(data.timer || 60, isMyTurn);
  // 通知后端前端已准备好，后端从此刻开始计时
  if (isMyTurn) socket.emit("speech_ready", {});
  renderPlayersMini(gameState.players || []);

  // 发言进度
  const progress = document.getElementById("speech-progress");
  if (progress && data.total) {
    progress.textContent = `${data.turn_index + 1} / ${data.total}`;
  }
}

function onSpeakingEnd(data) {
  if (data.state) gameState = data.state;
  clearTimers();
  const speakArea = document.getElementById("speaking-area");
  if (speakArea) speakArea.style.display = "none";
  isMyTurn = false;
  renderMessages(gameState.messages || []);
}

function onPlayerSpeech(data) {
  if (data.state) gameState = data.state;
  appendMessage({
    speaker_id: data.player_id,
    name: data.player_name,
    role_color: data.role_color,
    content: data.content,
    type: "speech",
  });
}

function onVoteStart(data) {
  gameState = data.state || data;
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = "🗳️ 投票阶段";
  startVotePhase(data);
}

function onVoteCast(data) {
  if (data.state) gameState = data.state;
  updateVoteTally(data.votes);
  // 如果我是狼人，显示队友投了谁
  if (myRole === "werewolf" && data.player_id !== myPlayerId) {
    const isTeammate = wolfTeammateIds.has(data.player_id);
    if (isTeammate) {
      showToast(`🐺 ${data.player_name} 投票给了 ${data.voted_name}`);
      // 高亮被队友投票的目标
      document.querySelectorAll(".vote-player-chip").forEach(c => c.classList.remove("wolf-voted"));
      const chips = document.querySelectorAll(".vote-player-chip");
      chips.forEach(c => {
        const nameEl = c.querySelector(".vp-name");
        if (nameEl && nameEl.textContent === data.voted_name) {
          c.classList.add("wolf-voted");
          let tag = c.querySelector(".vp-wolf-tag");
          if (!tag) {
            tag = document.createElement("span");
            tag.className = "vp-wolf-tag";
            c.appendChild(tag);
          }
          tag.textContent = `🐺 队友投此`;
        }
      });
    }
  }
}

function onVoteResult(data) {
  gameState = data.state || data;
  clearTimers();
  showVoteResultUI(data);
}

function onPKDiscussionStart(data) {
  gameState = data.state || data;
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = `⚔️ PK：${data.candidates.join(" vs ")}`;
  hideAllDayAreas();
  renderMessages(gameState.messages || []);
}

function onPKVoteStart(data) {
  gameState = data.state || data;
  const badge = document.getElementById("phase-badge");
  if (badge) badge.textContent = "⚔️ PK投票";
  showPKVotePhase(data);
}

function onGameEnd(data) {
  gameState = data.state || data;
  showEndGame(data);
}

function onWolfTeammateAction(data) {
  showToast(`🐺 ${data.player_name} 已选择击杀 ${data.target}`);
  document.querySelectorAll("#night-targets .player-chip").forEach(c => {
    if (c.textContent.includes(data.target)) {
      c.classList.add("teammate-selected");
    }
  });
}

function sendWolfChat() {
  const input = document.getElementById("wolf-chat-input");
  if (!input) return;
  const msg = input.value.trim();
  if (!msg) return;
  socket.emit("wolf_chat", { content: msg });
  input.value = "";
}

function onWolfChat(data) {
  const container = document.getElementById("wolf-chat-messages");
  if (!container) return;
  const isMe = data.player_id === myPlayerId;
  const div = document.createElement("div");
  div.className = "wolf-chat-msg" + (isMe ? " me" : "");
  div.innerHTML = `<span class="wolf-msg-name">${escapeHtml(data.player_name)}：</span>${escapeHtml(data.content)}`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

// ========== 等待房间 ==========
function renderWaitingRoom(state) {
  const grid = document.getElementById("waiting-players");
  if (!grid) return;
  grid.innerHTML = "";

  (state.players || []).forEach(p => {
    const div = document.createElement("div");
    div.className = "player-slot";
    div.innerHTML = `
      <div class="player-avatar" style="background:${getRoleColor(null)}">${(p.name||"?").slice(0,1)}</div>
      <div class="player-name">${p.name}${p.is_me ? " (我)" : ""}</div>
      <div class="player-badge">${p.is_ai ? "🤖" : "👤"}</div>
    `;
    grid.appendChild(div);
  });

  const startBtn = document.getElementById("btn-start");
  const addAIBtn = document.getElementById("btn-add-ai");
  const cnt = (state.players || []).length;
  const isOwner = (state.players || [])[0] && state.players[0].id === myPlayerId;

  if (startBtn) {
    startBtn.disabled = cnt < 4;
    startBtn.classList.toggle("disabled", cnt < 4);
  }
  if (addAIBtn) {
    // 后端最大人数：6人局上限6，8人局上限8；用6作为最小满员判断
    // 实际由后端控制，这里只做乐观判断：满6人时禁用（不隐藏，保留提示）
    // 满6人即禁用（后端6人局上限），防止继续添加
    const full = cnt >= 6;
    addAIBtn.style.display = "inline-block";
    addAIBtn.disabled = full;
    addAIBtn.classList.toggle("disabled", full);
  }

  showPage("waiting");
}

// ========== 夜间阶段 ==========
function renderNightIdle(msg) {
  const label = document.getElementById("night-phase-label");
  const instr = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");

  if (label) label.textContent = "🌙 夜间阶段";
  if (instr) instr.textContent = msg || "天黑请闭眼...";
  if (targetSection) targetSection.style.display = "none";
  if (closedEyes) closedEyes.style.display = "flex";
}

function showRoleBadgeOnce() {
  if (roleBadgeShown) return;
  const myP = gameState && gameState.players ? gameState.players.find(p => p.id === myPlayerId) : null;
  if (!myP || !myP.role) return;

  const badge = document.getElementById("my-role-badge");
  const nameEl = document.getElementById("my-role-name");
  const hintEl = document.getElementById("my-role-hint");
  if (!badge || !nameEl) return;

  nameEl.textContent = myP.role_name || myP.role;
  nameEl.style.color = myP.role_color || "#f39c12";
  if (hintEl) {
    const hints = {
      werewolf: "你的任务：每晚击杀一名玩家，隐藏身份",
      seer: "你的任务：每晚查验一名玩家的身份",
      witch: "你有解药和毒药各一瓶，善用它们",
      villager: "你是村民，发言推理，找出狼人",
    };
    hintEl.textContent = hints[myP.role] || "";
  }
  badge.style.display = "block";
  roleBadgeShown = true;
  setTimeout(() => { if (badge) badge.style.display = "none"; }, 5000);
}

function showWolfTurn(data) {
  const myPlayer = gameState.players.find(p => p.id === myPlayerId);
  const isWolf = myPlayer && myPlayer.role === "werewolf" && myPlayer.alive;

  const label = document.getElementById("night-phase-label");
  const instr = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  const promptText = document.getElementById("night-prompt-text");

  if (label) label.textContent = "🐺 狼人请睁眼";
  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";

  if (isWolf) {
    const teammates = (data.teammates || []).map(t => t.name).join("、");
    if (instr) instr.innerHTML = teammates
      ? `<span style="color:#ff8888">🐺 狼队：${teammates}</span><br><small>选择今晚击杀目标</small>`
      : "你是唯一的狼人，选择今晚击杀目标";
    if (promptText) promptText.textContent = "选择击杀目标：";
    renderNightTargets(data.targets || [], "kill", data.teammates || []);
    // 显示狼人私聊
    const wolfChat = document.getElementById("wolf-chat");
    if (wolfChat) {
      wolfChat.style.display = "block";
      document.getElementById("wolf-chat-messages").innerHTML = "";
    }
  } else {
    if (instr) instr.textContent = "狼人正在商议...";
    const container = document.getElementById("night-targets");
    if (container) container.innerHTML = '<p class="waiting-msg">狼人正在选择击杀目标...</p>';
    const confirmBtn = document.getElementById("night-confirm-btn");
    if (confirmBtn) confirmBtn.style.display = "none";
  }
}

function showSeerTurn(data) {
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const isSeer = myPlayer && myPlayer.role === "seer" && myPlayer.alive;

  const label = document.getElementById("night-phase-label");
  const instr = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");
  const promptText = document.getElementById("night-prompt-text");

  if (label) label.textContent = "🔮 预言家请睁眼";
  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";

  if (isSeer) {
    if (instr) instr.textContent = "选择一名玩家进行查验";
    if (promptText) promptText.textContent = "查验谁？";
    renderNightTargets(data.targets || [], "check");
  } else {
    if (instr) instr.textContent = "预言家正在查验...";
    const container = document.getElementById("night-targets");
    if (container) container.innerHTML = '<p class="waiting-msg">预言家正在查验...</p>';
    const confirmBtn = document.getElementById("night-confirm-btn");
    if (confirmBtn) confirmBtn.style.display = "none";
  }
}

function showWitchTurn(data) {
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const isWitch = myPlayer && myPlayer.role === "witch" && myPlayer.alive;

  const label = document.getElementById("night-phase-label");
  const instr = document.getElementById("night-instruction");
  const targetSection = document.getElementById("night-target-section");
  const closedEyes = document.querySelector(".all-closed-eyes");

  if (label) label.textContent = "🧪 女巫请睁眼";
  if (closedEyes) closedEyes.style.display = "none";
  if (targetSection) targetSection.style.display = "block";

  witchHealDone = false;
  nightPoisonTarget = null;

  const killTarget = data.kill_target;
  const canHeal = data.can_heal;
  const canPoison = data.can_poison;

  if (instr) {
    instr.innerHTML = killTarget
      ? `狼人今晚击杀了 <b style="color:#ff6666">${killTarget}</b>`
      : "今晚无人被击杀";
  }

  const container = document.getElementById("night-targets");
  if (!container) return;

  if (isWitch) {
    const alivePlayers = (gameState.players || []).filter(p => p.alive);
    let html = `<div class="witch-panel">`;

    // 解药部分
    if (killTarget && canHeal) {
      html += `<div class="witch-section" id="witch-heal-section">
        <p class="witch-label">💧 解药（可救 <b style="color:#ff6666">${killTarget}</b>）</p>
        <p class="witch-sublabel" style="color:#aaa;font-size:12px;margin-bottom:8px;">点击"救人"即立即生效，救人后本夜不可再用毒药</p>
        <button class="btn-witch-heal" id="witch-heal-btn" onclick="doWitchHeal('${killTarget}')">✅ 救人：${killTarget}</button>
        <button class="btn-witch-skip" onclick="skipWitchHeal()">不救，跳过</button>
      </div>`;
    } else if (!killTarget) {
      html += `<div class="witch-section"><p class="witch-label">今晚无人被击杀，解药无用武之地</p></div>`;
    } else if (!canHeal) {
      html += `<div class="witch-section"><p class="witch-label" style="color:#888">解药已用尽</p></div>`;
    }

    // 毒药部分
    if (canPoison) {
      html += `<div class="witch-section">
        <p class="witch-label">☠️ 毒药（选择毒杀目标，可不用）</p>
        <div class="players-row-small" id="poison-targets">`;
      alivePlayers.forEach(t => {
        html += `<div class="player-chip" id="pchip-${t.id}" onclick="selectPoison('${t.name}','${t.id}')">${t.name}</div>`;
      });
      html += `</div></div>`;
    } else {
      html += `<div class="witch-section"><p class="witch-label" style="color:#888">毒药已用尽</p></div>`;
    }

    html += `<button class="btn-primary" style="margin-top:12px" onclick="confirmWitch()">确认</button>`;
    html += `</div>`;

    container.innerHTML = html;
    const confirmBtn = document.getElementById("night-confirm-btn");
    if (confirmBtn) confirmBtn.style.display = "none";
  } else {
    container.innerHTML = '<p class="waiting-msg">女巫正在决策...</p>';
    const confirmBtn = document.getElementById("night-confirm-btn");
    if (confirmBtn) confirmBtn.style.display = "none";
  }
}

function doWitchHeal(target) {
  socket.emit("night_action", { action: "heal", target: target });
  witchHealDone = true;
  // 替换解药区域为"已救"提示，隐藏毒药区域
  const healSection = document.getElementById("witch-heal-section");
  if (healSection) {
    healSection.innerHTML = `<p class="witch-label" style="color:#52c41a">💧 已使用解药救了 <b>${target}</b>，等待结算...</p>`;
  }
  const poisonSection = healSection && healSection.parentElement
    ? healSection.parentElement.querySelector('.witch-section:last-of-type')
    : null;
  // 隐藏毒药选项和确认按钮（救人后直接等结算）
  const container = document.getElementById("night-targets");
  if (container) {
    const confirmBtn = container.querySelector(".btn-primary");
    if (confirmBtn) confirmBtn.style.display = "none";
    const poisonDiv = container.querySelectorAll(".witch-section");
    poisonDiv.forEach((el, i) => { if (i > 0) el.style.display = "none"; });
  }
}

function skipWitchHeal() {
  witchHealDone = true;
  const btn = document.getElementById("witch-heal-btn");
  if (btn) btn.disabled = true;
  const skipBtn = document.querySelector(".btn-witch-skip");
  if (skipBtn) skipBtn.disabled = true;
}

function selectPoison(name, id) {
  nightPoisonTarget = name;
  document.querySelectorAll(".player-chip[id^='pchip-']").forEach(c => c.classList.remove("selected"));
  const el = document.getElementById("pchip-" + id);
  if (el) el.classList.add("selected");
}

function confirmWitch() {
  if (nightPoisonTarget) {
    socket.emit("night_action", { action: "poison", target: nightPoisonTarget });
    showToast("☠️ 毒药已使用！");
  } else {
    socket.emit("night_action", { action: "skip" });
  }
  renderNightIdle("等待夜间结算...");
}

function renderNightTargets(targets, actionType, teammates) {
  const container = document.getElementById("night-targets");
  if (!container) return;
  container.innerHTML = "";
  nightTarget = null;

  const confirmBtn = document.getElementById("night-confirm-btn");
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.style.display = "block"; }

  const teammateIds = (teammates || []).map(t => t.id);

  targets.forEach(t => {
    const div = document.createElement("div");
    const isTeammate = teammateIds.includes(t.id);
    div.className = "player-chip" + (isTeammate ? " wolf-teammate" : "");
    div.textContent = (isTeammate ? "🐺 " : "") + t.name;
    div.addEventListener("click", () => {
      nightTarget = t.name;
      document.querySelectorAll("#night-targets .player-chip").forEach(c => c.classList.remove("selected"));
      div.classList.add("selected");
      if (confirmBtn) confirmBtn.disabled = false;
    });
    container.appendChild(div);
  });
}

function confirmNightAction() {
  if (!nightTarget) return;
  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  if (!myPlayer) return;
  const action = myPlayer.role === "seer" ? "check" : "kill";
  socket.emit("night_action", { action: action, target: nightTarget });

  if (action === "check") {
    renderNightIdle("查验中，等待结果...");
  } else {
    renderNightIdle("击杀目标已选定，等待其他人...");
  }
  nightTarget = null;
}

// ========== 预言家结果弹窗 ==========
function showSeerResultModal(result) {
  const existing = document.getElementById("seer-result-modal");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "seer-result-modal";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:1000;display:flex;align-items:center;justify-content:center;";
  overlay.innerHTML = `
    <div style="background:#1a1a2e;border:2px solid #f39c12;border-radius:16px;padding:32px 40px;text-align:center;max-width:320px;">
      <div style="font-size:48px;margin-bottom:12px;">🔮</div>
      <div style="color:#f39c12;font-size:14px;margin-bottom:8px;font-weight:bold;">预言家查验结果</div>
      <div style="color:white;font-size:20px;font-weight:bold;margin-bottom:8px;">${escapeHtml(result)}</div>
      <div style="color:#888;font-size:12px;margin-bottom:16px;">仅你可见</div>
      <button onclick="this.closest('#seer-result-modal').remove()" style="background:#f39c12;border:none;padding:8px 24px;border-radius:8px;cursor:pointer;font-size:14px;">知道了</button>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
  setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 5000);
}

// ========== 夜间结果页 ==========
function showNightResultPage(data) {
  showPage("night-result");
  const container = document.getElementById("night-result-content");
  if (!container) return;

  let html = `<div class="night-result-card"><h2 class="result-title">🌅 天亮了</h2>`;

  if (data.dead && data.dead.length > 0) {
    html += `<p class="dead-label">昨夜死亡：</p>`;
    data.dead.forEach((name, idx) => {
      const roleName = (data.dead_roles && data.dead_roles[name]) || "";
      html += `<div class="dead-reveal-card" id="night-card-${idx}">
        <div class="card-front">${escapeHtml(name)}</div>
        <div class="card-back ${getGlowClass(roleName)}">${escapeHtml(roleName)}</div>
      </div>`;
    });
  } else {
    html += `<p class="no-dead">🕊️ 今夜平安，无人死亡</p>`;
  }

  if (data.healed) {
    html += `<p style="color:#52c41a;margin-top:8px;">💧 女巫今晚用解药救了人</p>`;
  }
  html += `</div>`;
  container.innerHTML = html;

  requestAnimationFrame(() => {
    document.querySelectorAll(".dead-reveal-card").forEach((card, i) => {
      setTimeout(() => card.classList.add("flipped"), 400 + i * 700);
    });
  });
}

// ========== 白天阶段 ==========
function onDayStartBase(data) {
  gameState = data.state || data;
  showPage("day");
  document.getElementById("day-num").textContent = `第${data.day || gameState.day}天`;
  hideAllDayAreas();
  clearTimers();
  renderMessages(gameState.messages || []);
  renderPlayersMini(gameState.players || []);
}

function hideAllDayAreas() {
  const ids = ["speaking-area", "vote-area", "pk-area"];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  });
}

function startSpeakTimer(seconds, autoSend) {
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
      if (autoSend && isMyTurn) sendSpeech();
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
  const content = (input ? input.value.trim() : "") || "（过）";
  socket.emit("speech", { content });
  if (input) input.value = "";
  // 只隐藏输入框，不隐藏整个 speaking-area（聊天记录仍可见）
  const inputWrap = document.getElementById("speech-input-wrap");
  if (inputWrap) inputWrap.style.display = "none";
  isMyTurn = false;
}

// ========== 投票阶段 ==========
function startVotePhase(data) {
  hideAllDayAreas();
  const voteArea = document.getElementById("vote-area");
  if (voteArea) voteArea.style.display = "block";

  const confirmWrap = document.getElementById("vote-confirm-wrap");
  if (confirmWrap) {
    confirmWrap.style.display = "none";
    // 重置为初始按钮状态（防止上一轮投票后 innerHTML 被替换为"已投给XX"）
    confirmWrap.innerHTML = `<button class="btn-primary" onclick="sendVote()">确认投票</button>`;
  }
  myVoteTarget = null;

  const container = document.getElementById("vote-players");
  if (!container) return;
  container.innerHTML = "";

  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const canVote = myPlayer && myPlayer.alive;

  // 优先使用后端发来的 candidates 列表，避免用 gameState.players 过滤出错
  const candidates = data.candidates || (gameState.players || []).filter(p => p.alive && p.id !== myPlayerId);
  const alive = candidates.filter(p => p.id !== myPlayerId);
  alive.forEach(p => {
    const pName = p.name;
    const pId = p.id;
    const div = document.createElement("div");
    div.className = "vote-player-chip";
    div.id = "vote-chip-" + pId;
    div.innerHTML = `<div class="vp-name">${escapeHtml(pName)}</div><div class="vp-tally" id="tally-${pId}"></div>`;
    if (canVote) div.addEventListener("click", function() { selectVote(pId, pName); });
    container.appendChild(div);
  });

  startVoteTimer(data.timer || 30);
  renderPlayersMini(gameState.players || []);
}

function selectVote(playerId, name) {
  myVoteTarget = name;
  document.querySelectorAll(".vote-player-chip").forEach(c => c.classList.remove("selected"));
  const el = document.getElementById("vote-chip-" + playerId);
  if (el) el.classList.add("selected");
  const confirmWrap = document.getElementById("vote-confirm-wrap");
  if (confirmWrap) confirmWrap.style.display = "block";
}

function sendVote() {
  if (!myVoteTarget) return;
  socket.emit("vote", { target: myVoteTarget });
  const confirmWrap = document.getElementById("vote-confirm-wrap");
  if (confirmWrap) confirmWrap.innerHTML = `<p class="voted-confirm">✅ 已投给 <b>${escapeHtml(myVoteTarget)}</b></p>`;
  clearInterval(voteTimerInterval);
}

function updateVoteTally(votes) {
  if (!votes) return;
  const counts = {};
  Object.values(votes).forEach(n => { counts[n] = (counts[n] || 0) + 1; });

  // 清空旧票数
  document.querySelectorAll(".vp-tally").forEach(el => el.textContent = "");

  // 找最高票
  const max = Math.max(...Object.values(counts), 0);

  // 更新每个玩家的票数显示
  (gameState.players || []).forEach(p => {
    const el = document.getElementById("tally-" + p.id);
    if (el) {
      const c = counts[p.name] || 0;
      if (c > 0) {
        el.textContent = c + "票";
        el.style.color = c === max ? "#e74c3c" : "#aaa";
      }
    }
    const chip = document.getElementById("vote-chip-" + p.id);
    if (chip) chip.classList.toggle("top-voted", counts[p.name] === max && max > 0);
  });
}

function startVoteTimer(seconds) {
  clearInterval(voteTimerInterval);
  const bar = document.getElementById("vote-timer-bar");
  const num = document.getElementById("vote-timer-num");
  if (!bar || !num) return;
  const total = seconds;
  bar.style.width = "100%";
  num.textContent = total;

  voteTimerInterval = setInterval(() => {
    const cur = parseInt(num.textContent);
    if (cur <= 1) { clearInterval(voteTimerInterval); return; }
    const next = cur - 1;
    num.textContent = next;
    bar.style.width = ((next / total) * 100) + "%";
  }, 1000);
}

function showVoteResultUI(data) {
  clearTimers();
  hideAllDayAreas();

  const dayReveal = document.getElementById("day-dead-reveal");
  if (dayReveal) {
    dayReveal.style.display = "block";
    let html = `<div class="vote-result-card">
      <p class="vote-result-text">${escapeHtml(data.result || "投票结束")}</p>`;

    if (data.dead && data.dead.length > 0) {
      data.dead.forEach(name => {
        const roleName = (data.dead_roles && data.dead_roles[name]) || "";
        html += `<div class="dead-reveal-card flipped" style="margin:8px auto;">
          <div class="card-front">${escapeHtml(name)}</div>
          <div class="card-back ${getGlowClass(roleName)}">${escapeHtml(roleName)}</div>
        </div>`;
      });
    }
    if (data.pk_candidates && data.pk_candidates.length > 0) {
      html += `<p class="pk-hint">⚔️ 平票！进入PK：${data.pk_candidates.map(escapeHtml).join(" vs ")}</p>`;
    }
    html += `</div>`;
    dayReveal.innerHTML = html;
  }

  renderMessages(gameState.messages || []);
  renderPlayersMini(gameState.players || []);
}

// ========== PK投票 ==========
function showPKVotePhase(data) {
  hideAllDayAreas();
  const pkArea = document.getElementById("pk-area");
  if (pkArea) pkArea.style.display = "block";
  selectedPKTarget = null;

  const confirmWrap = document.getElementById("pk-vote-confirm-wrap");
  if (confirmWrap) confirmWrap.style.display = "none";

  const container = document.getElementById("pk-players");
  if (!container) return;
  container.innerHTML = "";

  const myPlayer = (gameState.players || []).find(p => p.id === myPlayerId);
  const canVote = myPlayer && myPlayer.alive && !data.candidates.includes(myPlayer.name);

  (data.candidates || []).forEach(name => {
    const div = document.createElement("div");
    div.className = "pk-chip";
    div.id = "pk-" + name;
    div.textContent = name;
    if (canVote) div.addEventListener("click", () => selectPKTarget(name));
    container.appendChild(div);
  });

  startVoteTimer(data.timer || 30);
  renderPlayersMini(gameState.players || []);
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
  if (confirmWrap) confirmWrap.innerHTML = `<p class="voted-confirm">✅ 已投给 <b>${escapeHtml(selectedPKTarget)}</b></p>`;
}

// ========== 游戏结束 ==========
function showEndGame(data) {
  showPage("end");
  const container = document.getElementById("end-content");
  if (!container) return;
  const isGood = data.winner === "good";
  const players = (data.state ? data.state.players : gameState.players) || [];

  container.innerHTML = `
    <div class="end-card">
      <h1 class="end-title ${isGood ? "good-win" : "wolf-win"}">
        ${isGood ? "🎉 好人大获全胜！" : "🐺 狼人胜利！"}
      </h1>
      <p class="end-sub">${escapeHtml(data.winner_name || "")}</p>
      <div class="all-roles">
        ${players.map(p => `
          <div class="role-reveal-card ${p.alive ? "alive" : "dead"}">
            <div class="rrc-avatar" style="background:${p.role_color||'#999'}">${(p.name||"?").slice(0,1)}</div>
            <div class="rrc-name">${escapeHtml(p.name)}</div>
            <div class="rrc-role" style="color:${p.role_color||'#999'}">${escapeHtml(p.role_name||p.role||"")}</div>
            <div class="rrc-status">${p.alive ? "✅ 存活" : "💀 死亡"}</div>
          </div>
        `).join("")}
      </div>
      <button class="btn-primary" style="margin-top:24px" onclick="location.href='/'">返回大厅</button>
    </div>
  `;
}

// ========== 消息渲染 ==========
function renderMessages(messages) {
  const container = document.getElementById("messages-area");
  if (!container) return;
  container.innerHTML = "";
  (messages || []).forEach(m => appendMessage(m, false));
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
      <div class="msg-name" style="color:${msg.name===myPlayerName?'#a78bfa':'#aaa'}">${escapeHtml(msg.name||"???")}</div>
      <div class="msg-bubble" style="${isMe?"background:#667eea":"background:rgba(255,255,255,0.12)"}">
        ${escapeHtml(msg.content||"")}
      </div>
    `;
  }
  container.appendChild(div);
  if (scroll) container.scrollTop = container.scrollHeight;
}

// ========== 浮动玩家列表 ==========
function renderPlayersMini(players) {
  const container = document.getElementById("player-list-float");
  if (!container) return;
  container.innerHTML = "";
  (players || []).forEach(p => {
    const div = document.createElement("div");
    div.className = "pm-chip" + (p.alive ? "" : " dead") + (p.id === myPlayerId ? " me" : "");
    // 只有自己才显示角色颜色，其他人统一用灰色，避免泄露身份
    const dotColor = p.id === myPlayerId ? (p.role_color || "#999") : "#888";
    const dot = `<span class="pm-dot" style="background:${dotColor}"></span>`;
    div.innerHTML = dot + escapeHtml(p.name) + (p.alive ? "" : " 💀");
    container.appendChild(div);
  });
}

// ========== 工具函数 ==========
function updateMyRole() {
  if (!gameState) return;
  const p = (gameState.players || []).find(p => p.id === myPlayerId);
  if (p && p.role) myRole = p.role;
}

function clearTimers() {
  clearInterval(timerInterval);
  clearInterval(voteTimerInterval);
}

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
  div.textContent = String(str);
  return div.innerHTML;
}

// ========== API 辅助 ==========
async function addAI() {
  try {
    await fetch(`/api/room/${myRoomId}/add-ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    showToast("AI玩家已添加");
  } catch(e) {
    showToast("添加AI失败");
  }
}

async function startGame() {
  try {
    const res = await fetch(`/api/room/${myRoomId}/start`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) showToast(data.error || "无法开始游戏");
  } catch(e) {
    showToast("开始游戏失败");
  }
}

function copyRoomId() {
  navigator.clipboard.writeText(myRoomId).then(() => showToast("房间号已复制！"));
}

function copyLink() {
  navigator.clipboard.writeText(location.origin + `/?room=${myRoomId}`).then(() => showToast("邀请链接已复制！"));
}

// ========== 规则弹窗 ==========
function openRules() {
  const el = document.getElementById("modal-rules");
  if (el) el.classList.add("open");
}
function closeRules() {
  const el = document.getElementById("modal-rules");
  if (el) el.classList.remove("open");
}
