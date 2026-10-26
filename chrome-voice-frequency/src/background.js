import {
  DEFAULT_SETTINGS,
  buildStartOptions,
  isActiveStatus,
  normalizeWebSocketUrl,
} from '../shared/config.js'

const OFFSCREEN_PATH = 'src/offscreen.html'
const OFFSCREEN_URL = chrome.runtime.getURL(OFFSCREEN_PATH)

const INITIAL_STATE = Object.freeze({
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
})

let runtimeState = { ...INITIAL_STATE }
let hydrationPromise = null
let offscreenCreationPromise = null

chrome.runtime.onInstalled.addListener(() => {
  void updateBadge()
})

chrome.runtime.onStartup.addListener(() => {
  void updateBadge()
})

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target === 'background' && message.type === 'OFFSCREEN_STATE') {
    runtimeState = { ...runtimeState, ...message.state }
    void broadcastState()
    void updateBadge()
    sendResponse?.({ ok: true })
    return false
  }

  if (message?.target === 'background' && message.type === 'OFFSCREEN_LEVEL') {
    runtimeState = {
      ...runtimeState,
      level: Number(message.level) || 0,
    }
    void broadcastLevel()
    sendResponse?.({ ok: true })
    return false
  }

  if (message?.target !== 'background') {
    return false
  }

  void handleBackgroundMessage(message)
    .then((response) => sendResponse(response))
    .catch((error) => {
      sendResponse({ ok: false, error: toMessage(error) })
    })
  return true
})

chrome.tabs.onRemoved.addListener((tabId) => {
  if (runtimeState.tabId === tabId && isActiveStatus(runtimeState.status)) {
    void stopCapture()
  }
})

async function handleBackgroundMessage(message) {
  switch (message.type) {
    case 'GET_STATE':
      await hydrateFromOffscreen()
      return { ok: true, state: runtimeState }
    case 'CAPTURE_START':
      return startCapture(message)
    case 'CAPTURE_STOP':
      return stopCapture()
    case 'CLEAR_TRANSCRIPT':
      return clearTranscript()
    default:
      throw new Error(`未知消息类型: ${message.type}`)
  }
}

async function startCapture(message) {
  if (isActiveStatus(runtimeState.status) || runtimeState.status === 'stopping') {
    throw new Error('已有标签页正在转写')
  }
  if (!message.streamId || !Number.isInteger(message.tabId)) {
    throw new Error('标签页采集参数无效')
  }

  const serverUrl = normalizeWebSocketUrl(message.serverUrl)
  const settings = {
    mode: message.settings?.mode,
    speakerEnabled: Boolean(message.settings?.speakerEnabled),
    energyThreshold: message.settings?.energyThreshold,
  }

  runtimeState = {
    ...INITIAL_STATE,
    status: 'starting',
    tabId: message.tabId,
    tabTitle: String(message.tabTitle || ''),
    tabUrl: String(message.tabUrl || ''),
    serverUrl,
    startedAt: Date.now(),
  }
  void broadcastState()
  void updateBadge()

  try {
    const response = await sendToOffscreen({
      type: 'CAPTURE_START',
      streamId: message.streamId,
      tabId: message.tabId,
      tabTitle: runtimeState.tabTitle,
      tabUrl: runtimeState.tabUrl,
      serverUrl,
      settings,
      options: buildStartOptions(settings),
    })
    if (!response?.ok) {
      throw new Error(response?.error || '无法启动标签页音频采集')
    }
    return { ok: true }
  } catch (error) {
    runtimeState = {
      ...runtimeState,
      status: 'error',
      error: toMessage(error),
      level: 0,
    }
    void broadcastState()
    void updateBadge()
    throw error
  }
}

async function stopCapture() {
  if (!isActiveStatus(runtimeState.status) && runtimeState.status !== 'error') {
    return { ok: true }
  }

  runtimeState = { ...runtimeState, status: 'stopping', error: '' }
  void broadcastState()
  void updateBadge()

  if (await hasOffscreenDocument()) {
    try {
      await sendToOffscreen({ type: 'CAPTURE_STOP' })
    } catch {
      runtimeState = {
        ...runtimeState,
        status: 'idle',
        level: 0,
        partial: null,
      }
      void broadcastState()
      void updateBadge()
    }
  } else {
    runtimeState = {
      ...runtimeState,
      status: 'idle',
      level: 0,
      partial: null,
    }
    void broadcastState()
    void updateBadge()
  }
  return { ok: true }
}

async function clearTranscript() {
  if (await hasOffscreenDocument()) {
    await sendToOffscreen({ type: 'CLEAR_TRANSCRIPT' })
  } else {
    runtimeState = {
      ...runtimeState,
      finals: [],
      partial: null,
      sessionAudioId: '',
    }
    void broadcastState()
  }
  return { ok: true }
}

async function hydrateFromOffscreen() {
  if (hydrationPromise) {
    return hydrationPromise
  }
  hydrationPromise = (async () => {
    if (!(await hasOffscreenDocument())) {
      return
    }
    try {
      const response = await chrome.runtime.sendMessage({
        target: 'offscreen',
        type: 'GET_STATE',
      })
      if (response?.ok && response.state) {
        runtimeState = { ...runtimeState, ...response.state }
        void updateBadge()
      }
    } catch {
      // The offscreen document may be closing. Keep the last known state.
    }
  })().finally(() => {
    hydrationPromise = null
  })
  return hydrationPromise
}

async function sendToOffscreen(message) {
  await ensureOffscreenDocument()
  return chrome.runtime.sendMessage({
    target: 'offscreen',
    ...message,
  })
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) {
    return
  }
  if (offscreenCreationPromise) {
    return offscreenCreationPromise
  }

  offscreenCreationPromise = (async () => {
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_PATH,
        reasons: ['USER_MEDIA'],
        justification: 'Capture tab audio and stream PCM data to the ASR backend.',
      })
    } catch (error) {
      if (!toMessage(error).includes('Only a single offscreen document')) {
        throw error
      }
    }
  })().finally(() => {
    offscreenCreationPromise = null
  })
  return offscreenCreationPromise
}

async function hasOffscreenDocument() {
  if (!chrome.runtime.getContexts) {
    return false
  }
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [OFFSCREEN_URL],
  })
  return contexts.length > 0
}

async function broadcastState() {
  try {
    await chrome.runtime.sendMessage({
      target: 'popup',
      type: 'STATE_UPDATE',
      state: runtimeState,
    })
  } catch {
    // Popup is optional and often closed.
  }
}

async function broadcastLevel() {
  try {
    await chrome.runtime.sendMessage({
      target: 'popup',
      type: 'LEVEL_UPDATE',
      level: runtimeState.level,
    })
  } catch {
    // Popup is optional and often closed.
  }
}

async function updateBadge() {
  const active = isActiveStatus(runtimeState.status)
  const text = active
    ? 'REC'
    : runtimeState.status === 'error'
      ? '!'
      : ''
  await chrome.action.setBadgeBackgroundColor({
    color: runtimeState.status === 'error' ? '#d95c66' : '#23b889',
  })
  await chrome.action.setBadgeText({ text })
}

function toMessage(error) {
  return error instanceof Error ? error.message : String(error)
}
