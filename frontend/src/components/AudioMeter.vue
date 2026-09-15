<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  level: number
  active: boolean
}>()

const bars = computed(() =>
  Array.from({ length: 24 }, (_, index) => {
    const centerWeight = 1 - Math.abs(index - 11.5) / 16
    const threshold = 0.035 + (index / 24) * 0.72
    const lit = props.active && props.level * centerWeight * 5.2 > threshold
    const height = 12 + ((index * 17) % 28) + centerWeight * 10
    return { lit, height }
  }),
)
</script>

<template>
  <div class="audio-meter" :class="{ active }" aria-label="麦克风输入音量">
    <span
      v-for="(bar, index) in bars"
      :key="index"
      :class="{ lit: bar.lit }"
      :style="{ height: `${bar.height}%` }"
    />
  </div>
</template>
