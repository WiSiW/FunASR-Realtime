import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AsrSocketClient } from '../services/asrSocket'
import { useMicrophone } from './useMicrophone'
import type {
  RecognitionMode,
  ServerMessage,
  SessionState,
  StartSessionOptions,
  TranscriptSegment,
} from '../types/asr'

const WS_PATH = '/api/v1/asr/stream'
const RECONNECT_DELAYS_MS = [800, 1600, 3200, 6400, 10_000, 15_000]
const SPEAKER_ENABLED_STORAGE_KEY = 'funasr.speakerEnabled'

export function useAsrSession() {
  const mode = ref<RecognitionMode>('auto')
  const state = ref<SessionState>('idle')
  const level = ref(0)
  const error = ref('')
  const finals = ref<TranscriptSegment[]>([])
  const partial = ref<TranscriptSegment | null>(null)
  const energyThreshold = ref(0.012)
  const speakerEnabled = ref(
    window.localStorage.getItem(SPEAKER_ENABLED_STORAGE_KEY) !== 'false',
  )
  const sessionAudioId = ref('')

  watch(speakerEnabled, (enabled) => {
    window.localStorage.setItem(SPEAKER_ENABLED_STORAGE_KEY, String(enabled))
  })

  const client = new AsrSocketClient()
  const microphone = useMicrophone()

  let recordingIntent = false
  let sessionReady = false
  let reconnecting = false
  let reconnectGeneration = 0
  let reconnectTimer: number | null = null
  let resolveReconnectWait: (() => void) | null = null

  const active = computed(() =>
    ['initializing', 'listening', 'speech', 'processing', 'reconnecting'].includes(
      state.value,
    ),
  )
  const busy = computed(() =>
    ['connecting', 'initializing', 'processing', 'stopping'].includes(state.value),
  )

  client.onEvent(handleServerEvent)
  client.onClose((closeError) => {
    const wasReady = sessionReady
    sessionReady = false
    if (reconnecting) {
      return
    }
    if (recordingIntent && wasReady) {
      void recoverConnection(closeError)
      return
    }
    if (active.value || busy.value) {
      state.value = 'error'
      error.value = closeError.message
      void microphone.stop()
    }
  })

  function handleServerEvent(message: ServerMessage): void {
    const data = message.data || {}
    switch (message.type) {
      case 'ready': {
        sessionAudioId.value = String(data.session_audio_id || '')
        break
      }
      case 'status': {
        const serverState = String(data.state || '')
        if (
          serverState === 'listening' ||
          serverState === 'speech' ||
          serverState === 'processing' ||
          serverState === 'stopped'
        ) {
          state.value = serverState
        }
        break
      }
      case 'level':
        level.value = Number(data.energy || 0)
        break
      case 'partial': {
        const text = String(data.text || '')
        if (!text) return
        partial.value = {
          id: String(data.segment_id || 'partial'),
          text,
          final: false,
          createdAt: Date.now(),
          speakerId: String(data.speaker_id || '') || undefined,
          speakerName: String(data.speaker_name || '') || undefined,
          speakerPending: Boolean(data.speaker_pending),
          speakerEnrolled: Boolean(data.speaker_enrolled),
        }
        break
      }
      case 'final': {
        const text = String(data.text || '').trim()
        if (text) {
          finals.value.push({
            id: String(data.segment_id || crypto.randomUUID()),
            text,
            final: true,
            createdAt: Date.now(),
            latencyMs: Number(data.latency_ms || 0) || undefined,
            audioId: String(data.audio_id || '') || undefined,
            speakerId: String(data.speaker_id || '') || undefined,
            speakerName: String(data.speaker_name || '') || undefined,
            speakerConfidence: Number(data.speaker_confidence || 0) || undefined,
            speakerPending: Boolean(data.speaker_pending),
            speakerEnrolled: Boolean(data.speaker_enrolled),
          })
        }
        partial.value = null
        break
      }
      case 'speaker': {
        const speakerId = String(data.speaker_id || '')
        if (partial.value && speakerId && !partial.value.speakerId) {
          partial.value = {
            ...partial.value,
            speakerId,
            speakerName: String(data.speaker_name || '') || undefined,
            speakerPending: false,
            speakerEnrolled: Boolean(data.speaker_enrolled),
          }
        }
        break
      }
      case 'error': {
        error.value = String(data.message || '识别服务发生错误')
        if (data.code === 'internal_error' || !active.value) {
          recordingIntent = false
          sessionReady = false
          reconnectGeneration += 1
          cancelReconnectWait()
          state.value = 'error'
          void microphone.stop()
          client.disconnect()
        }
        break
      }
    }
  }

  async function start(): Promise<void> {
    if (active.value || busy.value) {
      return
    }
    recordingIntent = true
    sessionReady = false
    reconnecting = false
    reconnectGeneration += 1
    error.value = ''
    partial.value = null
    state.value = 'connecting'

    try {
      await connectAndStart()
    } catch (cause) {
      recordingIntent = false
      sessionReady = false
      await microphone.stop()
      state.value = 'error'
      error.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  async function stop(): Promise<void> {
    if (!recordingIntent && !active.value && state.value !== 'reconnecting') {
      return
    }
    if (state.value === 'processing') {
      return
    }

    const wasReconnecting = state.value === 'reconnecting' || reconnecting
    recordingIntent = false
    sessionReady = false
    reconnectGeneration += 1
    reconnecting = false
    cancelReconnectWait()
    state.value = 'stopping'
    await microphone.stop()

    if (wasReconnecting) {
      client.disconnect()
      state.value = 'idle'
      level.value = 0
      partial.value = null
      return
    }

    try {
      await client.stop()
      state.value = 'idle'
    } catch (cause) {
      state.value = 'error'
      error.value = cause instanceof Error ? cause.message : String(cause)
    } finally {
      level.value = 0
      partial.value = null
    }
  }

  async function connectAndStart(): Promise<void> {
    await client.connect(resolveWebSocketUrl())
    state.value = 'initializing'
    await client.start(buildStartOptions())
    sessionReady = true

    if (!microphone.capturing.value) {
      await microphone.start({
        onAudio: (pcm) => client.sendAudio(pcm),
        onLevel: (value) => {
          level.value = value
        },
      })
    }
    state.value = 'listening'
  }

  async function recoverConnection(cause: Error): Promise<void> {
    if (reconnecting || !recordingIntent) {
      return
    }

    reconnecting = true
    sessionReady = false
    const generation = ++reconnectGeneration
    state.value = 'reconnecting'
    error.value = `${cause.message}，正在自动重连…`

    try {
      for (let attempt = 0; attempt < RECONNECT_DELAYS_MS.length; attempt += 1) {
        await waitForReconnect(RECONNECT_DELAYS_MS[attempt])
        if (!recordingIntent || generation !== reconnectGeneration) {
          return
        }

        try {
          client.disconnect()
          await client.connect(resolveWebSocketUrl())
          state.value = 'initializing'
          await client.start(buildStartOptions())
          if (!recordingIntent || generation !== reconnectGeneration) {
            client.disconnect()
            return
          }

          sessionReady = true
          if (!microphone.capturing.value) {
            await microphone.start({
              onAudio: (pcm) => client.sendAudio(pcm),
              onLevel: (value) => {
                level.value = value
              },
            })
          }
          error.value = ''
          state.value = 'listening'
          return
        } catch (reconnectError) {
          client.disconnect()
          if (!recordingIntent || generation !== reconnectGeneration) {
            return
          }
          const message =
            reconnectError instanceof Error ? reconnectError.message : String(reconnectError)
          error.value = `连接中断：${message}，正在重连（${attempt + 1}/${RECONNECT_DELAYS_MS.length}）…`
        }
      }

      recordingIntent = false
      sessionReady = false
      await microphone.stop()
      state.value = 'error'
      error.value = '多次重连失败，请检查后端服务后重新开始识别'
    } finally {
      if (generation === reconnectGeneration) {
        reconnecting = false
      }
    }
  }

  function buildStartOptions(): StartSessionOptions {
    return {
      mode: mode.value,
      sample_rate: 16000,
      channels: 1,
      audio_format: 'pcm_s16le',
      vad: {
        energy_threshold: energyThreshold.value,
        hangover_sec: 0.6,
        min_speech_sec: 0.25,
        max_speech_sec: 30,
        pre_roll_sec: 0.4,
      },
      speaker: {
        enabled: speakerEnabled.value,
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

  function clear(): void {
    finals.value = []
    partial.value = null
  }

  function waitForReconnect(delayMs: number): Promise<void> {
    return new Promise((resolve) => {
      resolveReconnectWait = resolve
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        resolveReconnectWait = null
        resolve()
      }, delayMs)
    })
  }

  function cancelReconnectWait(): void {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (resolveReconnectWait) {
      resolveReconnectWait()
      resolveReconnectWait = null
    }
  }

  function disconnect(): void {
    recordingIntent = false
    sessionReady = false
    reconnectGeneration += 1
    cancelReconnectWait()
    void microphone.stop()
    client.disconnect()
  }

  onBeforeUnmount(disconnect)

  return {
    mode,
    state,
    level,
    error,
    finals,
    partial,
    energyThreshold,
    speakerEnabled,
    sessionAudioId,
    active,
    busy,
    capturing: microphone.capturing,
    start,
    stop,
    clear,
  }
}

function resolveWebSocketUrl(): string {
  const configured = import.meta.env.VITE_WS_URL as string | undefined
  if (configured) {
    return configured
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${WS_PATH}`
}
