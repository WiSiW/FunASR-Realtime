/**
 * Stateful linear resampler that converts AudioWorklet float samples to
 * 16 kHz mono signed 16-bit PCM without resetting phase at chunk boundaries.
 */
export class StreamingPcm16Resampler {
  private readonly ratio: number
  private previousSample: number | undefined
  private consumedSamples = 0
  private nextOutputPosition = 0

  constructor(
    private readonly inputRate: number,
    private readonly outputRate = 16000,
  ) {
    if (inputRate <= 0 || outputRate <= 0) {
      throw new Error('采样率必须大于 0')
    }
    this.ratio = inputRate / outputRate
  }

  process(input: Float32Array): Int16Array {
    if (input.length === 0) {
      return new Int16Array(0)
    }

    const hasPrevious = this.previousSample !== undefined
    const source = hasPrevious ? new Float32Array(input.length + 1) : input
    if (hasPrevious) {
      source[0] = this.previousSample as number
      source.set(input, 1)
    }

    const totalAfterChunk = this.consumedSamples + input.length
    const sourceStart = this.consumedSamples - (hasPrevious ? 1 : 0)
    const output: number[] = []

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

  reset(): void {
    this.previousSample = undefined
    this.consumedSamples = 0
    this.nextOutputPosition = 0
  }

  private floatToInt16(value: number): number {
    const clamped = Math.max(-1, Math.min(1, value))
    return clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767)
  }
}
