<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  FAST_DECODE_MAX_DURATION_SEC,
  isSupportedMediaFile,
  isVideoFile,
} from '../services/mediaAudio'
import type { FileRecognitionPhase } from '../types/asr'

const props = defineProps<{
  active: boolean
  disabled: boolean
  progress: number
  phase: FileRecognitionPhase
  durationSec: number
}>()

const emit = defineEmits<{
  start: [file: File]
  cancel: []
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const previewUrl = ref('')
const dragging = ref(false)
const validationError = ref('')

const video = computed(() => (selectedFile.value ? isVideoFile(selectedFile.value) : false))
const isWorking = computed(
  () =>
    props.active ||
    ['decoding', 'uploading', 'streaming', 'finishing'].includes(props.phase),
)
const canChoose = computed(() => !props.disabled && !props.active)
const progressWidth = computed(() => `${Math.max(0, Math.min(100, props.progress))}%`)

const phaseLabel = computed(() => {
  switch (props.phase) {
    case 'decoding':
      return '正在解码并提取音轨…'
    case 'uploading':
      return `正在发送音频并识别… ${Math.round(props.progress)}%`
    case 'streaming': {
      const estimate = formatDuration(props.durationSec)
      const prefix =
        props.durationSec > FAST_DECODE_MAX_DURATION_SEC
          ? '长文件正在按原速提取音轨'
          : '正在流式提取音轨'
      return estimate ? `${prefix}（约 ${estimate}）…` : `${prefix}…`
    }
    case 'finishing':
      return '正在生成最后一段结果…'
    case 'done':
      return '识别完成，可回放文件或重新识别'
    case 'cancelled':
      return '本次识别已取消'
    case 'error':
      return '识别失败，可重新尝试'
    default:
      return ''
  }
})

watch(selectedFile, (file) => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  validationError.value = ''
  if (file) {
    previewUrl.value = URL.createObjectURL(file)
  }
})

onBeforeUnmount(() => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
  }
})

function openPicker(): void {
  if (!canChoose.value) return
  fileInput.value?.click()
}

function selectFile(file: File | undefined): void {
  if (!file || !canChoose.value) return
  if (!isSupportedMediaFile(file)) {
    validationError.value = '请选择音频或视频文件'
    return
  }
  if (file.size === 0) {
    validationError.value = '文件为空，请选择其他文件'
    return
  }
  selectedFile.value = file
}

function handleInput(event: Event): void {
  const input = event.target as HTMLInputElement
  selectFile(input.files?.[0])
  input.value = ''
}

function handleDrop(event: DragEvent): void {
  dragging.value = false
  if (!canChoose.value) return
  selectFile(event.dataTransfer?.files?.[0])
}

function startRecognition(): void {
  if (!selectedFile.value || !canChoose.value) return
  emit('start', selectedFile.value)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}
</script>

<template>
  <div class="file-upload">
    <input
      ref="fileInput"
      class="file-upload__input"
      type="file"
      tabindex="-1"
      aria-hidden="true"
      accept="audio/*,video/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mov,.webm,.mkv,.avi"
      :disabled="!canChoose"
      @change="handleInput"
    />

    <div
      class="file-drop-zone"
      :class="{ dragging, disabled: !canChoose }"
      role="button"
      tabindex="0"
      @click="openPicker"
      @keydown.enter.prevent="openPicker"
      @keydown.space.prevent="openPicker"
      @dragenter.prevent="dragging = true"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
    >
      <span class="file-drop-zone__icon" aria-hidden="true">
        <i />
      </span>
      <div>
        <strong>{{ selectedFile ? '更换文件' : '拖入音频或视频文件' }}</strong>
        <span>或点击选择 · MP3 / WAV / M4A / MP4 / MOV / WebM</span>
      </div>
    </div>

    <p v-if="validationError" class="file-upload__validation" role="alert">
      {{ validationError }}
    </p>

    <div v-if="selectedFile" class="file-selected">
      <div class="file-selected__meta">
        <span class="file-selected__type">{{ video ? 'VIDEO' : 'AUDIO' }}</span>
        <div>
          <strong :title="selectedFile.name">{{ selectedFile.name }}</strong>
          <span>
            {{ formatSize(selectedFile.size) }}
            <template v-if="formatDuration(durationSec)">
              · {{ formatDuration(durationSec) }}
            </template>
          </span>
        </div>
        <button
          type="button"
          :disabled="disabled || active"
          title="移除文件"
          @click.stop="selectedFile = null"
        >
          移除
        </button>
      </div>

      <video
        v-if="video"
        class="file-selected__preview"
        :src="previewUrl"
        controls
        playsinline
        preload="metadata"
      />
      <audio
        v-else
        class="file-selected__preview file-selected__preview--audio"
        :src="previewUrl"
        controls
        preload="metadata"
      />
    </div>

    <div v-if="selectedFile && phase !== 'idle'" class="file-recognition-status">
      <div class="file-recognition-status__line">
        <span>{{ phaseLabel }}</span>
        <strong v-if="phase === 'uploading' || phase === 'streaming'">{{ Math.round(progress) }}%</strong>
      </div>
      <div
        class="file-progress"
        :class="{ indeterminate: phase === 'decoding' || phase === 'finishing' }"
        role="progressbar"
        :aria-valuenow="phase === 'uploading' || phase === 'streaming' ? Math.round(progress) : undefined"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <span :style="{ width: progressWidth }" />
      </div>
    </div>

    <div class="file-upload__actions">
      <button
        v-if="isWorking"
        class="file-cancel-action"
        type="button"
        @click="emit('cancel')"
      >
        取消识别
      </button>
      <button
        v-else
        class="primary-action file-start-action"
        type="button"
        :disabled="!selectedFile || disabled"
        @click="startRecognition"
      >
        <span class="primary-action__icon" aria-hidden="true" />
        {{ phase === 'done' || phase === 'cancelled' || phase === 'error' ? '重新识别' : '开始识别文件' }}
      </button>
    </div>

    <p class="file-upload__privacy">
      文件在浏览器本地提取为单声道音频后发送到当前后端，不会上传原始视频。
    </p>
  </div>
</template>
