<script setup lang="ts">
import { computed } from 'vue'
import type { SessionState, TranscriptSegment } from '../types/asr'

const props = defineProps<{
  finals: TranscriptSegment[]
  partial: TranscriptSegment | null
  state: SessionState
}>()

const emit = defineEmits<{
  clear: []
}>()

const plainText = computed(() => props.finals.map((item) => item.text).join('\n'))
const hasText = computed(() => plainText.value.length > 0)

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
  anchor.download = `funasr-transcript-${new Date().toISOString().slice(0, 10)}.txt`
  anchor.click()
  URL.revokeObjectURL(url)
}
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
        <button type="button" :disabled="!finals.length && !partial" @click="emit('clear')">
          清空
        </button>
      </div>
    </header>

    <div class="transcript-body" aria-live="polite">
      <template v-if="finals.length || partial">
        <article
          v-for="(item, index) in finals"
          :key="item.id"
          v-memo="[item.text, item.latencyMs, index]"
          class="transcript-line"
        >
          <span class="transcript-line__index">
            {{ String(index + 1).padStart(2, '0') }}
          </span>
          <p>{{ item.text }}</p>
          <span v-if="item.latencyMs" class="transcript-line__latency">
            {{ item.latencyMs }} ms
          </span>
        </article>

        <article v-if="partial" class="transcript-line transcript-line--partial">
          <span class="transcript-line__index">LIVE</span>
          <p>{{ partial.text }}<i class="typing-cursor" /></p>
        </article>
      </template>

      <div v-else class="empty-state">
        <div class="empty-state__rings" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <template v-if="state === 'idle' || state === 'stopped'">
          <h3>等待开始识别</h3>
          <p>选择识别模式并授权麦克风，文字会实时出现在这里。</p>
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
