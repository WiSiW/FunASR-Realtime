import { ref } from 'vue'
import { useMicrophone } from './useMicrophone'
import {
  deleteSpeakerProfile,
  enrollSpeakerProfile,
  listSpeakerProfiles,
} from '../services/speakerApi'
import type { SpeakerProfile } from '../types/asr'

const DEFAULT_ENROLL_MS = 3000

export function useSpeakerEnrollment() {
  const profiles = ref<SpeakerProfile[]>([])
  const loading = ref(false)
  const recording = ref(false)
  const level = ref(0)
  const error = ref('')
  const message = ref('')
  const microphone = useMicrophone()

  async function refresh(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      profiles.value = await listSpeakerProfiles()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause)
    } finally {
      loading.value = false
    }
  }

  async function enroll(name: string, durationMs = DEFAULT_ENROLL_MS): Promise<void> {
    const normalizedName = name.trim()
    if (!normalizedName) {
      error.value = '请输入说话人姓名'
      return
    }
    if (recording.value) {
      return
    }

    recording.value = true
    error.value = ''
    message.value = `正在录音，请持续说话 ${Math.round(durationMs / 1000)} 秒…`
    const chunks: Int16Array[] = []
    try {
      await microphone.start({
        onAudio: (pcm) => chunks.push(pcm),
        onLevel: (value) => {
          level.value = value
        },
      })
      await delay(durationMs)
      await microphone.stop()

      const pcm = mergePcm(chunks)
      if (pcm.length < 16000) {
        throw new Error('有效录音不足 1 秒，请重试')
      }

      const profile = await enrollSpeakerProfile(normalizedName, pcm)
      message.value = `${profile.speaker_id} · ${profile.name} 注册成功，下次开始识别后生效`
      await refresh()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause)
      message.value = ''
    } finally {
      recording.value = false
      level.value = 0
      await microphone.stop()
    }
  }

  async function remove(speakerId: string): Promise<void> {
    error.value = ''
    try {
      await deleteSpeakerProfile(speakerId)
      if (profiles.value.length === 1) {
        message.value = '声纹库已清空'
      }
      await refresh()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  return {
    profiles,
    loading,
    recording,
    level,
    error,
    message,
    refresh,
    enroll,
    remove,
  }
}

function mergePcm(chunks: Int16Array[]): Int16Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
  const merged = new Int16Array(total)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.length
  }
  return merged
}

function delay(durationMs: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, durationMs))
}
