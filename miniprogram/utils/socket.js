/**
 * Socket.IO v4 client for WeChat Mini Program
 * EIO4 over wx.connectSocket (WebSocket transport only)
 */

class SocketIO {
  constructor() {
    this._handlers = {}
    this._ws = null
    this.connected = false
    this._pingTimer = null
    this._reconnectTimer = null
    this._serverUrl = ''
    this._destroyed = false
  }

  connect(serverUrl) {
    this._serverUrl = serverUrl
    this._destroyed = false
    this._doConnect()
  }

  _doConnect() {
    if (this._destroyed) return
    const wsUrl = this._serverUrl.replace(/^https/, 'wss') + '/socket.io/?EIO=4&transport=websocket'
    console.log('[socket] connecting', wsUrl)

    const ws = wx.connectSocket({ url: wsUrl })
    this._ws = ws

    ws.onOpen(() => {
      console.log('[socket] ws open')
      // 等 EIO open 包(type=0)收到后再发 '40'，不在这里主动发
    })

    ws.onMessage(({ data }) => {
      this._onMessage(data)
    })

    ws.onClose(({ code, reason }) => {
      console.log('[socket] closed', code, reason)
      this.connected = false
      this._clearPing()
      this._trigger('disconnect')
      if (!this._destroyed) {
        this._reconnectTimer = setTimeout(() => this._doConnect(), 2000)
      }
    })

    ws.onError((err) => {
      console.error('[socket] error', err)
      this._trigger('connect_error', err)
    })
  }

  _onMessage(raw) {
    if (!raw || typeof raw !== 'string') return
    const eioType = parseInt(raw[0])

    switch (eioType) {
      case 0: { // EIO open — server sends handshake
        let payload = {}
        try { payload = JSON.parse(raw.slice(1)) } catch (e) {}
        // 收到 EIO open 后再发 SIO connect，这是最可靠的时机
        this._safeSend('40')
        const interval = (payload.pingInterval || 25000) * 0.9
        this._clearPing()
        this._pingTimer = setInterval(() => {
          // EIO4: server pings(2), client pongs(3) — handled in case 2
        }, interval)
        break
      }
      case 2: { // ping from server
        this._safeSend('3') // pong
        break
      }
      case 4: { // SIO message
        if (raw.length < 2) break
        const sioType = parseInt(raw[1])
        if (sioType === 0) { // CONNECT ack
          this.connected = true
          console.log('[socket] connected')
          this._trigger('connect')
        } else if (sioType === 2) { // EVENT
          try {
            const arr = JSON.parse(raw.slice(2))
            if (Array.isArray(arr) && arr.length >= 1) {
              this._trigger(arr[0], arr[1])
            }
          } catch (e) {
            console.error('[socket] parse error', e, raw)
          }
        }
        break
      }
    }
  }

  _safeSend(data) {
    if (!this._ws) return
    this._ws.send({
      data,
      fail: (err) => console.error('[socket] send fail', err),
    })
  }

  on(event, handler) {
    if (!this._handlers[event]) this._handlers[event] = []
    this._handlers[event].push(handler)
    return this
  }

  off(event) {
    delete this._handlers[event]
  }

  _trigger(event, data) {
    ;(this._handlers[event] || []).forEach(h => {
      try { h(data) } catch (e) { console.error('[socket] handler error', event, e) }
    })
  }

  emit(event, data) {
    if (!this.connected) {
      console.warn('[socket] not connected, drop emit:', event)
      return
    }
    this._safeSend('42' + JSON.stringify([event, data]))
  }

  disconnect() {
    this._destroyed = true
    this._clearPing()
    if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null }
    if (this._ws) { this._ws.close({}); this._ws = null }
    this.connected = false
  }

  _clearPing() {
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null }
  }
}

module.exports = new SocketIO()
