export class StreamingPcm16Resampler {
  constructor(inputRate, outputRate = 16000) {
    if (inputRate <= 0 || outputRate <= 0) {
      throw new Error('采样率必须大于 0')
    }
    this.inputRate = inputRate
    this.outputRate = outputRate
    this.ratio = inputRate / outputRate
    this.previousSample = undefined
    this.consumedSamples = 0
    this.nextOutputPosition = 0
  }

  process(input) {
    if (input.length === 0) {
      return new Int16Array(0)
    }

    const hasPrevious = this.previousSample !== undefined
    const source = hasPrevious ? new Float32Array(input.length + 1) : input
    if (hasPrevious) {
      source[0] = this.previousSample
      source.set(input, 1)
    }

    const totalAfterChunk = this.consumedSamples + input.length
    const sourceStart = this.consumedSamples - (hasPrevious ? 1 : 0)
    const output = []

    while (this.nextOutputPosition < totalAfterChunk) {
      const sourcePosition = this.nextOutputPosition - sourceStart
      const leftIndex = Math.floor(sourcePosition)
      const rightIndex = leftIndex + 1
      if (leftIndex < 0 || rightIndex >= source.length) {
        break
      }

      const fraction = sourcePosition - leftIndex
      const value =
        source[leftIndex] * (1 - fraction) + source[rightIndex] * fraction
      output.push(this.floatToInt16(value))
      this.nextOutputPosition += this.ratio
    }

    this.previousSample = input[input.length - 1]
    this.consumedSamples = totalAfterChunk
    return Int16Array.from(output)
  }

  reset() {
    this.previousSample = undefined
    this.consumedSamples = 0
    this.nextOutputPosition = 0
  }

  floatToInt16(value) {
    const clamped = Math.max(-1, Math.min(1, value))
    return clamped < 0
      ? Math.round(clamped * 32768)
      : Math.round(clamped * 32767)
  }
}
