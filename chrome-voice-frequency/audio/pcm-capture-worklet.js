const DEFAULT_CHUNK_DURATION_MS = 160

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const configuredDuration = Number(options?.processorOptions?.chunkDurationMs)
    const chunkDurationMs =
      Number.isFinite(configuredDuration) && configuredDuration > 0
        ? configuredDuration
        : DEFAULT_CHUNK_DURATION_MS

    this.chunkSize = Math.max(
      128,
      Math.round((sampleRate * chunkDurationMs) / 1000),
    )
    this.buffer = new Float32Array(this.chunkSize)
    this.monoBuffer = null
    this.writeIndex = 0
    this.port.onmessage = (event) => {
      if (event.data?.type === 'flush') {
        this.flush()
      }
    }
  }

  process(inputs) {
    const channels = inputs[0]
    if (!channels || channels.length === 0 || channels[0].length === 0) {
      return true
    }

    const channel = this.toMono(channels)
    let readIndex = 0
    while (readIndex < channel.length) {
      const writable = this.chunkSize - this.writeIndex
      const copyLength = Math.min(writable, channel.length - readIndex)
      this.buffer.set(
        channel.subarray(readIndex, readIndex + copyLength),
        this.writeIndex,
      )
      this.writeIndex += copyLength
      readIndex += copyLength

      if (this.writeIndex === this.chunkSize) {
        const samples = this.buffer.slice()
        this.port.postMessage(
          { samples, level: calculateRms(samples) },
          [samples.buffer],
        )
        this.writeIndex = 0
      }
    }
    return true
  }

  toMono(channels) {
    if (channels.length === 1) {
      return channels[0]
    }
    const frameLength = channels[0].length
    if (!this.monoBuffer || this.monoBuffer.length !== frameLength) {
      this.monoBuffer = new Float32Array(frameLength)
    }
    this.monoBuffer.fill(0)
    for (const channel of channels) {
      for (let index = 0; index < frameLength; index += 1) {
        this.monoBuffer[index] += channel[index] || 0
      }
    }
    const scale = 1 / channels.length
    for (let index = 0; index < frameLength; index += 1) {
      this.monoBuffer[index] *= scale
    }
    return this.monoBuffer
  }

  flush() {
    if (this.writeIndex > 0) {
      const samples = this.buffer.slice(0, this.writeIndex)
      this.port.postMessage(
        { samples, level: calculateRms(samples) },
        [samples.buffer],
      )
      this.writeIndex = 0
    }
    this.port.postMessage({ flushed: true })
  }
}

function calculateRms(samples) {
  if (samples.length === 0) {
    return 0
  }
  let sum = 0
  for (let index = 0; index < samples.length; index += 1) {
    const sample = samples[index]
    sum += sample * sample
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 3)
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
