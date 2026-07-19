"""
Piper TTS Transport for Pipecat

Uses piper-tts library for local text-to-speech synthesis.
Supports multiple voices via PiperVoiceManager with per-agent mapping.
Voice directory: /voices/piper/ (mounted from host)
Config: /voices/config.json (agent → voice mapping)
"""

import json
import logging
import io
import os
import threading
import wave
import numpy as np
from typing import Optional, Tuple, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# Piper/onnx runtime is not safe under concurrent synthesize from one process.
# Chat TTS used to fire every sentence in parallel; middle chunks timed out and
# the browser skipped them. Serialize synthesis process-wide (still fast on CPU).
_PIPER_SYNTH_LOCK = threading.Lock()

# Voice directory inside container (mounted from host on a GPU/founder box)
VOICES_DIR = Path("/voices/piper")
VOICES_CONFIG = Path("/voices/config.json")
# Writable, persisted cache for self-downloaded voices (open-source clean install:
# no host mount, so Piper fetches its voice the same way Whisper fetches its STT model).
# Persisted via the voice_cache volume the provisioner mounts at /root/.cache.
VOICE_CACHE_DIR = Path(os.getenv("PIPER_CACHE_DIR", "/root/.cache/piper-voices"))


class PiperTTSTransport:
    """
    Text-to-Speech transport using Piper.
    
    Features:
    - Local synthesis (no cloud API)
    - CPU-only operation
    - WAV audio output
    """
    
    FALLBACK_VOICE = "en_US-lessac-medium"

    def __init__(
        self,
        voice: str = None,
        sample_rate: int = 22050,
        length_scale: float = 1.0,
        noise_scale: float = 0.667,
        noise_w: float = 0.8
    ):
        # Read default voice from config.json if no voice specified
        if voice is None:
            voice = self._read_config_default()
        self.voice = voice
        self.sample_rate = sample_rate
        self.length_scale = length_scale
        self.noise_scale = noise_scale
        self.noise_w = noise_w
        self.model = None
        self.config = None
        self.speaker_id = None
        self.is_initialized = False

    @staticmethod
    def _read_config_default() -> str:
        """Read default voice from /voices/config.json, fall back to hardcoded."""
        try:
            if VOICES_CONFIG.exists():
                with open(VOICES_CONFIG) as f:
                    cfg = json.load(f)
                default = cfg.get("default", PiperTTSTransport.FALLBACK_VOICE)
                logger.info(f"Voice config: using default '{default}'")
                return default
        except Exception as e:
            logger.warning(f"Could not read voice config: {e}")
        return PiperTTSTransport.FALLBACK_VOICE

    def initialize(self) -> bool:
        """
        Initialize the Piper TTS model.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            from piper import PiperVoice
            
            logger.info(f"Loading Piper voice: {self.voice}")

            # Voice path resolution — mounted dir (founder/GPU box), system dir, cache, cwd.
            model_path, config_path = self._resolve_voice_paths()

            # Open-source clean install: no host mount, so self-provision the voice from
            # rhasspy/piper-voices (same pattern as Whisper self-downloading its STT model).
            if model_path is None:
                model_path, config_path = self._download_voice()

            if model_path is None or not model_path.exists():
                logger.error(f"Voice model unavailable after download attempt: {self.voice}")
                return False

            # Load the voice
            self.model = PiperVoice.load(str(model_path), config_path=str(config_path) if (config_path and config_path.exists()) else None)
            self.is_initialized = True
            
            logger.info(f"Piper TTS initialized with voice: {self.voice}")
            logger.info(f"Sample rate: {self.sample_rate} Hz, Device: CPU")
            return True
            
        except ImportError:
            logger.error("piper-tts not installed. Run: pip install piper-tts")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Piper TTS: {e}")
            return False
    
    def _resolve_voice_paths(self):
        """Return (model_path, config_path) if the .onnx is already on disk, else (None, None).
        Searches the host mount, the system dir, the self-download cache, and cwd."""
        for d in (VOICES_DIR, Path("/usr/share/piper-voices"), VOICE_CACHE_DIR, Path(".")):
            mp = d / f"{self.voice}.onnx"
            if mp.exists():
                cp = d / f"{self.voice}.onnx.json"
                return mp, (cp if cp.exists() else None)
        return None, None

    def _download_voice(self):
        """Fetch the Piper voice from rhasspy/piper-voices into the persisted cache.
        Voice id format '{locale}-{name}-{quality}' (e.g. en_US-lessac-medium) maps to
        the repo path '{lang}/{locale}/{name}/{quality}/'. Returns (model_path, config_path)
        or (None, None) on failure (TTS then degrades off; STT/dictation is unaffected)."""
        try:
            import urllib.request

            parts = self.voice.split("-")
            if len(parts) < 3:
                logger.error(f"Unrecognized Piper voice id (need locale-name-quality): {self.voice}")
                return None, None
            locale, name, quality = parts[0], parts[1], parts[2]
            lang = locale.split("_")[0]
            base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{lang}/{locale}/{name}/{quality}"

            VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            model_path = VOICE_CACHE_DIR / f"{self.voice}.onnx"
            config_path = VOICE_CACHE_DIR / f"{self.voice}.onnx.json"

            for path, url in ((model_path, f"{base_url}/{self.voice}.onnx"),
                              (config_path, f"{base_url}/{self.voice}.onnx.json")):
                if not path.exists():
                    logger.info(f"Downloading Piper voice asset: {url}")
                    urllib.request.urlretrieve(url, str(path))
                    logger.info(f"Downloaded: {path.name}")
            return model_path, config_path
        except Exception as e:
            logger.error(f"Failed to download Piper voice {self.voice}: {e}")
            return None, None
    
    def synthesize(
        self,
        text: str,
        return_wav: bool = True
    ) -> Optional[bytes]:
        """
        Synthesize text to audio.
        
        Args:
            text: Text to synthesize
            return_wav: If True, return WAV container; if False, raw PCM
            
        Returns:
            Audio bytes (WAV format by default) or None on error
        """
        if not self.is_initialized:
            logger.error("Piper TTS not initialized")
            return None
        
        try:
            # Clean text
            text = text.strip()
            if not text:
                return None
            
            logger.debug(f"Synthesizing: '{text[:50]}...'")

            with _PIPER_SYNTH_LOCK:
                # Synthesize via piper's chunk-based API
                raw_chunks = []
                for chunk in self.model.synthesize(text):
                    raw_chunks.append(chunk.audio_int16_bytes)

                raw_audio = b''.join(raw_chunks)

                if not raw_audio:
                    logger.warning("No audio generated")
                    return None

                if return_wav:
                    # Wrap raw PCM in WAV container
                    wav_io = io.BytesIO()
                    with wave.open(wav_io, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(self.sample_rate)
                        wf.writeframes(raw_audio)
                    return wav_io.getvalue()

                return raw_audio
                
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            return None
    
    def _pcm_to_wav(self, pcm_data: bytes) -> bytes:
        """Convert raw PCM to WAV format."""
        wav_buffer = io.BytesIO()
        
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm_data)
        
        wav_buffer.seek(0)
        return wav_buffer.read()
    
    def synthesize_stream(self, text: str):
        """
        Synthesize text and yield audio chunks for streaming.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio chunks as they're generated
        """
        if not self.is_initialized:
            logger.error("Piper TTS not initialized")
            return
        
        try:
            text = text.strip()
            if not text:
                return
            
            for audio_bytes in self.model.synthesize(
                text,
                speaker_id=self.speaker_id,
                length_scale=self.length_scale,
                noise_scale=self.noise_scale,
                noise_w=self.noise_w
            ):
                yield audio_bytes
                
        except Exception as e:
            logger.error(f"Streaming synthesis error: {e}")
    
    def cleanup(self):
        """Release model resources."""
        self.model = None
        self.is_initialized = False
        logger.info("Piper TTS cleaned up")


# =============================================================================
# Voice Manager — loads and caches per-agent voices from config.json
# =============================================================================

class PiperVoiceManager:
    """
    Manages multiple Piper voices with agent-to-voice mapping.

    Reads /voices/config.json for mapping:
      { "default": "en_US-lessac-medium", "agents": { "stuart": "...", "atlas": "..." } }

    Voices are loaded on first request and cached in memory.
    """

    def __init__(self):
        self._voices: Dict[str, PiperTTSTransport] = {}
        self._config: Dict = {"default": "en_US-lessac-medium", "agents": {}}
        self._load_config()

    def _load_config(self):
        """Load agent-to-voice mapping from config.json."""
        if VOICES_CONFIG.exists():
            try:
                self._config = json.loads(VOICES_CONFIG.read_text())
                logger.info(f"Voice config loaded: default={self._config.get('default')}, "
                            f"agents={list(self._config.get('agents', {}).keys())}")
            except Exception as e:
                logger.error(f"Failed to load voice config: {e}")
        else:
            logger.warning(f"No voice config at {VOICES_CONFIG}, using defaults")

    def reload_config(self):
        """Reload config without clearing cached voices."""
        self._load_config()

    def get_voice_name(self, agent: Optional[str] = None) -> str:
        """Resolve agent name to Piper voice name."""
        if agent:
            # Normalize: strip -cove suffix, lowercase
            agent_key = agent.lower().replace("-cove", "").replace("_cove", "").strip()
            voice = self._config.get("agents", {}).get(agent_key)
            if voice:
                return voice
        return self._config.get("default", "en_US-lessac-medium")

    def get_transport(self, agent: Optional[str] = None) -> Optional[PiperTTSTransport]:
        """Get a cached TTS transport for the given agent (or default)."""
        voice_name = self.get_voice_name(agent)

        if voice_name in self._voices:
            transport = self._voices[voice_name]
            if transport.is_initialized:
                return transport

        # Load on demand
        logger.info(f"Loading voice '{voice_name}' for agent '{agent or 'default'}'")
        transport = PiperTTSTransport(voice=voice_name)
        if transport.initialize():
            self._voices[voice_name] = transport
            return transport
        else:
            logger.error(f"Failed to load voice: {voice_name}")
            # Fall back to default if this wasn't already the default
            default_name = self._config.get("default", "en_US-lessac-medium")
            if voice_name != default_name and default_name in self._voices:
                logger.info(f"Falling back to default voice: {default_name}")
                return self._voices[default_name]
            return None

    def get_transport_by_voice(self, voice_name: str) -> Optional[PiperTTSTransport]:
        """Get TTS transport for a specific voice name (bypasses agent mapping).

        Used by the Settings UI voice override — the user picks a voice directly
        instead of relying on the agent-to-voice config mapping.
        """
        if voice_name in self._voices:
            transport = self._voices[voice_name]
            if transport.is_initialized:
                return transport

        # Load on demand
        logger.info(f"Loading voice '{voice_name}' (direct)")
        transport = PiperTTSTransport(voice=voice_name)
        if transport.initialize():
            self._voices[voice_name] = transport
            return transport
        else:
            logger.error(f"Failed to load voice (direct): {voice_name}")
            return None

    def list_voices(self) -> Dict:
        """Return current config and loaded voice status."""
        return {
            "config": self._config,
            "loaded": {name: t.is_initialized for name, t in self._voices.items()},
            "available_files": [f.stem.replace(".onnx", "") for f in VOICES_DIR.glob("*.onnx")] if VOICES_DIR.exists() else []
        }

    def cleanup(self):
        """Release all loaded voices."""
        for transport in self._voices.values():
            transport.cleanup()
        self._voices.clear()


# Global voice manager instance
_voice_manager = None

def get_voice_manager() -> PiperVoiceManager:
    """Get or create the global voice manager."""
    global _voice_manager
    if _voice_manager is None:
        _voice_manager = PiperVoiceManager()
    return _voice_manager


# Legacy compatibility — get default transport
async def get_tts_transport():
    """Get the default TTS transport (legacy compatibility)."""
    mgr = get_voice_manager()
    return mgr.get_transport()