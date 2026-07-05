"""
Cloud ASR transport — BYOK batch transcription for GPU-less / hosted Coves.

The hosted Lucid Cove runs on a CPU VPS that can't load Qwen3-ASR. This client
transcribes via a cloud ASR API instead (the operator's own key), and returns
the SAME transcript dict shape as QwenASRTransport.transcribe() so the
/api/stt/video route is engine-agnostic.

Provider is auto-detected from whichever key is present (override with
ASR_PROVIDER): groq (whisper-large-v3) | openai (whisper-1) | deepgram (nova-2).
Groq/OpenAI use the OpenAI-compatible /audio/transcriptions endpoint with
verbose_json (segments + duration); Deepgram uses its REST API.
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

# provider -> (env key, base url, default model, cost-map service name)
_PROVIDERS = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "whisper-large-v3", "groq-whisper"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "whisper-1", "openai-whisper"),
    "deepgram": ("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1", "nova-2", "deepgram-nova"),
}


class CloudASRTransport:
    """Drop-in replacement for QwenASRTransport using a cloud ASR API."""

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None):
        self.provider = (provider or os.getenv("ASR_PROVIDER") or self._autodetect() or "groq").lower()
        env_key, base, model, service = _PROVIDERS.get(self.provider, _PROVIDERS["groq"])
        # Request-supplied key (the Cove's saved AT-1 key, forwarded per-job) wins;
        # this container's own env is the fallback for env-provisioned setups.
        self.api_key = (api_key or "").strip() or os.getenv(env_key, "")
        self.base_url = base
        self.ASR_MODEL = os.getenv("ASR_MODEL", model)
        self.asr_service = service
        self.is_loaded = False  # cloud — nothing to load

    @staticmethod
    def _autodetect() -> Optional[str]:
        for name, (env_key, *_rest) in _PROVIDERS.items():
            if os.getenv(env_key):
                return name
        return None

    def get_duration(self, audio_path: str) -> Optional[float]:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries",
                 "format=duration", "-of", "csv=p=0", audio_path],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception:
            pass
        return None

    def _extract_audio(self, file_path: str, work_dir: str) -> str:
        """Extract a 16k mono wav from video so the upload stays small."""
        out = os.path.join(work_dir, Path(file_path).stem + ".cloud.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", file_path, "-ar", "16000", "-ac", "1", out],
            capture_output=True,
        )
        return out if os.path.isfile(out) else file_path

    def transcribe(self, file_path: str, use_timestamps: bool = True,
                   work_dir: str = "/tmp") -> dict:
        """Transcribe a file via the cloud API. Same return shape as Qwen3-ASR."""
        if not self.api_key:
            raise RuntimeError(
                f"Cloud ASR provider '{self.provider}' has no API key "
                f"(set {_PROVIDERS[self.provider][0]})."
            )
        file_path = str(file_path)
        ext = Path(file_path).suffix.lower()
        audio_path = self._extract_audio(file_path, work_dir) if ext in _VIDEO_EXTS else file_path
        source_type = "video" if ext in _VIDEO_EXTS else "audio"

        t_start = time.time()
        if self.provider == "deepgram":
            text, segments, lang = self._deepgram(audio_path, use_timestamps)
        else:
            text, segments, lang = self._openai_compatible(audio_path, use_timestamps)
        elapsed = time.time() - t_start

        transcript = {
            "source_file": file_path,
            "source_type": source_type,
            "language": lang or "en",
            "text": text,
            "model": f"{self.provider}/{self.ASR_MODEL}",
            "asr_service": self.asr_service,
            "transcription_seconds": round(elapsed, 2),
        }
        duration = self.get_duration(audio_path)
        if duration:
            transcript["audio_duration_seconds"] = round(duration, 2)
            transcript["realtime_factor"] = round(duration / elapsed, 1) if elapsed else None
        if use_timestamps and segments:
            transcript["segments"] = segments
        logger.info(f"Cloud ASR ({self.provider}) transcribed {len(text.split())} words in {elapsed:.1f}s")
        return transcript

    def _openai_compatible(self, audio_path: str, use_timestamps: bool):
        """Groq / OpenAI Whisper via /audio/transcriptions (verbose_json)."""
        with open(audio_path, "rb") as fh:
            files = {"file": (os.path.basename(audio_path), fh, "audio/wav")}
            # timestamp_granularities: WORD-level timing (2026-07-03) — the pipeline's
            # per-word caption mode needs word timestamps like the local Qwen +
            # ForcedAligner path produces. Segment-only responses made "word" captions
            # render whole sentences as one block. Groq + OpenAI both support this.
            data = {"model": self.ASR_MODEL, "response_format": "verbose_json",
                    "timestamp_granularities[]": ["word", "segment"]}
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files=files, data=data,
                )
                resp.raise_for_status()
                j = resp.json()
        text = (j.get("text") or "").strip()
        lang = j.get("language")
        segments = []
        if use_timestamps:
            words = j.get("words") or []
            if words:
                # Word-level (preferred): mirrors the Qwen/ForcedAligner shape.
                for w in words:
                    segments.append({
                        "text": (w.get("word") or "").strip(),
                        "start": w.get("start"),
                        "end": w.get("end"),
                    })
            else:
                # Fallback: sentence segments (provider ignored the granularity ask).
                for s in j.get("segments", []) or []:
                    segments.append({
                        "text": (s.get("text") or "").strip(),
                        "start": s.get("start"),
                        "end": s.get("end"),
                    })
        return text, segments, lang

    def _deepgram(self, audio_path: str, use_timestamps: bool):
        """Deepgram pre-recorded transcription."""
        params = {"model": self.ASR_MODEL, "smart_format": "true",
                  "punctuate": "true", "utterances": "true"}
        with open(audio_path, "rb") as fh:
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    f"{self.base_url}/listen",
                    headers={"Authorization": f"Token {self.api_key}",
                             "Content-Type": "audio/wav"},
                    params=params, content=fh.read(),
                )
                resp.raise_for_status()
                j = resp.json()
        chan = (j.get("results", {}).get("channels") or [{}])[0]
        alt = (chan.get("alternatives") or [{}])[0]
        text = (alt.get("transcript") or "").strip()
        segments = []
        if use_timestamps:
            for u in j.get("results", {}).get("utterances", []) or []:
                segments.append({
                    "text": (u.get("transcript") or "").strip(),
                    "start": u.get("start"), "end": u.get("end"),
                })
        return text, segments, None


def get_cloud_asr(provider: Optional[str] = None, api_key: Optional[str] = None) -> CloudASRTransport:
    # Per-call construction (no singleton): the key can differ per job when the
    # Cove forwards its saved key with the request.
    return CloudASRTransport(provider=provider, api_key=api_key)
