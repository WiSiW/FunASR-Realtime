<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { CaptureSource, SessionState, TranscriptSegment } from '../types/asr'

const props = defineProps<{
  finals: TranscriptSegment[]
  partial: TranscriptSegment | null
  state: SessionState
  source: CaptureSource
  speakerEnabled: boolean
  sessionAudioId?: string
  audioFileName?: string
}>()

const emit = defineEmits<{
  clear: []
}>()

const audioPlayer = ref<HTMLAudioElement | null>(null)
const activeSegmentId = ref('')
const loadingSegmentId = ref('')
const playbackError = ref('')
let activeStartMs = 0
let activeEndMs = 0
let playbackGeneration = 0

const plainText = computed(() =>
  props.finals
    .map((item) => {
      const timing = formatTimeRange(item)
      const speaker = props.speakerEnabled
        ? item.speakerId || 'speaker_?'
        : ''
      const label = props.speakerEnabled
        ? item.speakerName
          ? `${speaker} ${item.speakerName}`
          : speaker
        : ''
      const prefix = [timing, label ? `[${label}]` : '']
        .filter(Boolean)
        .join(' ')
      return prefix ? `${prefix} ${item.text}` : item.text
    })
    .join('\n'),
)
const hasText = computed(() => plainText.value.length > 0)
const exportBaseName = computed(() => {
  for (let index = props.finals.length - 1; index >= 0; index -= 1) {
    const name = props.finals[index].audioFileName
    if (name) return normalizeAudioBaseName(name)
  }
  return normalizeAudioBaseName(props.audioFileName || '')
})

async function copyAll(): Promise<void> {
  if (!hasText.value) return
  await navigator.clipboard.writeText(plainText.value)
}

function downloadAll(): void {
  if (!hasText.value) return
  const blob = new Blob([plainText.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${exportBaseName.value}-${formatFileTimestamp(new Date())}.txt`
  anchor.click()
  URL.revokeObjectURL(url)
}

async function playSegment(item: TranscriptSegment): Promise<void> {
  const audioId = item.sessionId || props.sessionAudioId
  if (!audioId || !canPlay(item)) return
  if (loadingSegmentId.value === item.id) {
    stopPlayback()
    return
  }
  if (activeSegmentId.value === item.id) {
    stopPlayback()
    return
  }

  const audio = audioPlayer.value
  if (!audio) return

  const generation = ++playbackGeneration
  playbackError.value = ''
  loadingSegmentId.value = item.id
  activeSegmentId.value = ''
  audio.pause()

  activeStartMs = item.startTimeMs as number
  activeEndMs = item.endTimeMs as number
  audio.src = `${audioUrl(audioId)}?v=${Date.now()}`
  audio.load()

  try {
    await waitForAudioMetadata(audio)
    if (generation !== playbackGeneration) return
    audio.currentTime = activeStartMs / 1000
    await audio.play()
    if (generation !== playbackGeneration) {
      audio.pause()
      return
    }
    activeSegmentId.value = item.id
  } catch (cause) {
    if (generation !== playbackGeneration) return
    activeSegmentId.value = ''
    playbackError.value = playbackErrorMessage(cause)
  } finally {
    if (generation === playbackGeneration) {
      loadingSegmentId.value = ''
    }
  }
}

function handleTimeUpdate(): void {
  const audio = audioPlayer.value
  if (!audio || !activeSegmentId.value) return
  if (audio.currentTime * 1000 >= activeEndMs - 20) {
    finishPlayback()
  }
}

function finishPlayback(): void {
  audioPlayer.value?.pause()
  activeSegmentId.value = ''
}

function stopPlayback(): void {
  playbackGeneration += 1
  const audio = audioPlayer.value
  if (audio) {
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
  }
  activeSegmentId.value = ''
  loadingSegmentId.value = ''
}

function handlePlaybackError(): void {
  if (!activeSegmentId.value && !loadingSegmentId.value) return
  activeSegmentId.value = ''
  loadingSegmentId.value = ''
  playbackError.value = '无法加载原始音频，音频可能已过期或后端服务不可用'
}

function clearAll(): void {
  stopPlayback()
  playbackError.value = ''
  emit('clear')
}

function canPlay(item: TranscriptSegment): boolean {
  return (
    Boolean(item.sessionId || props.sessionAudioId) &&
    item.startTimeMs !== undefined &&
    item.endTimeMs !== undefined &&
    item.endTimeMs >= item.startTimeMs
  )
}

function audioUrl(audioId: string): string {
  return `/api/v1/asr/audio/${encodeURIComponent(audioId)}`
}

function waitForAudioMetadata(audio: HTMLAudioElement): Promise<void> {
  if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
    return Promise.resolve()
  }
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      reject(new Error('加载原始音频超时'))
    }, 15_000)
    const cleanup = (): void => {
      window.clearTimeout(timer)
      audio.removeEventListener('loadedmetadata', handleLoaded)
      audio.removeEventListener('error', handleError)
    }
    const handleLoaded = (): void => {
      cleanup()
      resolve()
    }
    const handleError = (): void => {
      cleanup()
      reject(new Error('原始音频加载失败'))
    }
    audio.addEventListener('loadedmetadata', handleLoaded, { once: true })
    audio.addEventListener('error', handleError, { once: true })
  })
}

function normalizeAudioBaseName(name: string): string {
  const filename = name.split(/[\\/]/).pop()?.trim() || '音频'
  const withoutExtension = filename.replace(/\.[^.\\/]+$/, '')
  const safeName = withoutExtension
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .replace(/[. ]+$/g, '')
    .trim()
  return (safeName || '音频').slice(0, 100)
}

function formatFileTimestamp(date: Date): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    '-',
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join('')
}

function playbackErrorMessage(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : String(cause)
  return message ? `片段播放失败：${message}` : '片段播放失败'
}

function formatTimeRange(item: TranscriptSegment): string {
  if (item.startTimeMs === undefined && item.endTimeMs === undefined) return ''
  return `${formatClock(item.startTimeMs)} - ${formatClock(item.endTimeMs)}`
}

function formatClock(milliseconds: number | undefined): string {
  if (milliseconds === undefined) return '--:--.---'
  const total = Math.max(0, Math.round(milliseconds))
  const hours = Math.floor(total / 3_600_000)
  const minutes = Math.floor((total % 3_600_000) / 60_000)
  const seconds = Math.floor((total % 60_000) / 1000)
  const millis = total % 1000
  const clock = hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  return `${clock}.${String(millis).padStart(3, '0')}`
}

onBeforeUnmount(stopPlayback)
</script>

<template>
  <section class="transcript-card">
    <header class="transcript-card__header">
      <div>
        <span class="eyebrow">TRANSCRIPT</span>
        <h2>识别结果</h2>
      </div>
      <div class="transcript-actions">
        <button type="button" :disabled="!hasText" @click="copyAll">复制</button>
        <button type="button" :disabled="!hasText" @click="downloadAll">导出</button>
        <button type="button" :disabled="!finals.length && !partial" @click="clearAll">
          清空
        </button>
      </div>
    </header>

    <div class="transcript-body" aria-live="polite">
      <audio
        ref="audioPlayer"
        hidden
        preload="metadata"
        @timeupdate="handleTimeUpdate"
        @ended="finishPlayback"
        @error="handlePlaybackError"
      />

      <p v-if="playbackError" class="transcript-playback-error" role="alert">
        {{ playbackError }}
      </p>

      <template v-if="finals.length || partial">
        <article
          v-for="(item, index) in finals"
          :key="item.id"
          class="transcript-line"
        >
          <span class="transcript-line__index">
            {{ String(index + 1).padStart(2, '0') }}
          </span>
          <div class="transcript-line__content">
            <div class="transcript-line__meta">
              <span v-if="formatTimeRange(item)" class="transcript-line__time">
                {{ formatTimeRange(item) }}
              </span>
              <button
                v-if="canPlay(item)"
                class="segment-play"
                :class="{
                  playing: activeSegmentId === item.id,
                  loading: loadingSegmentId === item.id,
                }"
                type="button"
                :aria-pressed="activeSegmentId === item.id"
                :aria-label="
                  activeSegmentId === item.id
                    ? '暂停当前片段'
                    : `播放 ${formatTimeRange(item)} 片段`
                "
                @click="playSegment(item)"
              >
                <span class="segment-play__icon" aria-hidden="true" />
                {{
                  loadingSegmentId === item.id
                    ? '加载中'
                    : activeSegmentId === item.id
                      ? '暂停'
                      : '播放'
                }}
              </button>
            </div>
            <p>
              <span v-if="speakerEnabled" class="transcript-line__speaker">
                {{ item.speakerId || 'speaker_?' }}
                <template v-if="item.speakerName"> · {{ item.speakerName }}</template>
              </span>
              {{ item.text }}
            </p>
          </div>
          <span v-if="item.latencyMs" class="transcript-line__latency">
            {{ item.latencyMs }} ms
          </span>
        </article>

        <article v-if="partial" class="transcript-line transcript-line--partial">
          <span class="transcript-line__index">LIVE</span>
          <p>
            <span v-if="speakerEnabled" class="transcript-line__speaker">
              {{ partial.speakerId || 'speaker_?' }}
              <template v-if="partial.speakerName"> · {{ partial.speakerName }}</template>
            </span>
            {{ partial.text }}<i class="typing-cursor" />
          </p>
        </article>
      </template>

      <div v-else class="empty-state">
        <div class="empty-state__rings" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <template v-if="state === 'idle' || state === 'stopped'">
          <h3>{{ source === 'file' ? '等待文件识别' : '等待开始识别' }}</h3>
          <p>
            {{
              source === 'file'
                ? '选择音频或视频文件，提取音轨后会自动分段识别。'
                : '选择识别模式并授权麦克风，文字会实时出现在这里。'
            }}
          </p>
        </template>
        <template v-else-if="state === 'decoding'">
          <h3>正在解析文件</h3>
          <p>正在浏览器本地解码并提取 16 kHz 单声道音轨。</p>
        </template>
        <template v-else-if="state === 'uploading'">
          <h3>正在发送音频</h3>
          <p>音频会分段送入识别服务，结果将逐步出现在这里。</p>
        </template>
        <template v-else-if="state === 'reconnecting'">
          <h3>正在恢复连接</h3>
          <p>麦克风将保持授权，连接恢复后会继续识别。</p>
        </template>
        <template v-else-if="state === 'processing'">
          <h3>正在识别上一句</h3>
          <p>离线模型正在生成带标点的完整文本。</p>
        </template>
        <template v-else>
          <h3>正在聆听</h3>
          <p>请自然说话，系统会根据所选模式处理音频。</p>
        </template>
      </div>
    </div>
  </section>
</template>
