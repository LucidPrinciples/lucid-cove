"""
Qwen3-ASR Batch Transcription Transport

On-demand model loading for batch video/audio transcription.
Loads Qwen3-ASR-1.7B + ForcedAligner-0.6B, transcribes, then unloads
to free ~7-9GB VRAM for other GPU workloads.

Not for real-time use — see whisper_stt.py for dictation/Jules.
Session 145, June 2026.
"""

import gc
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class QwenASRTransport:
    """
    Batch Speech-to-Text using Qwen3-ASR-1.7B + ForcedAligner.

    - Loads model on-demand, unloads after transcription
    - Extracts audio from video via ffmpeg
    - Returns transcript with word-level timestamps
    - ~5-7GB VRAM (ASR) + ~1-2GB (aligner) during transcription
    """

    ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
    ALIGNER_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"

    def __init__(self):
        self.model = None
        self.is_loaded = False

    def _load_model(self, use_aligner: bool = True):
        """Load ASR model + optional forced aligner onto GPU."""
        import torch
        from qwen_asr import Qwen3ASRModel

        logger.info(f"Loading {self.ASR_MODEL}...")
        load_start = time.time()

        kwargs = dict(
            dtype=torch.bfloat16,
            device_map="cuda:0",
            max_inference_batch_size=32,
            max_new_tokens=4096,
        )

        if use_aligner:
            kwargs["forced_aligner"] = self.ALIGNER_MODEL
            kwargs["forced_aligner_kwargs"] = dict(
                dtype=torch.bfloat16,
                device_map="cuda:0",
            )

        self.model = Qwen3ASRModel.from_pretrained(
            self.ASR_MODEL, **kwargs
        )
        self.is_loaded = True
        logger.info(f"Qwen3-ASR loaded in {time.time() - load_start:.1f}s")

    def _unload_model(self):
        """Unload model and free GPU memory."""
        if self.model is not None:
            import torch
            del self.model
            self.model = None
            self.is_loaded = False
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("Qwen3-ASR unloaded, VRAM freed")

    def extract_audio(self, video_path: str, output_dir: str) -> str:
        """Extract 16kHz mono WAV from video file."""
        stem = Path(video_path).stem
        audio_path = os.path.join(output_dir, f"{stem}.wav")

        if os.path.exists(audio_path):
            logger.info(f"Audio already extracted: {audio_path}")
            return audio_path

        logger.info(f"Extracting audio from {Path(video_path).name}...")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-y", audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")

        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        logger.info(f"Audio extracted: {size_mb:.1f} MB")
        return audio_path

    def get_duration(self, audio_path: str) -> Optional[float]:
        """Get audio duration in seconds via ffprobe."""
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

    def transcribe(
        self,
        file_path: str,
        use_timestamps: bool = True,
        work_dir: str = "/tmp",
    ) -> dict:
        """
        Transcribe a video or audio file.

        Loads model, transcribes, unloads. Returns dict with text,
        segments (if timestamps), duration, and timing info.
        """
        file_path = str(file_path)
        path = Path(file_path)

        # Determine if we need to extract audio
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
        audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus"}
        ext = path.suffix.lower()

        if ext in video_exts:
            audio_path = self.extract_audio(file_path, work_dir)
            source_type = "video"
        elif ext in audio_exts:
            audio_path = file_path
            source_type = "audio"
        else:
            audio_path = file_path
            source_type = "unknown"

        # Load model
        try:
            self._load_model(use_aligner=use_timestamps)

            # Transcribe
            logger.info(f"Transcribing {path.name}...")
            t_start = time.time()

            results = self.model.transcribe(
                audio=audio_path,
                language="English",
                return_time_stamps=use_timestamps,
            )

            elapsed = time.time() - t_start
            result = results[0]

            # Build output
            transcript = {
                "source_file": file_path,
                "source_type": source_type,
                "language": result.language,
                "text": result.text,
                "model": self.ASR_MODEL,
                "transcription_seconds": round(elapsed, 2),
            }

            # Audio duration + speed
            duration = self.get_duration(audio_path)
            if duration:
                transcript["audio_duration_seconds"] = round(duration, 2)
                transcript["realtime_factor"] = round(duration / elapsed, 1)

            # Timestamps
            if use_timestamps and hasattr(result, "time_stamps") and result.time_stamps:
                segments = []
                for stamp_group in result.time_stamps:
                    if hasattr(stamp_group, "__iter__"):
                        for stamp in stamp_group:
                            segments.append({
                                "text": stamp.text,
                                "start": stamp.start_time,
                                "end": stamp.end_time,
                            })
                    else:
                        segments.append({
                            "text": stamp_group.text,
                            "start": stamp_group.start_time,
                            "end": stamp_group.end_time,
                        })
                transcript["segments"] = segments

            logger.info(
                f"Transcribed: {len(transcript['text'].split())} words, "
                f"{elapsed:.1f}s"
                + (f", {transcript.get('realtime_factor', '?')}x realtime" if duration else "")
            )

            return transcript

        finally:
            # Always unload, even on error
            self._unload_model()

            # Clean up extracted audio
            if source_type == "video" and os.path.exists(audio_path):
                os.remove(audio_path)
                logger.info("Cleaned up extracted audio")
