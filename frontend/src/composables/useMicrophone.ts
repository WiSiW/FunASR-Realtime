import { onBeforeUnmount, ref } from 'vue'
import { StreamingPcm16Resampler } from '../services/resampler'

interface MicrophoneCallbacks {
  onAudio: (pcm: Int16Array) => void
  onLevel: (level: number) => void
}

export function useMicrophone() {
  const capturing = ref(false)
  const level = ref(0)
  const error = ref('')

  let mediaStream: MediaStream | null = null
  let audioContext: AudioContext | null = null
  let sourceNode: MediaStreamAudioSourceNode | null = null
  let workletNode: AudioWorkletNode | null = null
  let silentGain: GainNode | null = null

  async function start(callbacks: MicrophoneCallbacks): Promise<void> {
    if (capturing.value) {
      return
    }
    error.value = ''

    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('当前浏览器不支持麦克风采集，请使用最新版 Chrome/Edge/Firefox')
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })

      audioContext = new AudioContext({ latencyHint: 'interactive' })
      await audioContext.audioWorklet.addModule(
        `${import.meta.env.BASE_URL}pcm-capture-worklet.js`,
      )
      await audioContext.resume()

      sourceNode = audioContext.createMediaStreamSource(mediaStream)
      workletNode = new AudioWorkletNode(audioContext, 'pcm-capture')
      silentGain = audioContext.createGain()
      silentGain.gain.value = 0

      const resampler = new StreamingPcm16Resampler(
        audioContext.sampleRate,
        16000,
      )

      workletNode.port.onmessage = (event: MessageEvent<Float32Array>) => {
        const samples = event.data
        const currentLevel = calculateRms(samples)
        level.value = currentLevel
        callbacks.onLevel(currentLevel)
        const pcm = resampler.process(samples)
        if (pcm.length > 0) {
          callbacks.onAudio(pcm)
        }
      }

      sourceNode.connect(workletNode)
      workletNode.connect(silentGain)
      silentGain.connect(audioContext.destination)
      capturing.value = true
    } catch (cause) {
      await stop()
      const message = cause instanceof Error ? cause.message : String(cause)
      error.value = normalizeMicrophoneError(message)
      throw new Error(error.value)
    }
  }

  async function stop(): Promise<void> {
    capturing.value = false
    level.value = 0
    workletNode?.port.close()
    workletNode?.disconnect()
    sourceNode?.disconnect()
    silentGain?.disconnect()
    mediaStream?.getTracks().forEach((track) => track.stop())

    if (audioContext && audioContext.state !== 'closed') {
      await audioContext.close()
    }

    workletNode = null
    sourceNode = null
    silentGain = null
    mediaStream = null
    audioContext = null
  }

  onBeforeUnmount(() => {
    void stop()
  })

  return { capturing, level, error, start, stop }
}

function calculateRms(samples: Float32Array): number {
  if (samples.length === 0) {
    return 0
  }
  let sum = 0
  for (const sample of samples) {
    sum += sample * sample
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 3)
}

function normalizeMicrophoneError(message: string): string {
  if (/NotAllowedError|Permission denied|Permission dismissed/i.test(message)) {
    return '麦克风权限被拒绝，请在浏览器地址栏中允许访问麦克风'
  }
  if (/NotFoundError|Requested device not found/i.test(message)) {
    return '未找到可用麦克风，请检查系统输入设备'
  }
  if (/NotReadableError|Device in use/i.test(message)) {
    return '麦克风被其他程序占用，请关闭占用程序后重试'
  }
  return `麦克风启动失败：${message}`
}
