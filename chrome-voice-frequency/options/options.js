import {
  DEFAULT_SETTINGS,
  normalizeWebSocketUrl,
} from '../shared/config.js'

const serverUrlInput = document.querySelector('#serverUrl')
const authorizeButton = document.querySelector('#authorizeButton')
const statusMessage = document.querySelector('#statusMessage')

let settings = { ...DEFAULT_SETTINGS }

authorizeButton.addEventListener('click', () => {
  void authorizeAndCheck()
})

void initialize()

async function initialize() {
  const stored = await chrome.storage.local.get({
    serverUrl: DEFAULT_SETTINGS.serverUrl,
  })
  settings = {
    ...settings,
    serverUrl: normalizeWebSocketUrl(stored.serverUrl),
  }
  serverUrlInput.value = settings.serverUrl
}

async function authorizeAndCheck() {
  authorizeButton.disabled = true
  setStatus('正在请求本地网络访问权限…', '')

  try {
    settings = {
      ...settings,
      serverUrl: normalizeWebSocketUrl(serverUrlInput.value),
    }
    serverUrlInput.value = settings.serverUrl
    await chrome.storage.local.set({ serverUrl: settings.serverUrl })

    const healthUrl = websocketUrlToHealthUrl(settings.serverUrl)
    const response = await fetch(healthUrl, {
      cache: 'no-store',
      credentials: 'omit',
    })
    if (!response.ok) {
      throw new Error(`后端返回 HTTP ${response.status}`)
    }

    const payload = await response.json()
    const service = String(payload.service || 'FunASR Realtime API')
    await probeWebSocket(settings.serverUrl)
    const permissionState = await getPermissionState()
    setStatus(
      `连接成功：${service}。WebSocket 握手正常，本地网络权限：${permissionState}`,
      'success',
    )
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    setStatus(
      `连接失败：${message}。请在浏览器提示中允许本地网络访问，并确认后端已启动。`,
      'error',
    )
  } finally {
    authorizeButton.disabled = false
  }
}

async function getPermissionState() {
  if (!navigator.permissions?.query) {
    return 'unsupported'
  }
  try {
    const status = await navigator.permissions.query({
      name: 'local-network-access',
    })
    return status.state
  } catch {
    return 'unknown'
  }
}

function websocketUrlToHealthUrl(serverUrl) {
  const url = new URL(serverUrl)
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:'
  url.pathname = '/api/v1/health'
  url.search = ''
  url.hash = ''
  return url.toString()
}

function probeWebSocket(serverUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(serverUrl)
    const timer = window.setTimeout(() => {
      socket.close()
      reject(new Error('WebSocket 握手超时'))
    }, 8000)

    socket.onmessage = (event) => {
      const data = String(event.data)
      if (!data.includes('"type":"connected"')) {
        return
      }
      window.clearTimeout(timer)
      socket.close(1000, 'authorization complete')
      resolve()
    }
    socket.onerror = () => {
      window.clearTimeout(timer)
      socket.close()
      reject(
        new Error(
          'WebSocket 被浏览器拦截，请允许扩展访问本地网络后重试',
        ),
      )
    }
    socket.onclose = (event) => {
      if (event.code !== 1000) {
        window.clearTimeout(timer)
        reject(new Error(`WebSocket 已关闭 (${event.code || 'unknown'})`))
      }
    }
  })
}

function setStatus(message, state) {
  statusMessage.textContent = message
  statusMessage.className = state
}
