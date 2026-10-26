import { AsrSocketClient } from '../shared/asr-socket.js'
import {
  DEFAULT_SETTINGS,
  buildStartOptions,
  normalizeWebSocketUrl,
} from '../shared/config.js'
import { StreamingPcm16Resampler } from '../shared/resampler.js'

const RECONNECT_DELAYS_MS = [800, 1600, 3200, 6400, 10_000]

let runtimeState = createInitialState()
let mediaStream = null
let audioContext = null
let sourceNode = null
let workletNode = null
let silentGain = null
let resampler = null
let client = null
let sessionOptions = null
let active = false
let stopping = false
let reconnecting = false
let failing = false
let reconnectToken = 0
let pendingFlushResolve = null

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target !== 'offscreen') {
    return false
  }

  switch (message.type) {
    case 'GET_STATE':
      sendResponse({ ok: true, state: runtimeState })
      return false
    case 'CAPTURE_START':
      void startCapture(message)
        .then(() => sendResponse({ ok: true }))
        .catch((error) => sendResponse({ ok: false, error: toMessage(error) }))
      return true
    case 'CAPTURE_STOP':
      void stopCapture()
      sendResponse({ ok: true })
      return false
    case 'CLEAR_TRANSCRIPT':
      runtimeState = {
        ...runtimeState,
        finals: [],
        partial: null,
        sessionAudioId: '',
      }
      void publishState()
      sendResponse({ ok: true })
      return false
    default:
      sendResponse({ ok: false, error: `未知消息类型: ${message.type}` })
      return false
  }
})

async function startCapture(message) {
  if (active) {
    throw new Error('标签页音频采集已启动')
  }

  await releaseAudioGraph()
  runtimeState = {
    ...createInitialState(),
    status: 'connecting',
    tabId: message.tabId,
    tabTitle: String(message.tabTitle || ''),
    tabUrl: String(message.tabUrl || ''),
    serverUrl: normalizeWebSocketUrl(message.serverUrl),
    startedAt: Date.now(),
  }
  stopping = false
  reconnectToken += 1
  sessionOptions =
    message.options || buildStartOptions(message.settings || DEFAULT_SETTINGS)
  void publishState()

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: message.streamId,
        },
      },
      video: false,
    })
  } catch (error) {
    runtimeState = {
      ...runtimeState,
      status: 'error',
      error: explainCaptureError(error),
    }
    void publishState()
    throw new Error(runtimeState.error)
  }

  const track = mediaStream.getAudioTracks()[0]
  if (!track) {
    await releaseAudioGraph()
    throw new Error('当前标签页没有可采集的音轨')
  }

  active = true
  track.addEventListener('ended', handleTrackEnded)
  void initializeSession(message)
}

async function initializeSession(message) {
  try {
    audioContext = new AudioContext({ latencyHint: 'interactive' })
    if (audioContext.state === 'suspended') {
      await audioContext.resume()
    }

    sourceNode = audioContext.createMediaStreamSource(mediaStream)
    // tabCapture consumes the tab audio. Routing it to the output keeps the
    // captured page audible while the worklet taps the same stream.
    sourceNode.connect(audioContext.destination)

    await connectClient(message)
    if (!active || stopping) {
      client?.disconnect()
      return
    }

    await audioContext.audioWorklet.addModule(
      chrome.runtime.getURL('audio/pcm-capture-worklet.js'),
    )
    if (!active || stopping) {
      client?.disconnect()
      return
    }

    workletNode = new AudioWorkletNode(audioContext, 'pcm-capture', {
      processorOptions: { chunkDurationMs: 160 },
    })
    silentGain = audioContext.createGain()
    silentGain.gain.value = 0
    resampler = new StreamingPcm16Resampler(audioContext.sampleRate, 16000)
    workletNode.port.onmessage = handleWorkletMessage
    sourceNode.connect(workletNode)
    workletNode.connect(silentGain)
    silentGain.connect(audioContext.destination)

    runtimeState = {
      ...runtimeState,
      status: 'listening',
      error: '',
    }
    void publishState()
  } catch (error) {
    if (!stopping) {
      await failSession(error)
    }
  }
}

async function connectClient(message) {
  client = createClient()
  await client.connect(runtimeState.serverUrl)
  await client.start(
    sessionOptions || message.options || buildStartOptions(DEFAULT_SETTINGS),
  )
}

function createClient() {
  const nextClient = new AsrSocketClient()
  nextClient.onEvent(handleServerEvent)
  nextClient.onClose(handleClientClose)
  return nextClient
}

async function recoverConnection(cause) {
  if (reconnecting || !active || stopping) {
    return
  }

  reconnecting = true
  const token = ++reconnectToken
  runtimeState = {
    ...runtimeState,
    status: 'reconnecting',
    error: `${toMessage(cause)}，正在自动重连`,
  }
  void publishState()

  try {
    for (let attempt = 0; attempt < RECONNECT_DELAYS_MS.length; attempt += 1) {
      await delay(RECONNECT_DELAYS_MS[attempt])
      if (!active || stopping || token !== reconnectToken) {
        return
      }

      try {
        client?.disconnect()
        client = createClient()
        await client.connect(runtimeState.serverUrl)
        await client.start(sessionOptions || buildStartOptions(DEFAULT_SETTINGS))
        if (!active || stopping || token !== reconnectToken) {
          client.disconnect()
          return
        }
        runtimeState = {
          ...runtimeState,
          status: 'listening',
          error: '',
        }
        void publishState()
        return
      } catch (error) {
        client?.disconnect()
        runtimeState = {
          ...runtimeState,
          status: 'reconnecting',
          error: `连接中断：${toMessage(error)}（${attempt + 1}/${RECONNECT_DELAYS_MS.length}）`,
        }
        void publishState()
      }
    }
    await failSession(new Error('多次重连失败，请检查后端服务'))
  } finally {
    if (token === reconnectToken) {
      reconnecting = false
    }
  }
}

function handleServerEvent(message) {
  const data = message.data || {}
  switch (message.type) {
    case 'ready':
      runtimeState = {
        ...runtimeState,
        sessionAudioId: String(data.session_audio_id || ''),
      }
      break
    case 'status': {
      const serverState = String(data.state || '')
      if (
        ['listening', 'speech', 'processing', 'stopped'].includes(serverState)
      ) {
        runtimeState = { ...runtimeState, status: serverState }
      }
      break
    }
    case 'level':
      runtimeState = {
        ...runtimeState,
        level: clampNumber(data.energy, 0, 1),
      }
      void publishLevel()
      return
    case 'partial': {
      const text = String(data.text || '').trim()
      runtimeState = {
        ...runtimeState,
        partial: text
          ? {
              id: String(data.segment_id || 'partial'),
              text,
              speakerId: String(data.speaker_id || ''),
              speakerName: String(data.speaker_name || ''),
              speakerPending: Boolean(data.speaker_pending),
              speakerEnrolled: Boolean(data.speaker_enrolled),
            }
          : null,
      }
      break
    }
    case 'final': {
      const text = String(data.text || '').trim()
      if (text) {
        runtimeState = {
          ...runtimeState,
          finals: [
            ...runtimeState.finals,
            {
              id: String(data.segment_id || createId()),
              text,
              createdAt: Date.now(),
              latencyMs: Number(data.latency_ms || 0) || null,
              audioId: String(data.audio_id || ''),
              speakerId: String(data.speaker_id || ''),
              speakerName: String(data.speaker_name || ''),
              speakerPending: Boolean(data.speaker_pending),
              speakerEnrolled: Boolean(data.speaker_enrolled),
            },
          ],
        }
      }
      runtimeState = { ...runtimeState, partial: null }
      break
    }
    case 'speaker': {
      const speakerId = String(data.speaker_id || '')
      if (runtimeState.partial && speakerId && !runtimeState.partial.speakerId) {
        runtimeState = {
          ...runtimeState,
          partial: {
            ...runtimeState.partial,
            speakerId,
            speakerName: String(data.speaker_name || ''),
            speakerEnrolled: Boolean(data.speaker_enrolled),
          },
        }
      }
      break
    }
    case 'error': {
      const error = String(data.message || '识别服务发生错误')
      void failSession(new Error(error))
      return
    }
    default:
      return
  }
  void publishState()
}

function handleClientClose(error) {
  if (stopping || reconnecting || !active) {
    return
  }
  void recoverConnection(error)
}

function handleWorkletMessage(event) {
  if (event.data?.flushed) {
    pendingFlushResolve?.()
    pendingFlushResolve = null
    return
  }
  const samples = event.data?.samples
  if (!(samples instanceof Float32Array) || !resampler) {
    return
  }
  runtimeState = {
    ...runtimeState,
    level: clampNumber(event.data.level, 0, 1),
  }
  void publishLevel()
  client?.sendAudio(resampler.process(samples))
}

function handleTrackEnded() {
  if (stopping || !active) {
    return
  }
  runtimeState = {
    ...runtimeState,
    status: 'error',
    error: '标签页音频采集已结束',
    level: 0,
  }
  void publishState()
  void stopCapture({ preserveError: true })
}

async function stopCapture(options = {}) {
  if (stopping) {
    return
  }
  if (!active && !client && !mediaStream) {
    if (!options.preserveError) {
      runtimeState = {
        ...runtimeState,
        status: runtimeState.status === 'error' ? 'error' : 'idle',
        level: 0,
        partial: null,
      }
      void publishState()
    }
    return
  }

  stopping = true
  reconnectToken += 1
  runtimeState = { ...runtimeState, status: 'stopping' }
  void publishState()

  await flushWorklet()
  active = false
  await releaseAudioGraph()

  if (client) {
    try {
      await client.stop()
    } catch (error) {
      if (!options.preserveError && !runtimeState.error) {
        runtimeState = { ...runtimeState, error: toMessage(error) }
      }
    }
    client.disconnect()
    client = null
  }

  runtimeState = {
    ...runtimeState,
    status: options.preserveError && runtimeState.error ? 'error' : 'idle',
    level: 0,
    partial: null,
  }
  stopping = false
  reconnecting = false
  void publishState()
}

async function failSession(error) {
  if (failing || stopping) {
    return
  }
  failing = true
  runtimeState = {
    ...runtimeState,
    status: 'error',
    error: toMessage(error),
    level: 0,
    partial: null,
  }
  void publishState()
  await stopCapture({ preserveError: true })
  failing = false
}

async function releaseAudioGraph() {
  pendingFlushResolve?.()
  pendingFlushResolve = null
  if (workletNode) {
    workletNode.port.onmessage = null
    workletNode.disconnect()
    workletNode = null
  }
  if (silentGain) {
    silentGain.disconnect()
    silentGain = null
  }
  if (sourceNode) {
    sourceNode.disconnect()
    sourceNode = null
  }
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) {
      track.removeEventListener('ended', handleTrackEnded)
      track.stop()
    }
    mediaStream = null
  }
  if (audioContext) {
    const context = audioContext
    audioContext = null
    try {
      await context.close()
    } catch {
      // The context may already have been closed by the browser.
    }
  }
  resampler = null
}

function flushWorklet() {
  const node = workletNode
  if (!node) {
    return Promise.resolve()
  }
  return new Promise((resolve) => {
    const timer = globalThis.setTimeout(() => {
      pendingFlushResolve = null
      resolve()
    }, 200)
    pendingFlushResolve = () => {
      globalThis.clearTimeout(timer)
      resolve()
    }
    node.port.postMessage({ type: 'flush' })
  })
}

async function publishState() {
  try {
    await chrome.runtime.sendMessage({
      target: 'background',
      type: 'OFFSCREEN_STATE',
      state: runtimeState,
    })
  } catch {
    // The service worker can be restarting while capture continues.
  }
}

async function publishLevel() {
  try {
    await chrome.runtime.sendMessage({
      target: 'background',
      type: 'OFFSCREEN_LEVEL',
      level: runtimeState.level,
    })
  } catch {
    // The service worker can be restarting while capture continues.
  }
}

function createInitialState() {
  return {
    status: 'idle',
    level: 0,
    partial: null,
    finals: [],
    error: '',
    tabId: null,
    tabTitle: '',
    tabUrl: '',
    serverUrl: DEFAULT_SETTINGS.serverUrl,
    startedAt: null,
    sessionAudioId: '',
  }
}

function explainCaptureError(error) {
  const message = toMessage(error)
  if (message.includes('Cannot capture') || message.includes('not allowed')) {
    return 'Chrome 不允许采集此页面，请切换到普通网页后重试'
  }
  if (message.includes('Permission')) {
    return '标签页音频采集权限已被拒绝'
  }
  return `无法采集当前标签页音频：${message}`
}

function clampNumber(value, minimum, maximum) {
  const number = Number(value)
  if (!Number.isFinite(number)) {
    return minimum
  }
  return Math.max(minimum, Math.min(maximum, number))
}

function delay(milliseconds) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds))
}

function createId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
}

function toMessage(error) {
  return error instanceof Error ? error.message : String(error)
}
