<script setup lang="ts">
import { RECOGNITION_MODES, type RecognitionMode } from '../types/asr'

defineProps<{
  modelValue: RecognitionMode
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: RecognitionMode]
}>()
</script>

<template>
  <div class="mode-grid" role="radiogroup" aria-label="识别模式">
    <button
      v-for="option in RECOGNITION_MODES"
      :key="option.id"
      class="mode-card"
      :class="{ selected: modelValue === option.id }"
      :disabled="disabled"
      type="button"
      role="radio"
      :aria-checked="modelValue === option.id"
      @click="emit('update:modelValue', option.id)"
    >
      <span class="mode-card__topline">
        <span class="mode-card__radio" aria-hidden="true" />
        <span class="mode-card__code">{{ option.shortName }}</span>
      </span>
      <strong>{{ option.name }}</strong>
      <span class="mode-card__description">{{ option.description }}</span>
    </button>
  </div>
</template>
