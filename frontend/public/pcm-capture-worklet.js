const DEFAULT_CHUNK_DURATION_MS = 160

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const configuredDuration = Number(options?.processorOptions?.chunkDurationMs)
    const chunkDurationMs =
      Number.isFinite(configuredDuration) && configuredDuration > 0
        ? configuredDuration
        : DEFAULT_CHUNK_DURATION_MS

    // AudioWorkletGlobalScope exposes the actual device sample rate.  Chunk by
    // duration instead of a fixed sample count so 44.1/48 kHz devices do not
    // send a WebSocket message every ~27 ms.
    this.chunkSize = Math.max(128, Math.round((sampleRate * chunkDurationMs) / 1000))
    this.buffer = new Float32Array(this.chunkSize)
    this.writeIndex = 0
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'flush') {
        this.flush()
      }
    }
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel || channel.length === 0) {
      return true
    }

    let readIndex = 0
    while (readIndex < channel.length) {
      const writable = this.chunkSize - this.writeIndex
      const copyLength = Math.min(writable, channel.length - readIndex)
      this.buffer.set(channel.subarray(readIndex, readIndex + copyLength), this.writeIndex)
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
