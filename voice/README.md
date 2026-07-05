# cove-core/voice — packaged CPU voice (jules)

Local, GPU-less voice for a clean Cove. Provides jules voice dictation (faster-whisper
STT, int8 CPU) and Piper TTS over HTTP/WebSocket on port 8300. The provisioner adds a
`voice` service to a Cove's docker-compose (built from `Dockerfile.cpu`) whenever
`compute.voice.mode: local` (the default). See `provision/cove.config.example.yaml`.

## How it's wired
- The Cove app reaches it in-network at `http://{cove}-voice:8300` (transcribe proxy,
  via `VOICE_INTERNAL_URL`).
- The browser reaches it on the host's published voice port (`compute.voice` → resolved
  by `src/config.resolve_voice_urls()` → `/api/config` → `MC.voiceUrl()`).
- A Cove with a domain serves it at `voice.{domain}` via Caddy instead.

## Model assets (read before first boot)
- **STT (dictation):** `faster-whisper` downloads its model (`WHISPER_MODEL=small`) on
  first boot and caches it in the `voice_cache` volume. No bundled model needed.
- **TTS (agent voice replies):** Piper needs `.onnx` voice files in `voices/`. Only
  `voices/config.json` ships here; drop the Piper models you want into `voices/` (or
  mount them) to enable spoken replies. Dictation works without them.
- **GPU ASR (Qwen3) and CUDA are intentionally absent** — that's the GPU `Dockerfile`,
  not this one. A GPU Cove can set `compute.video_asr`/voice to local-GPU or external.

## Source of truth
This is packaged from `Services/pipecat-voice/` (GPU deploy = RB14). Keep the two in
sync when the voice pipeline changes; this copy is the open-source/CPU distribution.
