const HEARTBEAT_INTERVAL_MS = 15_000
const PONG_TIMEOUT_MS = 10_000
const CONNECT_TIMEOUT_MS = 10_000

export class AsrSocketClient {
  constructor() {
    this.socket = null
    this.eventHandler = null
    this.closeHandler = null
    this.waiters = []
    this.heartbeatTimer = null
    this.pongTimer = null
    this.pendingPingId = null
  }

  onEvent(handler) {
    this.eventHandler = handler
  }

  onClose(handler) {
    this.closeHandler = handler
  }

  async connect(url) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return
    }
    if (this.socket?.readyState === WebSocket.CONNECTING) {
      await this.waitForOpen(this.socket)
      return
    }

    this.detachSocket()
    const socket = new WebSocket(url)
    this.socket = socket
    socket.binaryType = 'arraybuffer'
    socket.onmessage = (event) => this.handleMessage(event)
    socket.onerror = () => {
      // onclose owns error recovery.
    }
    socket.onclose = (event) => this.handleClose(socket, event)

    await this.waitForOpen(socket)
    if (this.socket !== socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 连接尚未就绪')
    }
    this.startHeartbeat()
  }

  async start(options) {
    const requestId = createRequestId()
    const waiter = this.createWaiter('ready', requestId, 180_000)
    try {
      this.sendJson({
        type: 'start',
        request_id: requestId,
        data: options,
      })
    } catch (cause) {
      this.rejectMatching(requestId, asError(cause))
    }
    return waiter
  }

  async stop() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return { type: 'stopped', data: { already_stopped: true } }
    }
    const requestId = createRequestId()
    const waiter = this.createWaiter('stopped', requestId, 180_000)
    try {
      this.sendJson({
        type: 'stop',
        request_id: requestId,
      })
    } catch (cause) {
      this.rejectMatching(requestId, asError(cause))
    }
    return waiter
  }

  sendAudio(pcm) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return
    }
    const payload = pcm.buffer.slice(
      pcm.byteOffset,
      pcm.byteOffset + pcm.byteLength,
    )
    this.socket.send(payload)
  }

  disconnect() {
    this.stopHeartbeat()
    this.rejectAll(new Error('连接已关闭'))
    this.detachSocket()
  }

  waitForOpen(socket) {
    if (socket.readyState === WebSocket.OPEN) {
      return Promise.resolve()
    }
    if (socket.readyState === WebSocket.CLOSED) {
      return Promise.reject(new Error('WebSocket 已关闭'))
    }

    return new Promise((resolve, reject) => {
      const timer = globalThis.setTimeout(() => {
        const error = new Error('连接后端超时，请确认服务已启动')
        this.dropSocket(socket, error)
        reject(error)
      }, CONNECT_TIMEOUT_MS)

      socket.addEventListener(
        'open',
        () => {
          globalThis.clearTimeout(timer)
          resolve()
        },
        { once: true },
      )
      socket.addEventListener(
        'error',
        () => {
          globalThis.clearTimeout(timer)
          const error = new Error(
            '无法连接语音识别服务，请先在扩展设置中完成本地网络授权',
          )
          this.dropSocket(socket, error)
          reject(error)
        },
        { once: true },
      )
    })
  }

  createWaiter(type, requestId, timeoutMs) {
    return new Promise((resolve, reject) => {
      const timer = globalThis.setTimeout(() => {
        this.waiters = this.waiters.filter((item) => item.timer !== timer)
        reject(new Error(`等待 ${type} 响应超时`))
      }, timeoutMs)
      this.waiters.push({ type, requestId, resolve, reject, timer })
    })
  }

  handleMessage(event) {
    let message
    try {
      message = JSON.parse(event.data)
    } catch {
      this.rejectAll(new Error('后端返回了无法解析的消息'))
      return
    }

    if (message.type === 'pong') {
      if (!this.pendingPingId || message.request_id === this.pendingPingId) {
        this.clearPongTimer()
      }
      return
    }

    this.eventHandler?.(message)
    if (message.type === 'error') {
      const text = String(message.data?.message || '识别服务发生错误')
      this.rejectMatching(message.request_id, new Error(text))
      return
    }

    const index = this.waiters.findIndex(
      (waiter) =>
        waiter.type === message.type &&
        (!message.request_id || waiter.requestId === message.request_id),
    )
    if (index >= 0) {
      const [waiter] = this.waiters.splice(index, 1)
      globalThis.clearTimeout(waiter.timer)
      waiter.resolve(message)
    }
  }

  rejectMatching(requestId, error) {
    let index = this.waiters.findIndex(
      (waiter) => requestId && waiter.requestId === requestId,
    )
    if (index < 0) {
      index = this.waiters.findIndex(
        (waiter) => waiter.type === 'ready' || waiter.type === 'stopped',
      )
    }
    if (index >= 0) {
      const [waiter] = this.waiters.splice(index, 1)
      globalThis.clearTimeout(waiter.timer)
      waiter.reject(error)
    }
  }

  rejectAll(error) {
    for (const waiter of this.waiters.splice(0)) {
      globalThis.clearTimeout(waiter.timer)
      waiter.reject(error)
    }
  }

  sendJson(payload) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 未连接')
    }
    this.socket.send(JSON.stringify(payload))
  }

  startHeartbeat() {
    this.stopHeartbeat()
    this.heartbeatTimer = globalThis.setInterval(
      () => this.sendHeartbeat(),
      HEARTBEAT_INTERVAL_MS,
    )
  }

  sendHeartbeat() {
    if (
      !this.socket ||
      this.socket.readyState !== WebSocket.OPEN ||
      this.pendingPingId
    ) {
      return
    }

    const requestId = createRequestId()
    this.pendingPingId = requestId
    this.pongTimer = globalThis.setTimeout(() => {
      if (this.pendingPingId !== requestId || !this.socket) {
        return
      }
      const error = new Error('WebSocket 心跳超时，服务端无响应')
      this.dropSocket(this.socket, error)
    }, PONG_TIMEOUT_MS)

    try {
      this.sendJson({ type: 'ping', request_id: requestId })
    } catch (cause) {
      this.clearPongTimer()
      this.dropSocket(this.socket, asError(cause))
    }
  }

  handleClose(socket, event) {
    if (this.socket !== socket) {
      return
    }
    this.socket = null
    this.stopHeartbeat()
    const error = new Error(`WebSocket 已断开 (${event.code || 'unknown'})`)
    this.rejectAll(error)
    this.closeHandler?.(error)
  }

  dropSocket(socket, error) {
    if (!socket || this.socket !== socket) {
      return
    }
    this.socket = null
    this.stopHeartbeat()
    const finalError = error || new Error('WebSocket 已断开')
    this.rejectAll(finalError)
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    if (
      socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING
    ) {
      socket.close(4000, 'connection reset')
    }
    this.closeHandler?.(finalError)
  }

  detachSocket() {
    const socket = this.socket
    this.socket = null
    if (!socket) {
      return
    }
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    if (
      socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING
    ) {
      socket.close(1000, 'client closed')
    }
  }

  clearPongTimer() {
    this.pendingPingId = null
    if (this.pongTimer !== null) {
      globalThis.clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  stopHeartbeat() {
    this.clearPongTimer()
    if (this.heartbeatTimer !== null) {
      globalThis.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }
}

function createRequestId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
}

function asError(cause) {
  return cause instanceof Error ? cause : new Error(String(cause))
}
