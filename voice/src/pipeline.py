"""
Pipecat Pipeline Assembly

Orchestrates the complete voice pipeline:
    Audio Input → STT (faster-whisper/GPU) → 
    Text → LLM (Ollama/qwen3:32b) → 
    Response → TTS (Piper/CPU) → 
    Audio Output
"""

import asyncio
import logging
import time
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class PipelineStatus(Enum):
    IDLE = "idle"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class PipelineResult:
    """Result from pipeline processing."""
    transcript: str
    response_text: str
    response_audio: Optional[bytes]
    duration_ms: float
    status: PipelineStatus


class VoicePipeline:
    """Main voice processing pipeline."""
    
    def __init__(self):
        self.stt = None
        self.llm = None
        self.tts = None
        self.initialized = False
        self._status_callback: Optional[Callable] = None
    
    def set_status_callback(self, callback):
        self._status_callback = callback
    
    def _emit(self, status, message, data=None):
        if self._status_callback:
            asyncio.get_event_loop().call_soon(self._status_callback, {
                "status": status.value, "message": message, "data": data
            })
    
    async def initialize(self) -> bool:
        """Initialize all pipeline components."""
        logger.info("Initializing voice pipeline...")
        
        try:
            import os

            from src.transports.whisper_stt import WhisperSTTTransport
            from src.transports.ollama_llm import OllamaLLMTransport
            from src.transports.piper_tts import PiperTTSTransport

            # WHISPER_DEVICE=cpu|cuda|auto — default auto. Batch video ASR (Qwen)
            # needs the 3090 free; set cpu on video-heavy hosts so live STT does
            # not pin large-v3-turbo on CUDA at boot (~10–13 GiB).
            whisper_device = (os.environ.get("WHISPER_DEVICE") or "auto").strip().lower()
            if whisper_device not in ("auto", "cpu", "cuda"):
                logger.warning(
                    "WHISPER_DEVICE=%r invalid; using auto", whisper_device
                )
                whisper_device = "auto"
            logger.info("Whisper device preference: %s", whisper_device)
            self.stt = WhisperSTTTransport(device=whisper_device)
            stt_ok = self.stt.initialize()
            
            self.llm = OllamaLLMTransport()
            llm_ok = self.llm.initialize()
            
            self.tts = PiperTTSTransport()
            tts_ok = self.tts.initialize()
            
            self.initialized = all([stt_ok, llm_ok, tts_ok])
            
            if self.initialized:
                logger.info("Voice pipeline fully initialized")
            else:
                failed = []
                if not stt_ok: failed.append("STT")
                if not llm_ok: failed.append("LLM")
                if not tts_ok: failed.append("TTS")
                logger.error(f"Pipeline partially initialized. Failed: {failed}")
            
            return self.initialized
            
        except Exception as e:
            logger.error(f"Pipeline initialization failed: {e}")
            return False
    
    async def process_audio(self, audio_bytes: bytes) -> PipelineResult:
        """Process audio through the complete pipeline."""
        start_time = time.time()
        
        if not self.initialized:
            raise RuntimeError("Pipeline not initialized")
        
        try:
            # Step 1: STT
            self._emit(PipelineStatus.TRANSCRIBING, "Transcribing...")
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, self.stt.transcribe, audio_bytes)
            
            if not transcript:
                return PipelineResult("", "", None, 0, PipelineStatus.ERROR)
            
            self._emit(PipelineStatus.TRANSCRIBING, "Done", {"text": transcript})
            
            # Step 2: LLM
            self._emit(PipelineStatus.THINKING, "Generating...")
            response_text = await self.llm.generate_simple(transcript)
            self._emit(PipelineStatus.THINKING, "Done", {"text": response_text})
            
            # Step 3: TTS
            self._emit(PipelineStatus.SYNTHESIZING, "Synthesizing...")
            response_audio = await loop.run_in_executor(None, self.tts.synthesize, response_text)
            self._emit(PipelineStatus.SYNTHESIZING, "Done", {"size": len(response_audio) if response_audio else 0})
            
            duration_ms = (time.time() - start_time) * 1000
            
            return PipelineResult(
                transcript=transcript,
                response_text=response_text,
                response_audio=response_audio,
                duration_ms=duration_ms,
                status=PipelineStatus.SPEAKING
            )
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            return PipelineResult("", "", None, 0, PipelineStatus.ERROR)
    
    async def cleanup(self):
        """Cleanup pipeline resources."""
        logger.info("Cleaning up...")
        if self.stt: self.stt.cleanup()
        if self.llm: await self.llm.cleanup()
        if self.tts: self.tts.cleanup()
        self.initialized = False