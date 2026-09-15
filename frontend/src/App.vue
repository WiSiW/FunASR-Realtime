<script setup lang="ts">
import { computed } from 'vue'
import AudioMeter from './components/AudioMeter.vue'
import ModeSelector from './components/ModeSelector.vue'
import TranscriptPanel from './components/TranscriptPanel.vue'
import { useAsrSession } from './composables/useAsrSession'

const {
  mode,
  state,
  level,
  error,
  finals,
  partial,
  energyThreshold,
  active,
  busy,
  start,
  stop,
  clear,
} = useAsrSession()

const stateLabel = computed(() => {
  const labels: Record<string, string> = {
    idle: '待机',
    connecting: '连接中',
    initializing: '加载模型',
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
        <h1>浏览器实时语音识别</h1>
        <p>麦克风音频通过 WebSocket 持续传输，识别过程保留在本机后端。</p>
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
            <span class="eyebrow">01 · MODE</span>
            <h2>选择识别模式</h2>
          </div>
          <span class="format-badge">16 kHz · PCM</span>
        </div>

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

        <details class="advanced-panel">
          <summary>VAD 灵敏度</summary>
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
        </details>

        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      </section>

      <TranscriptPanel
        :finals="finals"
        :partial="partial"
        :state="state"
        @clear="clear"
      />
    </main>

    <footer>
      <span>音频仅发送到当前后端服务</span>
      <span>生产环境请使用 HTTPS，以启用浏览器麦克风权限</span>
    </footer>
  </div>
</template>
