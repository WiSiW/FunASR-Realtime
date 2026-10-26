/** Decode and stream browser-supported audio/video files as 16 kHz PCM. */
import { StreamingPcm16Resampler } from './resampler'

export const TARGET_SAMPLE_RATE = 16000
export const FAST_DECODE_MAX_DURATION_SEC = 120
export const FAST_DECODE_MAX_FILE_BYTES = 48 * 1024 * 1024

export interface DecodedMediaAudio {
  buffer: AudioBuffer
  close: () => Promise<void>
}

export interface PcmChunk {
  pcm: Int16Array
  level: number
}

export interface MediaStreamCallbacks {
  onChunk: (chunk: PcmChunk) => void
  onDuration: (durationSec: number) => void
  onProgress: (progress: number) => void
  signal?: AbortSignal
}

/**
 * Fast path for formats supported by AudioContext.decodeAudioData. Video
 * containers are accepted when the browser can demux their audio track.
 */
export async function decodeMediaFile(file: File): Promise<DecodedMediaAudio> {
  if (file.size === 0) {
    throw new Error('文件为空，请选择包含音轨的音视频文件')
  }

  let context: AudioContext
  try {
    context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE, latencyHint: 'playback' })
  } catch {
    context = new AudioContext({ latencyHint: 'playback' })
  }

  try {
    const encoded = await file.arrayBuffer()
    let buffer = await context.decodeAudioData(encoded)

    if (context.sampleRate !== TARGET_SAMPLE_RATE) {
      buffer = await resampleBuffer(buffer)
    }

    return {
      buffer,
      close: async () => {
        if (context.state !== 'closed') {
          await context.close()
        }
      },
    }
  } catch (cause) {
    if (context.state !== 'closed') {
      await context.close()
    }
    throw normalizeMediaError(cause)
  }
}

/** Slice an AudioBuffer into mono PCM blocks and calculate each block's RMS level. */
export function readPcmChunk(
  buffer: AudioBuffer,
  start: number,
  length: number,
): PcmChunk {
  const safeLength = Math.max(0, Math.min(length, buffer.length - start))
  const pcm = new Int16Array(safeLength)
  if (safeLength === 0) {
    return { pcm, level: 0 }
  }

  const channels = Array.from(
    { length: buffer.numberOfChannels },
    (_, index) => buffer.getChannelData(index),
  )
  let energy = 0

  for (let offset = 0; offset < safeLength; offset += 1) {
    let sample = 0
    for (const channel of channels) {
      sample += channel[start + offset] || 0
    }
    sample /= Math.max(1, channels.length)
    energy += sample * sample
    const clamped = Math.max(-1, Math.min(1, sample))
    pcm[offset] = clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767)
  }

  return { pcm, level: Math.sqrt(energy / safeLength) }
}

export async function probeMediaFile(
  file: File,
  signal?: AbortSignal,
): Promise<number> {
  const media = document.createElement(isVideoFile(file) ? 'video' : 'audio') as HTMLMediaElement
  const sourceUrl = URL.createObjectURL(file)
  media.src = sourceUrl
  media.preload = 'metadata'

  try {
    await waitForMediaMetadata(media, signal)
    return Number.isFinite(media.duration) ? Math.max(0, media.duration) : 0
  } finally {
    media.removeAttribute('src')
    media.load()
    URL.revokeObjectURL(sourceUrl)
  }
}

export function canFastDecodeFile(file: File, durationSec: number): boolean {
  return (
    durationSec > 0 &&
    durationSec <= FAST_DECODE_MAX_DURATION_SEC &&
    file.size <= FAST_DECODE_MAX_FILE_BYTES
  )
}

/**
 * Fallback for video/audio containers that decodeAudioData cannot demux.
 * The media element is played silently and its audio graph is sampled in real
 * time, so this keeps video upload support without a backend ffmpeg endpoint.
 */
export async function streamMediaFile(
  file: File,
  callbacks: MediaStreamCallbacks,
): Promise<void> {
  if (file.size === 0) {
    throw new Error('文件为空，请选择包含音轨的音视频文件')
  }
  if (callbacks.signal?.aborted) {
    throw abortError()
  }

  const kind = isVideoFile(file) ? 'video' : 'audio'
  const media = document.createElement(kind) as HTMLMediaElement
  const sourceUrl = URL.createObjectURL(file)
  media.src = sourceUrl
  media.preload = 'auto'
  if (kind === 'video') (media as HTMLVideoElement).playsInline = true

  let context: AudioContext
  try {
    context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE, latencyHint: 'playback' })
  } catch {
    context = new AudioContext({ latencyHint: 'playback' })
  }

  const sourceNode = context.createMediaElementSource(media)
  const processor = context.createScriptProcessor(4096, 1, 1)
  const silentGain = context.createGain()
  silentGain.gain.value = 0
  sourceNode.connect(processor)
  processor.connect(silentGain)
  silentGain.connect(context.destination)

  const resampler = new StreamingPcm16Resampler(context.sampleRate, TARGET_SAMPLE_RATE)
  let receivedAudio = false

  const onAudioProcess = (event: AudioProcessingEvent): void => {
    const input = event.inputBuffer.getChannelData(0)
    let energy = 0
    for (let index = 0; index < input.length; index += 1) {
      energy += input[index] * input[index]
    }
    const pcm = resampler.process(input)
    if (pcm.length === 0) {
      return
    }
    receivedAudio = true
    callbacks.onChunk({
      pcm,
      level: Math.sqrt(energy / Math.max(1, input.length)),
    })
    const duration = Number.isFinite(media.duration) ? media.duration : 0
    callbacks.onProgress(
      duration > 0 ? Math.min(0.995, Math.max(0, media.currentTime / duration)) : 0,
    )
  }
  processor.onaudioprocess = onAudioProcess

  try {
    await waitForMediaMetadata(media, callbacks.signal)
    callbacks.onDuration(Number.isFinite(media.duration) ? media.duration : 0)
    await context.resume()

    await new Promise<void>((resolve, reject) => {
      let settled = false
      const cleanup = (): void => {
        media.removeEventListener('ended', handleEnded)
        media.removeEventListener('error', handleError)
        callbacks.signal?.removeEventListener('abort', handleAbort)
      }
      const settle = (handler: () => void): void => {
        if (settled) return
        settled = true
        cleanup()
        handler()
      }
      const handleEnded = (): void => settle(resolve)
      const handleError = (): void =>
        settle(() => reject(normalizeMediaError(media.error)))
      const handleAbort = (): void => settle(() => reject(abortError()))

      media.addEventListener('ended', handleEnded, { once: true })
      media.addEventListener('error', handleError, { once: true })
      callbacks.signal?.addEventListener('abort', handleAbort, { once: true })

      void media.play().catch((cause: unknown) => {
        settle(() => reject(normalizeMediaError(cause)))
      })
    })

    if (!receivedAudio) {
      throw new Error('未从文件中检测到可用音轨')
    }
    callbacks.onProgress(1)
  } catch (cause) {
    if (callbacks.signal?.aborted) {
      throw abortError()
    }
    if (cause instanceof Error) {
      throw cause
    }
    throw normalizeMediaError(cause)
  } finally {
    media.pause()
    processor.onaudioprocess = null
    sourceNode.disconnect()
    processor.disconnect()
    silentGain.disconnect()
    URL.revokeObjectURL(sourceUrl)
    if (context.state !== 'closed') {
      await context.close()
    }
  }
}

export function isSupportedMediaFile(file: File): boolean {
  if (file.type.startsWith('audio/') || file.type.startsWith('video/')) return true
  return /\.(mp3|wav|wave|m4a|aac|flac|ogg|oga|opus|webm|mp4|m4v|mov|mkv|avi|wmv|flv|ts)$/i.test(file.name)
}

export function isVideoFile(file: File): boolean {
  if (file.type.startsWith('video/')) return true
  return /\.(mp4|m4v|mov|webm|mkv|avi|wmv|flv|ts)$/i.test(file.name)
}

export function normalizeMediaError(cause: unknown): Error {
  const message = cause instanceof Error ? cause.message : String(cause || '')
  if (cause instanceof DOMException && cause.name === 'AbortError') {
    return abortError()
  }
  if (/decode|encoding|format|unsupported|not supported|media_err/i.test(message)) {
    return new Error(
      '无法解析该音视频文件，请确认文件包含浏览器可播放的音轨（如 MP3、WAV、M4A、AAC、OGG、MP4、MOV、WebM）',
    )
  }
  return new Error(message ? `音视频解析失败：${message}` : '音视频解析失败')
}

async function resampleBuffer(buffer: AudioBuffer): Promise<AudioBuffer> {
  const outputLength = Math.max(
    1,
    Math.ceil((buffer.duration * TARGET_SAMPLE_RATE) || 1),
  )
  const offline = new OfflineAudioContext(1, outputLength, TARGET_SAMPLE_RATE)
  const source = offline.createBufferSource()
  source.buffer = buffer
  source.connect(offline.destination)
  source.start()
  return offline.startRendering()
}

function waitForMediaMetadata(
  media: HTMLMediaElement,
  signal?: AbortSignal,
): Promise<void> {
  if (media.readyState >= HTMLMediaElement.HAVE_METADATA) {
    return Promise.resolve()
  }

  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      reject(new Error('读取文件媒体信息超时，请确认文件格式受浏览器支持'))
    }, 15_000)
    const cleanup = (): void => {
      window.clearTimeout(timer)
      media.removeEventListener('loadedmetadata', handleLoaded)
      media.removeEventListener('error', handleError)
      signal?.removeEventListener('abort', handleAbort)
    }
    const handleLoaded = (): void => {
      cleanup()
      resolve()
    }
    const handleError = (): void => {
      cleanup()
      reject(normalizeMediaError(media.error))
    }
    const handleAbort = (): void => {
      cleanup()
      reject(abortError())
    }

    media.addEventListener('loadedmetadata', handleLoaded, { once: true })
    media.addEventListener('error', handleError, { once: true })
    signal?.addEventListener('abort', handleAbort, { once: true })
    media.load()
  })
}

function abortError(): DOMException {
  return new DOMException('文件识别已取消', 'AbortError')
}
