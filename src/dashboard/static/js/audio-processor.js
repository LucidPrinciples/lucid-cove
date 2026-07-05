// =============================================================================
// AudioWorklet Processor — mic audio capture with resampling + batching
// =============================================================================
// Runs in a separate thread. Receives mic audio at the device's native sample
// rate, resamples to 16kHz (what pipecat-voice expects), converts to Int16,
// and batches to ~4096-sample chunks before posting to main thread.
// This matches the buffer size the old ScriptProcessor used.
// =============================================================================

class AudioProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.targetRate = 16000;
        this.batchSize = 4096;       // match old ScriptProcessor buffer
        this.buffer = new Float32Array(this.batchSize);
        this.buffered = 0;
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || input.length === 0 || !input[0] || input[0].length === 0) return true;

        const f32 = input[0];
        const nativeRate = sampleRate; // AudioWorklet global

        // Resample if native rate differs from target
        let samples;
        if (nativeRate !== this.targetRate) {
            const ratio = nativeRate / this.targetRate;
            const newLen = Math.floor(f32.length / ratio);
            samples = new Float32Array(newLen);
            for (let i = 0; i < newLen; i++) {
                const srcIdx = i * ratio;
                const idx = Math.floor(srcIdx);
                const frac = srcIdx - idx;
                const next = Math.min(idx + 1, f32.length - 1);
                samples[i] = f32[idx] * (1 - frac) + f32[next] * frac;
            }
        } else {
            samples = new Float32Array(f32); // copy since input buffer is reused
        }

        // Accumulate into batch buffer
        for (let i = 0; i < samples.length; i++) {
            this.buffer[this.buffered++] = samples[i];

            if (this.buffered >= this.batchSize) {
                this._flush();
            }
        }

        return true;
    }

    _flush() {
        if (this.buffered === 0) return;

        const chunk = this.buffer.subarray(0, this.buffered);

        // RMS for silence detection
        let sum = 0;
        for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
        const rms = Math.sqrt(sum / chunk.length);

        // Convert float32 → int16
        const i16 = new Int16Array(chunk.length);
        for (let i = 0; i < chunk.length; i++) {
            const s = Math.max(-1, Math.min(1, chunk[i]));
            i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        this.port.postMessage({ audio: i16.buffer, rms }, [i16.buffer]);
        this.buffered = 0;
    }
}

registerProcessor('audio-processor', AudioProcessor);
