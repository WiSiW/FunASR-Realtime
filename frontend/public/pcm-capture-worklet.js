class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.pending = new Float32Array(0)
    this.chunkSize = 1280 // 80 ms of input at 16 kHz; other rates are chunked by sample count.
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel || channel.length === 0) {
      return true
    }

    const merged = new Float32Array(this.pending.length + channel.length)
    merged.set(this.pending)
    merged.set(channel, this.pending.length)

    let offset = 0
    while (merged.length - offset >= this.chunkSize) {
      const output = merged.slice(offset, offset + this.chunkSize)
      this.port.postMessage(output, [output.buffer])
      offset += this.chunkSize
    }
    this.pending = merged.slice(offset)
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
