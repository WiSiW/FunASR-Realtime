export type RecognitionMode = 'auto' | 'stream' | 'push'

export type SessionState =
  | 'idle'
  | 'connecting'
  | 'initializing'
  | 'listening'
  | 'speech'
  | 'processing'
  | 'reconnecting'
  | 'stopping'
  | 'stopped'
  | 'error'

export interface RecognitionModeOption {
  id: RecognitionMode
  name: string
  shortName: string
  description: string
}

export const RECOGNITION_MODES: RecognitionModeOption[] = [
  {
    id: 'auto',
    name: '自动分段',
    shortName: 'AUTO',
    description: '检测停顿自动断句，适合连续发言，结果带标点。',
  },
  {
    id: 'stream',
    name: '实时流式',
    shortName: 'STREAM',
    description: '边说边出字，低延迟字幕模式，句子结束后定稿。',
  },
  {
    id: 'push',
    name: '按键说话',
    shortName: 'PUSH',
    description: '持续采集到手动停止，再统一识别，适合短句输入。',
  },
]

export interface TranscriptSegment {
  id: string
  text: string
  final: boolean
  createdAt: number
  latencyMs?: number
  audioId?: string
  speakerId?: string
  speakerName?: string
  speakerConfidence?: number
  speakerPending?: boolean
  speakerEnrolled?: boolean
}

export interface SpeakerProfile {
  speaker_id: string
  name: string
  dimension: number
  sample_count: number
  created_at?: string
  updated_at?: string
}

export interface ServerMessage {
  type: string
  request_id?: string
  data?: Record<string, unknown>
}

export interface StartSessionOptions {
  mode: RecognitionMode
  sample_rate: 16000
  channels: 1
  audio_format: 'pcm_s16le'
  vad: {
    energy_threshold: number
    hangover_sec: number
    min_speech_sec: number
    max_speech_sec: number
    pre_roll_sec: number
  }
  speaker: {
    enabled: boolean
    similarity_threshold: number
    new_speaker_threshold: number
    switch_margin: number
    min_segment_sec: number
    max_speakers: number
    embedding_window_sec: number
    embedding_interval_sec: number
    centroid_update_alpha: number
  }
}
