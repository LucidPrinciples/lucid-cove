"""
Whisper STT Transport for Pipecat

Uses faster-whisper for efficient speech-to-text on GPU.
Model: 'large-v3-turbo' (809M parameters) loaded on CUDA if available.
Upgraded from 'small' (244M) — session 145, June 2026.
"""

import logging
import subprocess
import io
import numpy as np
from typing import Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class WhisperSTTTransport:
    """
    Speech-to-Text transport using faster-whisper.

    Features:
    - GPU check before model loading
    - faster-whisper 'large-v3-turbo' model (809M params)
    - Audio frame transcription to text
    """

    # Model cache root — mounted from host. faster-whisper resolves HF cache structure.
    # Fallback: flat model dir from original small install
    MODEL_CACHE_ROOT = "/models/whisper-large-v3-turbo"
    FALLBACK_MODEL_PATH = "/models/whisper-small"

    def __init__(self, model_size: str = "large-v3-turbo", device: str = "auto"):
        self.model_size = model_size
        self.device = device
        self.model = None
        self.is_initialized = False
        
    def check_gpu_available(self) -> Tuple[bool, str]:
        """
        Check if GPU is available for model loading.
        Uses torch CUDA check (works with NVIDIA container runtime).

        Returns:
            (gpu_available, status_message)
        """
        try:
            import torch
            if torch.cuda.is_available():
                name = torch.cuda.get_device_name(0)
                return True, f"GPU available: {name}"
            return False, "torch.cuda not available"
        except ImportError:
            # Fallback to nvidia-smi if torch not installed
            try:
                result = subprocess.run(
                    ["nvidia-smi"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return True, "GPU detected via nvidia-smi"
                return False, "nvidia-smi returned error"
            except Exception:
                return False, "No GPU detection method available"
        except Exception as e:
            return False, f"GPU check failed: {e}"
    
    def initialize(self) -> bool:
        """
        Initialize the Whisper model.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            from faster_whisper import WhisperModel
            
            # Check GPU first
            gpu_available, gpu_msg = self.check_gpu_available()
            logger.info(f"GPU check: {gpu_msg}")
            
            # Determine device
            if self.device == "auto":
                compute_type = "float16" if gpu_available else "int8"
                device = "cuda" if gpu_available else "cpu"
            else:
                device = self.device
                compute_type = "float16" if device == "cuda" else "int8"
            
            # Load model: use HF cache at MODEL_CACHE_ROOT if it exists,
            # fall back to flat whisper-small dir, then download by name
            import os
            if os.path.isdir(self.MODEL_CACHE_ROOT):
                # HF cache structure — pass model name + download_root
                model_path = self.model_size
                download_root = self.MODEL_CACHE_ROOT
                logger.info(f"Loading Whisper model '{model_path}' from cache at {download_root} on {device}...")
            elif os.path.isdir(self.FALLBACK_MODEL_PATH):
                # Flat model dir (legacy whisper-small)
                model_path = self.FALLBACK_MODEL_PATH
                download_root = None
                logger.warning(f"Primary model cache not found, using fallback {self.FALLBACK_MODEL_PATH}")
            else:
                model_path = self.model_size
                download_root = None
                logger.info(f"No local model found, downloading '{model_path}'...")

            try:
                kwargs = dict(device=device, compute_type=compute_type)
                if download_root:
                    kwargs["download_root"] = download_root
                self.model = WhisperModel(model_path, **kwargs)
            except RuntimeError as cuda_err:
                if device == "cuda":
                    logger.warning(f"CUDA failed ({cuda_err}), falling back to CPU...")
                    device = "cpu"
                    compute_type = "int8"
                    self.model = WhisperModel(
                        self.model_size,
                        device=device,
                        compute_type=compute_type
                    )
                else:
                    raise

            self.is_initialized = True
            logger.info(f"Whisper model loaded successfully on {device}")
            return True
            
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            return False
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            return False
    
    def audio_bytes_to_numpy(self, audio_bytes: bytes, sample_rate: int = 16000) -> np.ndarray:
        """
        Convert audio bytes to numpy array for Whisper.
        
        Args:
            audio_bytes: Raw audio data (assumed 16-bit PCM)
            sample_rate: Target sample rate (Whisper expects 16kHz)
            
        Returns:
            Numpy array of float32 audio samples
        """
        # Convert bytes to numpy (16-bit PCM)
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        
        # Normalize to float32 [-1.0, 1.0]
        audio_float = audio_np.astype(np.float32) / 32768.0
        
        return audio_float
    
    def transcribe(self, audio_bytes: bytes, language: str = "en") -> Optional[str]:
        """
        Transcribe audio bytes to text.
        
        Args:
            audio_bytes: Raw audio data (16-bit PCM, 16kHz)
            language: Language code (default: 'en')
            
        Returns:
            Transcribed text, or None if transcription failed
        """
        if not self.is_initialized:
            logger.error("Whisper model not initialized")
            return None
        
        try:
            # Convert to numpy
            audio_np = self.audio_bytes_to_numpy(audio_bytes)
            
            # Run transcription
            segments, info = self.model.transcribe(
                audio_np,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )
            
            # Collect all text segments
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())
            
            full_text = " ".join(text_parts).strip()
            
            if full_text:
                logger.debug(f"Transcribed: '{full_text[:50]}...' "
                           f"(lang: {info.language}, prob: {info.language_probability:.2f})")
                return full_text
            else:
                return None
                
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None
    
    def transcribe_buffer(self, audio_buffer: list, sample_rate: int = 16000) -> Optional[str]:
        """
        Transcribe a buffer of audio frames.
        
        Args:
            audio_buffer: List of audio byte chunks
            sample_rate: Sample rate of audio
            
        Returns:
            Transcribed text, or None
        """
        if not audio_buffer:
            return None
        
        # Concatenate all frames
        combined = b''.join(audio_buffer)
        return self.transcribe(combined)
    
    def cleanup(self):
        """Release model resources."""
        if self.model is not None:
            # faster-whisper doesn't have explicit cleanup, 
            # but we can help GC by removing reference
            self.model = None
            self.is_initialized = False
            logger.info("Whisper model cleaned up")


# Convenience function for direct usage
def create_whisper_transport(model_size: str = "large-v3-turbo") -> WhisperSTTTransport:
    """Create and initialize a Whisper STT transport."""
    transport = WhisperSTTTransport(model_size=model_size)
    transport.initialize()
    return transport


class WhisperFileASR:
    """File-based batch ASR using faster-whisper, GPU-OPTIONAL (CPU int8 fallback).

    A drop-in for QwenASRTransport in /api/stt/video: same
    transcribe(file_path, use_timestamps, work_dir) -> dict shape — so a
    GPU-less Cove can transcribe video locally on CPU. Lazy-loads the model.
    """

    def __init__(self, model_size: str = None):
        import os
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "large-v3-turbo")
        self.ASR_MODEL = f"faster-whisper/{self.model_size}"
        self.asr_service = "local"  # local = $0 in the price map
        self._t = WhisperSTTTransport(model_size=self.model_size)
        self.is_loaded = False

    def _ensure(self):
        if not self.is_loaded:
            self.is_loaded = self._t.initialize()
        return self.is_loaded

    def transcribe(self, file_path: str, use_timestamps: bool = True,
                   work_dir: str = "/tmp") -> dict:
        import time as _time
        if not self._ensure():
            raise RuntimeError("faster-whisper failed to initialize")
        file_path = str(file_path)
        t0 = _time.time()
        # faster-whisper decodes media via ffmpeg/av — accepts a file path.
        segments_iter, info = self._t.model.transcribe(
            file_path, language="en", beam_size=5, vad_filter=True,
        )
        segs, parts = [], []
        for s in segments_iter:
            parts.append((s.text or "").strip())
            if use_timestamps:
                segs.append({"text": (s.text or "").strip(),
                             "start": round(s.start, 2), "end": round(s.end, 2)})
        elapsed = _time.time() - t0
        text = " ".join(p for p in parts if p).strip()
        transcript = {
            "source_file": file_path,
            "source_type": "media",
            "language": getattr(info, "language", "en"),
            "text": text,
            "model": self.ASR_MODEL,
            "asr_service": self.asr_service,
            "transcription_seconds": round(elapsed, 2),
        }
        duration = getattr(info, "duration", None)
        if duration:
            transcript["audio_duration_seconds"] = round(float(duration), 2)
            transcript["realtime_factor"] = round(duration / elapsed, 1) if elapsed else None
        if use_timestamps and segs:
            transcript["segments"] = segs
        return transcript


def get_whisper_file_asr(model_size: str = None) -> WhisperFileASR:
    return WhisperFileASR(model_size=model_size)


if __name__ == "__main__":
    # Test the transport
    logging.basicConfig(level=logging.INFO)
    
    print("Testing Whisper STT Transport...")
    transport = WhisperSTTTransport()
    
    if transport.initialize():
        print("Model loaded successfully!")
        
        # Create test audio (1 second of silence)
        test_audio = np.zeros(16000, dtype=np.int16).tobytes()
        result = transport.transcribe(test_audio)
        
        if result:
            print(f"Transcription: {result}")
        else:
            print("No transcription (expected for silence)")
        
        transport.cleanup()
    else:
        print("Failed to initialize transport")