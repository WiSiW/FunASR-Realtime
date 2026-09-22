import {
  DEFAULT_SETTINGS,
  isActiveStatus,
  normalizeWebSocketUrl,
} from '../shared/config.js'

const elements = {
  tabTitle: document.querySelector('#tabTitle'),
  statusPill: document.querySelector('#statusPill'),
  serverUrl: document.querySelector('#serverUrl'),
  modeGroup: document.querySelector('#modeGroup'),
  speakerEnabled: document.querySelector('#speakerEnabled'),
  energyThreshold: document.querySelector('#energyThreshold'),
  energyValue: document.querySelector('#energyValue'),
  levelMeter: document.querySelector('#levelMeter'),
  captureButton: document.querySelector('#captureButton'),
  captureLabel: document.querySelector('#captureLabel'),
  errorMessage: document.querySelector('#errorMessage'),
  segmentCount: document.querySelector('#segmentCount'),
  transcriptBody: document.querySelector('#transcriptBody'),
  copyButton: document.querySelector('#copyButton'),
  clearButton: document.querySelector('#clearButton'),
  openOptionsButton: document.querySelector('#openOptionsButton'),
}

const meterBars = Array.from({ length: 28 }, () => {
  const bar = document.createElement('span')
  elements.levelMeter.append(bar)
  return bar
})

let settings = { ...DEFAULT_SETTINGS }
let runtimeState = null
let currentTab = null
let localError = ''
let transcriptSignature = ''
let copyResetTimer = null

elements.captureButton.addEventListener('click', () => {
  if (isActiveStatus(runtimeState?.status)) {
    void stopCapture()
  } else {
    void startCapture()
  }
})

elements.modeGroup.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-mode]')
  if (!button || button.disabled) {
    return
  }
  settings = { ...settings, mode: button.dataset.mode }
  void saveSettings()
  renderMode()
})

elements.serverUrl.addEventListener('change', () => {
  try {
    settings = {
      ...settings,
      serverUrl: normalizeWebSocketUrl(elements.serverUrl.value),
    }
    elements.serverUrl.value = settings.serverUrl
    localError = ''
    renderError()
    void saveSettings()
  } catch (error) {
    showError(error)
  }
})

elements.speakerEnabled.addEventListener('change', () => {
  settings = {
    ...settings,
    speakerEnabled: elements.speakerEnabled.checked,
  }
  void saveSettings()
})

elements.energyThreshold.addEventListener('input', () => {
  const value = Number(elements.energyThreshold.value)
  settings = { ...settings, energyThreshold: value }
  elements.energyValue.textContent = value.toFixed(3)
  void saveSettings()
})

elements.copyButton.addEventListener('click', () => {
  void copyTranscript()
})

elements.clearButton.addEventListener('click', () => {
  void chrome.runtime.sendMessage({
    target: 'background',
    type: 'CLEAR_TRANSCRIPT',
  })
})

elements.openOptionsButton.addEventListener('click', () => {
  void chrome.runtime.openOptionsPage()
})

chrome.runtime.onMessage.addListener((message) => {
  if (message?.target === 'popup' && message.type === 'STATE_UPDATE') {
    runtimeState = message.state
    render()
  }
  if (message?.target === 'popup' && message.type === 'LEVEL_UPDATE') {
    runtimeState = {
      ...(runtimeState || {}),
      level: message.level,
    }
    renderLevel(Number(message.level) || 0)
  }
  return false
})

void initialize()

async function initialize() {
  const [settingsResult, tabsResult, stateResult] = await Promise.allSettled([
    chrome.storage.local.get({
      serverUrl: DEFAULT_SETTINGS.serverUrl,
      mode: DEFAULT_SETTINGS.mode,
      speakerEnabled: DEFAULT_SETTINGS.speakerEnabled,
      energyThreshold: DEFAULT_SETTINGS.energyThreshold,
    }),
    chrome.tabs.query({ active: true, currentWindow: true }),
    chrome.runtime.sendMessage({
      target: 'background',
      type: 'GET_STATE',
    }),
  ])

  if (settingsResult.status === 'fulfilled') {
    settings = normalizeSettings(settingsResult.value)
  }
  if (tabsResult.status === 'fulfilled') {
    currentTab = tabsResult.value[0] || null
  }
  if (stateResult.status === 'fulfilled') {
    runtimeState = stateResult.value?.state || null
  } else {
    showError(stateResult.reason)
  }
  applySettings()
  render()
}

async function startCapture() {
  let tab = currentTab
  if (!Number.isInteger(tab?.id) || tab.id < 0) {
    try {
      const tabs = await chrome.tabs.query({
        active: true,
        currentWindow: true,
      })
      tab = tabs[0] || null
      currentTab = tab
    } catch (error) {
      showError(error)
      return
    }
  }
  if (!Number.isInteger(tab?.id) || tab.id < 0) {
    showError(new Error('没有找到活动标签页，请关闭弹窗后重新打开'))
    return
  }
  if (isRestrictedUrl(tab.url)) {
    showError(new Error('Chrome 内部页面和扩展页面无法采集音频'))
    return
  }

  localError = ''
  renderError()
  elements.captureButton.disabled = true

  try {
    settings = {
      ...settings,
      serverUrl: normalizeWebSocketUrl(elements.serverUrl.value),
    }
    elements.serverUrl.value = settings.serverUrl

    const streamId = await chrome.tabCapture.getMediaStreamId({
      targetTabId: tab.id,
    })
    void saveSettings()
    const response = await chrome.runtime.sendMessage({
      target: 'background',
      type: 'CAPTURE_START',
      streamId,
      tabId: tab.id,
      tabTitle: tab.title || '',
      tabUrl: tab.url || '',
      serverUrl: settings.serverUrl,
      settings,
    })
    if (!response?.ok) {
      throw new Error(response?.error || '无法启动标签页采集')
    }
  } catch (error) {
    showError(error)
    elements.captureButton.disabled = false
  }
}

async function stopCapture() {
  elements.captureButton.disabled = true
  try {
    const response = await chrome.runtime.sendMessage({
      target: 'background',
      type: 'CAPTURE_STOP',
    })
    if (!response?.ok) {
      throw new Error(response?.error || '停止采集失败')
    }
  } catch (error) {
    showError(error)
    elements.captureButton.disabled = false
  }
}

function render() {
  const status = runtimeState?.status || 'idle'
  const active = isActiveStatus(status)
  const busy = status === 'starting' || status === 'stopping'

  elements.tabTitle.textContent =
    runtimeState?.tabTitle || currentTab?.title || '当前标签页'
  elements.statusPill.textContent = statusLabel(status)
  elements.statusPill.dataset.state = status === 'error'
    ? 'error'
    : busy
      ? 'busy'
      : active
        ? 'active'
        : 'idle'

  elements.captureButton.classList.toggle('stop', active)
  elements.captureButton.disabled = busy
  elements.captureLabel.textContent = active
    ? settings.mode === 'push'
      ? '停止并识别'
      : '停止转写'
    : '开始转写'

  elements.serverUrl.disabled = active || busy
  elements.speakerEnabled.disabled = active || busy
  elements.energyThreshold.disabled = active || busy
  for (const button of elements.modeGroup.querySelectorAll('button')) {
    button.disabled = active || busy
  }

  renderLevel(Number(runtimeState?.level || 0))
  renderError()
  renderTranscript()
}

function renderMode() {
  for (const button of elements.modeGroup.querySelectorAll('button')) {
    button.classList.toggle('selected', button.dataset.mode === settings.mode)
    button.setAttribute(
      'aria-checked',
      String(button.dataset.mode === settings.mode),
    )
  }
}

function renderLevel(value) {
  const level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0
  const litCount = level <= 0.001 ? 0 : Math.ceil(level * meterBars.length)
  meterBars.forEach((bar, index) => {
    bar.classList.toggle('lit', index < litCount)
    bar.classList.toggle(
      'peak',
      index < litCount && index >= Math.floor(meterBars.length * 0.82),
    )
  })
}

function renderError() {
  const message = localError || runtimeState?.error || ''
  elements.errorMessage.hidden = !message
  elements.errorMessage.textContent = message
}

function renderTranscript() {
  const finals = runtimeState?.finals || []
  const partial = runtimeState?.partial || null
  const signature = JSON.stringify({ finals, partial })
  if (signature === transcriptSignature) {
    return
  }
  transcriptSignature = signature

  elements.segmentCount.textContent = `${finals.length} 段`
  elements.copyButton.disabled = finals.length === 0 && !partial
  elements.clearButton.disabled = finals.length === 0 && !partial

  const shouldStickToBottom =
    elements.transcriptBody.scrollHeight -
      elements.transcriptBody.scrollTop -
      elements.transcriptBody.clientHeight <
    36

  elements.transcriptBody.replaceChildren()
  if (finals.length === 0 && !partial) {
    const empty = document.createElement('p')
    empty.className = 'empty-state'
    empty.textContent = '打开需要转写的标签页并开始采集'
    elements.transcriptBody.append(empty)
    return
  }

  finals.forEach((segment, index) => {
    elements.transcriptBody.append(
      createTranscriptLine(segment, String(index + 1), false),
    )
  })
  if (partial) {
    elements.transcriptBody.append(
      createTranscriptLine(partial, 'NOW', true),
    )
  }

  if (shouldStickToBottom) {
    elements.transcriptBody.scrollTop = elements.transcriptBody.scrollHeight
  }
}

function createTranscriptLine(segment, index, isPartial) {
  const line = document.createElement('article')
  line.className = `transcript-line${isPartial ? ' partial' : ''}`

  const indexNode = document.createElement('span')
  indexNode.className = 'transcript-index'
  indexNode.textContent = index

  const content = document.createElement('div')
  content.className = 'transcript-content'
  const speaker = formatSpeaker(segment)
  if (speaker) {
    const tag = document.createElement('span')
    tag.className = 'speaker-tag'
    tag.textContent = speaker
    content.append(tag)
  }
  const text = document.createElement('p')
  text.textContent = segment.text
  content.append(text)

  line.append(indexNode, content)

  if (segment.latencyMs) {
    const latency = document.createElement('span')
    latency.className = 'latency'
    latency.textContent = `${segment.latencyMs} ms`
    line.append(latency)
  }
  return line
}

async function copyTranscript() {
  const finals = runtimeState?.finals || []
  const partial = runtimeState?.partial
  const lines = finals.map(formatTranscriptLine)
  if (partial) {
    lines.push(formatTranscriptLine(partial))
  }
  const text = lines.filter(Boolean).join('\n')
  if (!text) {
    return
  }

  try {
    await navigator.clipboard.writeText(text)
    elements.copyButton.textContent = '已复制'
    if (copyResetTimer !== null) {
      clearTimeout(copyResetTimer)
    }
    copyResetTimer = window.setTimeout(() => {
      elements.copyButton.textContent = '复制'
      copyResetTimer = null
    }, 1200)
  } catch (error) {
    showError(error)
  }
}

function formatTranscriptLine(segment) {
  const speaker = formatSpeaker(segment)
  return speaker ? `[${speaker}] ${segment.text}` : segment.text
}

function formatSpeaker(segment) {
  if (!segment?.speakerId) {
    return ''
  }
  if (segment.speakerName) {
    return `${segment.speakerId} · ${segment.speakerName}`
  }
  if (segment.speakerPending) {
    return `${segment.speakerId} 待确认`
  }
  return segment.speakerId
}

function applySettings() {
  elements.serverUrl.value = settings.serverUrl
  elements.speakerEnabled.checked = settings.speakerEnabled
  elements.energyThreshold.value = String(settings.energyThreshold)
  elements.energyValue.textContent = settings.energyThreshold.toFixed(3)
  renderMode()
}

function normalizeSettings(raw) {
  try {
    return {
      serverUrl: normalizeWebSocketUrl(raw.serverUrl),
      mode: ['auto', 'stream', 'push'].includes(raw.mode)
        ? raw.mode
        : DEFAULT_SETTINGS.mode,
      speakerEnabled: Boolean(raw.speakerEnabled),
      energyThreshold: Number.isFinite(Number(raw.energyThreshold))
        ? Number(raw.energyThreshold)
        : DEFAULT_SETTINGS.energyThreshold,
    }
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

async function saveSettings() {
  await chrome.storage.local.set(settings)
}

function showError(error) {
  localError = error instanceof Error ? error.message : String(error)
  renderError()
}

function statusLabel(status) {
  const labels = {
    idle: '待机',
    starting: '启动中',
    connecting: '连接中',
    initializing: '加载模型',
    listening: '监听中',
    speech: '检测到语音',
    processing: '识别中',
    reconnecting: '重连中',
    stopping: '正在停止',
    stopped: '已停止',
    error: '异常',
  }
  return labels[status] || status
}

function isRestrictedUrl(url) {
  return /^(chrome|edge|about|devtools|chrome-extension|edge-extension):/i.test(
    String(url || ''),
  )
}
