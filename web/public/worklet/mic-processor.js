/**
 * Microphone capture worklet.
 *
 * The graph runs at 16kHz, so process() hands us 128-sample blocks. We batch
 * them into 512-sample frames -- Silero VAD's native frame size -- so the server
 * never has to re-window the stream.
 *
 * This runs on the audio render thread. Anything slow here causes dropouts, so
 * it only copies floats and computes a cheap RMS for the mic meter.
 */
const FRAME_SAMPLES = 512;

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(FRAME_SAMPLES);
    this._filled = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._filled++] = channel[i];

      if (this._filled === FRAME_SAMPLES) {
        let sumSquares = 0;
        for (let j = 0; j < FRAME_SAMPLES; j++) sumSquares += this._buffer[j] * this._buffer[j];

        // Transfer the buffer rather than copying it, then allocate a fresh one.
        const frame = this._buffer;
        this._buffer = new Float32Array(FRAME_SAMPLES);
        this._filled = 0;

        this.port.postMessage(
          { frame, level: Math.sqrt(sumSquares / FRAME_SAMPLES) },
          [frame.buffer],
        );
      }
    }

    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
