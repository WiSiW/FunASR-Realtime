<script setup lang="ts">
import { computed } from 'vue'
import AudioMeter from './components/AudioMeter.vue'
import FileUploadPanel from './components/FileUploadPanel.vue'
import ModeSelector from './components/ModeSelector.vue'
import SpeakerLibrary from './components/SpeakerLibrary.vue'
import TranscriptPanel from './components/TranscriptPanel.vue'
import { useAsrSession } from './composables/useAsrSession'

const {
  source,
  mode,
  state,
  level,
  error,
  finals,
  partial,
  energyThreshold,
  speakerEnabled,
  sessionAudioId,
  audioFileName,
  fileProgress,
  filePhase,
  fileDurationSec,
  active,
  busy,
  start,
  stop,
  startFile,
  cancelFile,
  clear,
} = useAsrSession()

const stateLabel = computed(() => {
  const labels: Record<string, string> = {
    idle: '待机',
    connecting: '连接中',
    initializing: '加载模型',
    decoding: '解析文件',
    uploading: '发送音频',
    listening: '聆听中',
    speech: '检测到语音',
    processing: '识别中',
    reconnecting: '自动重连',
    stopping: '正在结束',
    stopped: '已停止',
    error: '异常',
  }
  return labels[state.value] || state.value
})

const actionLabel = computed(() => {
  if (state.value === 'connecting') return '正在连接…'
  if (state.value === 'initializing') return '正在加载模型…'
  if (state.value === 'processing') return '正在识别…'
  if (state.value === 'reconnecting') return '停止重连'
  if (state.value === 'stopping') return '正在结束…'
  if (active.value) return mode.value === 'push' ? '结束并识别' : '停止识别'
  return '开始识别'
})

function audioUrl(audioId: string): string {
  return `/api/v1/asr/audio/${encodeURIComponent(audioId)}`
}

function startSelectedFile(file: File): void {
  void startFile(file)
}
</script>

<template>
  <div class="page-shell">
    <div class="ambient ambient--one" />
    <div class="ambient ambient--two" />

    <header class="hero">
      <div class="brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>
      <div>
        <span class="eyebrow">FUNASR · REALTIME</span>
        <h1>浏览器语音识别工作台</h1>
        <p>麦克风实时识别，或上传音频、视频文件提取音轨后识别。</p>
      </div>
      <div class="connection-pill" :class="{ online: active }">
        <span class="connection-pill__dot" />
        {{ stateLabel }}
      </div>
    </header>

    <main class="workspace">
      <section class="control-card">
        <div class="control-card__heading">
          <div>
            <span class="eyebrow">01 · SOURCE</span>
            <h2>选择输入方式</h2>
          </div>
          <span class="format-badge">16 kHz · PCM</span>
        </div>

        <div class="source-tabs" role="tablist" aria-label="输入方式">
          <button
            class="source-tab"
            :class="{ selected: source === 'microphone' }"
            type="button"
            role="tab"
            :aria-selected="source === 'microphone'"
            :disabled="active || busy"
            @click="source = 'microphone'"
          >
            <span>麦克风</span>
            <small>实时采集</small>
          </button>
          <button
            class="source-tab"
            :class="{ selected: source === 'file' }"
            type="button"
            role="tab"
            :aria-selected="source === 'file'"
            :disabled="active || busy"
            @click="source = 'file'"
          >
            <span>音视频文件</span>
            <small>本地上传</small>
          </button>
        </div>

        <template v-if="source === 'microphone'">
          <ModeSelector v-model="mode" :disabled="active || busy" />

          <div class="capture-panel">
            <div class="capture-panel__visual">
              <AudioMeter :level="level" :active="active" />
              <div class="capture-panel__meta">
                <span>{{ active ? '输入电平' : '等待麦克风' }}</span>
                <strong>{{ Math.round(Math.min(level, 1) * 100) }}%</strong>
              </div>
            </div>

            <button
              class="primary-action"
              :class="{ stop: active }"
              type="button"
              :disabled="busy"
              @click="active ? stop() : start()"
            >
              <span class="primary-action__icon" aria-hidden="true" />
              {{ actionLabel }}
            </button>
          </div>
        </template>

        <template v-else>
          <div class="file-mode-note">
            <span>文件识别</span>
            <p>自动检测停顿并分段，适合会议录音、访谈和视频内容。</p>
          </div>
          <FileUploadPanel
            :active="active"
            :disabled="active || busy"
            :progress="fileProgress"
            :phase="filePhase"
            :duration-sec="fileDurationSec"
            @start="startSelectedFile"
            @cancel="cancelFile"
          />
        </template>

        <details class="advanced-panel">
          <summary>识别设置</summary>
          <label>
            <span>
              能量阈值
              <small>环境安静时可调低</small>
            </span>
            <input
              v-model.number="energyThreshold"
              type="range"
              min="0.002"
              max="0.08"
              step="0.001"
              :disabled="active || busy"
            />
            <output>{{ energyThreshold.toFixed(3) }}</output>
          </label>
          <div class="check-row">
            <span>
              说话人识别
              <small>
                {{
                  speakerEnabled
                    ? '已开启：提取声纹并标记 speaker_01、speaker_02…'
                    : '已关闭：不做声纹提取，也不显示说话人'
                }}
              </small>
            </span>
            <button
              class="toggle-switch"
              :class="{ on: speakerEnabled }"
              type="button"
              :disabled="active || busy"
              @click="speakerEnabled = !speakerEnabled"
            >
              <i />
              <em>{{ speakerEnabled ? '开启' : '关闭' }}</em>
            </button>
          </div>
        </details>

        <SpeakerLibrary :disabled="active || busy" />

        <div v-if="sessionAudioId && !active && !busy" class="session-audio">
          <span>本次识别音频回放</span>
          <audio controls preload="none" :src="audioUrl(sessionAudioId)" />
        </div>

        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      </section>

      <TranscriptPanel
        :finals="finals"
        :partial="partial"
        :state="state"
        :source="source"
        :speaker-enabled="speakerEnabled"
        :session-audio-id="sessionAudioId"
        :audio-file-name="audioFileName"
        @clear="clear"
      />
    </main>

    <footer>
      <span>音频仅发送到当前后端服务</span>
      <span>音视频文件在浏览器本地提取音轨，无需上传原始视频</span>
    </footer>
  </div>
</template>
