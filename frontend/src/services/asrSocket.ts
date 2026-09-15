import type { ServerMessage, StartSessionOptions } from '../types/asr'

interface Waiter {
  type: string
  resolve: (message: ServerMessage) => void
  reject: (error: Error) => void
  timer: number
}

const HEARTBEAT_INTERVAL_MS = 15_000
const PONG_TIMEOUT_MS = 10_000

export class AsrSocketClient {
  private socket: WebSocket | null = null
  private eventHandler: ((message: ServerMessage) => void) | null = null
  private closeHandler: ((error: Error) => void) | null = null
  private waiters: Waiter[] = []
  private heartbeatTimer: number | null = null
  private pongTimer: number | null = null
  private pendingPingId: string | null = null

  onEvent(handler: (message: ServerMessage) => void): void {
    this.eventHandler = handler
  }

  onClose(handler: (error: Error) => void): void {
    this.closeHandler = handler
  }

  async connect(url: string): Promise<void> {
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
      // onclose handles recovery. Keeping onerror side-effect free avoids
      // rejecting start waiters twice.
    }
    socket.onclose = (event) => this.handleClose(socket, event)

    await this.waitForOpen(socket)
    if (this.socket !== socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 连接尚未就绪')
    }
    this.startHeartbeat()
  }

  async start(options: StartSessionOptions): Promise<ServerMessage> {
    const requestId = crypto.randomUUID()
    const waiter = this.createWaiter('ready', 180_000)
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

  async stop(): Promise<ServerMessage> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return { type: 'stopped', data: { already_stopped: true } }
    }
    const requestId = crypto.randomUUID()
    const waiter = this.createWaiter('stopped', 180_000)
    try {
      this.sendJson({ type: 'stop', request_id: requestId })
    } catch (cause) {
      this.rejectMatching(requestId, asError(cause))
    }
    return waiter
  }

  sendAudio(pcm: Int16Array): void {
    // Capture may continue briefly during an automatic reconnect. Dropping
    // those packets is safer than throwing inside the AudioWorklet callback.
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return
    }
    const payload = pcm.buffer.slice(
      pcm.byteOffset,
      pcm.byteOffset + pcm.byteLength,
    ) as ArrayBuffer
    this.socket.send(payload)
  }

  disconnect(): void {
    this.stopHeartbeat()
    this.rejectAll(new Error('连接已关闭'))
    this.detachSocket()
  }

  private waitForOpen(socket: WebSocket): Promise<void> {
    if (socket.readyState === WebSocket.OPEN) {
      return Promise.resolve()
    }
    if (socket.readyState === WebSocket.CLOSED) {
      return Promise.reject(new Error('WebSocket 已关闭'))
    }

    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        const error = new Error('连接后端超时，请确认服务已启动')
        this.dropSocket(socket, error)
        reject(error)
      }, 10_000)

      socket.addEventListener(
        'open',
        () => {
          window.clearTimeout(timer)
          resolve()
        },
        { once: true },
      )
      socket.addEventListener(
        'error',
        () => {
          window.clearTimeout(timer)
          const error = new Error('无法连接语音识别服务')
          this.dropSocket(socket, error)
          reject(error)
        },
        { once: true },
      )
    })
  }

  private createWaiter(type: string, timeoutMs: number): Promise<ServerMessage> {
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.waiters = this.waiters.filter((item) => item.timer !== timer)
        reject(new Error(`等待 ${type} 响应超时`))
      }, timeoutMs)
      this.waiters.push({ type, resolve, reject, timer })
    })
  }

  private handleMessage(event: MessageEvent<string>): void {
    let message: ServerMessage
    try {
      message = JSON.parse(event.data) as ServerMessage
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

    const index = this.waiters.findIndex((waiter) => waiter.type === message.type)
    if (index >= 0) {
      const [waiter] = this.waiters.splice(index, 1)
      window.clearTimeout(waiter.timer)
      waiter.resolve(message)
    }
  }

  private rejectMatching(requestId: string | undefined, error: Error): void {
    const index = this.waiters.findIndex(
      (waiter) => !requestId || waiter.type === 'ready' || waiter.type === 'stopped',
    )
    if (index >= 0) {
      const [waiter] = this.waiters.splice(index, 1)
      window.clearTimeout(waiter.timer)
      waiter.reject(error)
    }
  }

  private rejectAll(error: Error): void {
    for (const waiter of this.waiters.splice(0)) {
      window.clearTimeout(waiter.timer)
      waiter.reject(error)
    }
  }

  private sendJson(payload: unknown): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 未连接')
    }
    this.socket.send(JSON.stringify(payload))
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => this.sendHeartbeat(), HEARTBEAT_INTERVAL_MS)
  }

  private sendHeartbeat(): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN || this.pendingPingId) {
      return
    }

    const requestId = crypto.randomUUID()
    this.pendingPingId = requestId
    this.pongTimer = window.setTimeout(() => {
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
      const error = cause instanceof Error ? cause : new Error(String(cause))
      this.dropSocket(this.socket, error)
    }
  }

  private handleClose(socket: WebSocket, event: CloseEvent): void {
    if (this.socket !== socket) {
      return
    }
    this.socket = null
    this.stopHeartbeat()
    const error = new Error(`WebSocket 已断开 (${event.code || 'unknown'})`)
    this.rejectAll(error)
    this.closeHandler?.(error)
  }

  private dropSocket(socket: WebSocket | null, error?: Error): void {
    if (!socket || this.socket !== socket) {
      return
    }
    this.socket = null
    this.stopHeartbeat()
    this.rejectAll(error || new Error('WebSocket 已断开'))
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(4000, 'connection reset')
    }
    this.closeHandler?.(error || new Error('WebSocket 已断开'))
  }

  private detachSocket(): void {
    const socket = this.socket
    this.socket = null
    if (!socket) {
      return
    }
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(1000, 'client closed')
    }
  }

  private clearPongTimer(): void {
    this.pendingPingId = null
    if (this.pongTimer !== null) {
      window.clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private stopHeartbeat(): void {
    this.clearPongTimer()
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }
}

function asError(cause: unknown): Error {
  return cause instanceof Error ? cause : new Error(String(cause))
}
