const app = getApp()
const socket = require('../../utils/socket')

const ROLE_HINTS = {
  werewolf: '你的任务：每晚击杀一名玩家，隐藏身份',
  seer: '你的任务：每晚查验一名玩家的身份',
  witch: '你有解药和毒药各一瓶，善用它们',
  villager: '你是村民，发言推理，找出狼人',
}

Page({
  data: {
    view: 'waiting',
    connected: false,
    myPlayerId: '',
    myRoomId: '',
    players: [],
    messages: [],
    dayNum: 1,
    phaseBadge: '☀️ 白天阶段',

    // role badge
    showRoleBadge: false,
    roleBadgeName: '',
    roleBadgeHint: '',
    roleBadgeColor: '',

    // night
    nightPhaseLabel: '🌙 夜间阶段',
    nightInstruction: '天黑请闭眼...',
    showClosedEyes: true,
    showNightTargets: false,
    showWitchPanel: false,
    showNightWaiting: false,
    nightWaitingMsg: '',
    nightPromptText: '选择目标：',
    nightTargets: [],
    nightTarget: null,
    witchKillTarget: null,
    canHeal: false,
    canPoison: false,
    witchPoisonTarget: null,
    witchHealDone: false,
    witchPoisonTargets: [],

    // night result
    nightDead: [],
    nightDeadRoles: {},
    nightHealed: false,

    // day
    showDayDeadReveal: false,
    voteResultText: '',
    voteDeadList: [],
    pkHint: '',
    showSpeakingArea: false,
    showVoteArea: false,
    showPKArea: false,
    isMyTurn: false,
    currentSpeakerName: '',
    speechContent: '',
    speechProgress: '',
    timerSeconds: 60,
    timerPct: 100,
    timerTotal: 60,
    voteTimerSeconds: 30,
    voteTimerPct: 100,
    voteCandidates: [],
    voteTarget: null,
    voted: false,
    pkCandidates: [],
    pkTarget: null,
    pkVoted: false,
    scrollToMsg: '',

    // seer modal
    showSeerModal: false,
    seerResult: '',

    // end
    winner: null,
    endPlayers: [],

    // toast
    toastMsg: '',
    toastShow: false,

    // persona modal
    showPersonaModal: false,
    personaList: [],
    selectedPersona: null,
  },

  _timerInterval: null,
  _voteTimerInterval: null,
  _roleBadgeTimer: null,
  _myRole: null,
  _roleBadgeShown: false,

  onLoad(options) {
    const roomId = options.room
    const playerId = options.pid
    this.setData({ myRoomId: roomId, myPlayerId: playerId })

    socket.connect(app.globalData.serverUrl)
    this._bindSocketEvents()
  },

  onUnload() {
    socket.disconnect()
    this._clearTimers()
  },

  _bindSocketEvents() {
    socket.on('connect', () => {
      this.setData({ connected: true })
      socket.emit('join_room', { room_id: this.data.myRoomId, player_id: this.data.myPlayerId })
    })
    socket.on('disconnect', () => this.setData({ connected: false }))
    socket.on('connect_error', () => this.setData({ connected: false }))

    socket.on('room_state', d => this._onRoomState(d))
    socket.on('player_joined', d => this._onRoomState(d))
    socket.on('player_left', d => this._onRoomState(d))
    socket.on('player_online', d => this._onRoomState(d))

    socket.on('game_started', d => this._onGameStarted(d))
    socket.on('night_start', d => this._onNightStart(d))
    socket.on('role_turn', d => this._onRoleTurn(d))
    socket.on('action_confirmed', d => this._onActionConfirmed(d))
    socket.on('wolf_teammate_action', d => this._onWolfTeammateAction(d))
    socket.on('wolf_vote', d => this._onWolfVote(d))
    socket.on('seer_result_private', d => this._onSeerResultPrivate(d))
    socket.on('night_result', d => this._onNightResult(d))
    socket.on('last_words_start', d => this._onLastWordsStart(d))
    socket.on('your_last_words', d => this._onYourLastWords(d))
    socket.on('day_start', d => this._onDayStart(d))
    socket.on('discussion_start', d => this._onDiscussionStart(d))
    socket.on('speaking_start', d => this._onSpeakingStart(d))
    socket.on('speaking_end', d => this._onSpeakingEnd(d))
    socket.on('player_speech', d => this._onPlayerSpeech(d))
    socket.on('vote_start', d => this._onVoteStart(d))
    socket.on('vote_cast', d => this._onVoteCast(d))
    socket.on('vote_result', d => this._onVoteResult(d))
    socket.on('pk_discussion_start', d => this._onPKDiscussionStart(d))
    socket.on('pk_vote_start', d => this._onPKVoteStart(d))
    socket.on('game_end', d => this._onGameEnd(d))
    socket.on('error', d => this._toast('错误: ' + (d && d.message || '未知')))
  },

  // ===== Socket handlers =====

  _onRoomState(data) {
    const state = data.state || data
    if (state.phase === 'waiting') {
      const players = (state.players || []).map(p => ({
        ...p,
        is_me: p.id === this.data.myPlayerId,
      }))
      this.setData({ view: 'waiting', players })
    }
  },

  _onGameStarted(data) {
    const state = data.state || data
    this._updateMyRole(state)
    this._roleBadgeShown = false
    this.setData({
      view: 'night',
      players: state.players || [],
      messages: state.messages || [],
      nightPhaseLabel: '🌙 夜间阶段',
      nightInstruction: '游戏开始，天黑请闭眼...',
      showClosedEyes: true,
      showNightTargets: false,
      showWitchPanel: false,
      showNightWaiting: false,
    })
    this._showRoleBadgeOnce(state)
  },

  _onNightStart(data) {
    const state = data.state || data
    this.setData({
      view: 'night',
      nightPhaseLabel: '🌙 夜间阶段',
      nightInstruction: '天黑请闭眼...',
      showClosedEyes: true,
      showNightTargets: false,
      showWitchPanel: false,
      showNightWaiting: false,
      nightTarget: null,
      witchPoisonTarget: null,
      witchHealDone: false,
      players: state.players || [],
    })
  },

  _onRoleTurn(data) {
    const state = data.state || data
    this._updateMyRole(state)
    const role = data.role
    if (role === 'werewolf') this._showWolfTurn(data)
    else if (role === 'seer') this._showSeerTurn(data)
    else if (role === 'witch') this._showWitchTurn(data)
  },

  _showWolfTurn(data) {
    const myPlayer = this.data.players.find(p => p.id === this.data.myPlayerId)
    const isWolf = myPlayer && myPlayer.role === 'werewolf' && myPlayer.alive

    if (isWolf) {
      const teammates = (data.teammates || []).map(t => t.name).join('、')
      const targets = (data.targets || []).map(t => ({
        ...t,
        isTeammate: false,
        isTeammateSelected: false,
      }))
      this.setData({
        nightPhaseLabel: '🐺 狼人请睁眼',
        nightInstruction: teammates ? `🐺 狼队：${teammates}` : '你是唯一的狼人',
        showClosedEyes: false,
        showNightTargets: true,
        showWitchPanel: false,
        showNightWaiting: false,
        nightPromptText: '选择击杀目标：',
        nightTargets: targets,
        nightTarget: null,
      })
    } else {
      this.setData({
        nightPhaseLabel: '🐺 狼人请睁眼',
        nightInstruction: '狼人正在商议...',
        showClosedEyes: false,
        showNightTargets: false,
        showWitchPanel: false,
        showNightWaiting: true,
        nightWaitingMsg: '狼人正在选择击杀目标...',
      })
    }
  },

  _showSeerTurn(data) {
    const myPlayer = this.data.players.find(p => p.id === this.data.myPlayerId)
    const isSeer = myPlayer && myPlayer.role === 'seer' && myPlayer.alive

    if (isSeer) {
      const targets = (data.targets || []).map(t => ({ ...t, isTeammate: false, isTeammateSelected: false }))
      this.setData({
        nightPhaseLabel: '🔮 预言家请睁眼',
        nightInstruction: '选择一名玩家进行查验',
        showClosedEyes: false,
        showNightTargets: true,
        showWitchPanel: false,
        showNightWaiting: false,
        nightPromptText: '查验谁？',
        nightTargets: targets,
        nightTarget: null,
      })
    } else {
      this.setData({
        nightPhaseLabel: '🔮 预言家请睁眼',
        nightInstruction: '预言家正在查验...',
        showClosedEyes: false,
        showNightTargets: false,
        showWitchPanel: false,
        showNightWaiting: true,
        nightWaitingMsg: '预言家正在查验...',
      })
    }
  },

  _showWitchTurn(data) {
    const myPlayer = this.data.players.find(p => p.id === this.data.myPlayerId)
    const isWitch = myPlayer && myPlayer.role === 'witch' && myPlayer.alive

    if (isWitch) {
      const alivePlayers = this.data.players.filter(p => p.alive)
      this.setData({
        nightPhaseLabel: '🧪 女巫请睁眼',
        nightInstruction: data.kill_target ? `狼人今晚击杀了 ${data.kill_target}` : '今晚无人被击杀',
        showClosedEyes: false,
        showNightTargets: false,
        showWitchPanel: true,
        showNightWaiting: false,
        witchKillTarget: data.kill_target || null,
        canHeal: data.can_heal,
        canPoison: data.can_poison,
        witchPoisonTargets: alivePlayers,
        witchHealDone: false,
        witchPoisonTarget: null,
      })
    } else {
      this.setData({
        nightPhaseLabel: '🧪 女巫请睁眼',
        nightInstruction: '女巫正在决策...',
        showClosedEyes: false,
        showNightTargets: false,
        showWitchPanel: false,
        showNightWaiting: true,
        nightWaitingMsg: '女巫正在决策...',
      })
    }
  },

  _onActionConfirmed(data) {
    if (data.action === 'kill') this._toast('🐺 击杀目标已确认，等待结算...')
    if (data.action === 'heal') this._toast('💧 解药已使用！')
    if (data.action === 'poison') this._toast('☠️ 毒药已使用！')
    if (data.action === 'heal' || data.action === 'poison') {
      this.setData({ showWitchPanel: false, showNightWaiting: true, nightWaitingMsg: '等待夜间结算...' })
    }
  },

  _onWolfTeammateAction(data) {
    this._toast(`🐺 队友 ${data.player_name} 选择击杀 ${data.target}`)
    const targets = this.data.nightTargets.map(t => ({
      ...t,
      isTeammateSelected: t.name === data.target ? true : t.isTeammateSelected,
    }))
    this.setData({ nightTargets: targets })
  },

  _onWolfVote(data) {
    this._toast(`🐺 队友 ${data.player_name} 投了 ${data.voted_name}`)
  },

  _onSeerResultPrivate(data) {
    if (data.seer_result) {
      this.setData({ seerResult: data.seer_result, showSeerModal: true })
      setTimeout(() => this.setData({ showSeerModal: false }), 5000)
    }
  },

  _onNightResult(data) {
    const state = data.state || data
    this.setData({
      view: 'night-result',
      nightDead: data.dead || [],
      nightDeadRoles: data.dead_roles || {},
      nightHealed: !!data.healed,
      players: state.players || [],
    })
  },

  _onLastWordsStart(data) {
    const state = data.state || data
    const isMe = data.player_id === this.data.myPlayerId
    this.setData({
      view: 'day',
      dayNum: state.day || 1,
      phaseBadge: `💬 ${data.player_name} 发表遗言`,
      messages: state.messages || [],
      players: state.players || [],
      showSpeakingArea: true,
      showVoteArea: false,
      showPKArea: false,
      currentSpeakerName: data.player_name,
      isMyTurn: isMe,
      speechContent: '',
    })
    if (isMe) this._startTimer(data.timer || 30, true)
    else this._startTimer(data.timer || 30, false)
  },

  _onYourLastWords(data) {
    this.setData({
      view: 'day',
      phaseBadge: '⚰️ 你已死亡 - 发表遗言',
      showSpeakingArea: true,
      isMyTurn: true,
      currentSpeakerName: '你已死亡',
      speechContent: '',
    })
    this._startTimer(data.timer || 30, true)
  },

  _onDayStart(data) {
    const state = data.state || data
    this._clearTimers()
    this.setData({
      view: 'day',
      dayNum: data.day || state.day || 1,
      phaseBadge: '☀️ 白天阶段',
      messages: state.messages || [],
      players: state.players || [],
      showSpeakingArea: false,
      showVoteArea: false,
      showPKArea: false,
      showDayDeadReveal: false,
    })
  },

  _onDiscussionStart(data) {
    const state = data.state || data
    this.setData({
      messages: state.messages || [],
      players: state.players || [],
    })
  },

  _onSpeakingStart(data) {
    const state = data.state || data
    const isMyTurn = data.speaker_id === this.data.myPlayerId
    this.setData({
      phaseBadge: data.is_pk ? '⚔️ PK发言' : (isMyTurn ? '⏳ 轮到你发言！' : '💬 发言中'),
      showSpeakingArea: true,
      showVoteArea: false,
      showPKArea: false,
      currentSpeakerName: data.speaker_name || '???',
      isMyTurn,
      speechContent: '',
      speechProgress: data.total ? `${data.turn_index + 1} / ${data.total}` : '',
      players: state.players || [],
    })
    this._startTimer(data.timer || 60, isMyTurn)
  },

  _onSpeakingEnd(data) {
    const state = data.state || {}
    this._clearTimers()
    this.setData({
      showSpeakingArea: false,
      isMyTurn: false,
      messages: state.messages || this.data.messages,
    })
  },

  _onPlayerSpeech(data) {
    const state = data.state || {}
    const msg = {
      id: Date.now() + Math.random(),
      speaker_id: data.player_id,
      name: data.player_name,
      content: data.content,
      type: 'speech',
    }
    const messages = [...(state.messages || this.data.messages)]
    this.setData({ messages, scrollToMsg: 'msg-bottom' })
  },

  _onVoteStart(data) {
    const state = data.state || data
    const myPlayer = state.players && state.players.find(p => p.id === this.data.myPlayerId)
    const canVote = myPlayer && myPlayer.alive
    const candidates = (data.candidates || state.players || [])
      .filter(p => p.alive && p.id !== this.data.myPlayerId)
      .map(p => ({ ...p, tally: '', topVoted: false }))

    this.setData({
      phaseBadge: '🗳️ 投票阶段',
      showSpeakingArea: false,
      showVoteArea: canVote,
      showPKArea: false,
      voteCandidates: candidates,
      voteTarget: null,
      voted: false,
      players: state.players || [],
    })
    this._startVoteTimer(data.timer || 30)
  },

  _onVoteCast(data) {
    const votes = data.votes || {}
    const counts = {}
    Object.values(votes).forEach(n => { counts[n] = (counts[n] || 0) + 1 })
    const max = Math.max(...Object.values(counts), 0)
    const candidates = this.data.voteCandidates.map(p => ({
      ...p,
      tally: counts[p.name] ? `${counts[p.name]}票` : '',
      topVoted: counts[p.name] === max && max > 0,
    }))
    this.setData({ voteCandidates: candidates })
  },

  _onVoteResult(data) {
    const state = data.state || data
    this._clearTimers()
    const voteDeadList = (data.dead || []).map(name => ({
      name,
      role: data.dead_roles && data.dead_roles[name] || '',
    }))
    this.setData({
      showVoteArea: false,
      showPKArea: false,
      showDayDeadReveal: true,
      voteResultText: data.result || '投票结束',
      voteDeadList,
      pkHint: data.pk_candidates && data.pk_candidates.length > 0
        ? `⚔️ 平票！进入PK：${data.pk_candidates.join(' vs ')}`
        : '',
      messages: state.messages || this.data.messages,
      players: state.players || this.data.players,
    })
  },

  _onPKDiscussionStart(data) {
    const state = data.state || data
    this.setData({
      phaseBadge: `⚔️ PK：${(data.candidates || []).join(' vs ')}`,
      showSpeakingArea: false,
      showVoteArea: false,
      showPKArea: false,
      showDayDeadReveal: false,
      messages: state.messages || this.data.messages,
    })
  },

  _onPKVoteStart(data) {
    const state = data.state || data
    const myPlayer = state.players && state.players.find(p => p.id === this.data.myPlayerId)
    const canVote = myPlayer && myPlayer.alive && !(data.candidates || []).includes(myPlayer.name)
    this.setData({
      phaseBadge: '⚔️ PK投票',
      showSpeakingArea: false,
      showVoteArea: false,
      showPKArea: canVote,
      pkCandidates: data.candidates || [],
      pkTarget: null,
      pkVoted: false,
    })
    this._startVoteTimer(data.timer || 30)
  },

  _onGameEnd(data) {
    const state = data.state || data
    this._clearTimers()
    this.setData({
      view: 'end',
      winner: data.winner,
      endPlayers: state.players || [],
    })
  },

  // ===== User actions =====

  selectNightTarget(e) {
    const { name, isTeammate } = e.currentTarget.dataset
    if (isTeammate) return
    this.setData({ nightTarget: name })
  },

  confirmNightAction() {
    const { nightTarget, myPlayerId } = this.data
    if (!nightTarget) return
    const myPlayer = this.data.players.find(p => p.id === myPlayerId)
    if (!myPlayer) return
    const action = myPlayer.role === 'seer' ? 'check' : 'kill'
    socket.emit('night_action', { action, target: nightTarget })
    this.setData({
      showNightTargets: false,
      showNightWaiting: true,
      nightWaitingMsg: action === 'check' ? '查验中，等待结果...' : '击杀目标已选定，等待其他人...',
      nightTarget: null,
    })
  },

  doWitchHeal() {
    const { witchKillTarget } = this.data
    socket.emit('night_action', { action: 'heal', target: witchKillTarget })
    // 救人后立即隐藏面板，等待结算（标准规则：救人后不能再用毒药）
    this.setData({
      showWitchPanel: false,
      showNightWaiting: true,
      nightWaitingMsg: `💧 已使用解药救了 ${witchKillTarget}，等待结算...`,
    })
  },

  skipWitchHeal() {
    this.setData({ witchHealDone: true })
  },

  selectPoison(e) {
    this.setData({ witchPoisonTarget: e.currentTarget.dataset.name })
  },

  confirmWitch() {
    const { witchPoisonTarget } = this.data
    if (witchPoisonTarget) {
      socket.emit('night_action', { action: 'poison', target: witchPoisonTarget })
      this._toast('☠️ 毒药已使用！')
    } else {
      socket.emit('night_action', { action: 'skip' })
    }
    this.setData({ showWitchPanel: false, showNightWaiting: true, nightWaitingMsg: '等待夜间结算...' })
  },

  onSpeechInput(e) {
    this.setData({ speechContent: e.detail.value })
  },

  sendSpeech() {
    this._clearTimers()
    const content = this.data.speechContent.trim() || '（过）'
    socket.emit('speech', { content })
    this.setData({ speechContent: '', isMyTurn: false, showSpeakingArea: false })
  },

  selectVote(e) {
    this.setData({ voteTarget: e.currentTarget.dataset.name })
  },

  sendVote() {
    const { voteTarget } = this.data
    if (!voteTarget) return
    socket.emit('vote', { target: voteTarget })
    this._clearVoteTimer()
    this.setData({ voted: true })
  },

  selectPKTarget(e) {
    this.setData({ pkTarget: e.currentTarget.dataset.name })
  },

  sendPKVote() {
    const { pkTarget } = this.data
    if (!pkTarget) return
    socket.emit('vote', { target: pkTarget })
    this.setData({ pkVoted: true })
  },

  closeSeerModal() {
    this.setData({ showSeerModal: false })
  },

  copyRoomId() {
    wx.setClipboardData({ data: this.data.myRoomId, success: () => this._toast('房间号已复制！') })
  },

  async addAI() {
    // 弹出人设选择框（/api/personas 是 GET，需单独处理）
    try {
      const personasRes = await new Promise((resolve, reject) => {
        wx.request({
          url: app.globalData.serverUrl + '/api/personas',
          method: 'GET',
          success: resolve,
          fail: reject,
        })
      })
      const personas = personasRes.data?.personas || {}
      const list = Object.entries(personas).map(([key, val]) => ({ key, ...val }))
      if (list.length === 0) {
        // API 返回空，直接添加随机AI
        await this._request(`/api/room/${this.data.myRoomId}/add-ai`, {})
        this._toast('AI玩家已添加（随机风格）')
        return
      }
      this.setData({ showPersonaModal: true, personaList: list, selectedPersona: null })
    } catch (e) {
      // API 失败时直接添加随机AI
      try {
        await this._request(`/api/room/${this.data.myRoomId}/add-ai`, {})
        this._toast('AI玩家已添加（随机风格）')
      } catch (e2) {
        this._toast('添加AI失败')
      }
    }
  },

  selectPersona(e) {
    const key = e.currentTarget.dataset.key
    console.log('[selectPersona] tapped key=', key, 'dataset=', e.currentTarget.dataset)
    this.setData({ selectedPersona: key })
  },

  async confirmAddAI() {
    console.log('[confirmAddAI] CALLED - selectedPersona from this.data:', this.data.selectedPersona)
    const { selectedPersona, myRoomId, personaList } = this.data
    this.setData({ showPersonaModal: false })
    console.log('[AI选择] selectedPersona=', selectedPersona, 'myRoomId=', myRoomId, 'list=', personaList)
    const personaToUse = selectedPersona || null
    try {
      if (personaToUse) {
        console.log('[AI选择] 调用add-ai-preset, persona=', personaToUse)
        await this._request(`/api/room/${myRoomId}/add-ai-preset`, { persona: personaToUse })
        const name = personaList.find(p => p.key === personaToUse)?.name || ''
        this._toast(`已添加 ${name} AI`)
      } else {
        console.log('[AI选择] 调用add-ai（随机）')
        await this._request(`/api/room/${myRoomId}/add-ai`, {})
        this._toast('AI玩家已添加（随机风格）')
      }
    } catch (e) {
      console.log('[AI选择] 失败:', e.message || e)
      this._toast('添加AI失败')
    }
  },

  closePersonaModal() {
    this.setData({ showPersonaModal: false })
  },

  async startGame() {
    try {
      const res = await this._request(`/api/room/${this.data.myRoomId}/start`, {})
      if (!res.ok) this._toast(res.error || '无法开始游戏')
    } catch (e) {
      this._toast('开始游戏失败')
    }
  },

  backToLobby() {
    socket.disconnect()
    wx.navigateBack({ delta: 1 })
  },

  // ===== Helpers =====

  _updateMyRole(state) {
    const myPlayer = (state.players || []).find(p => p.id === this.data.myPlayerId)
    if (myPlayer && myPlayer.role) this._myRole = myPlayer.role
    // Sync role_color into players list
    const players = (state.players || []).map(p => ({
      ...p,
      is_me: p.id === this.data.myPlayerId,
    }))
    this.setData({ players })
  },

  _showRoleBadgeOnce(state) {
    if (this._roleBadgeShown) return
    const myPlayer = (state.players || []).find(p => p.id === this.data.myPlayerId)
    if (!myPlayer || !myPlayer.role) return
    this._roleBadgeShown = true
    this.setData({
      showRoleBadge: true,
      roleBadgeName: myPlayer.role_name || myPlayer.role,
      roleBadgeColor: myPlayer.role_color || '#f39c12',
      roleBadgeHint: ROLE_HINTS[myPlayer.role] || '',
    })
    this._roleBadgeTimer = setTimeout(() => this.setData({ showRoleBadge: false }), 5000)
  },

  _startTimer(seconds, autoSend) {
    this._clearTimers()
    let remaining = seconds
    this.setData({ timerSeconds: remaining, timerPct: 100, timerTotal: seconds })
    this._timerInterval = setInterval(() => {
      remaining -= 1
      if (remaining <= 0) {
        clearInterval(this._timerInterval)
        this.setData({ timerSeconds: 0, timerPct: 0 })
        if (autoSend && this.data.isMyTurn) this.sendSpeech()
        return
      }
      this.setData({
        timerSeconds: remaining,
        timerPct: Math.round((remaining / seconds) * 100),
      })
    }, 1000)
  },

  _startVoteTimer(seconds) {
    this._clearVoteTimer()
    let remaining = seconds
    this.setData({ voteTimerSeconds: remaining, voteTimerPct: 100 })
    this._voteTimerInterval = setInterval(() => {
      remaining -= 1
      if (remaining <= 0) {
        clearInterval(this._voteTimerInterval)
        this.setData({ voteTimerSeconds: 0, voteTimerPct: 0 })
        return
      }
      this.setData({
        voteTimerSeconds: remaining,
        voteTimerPct: Math.round((remaining / seconds) * 100),
      })
    }, 1000)
  },

  _clearTimers() {
    if (this._timerInterval) { clearInterval(this._timerInterval); this._timerInterval = null }
    if (this._roleBadgeTimer) { clearTimeout(this._roleBadgeTimer); this._roleBadgeTimer = null }
  },

  _clearVoteTimer() {
    if (this._voteTimerInterval) { clearInterval(this._voteTimerInterval); this._voteTimerInterval = null }
  },

  _toast(msg) {
    this.setData({ toastMsg: msg, toastShow: true })
    setTimeout(() => this.setData({ toastShow: false }), 2500)
  },

  _request(path, body) {
    return new Promise((resolve, reject) => {
      wx.request({
        url: app.globalData.serverUrl + path,
        method: 'POST',
        data: body,
        header: { 'Content-Type': 'application/json' },
        success: ({ data }) => {
          if (data && data.error) reject(new Error(data.error))
          else resolve(data)
        },
        fail: reject,
      })
    })
  },
})
