<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSpeakerEnrollment } from '../composables/useSpeakerEnrollment'

defineProps<{
  disabled: boolean
}>()

const name = ref('')
const {
  profiles,
  loading,
  recording,
  level,
  error,
  message,
  refresh,
  enroll,
  remove,
} = useSpeakerEnrollment()

onMounted(() => {
  void refresh()
})

async function submit(): Promise<void> {
  await enroll(name.value)
  name.value = ''
}
</script>

<template>
  <details class="speaker-library">
    <summary>声纹库</summary>
    <div class="speaker-library__body">
      <label class="speaker-library__field">
        <span>说话人姓名</span>
        <input
          v-model="name"
          type="text"
          maxlength="64"
          placeholder="例如：张三"
          :disabled="disabled || recording"
        />
      </label>
      <button
        class="speaker-library__record"
        type="button"
        :disabled="disabled || recording || loading"
        @click="submit"
      >
        {{ recording ? '录音中…' : '录音 3 秒并注册' }}
      </button>
      <div v-if="recording" class="speaker-library__meter">
        <span :style="{ width: `${Math.round(level * 100)}%` }" />
      </div>
      <p v-if="message" class="speaker-library__message">{{ message }}</p>
      <p v-if="error" class="speaker-library__error">{{ error }}</p>

      <div class="speaker-library__list">
        <div
          v-for="profile in profiles"
          :key="profile.speaker_id"
          class="speaker-library__item"
        >
          <div>
            <strong>{{ profile.name }}</strong>
            <span>{{ profile.speaker_id }} · {{ profile.sample_count }} 个样本</span>
          </div>
          <button
            type="button"
            :disabled="disabled || recording"
            @click="remove(profile.speaker_id)"
          >
            删除
          </button>
        </div>
        <p v-if="!profiles.length && !loading" class="speaker-library__empty">
          暂无已注册声纹
        </p>
      </div>
    </div>
  </details>
</template>
