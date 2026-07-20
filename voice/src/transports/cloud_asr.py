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

Long media is chunked before upload. Groq/OpenAI reject large bodies (~25MB
file caps; multi-hour talks blow past that as a single wav). We split the
extracted 16k mono wav into ~10-minute pieces, transcribe each, and stitch
segments with time offsets so captions still land on the right words.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
import wave
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

# OpenAI-compatible providers cap request bodies well under a long 16k mono wav.
# 10 minutes of 16kHz mono PCM16 ≈ 19MB — under Groq's ~25MB file limit with margin.
_CHUNK_SECONDS = int(os.getenv("CLOUD_ASR_CHUNK_SECONDS", "600") or "600")
_MAX_UPLOAD_BYTES = int(
    os.getenv("CLOUD_ASR_MAX_UPLOAD_BYTES", str(24 * 1024 * 1024))
    or str(24 * 1024 * 1024)
)


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
            if result.returncode == 0 and (result.stdout or "").strip():
                return float(result.stdout.strip())
        except Exception:
            pass
        try:
            with wave.open(audio_path, "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except Exception:
            return None

    def _extract_audio(self, file_path: str, work_dir: str) -> str:
        """Extract a 16k mono wav from video so the upload stays smaller than raw MOV."""
        out = os.path.join(work_dir, Path(file_path).stem + ".cloud.wav")
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", file_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", out],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not os.path.isfile(out):
            err = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(f"ffmpeg audio extract failed: {err}")
        size_mb = os.path.getsize(out) / (1024 * 1024)
        logger.info(f"Cloud ASR extracted audio: {size_mb:.1f} MB → {out}")
        return out

    def _split_wav(self, audio_path: str, work_dir: str, chunk_seconds: int) -> list[tuple[str, float]]:
        """Split wav into sequential chunk files. Returns [(path, start_offset_sec), ...]."""
        duration = self.get_duration(audio_path) or 0.0
        size = os.path.getsize(audio_path) if os.path.isfile(audio_path) else 0
        if duration and duration <= chunk_seconds and size <= _MAX_UPLOAD_BYTES:
            return [(audio_path, 0.0)]
        if not duration and size <= _MAX_UPLOAD_BYTES:
            return [(audio_path, 0.0)]

        seg_secs = max(10, int(chunk_seconds or 600))
        # Prefer pure-Python PCM slicing when the source is already a wav — no
        # ffmpeg dependency for the split itself (extract still uses ffmpeg).
        wav_chunks = self._split_wav_pcm(audio_path, work_dir, seg_secs)
        if wav_chunks is not None:
            chunks_paths = wav_chunks
        else:
            pattern = os.path.join(work_dir, f"{Path(audio_path).stem}.chunk_%03d.wav")
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-f", "segment", "-segment_time", str(seg_secs),
                "-reset_timestamps", "1",
                "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                pattern,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            found = sorted(Path(work_dir).glob(f"{Path(audio_path).stem}.chunk_*.wav"))
            if result.returncode != 0 or not found:
                err = (result.stderr or "")[-400:]
                raise RuntimeError(
                    f"Failed to split long audio for cloud ASR ({size / (1024 * 1024):.1f} MB, "
                    f"{duration:.0f}s). ffmpeg: {err}"
                )
            chunks_paths = [str(c) for c in found]

        out: list[tuple[str, float]] = []
        offset = 0.0
        for cpath in chunks_paths:
            try:
                csize = os.path.getsize(cpath)
            except OSError:
                continue
            if csize < 1000:
                try:
                    os.remove(cpath)
                except Exception:
                    pass
                continue
            if csize > _MAX_UPLOAD_BYTES:
                raise RuntimeError(
                    f"Cloud ASR chunk still exceeds upload cap "
                    f"({csize / (1024 * 1024):.1f} MB > "
                    f"{_MAX_UPLOAD_BYTES / (1024 * 1024):.0f} MB). "
                    f"Lower CLOUD_ASR_CHUNK_SECONDS (now {chunk_seconds})."
                )
            out.append((cpath, offset))
            d = self.get_duration(cpath) or float(chunk_seconds)
            offset += d
        if not out:
            raise RuntimeError("Cloud ASR split produced no usable audio chunks")
        logger.info(
            f"Cloud ASR split {duration:.0f}s audio into {len(out)} chunk(s) "
            f"(~{chunk_seconds}s, cap {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB)"
        )
        return out

    def _split_wav_pcm(self, audio_path: str, work_dir: str, chunk_seconds: int) -> list[str] | None:
        """Slice a PCM wav into chunk files without ffmpeg. None if not a readable wav."""
        try:
            with wave.open(audio_path, "rb") as src:
                nch, sw, fr, nframes = (
                    src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getnframes(),
                )
                if fr <= 0 or sw <= 0:
                    return None
                frames_per = max(1, int(chunk_seconds * fr))
                paths: list[str] = []
                idx = 0
                while True:
                    frames = src.readframes(frames_per)
                    if not frames:
                        break
                    dest = os.path.join(work_dir, f"{Path(audio_path).stem}.chunk_{idx:03d}.wav")
                    with wave.open(dest, "wb") as dst:
                        dst.setnchannels(nch)
                        dst.setsampwidth(sw)
                        dst.setframerate(fr)
                        dst.writeframes(frames)
                    paths.append(dest)
                    idx += 1
                    if len(frames) < frames_per * nch * sw:
                        break
                return paths or None
        except wave.Error:
            return None
        except Exception as e:
            logger.warning(f"PCM wav split fallback failed: {e}")
            return None

    def transcribe(self, file_path: str, use_timestamps: bool = True,
                   work_dir: str = "/tmp") -> dict:
        """Transcribe a file via the cloud API. Same return shape as Qwen3-ASR."""
        if not self.api_key:
            raise RuntimeError(
                f"Cloud ASR provider '{self.provider}' has no API key "
                f"(set {_PROVIDERS[self.provider][0]}, or save it under Pipeline Services "
                f"so the Cove can forward it with the job)."
            )
        file_path = str(file_path)
        ext = Path(file_path).suffix.lower()
        os.makedirs(work_dir, exist_ok=True)
        job_dir = tempfile.mkdtemp(prefix="cloud-asr-", dir=work_dir)
        try:
            audio_path = self._extract_audio(file_path, job_dir) if ext in _VIDEO_EXTS else file_path
            source_type = "video" if ext in _VIDEO_EXTS else "audio"

            t_start = time.time()
            pieces = self._split_wav(audio_path, job_dir, _CHUNK_SECONDS)
            all_text: list[str] = []
            all_segments: list[dict] = []
            lang = None

            for idx, (chunk_path, offset) in enumerate(pieces):
                logger.info(
                    f"Cloud ASR chunk {idx + 1}/{len(pieces)} "
                    f"offset={offset:.1f}s file={Path(chunk_path).name}"
                )
                try:
                    if self.provider == "deepgram":
                        text, segments, chunk_lang = self._deepgram(chunk_path, use_timestamps)
                    else:
                        text, segments, chunk_lang = self._openai_compatible(
                            chunk_path, use_timestamps
                        )
                except httpx.HTTPStatusError as he:
                    body = ""
                    try:
                        body = (he.response.text or "")[:400]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"Cloud ASR {self.provider} rejected chunk {idx + 1}/{len(pieces)} "
                        f"(HTTP {he.response.status_code}): {body or he}. "
                        f"Long videos are split automatically; if this persists, check the "
                        f"API key/quota or try Deepgram for very long talks."
                    ) from he

                if chunk_lang and not lang:
                    lang = chunk_lang
                if text:
                    all_text.append(text.strip())
                if use_timestamps and segments:
                    for s in segments:
                        item = dict(s)
                        if item.get("start") is not None:
                            item["start"] = round(float(item["start"]) + offset, 3)
                        if item.get("end") is not None:
                            item["end"] = round(float(item["end"]) + offset, 3)
                        all_segments.append(item)

            elapsed = time.time() - t_start
            text = " ".join(all_text).strip()
            transcript = {
                "source_file": file_path,
                "source_type": source_type,
                "language": lang or "en",
                "text": text,
                "model": f"{self.provider}/{self.ASR_MODEL}",
                "asr_service": self.asr_service,
                "transcription_seconds": round(elapsed, 2),
                "cloud_asr_chunks": len(pieces),
            }
            duration = self.get_duration(audio_path)
            if duration:
                transcript["audio_duration_seconds"] = round(duration, 2)
                transcript["realtime_factor"] = round(duration / elapsed, 1) if elapsed else None
            if use_timestamps and all_segments:
                transcript["segments"] = all_segments
            logger.info(
                f"Cloud ASR ({self.provider}) transcribed {len(text.split())} words "
                f"in {elapsed:.1f}s across {len(pieces)} chunk(s)"
            )
            return transcript
        finally:
            try:
                for root, _dirs, files in os.walk(job_dir, topdown=False):
                    for name in files:
                        try:
                            os.remove(os.path.join(root, name))
                        except Exception:
                            pass
                    try:
                        os.rmdir(root)
                    except Exception:
                        pass
            except Exception:
                pass

    def _openai_compatible(self, audio_path: str, use_timestamps: bool):
        """Groq / OpenAI Whisper via /audio/transcriptions (verbose_json)."""
        size = os.path.getsize(audio_path) if os.path.isfile(audio_path) else 0
        if size > _MAX_UPLOAD_BYTES:
            raise RuntimeError(
                f"Cloud ASR upload still too large ({size / (1024 * 1024):.1f} MB). "
                f"Chunking should have prevented this — check CLOUD_ASR_CHUNK_SECONDS."
            )
        with open(audio_path, "rb") as fh:
            files = {"file": (os.path.basename(audio_path), fh, "audio/wav")}
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
                for w in words:
                    segments.append({
                        "text": (w.get("word") or "").strip(),
                        "start": w.get("start"),
                        "end": w.get("end"),
                    })
            else:
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
