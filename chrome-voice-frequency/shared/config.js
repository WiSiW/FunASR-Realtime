export const DEFAULT_SETTINGS = Object.freeze({
  serverUrl: 'ws://127.0.0.1:8000/api/v1/asr/stream',
  mode: 'auto',
  speakerEnabled: false,
  energyThreshold: 0.012,
})

export const RECOGNITION_MODES = Object.freeze([
  {
    id: 'auto',
    label: '自动',
    description: '自动分段并输出标点',
  },
  {
    id: 'stream',
    label: '流式',
    description: '边说边显示临时结果',
  },
  {
    id: 'push',
    label: '停止后识别',
    description: '停止采集后统一识别',
  },
])

export function normalizeWebSocketUrl(rawValue) {
  let value = String(rawValue || '').trim()
  if (!value) {
    value = DEFAULT_SETTINGS.serverUrl
  }
  if (!/^[a-z][a-z\d+.-]*:\/\//i.test(value)) {
    value = `http://${value}`
  }

  const url = new URL(value)
  if (url.protocol === 'http:') {
    url.protocol = 'ws:'
  } else if (url.protocol === 'https:') {
    url.protocol = 'wss:'
  }
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('后端地址仅支持 http、https、ws 或 wss')
  }

  const normalizedPath = url.pathname.replace(/\/+$/, '')
  url.pathname = normalizedPath || '/api/v1/asr/stream'
  url.hash = ''
  return url.toString()
}

export function buildStartOptions(settings = {}) {
  const mode = RECOGNITION_MODES.some((item) => item.id === settings.mode)
    ? settings.mode
    : DEFAULT_SETTINGS.mode
  const energyThreshold = Number(settings.energyThreshold)

  return {
    mode,
    sample_rate: 16000,
    channels: 1,
    audio_format: 'pcm_s16le',
    vad: {
      energy_threshold:
        Number.isFinite(energyThreshold) && energyThreshold > 0
          ? energyThreshold
          : DEFAULT_SETTINGS.energyThreshold,
      hangover_sec: 0.6,
      min_speech_sec: 0.25,
      max_speech_sec: 30,
      pre_roll_sec: 0.4,
    },
    speaker: {
      enabled: Boolean(settings.speakerEnabled),
      similarity_threshold: 0.7,
      new_speaker_threshold: 0.45,
      switch_margin: 0.08,
      min_segment_sec: 0.8,
      max_speakers: 8,
      embedding_window_sec: 1.5,
      embedding_interval_sec: 0.8,
      centroid_update_alpha: 0.1,
    },
  }
}

export function isActiveStatus(status) {
  return [
    'starting',
    'connecting',
    'initializing',
    'listening',
    'speech',
    'processing',
    'reconnecting',
  ].includes(status)
}
