const app = getApp()

Page({
  data: {
    playerName: '',
    joinRoomId: '',
    loading: false,
  },

  onNameInput(e) {
    this.setData({ playerName: e.detail.value })
  },

  onRoomIdInput(e) {
    this.setData({ joinRoomId: e.detail.value })
  },

  async createRoom() {
    const { playerName } = this.data
    if (!playerName) return
    this.setData({ loading: true })
    try {
      const res = await this._request('/api/room/create', { player_name: playerName })
      app.globalData.roomId = res.room_id
      app.globalData.playerId = res.player_id
      app.globalData.playerName = playerName
      wx.navigateTo({ url: `/pages/game/game?room=${res.room_id}&pid=${res.player_id}` })
    } catch (e) {
      wx.showToast({ title: '创建失败', icon: 'none' })
    }
    this.setData({ loading: false })
  },

  async joinRoom() {
    const { playerName, joinRoomId } = this.data
    if (!playerName || !joinRoomId) return
    this.setData({ loading: true })
    try {
      const res = await this._request(`/api/room/${joinRoomId}/join`, { player_name: playerName })
      app.globalData.roomId = joinRoomId
      app.globalData.playerId = res.player_id
      app.globalData.playerName = playerName
      wx.navigateTo({ url: `/pages/game/game?room=${joinRoomId}&pid=${res.player_id}` })
    } catch (e) {
      wx.showToast({ title: e.message || '加入失败', icon: 'none' })
    }
    this.setData({ loading: false })
  },

  _request(path, body) {
    return new Promise((resolve, reject) => {
      wx.request({
        url: app.globalData.serverUrl + path,
        method: 'POST',
        data: body,
        header: { 'Content-Type': 'application/json' },
        success: ({ data }) => {
          if (data.error) reject(new Error(data.error))
          else resolve(data)
        },
        fail: (e) => reject(e),
      })
    })
  },
})
