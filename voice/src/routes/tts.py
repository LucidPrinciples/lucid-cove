"""TTS routes — text to speech via Piper."""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/tts")
async def text_to_speech(request: Request):
    """Convert text to speech audio via Piper TTS. Returns WAV.

    Body: { "text": "...", "agent": "stuart" }
    Agent param selects per-agent voice from /voices/config.json.
    """
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        agent = body.get("agent", "").strip() or None
        voice_override = body.get("voice", "").strip() or None
        if not text:
            return JSONResponse({"error": "No text provided"}, status_code=400)

        # Cap length to avoid huge synthesis jobs
        if len(text) > 5000:
            text = text[:5000]

        # Use voice manager for per-agent voice selection
        # voice_override (from Settings UI) bypasses agent-to-voice mapping
        from src.transports.piper_tts import get_voice_manager
        mgr = get_voice_manager()
        if voice_override:
            tts = mgr.get_transport_by_voice(voice_override)
        else:
            tts = mgr.get_transport(agent)

        if tts is None or not tts.is_initialized:
            return JSONResponse({"error": "TTS not available"}, status_code=503)

        # Synthesize in thread pool (CPU-bound)
        loop = asyncio.get_event_loop()
        wav_bytes = await loop.run_in_executor(None, tts.synthesize, text)

        if not wav_bytes:
            return JSONResponse({"error": "Synthesis produced no audio"}, status_code=500)

        voice_name = mgr.get_voice_name(agent)
        logger.info(f"TTS [{voice_name}]: {len(text)} chars -> {len(wav_bytes)} bytes WAV")
        return Response(content=wav_bytes, media_type="audio/wav")

    except Exception as e:
        logger.error(f"TTS endpoint error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tts/voices")
async def list_voices():
    """List available voices and current agent mapping."""
    from src.transports.piper_tts import get_voice_manager
    return get_voice_manager().list_voices()


@router.post("/api/tts/reload")
async def reload_voice_config():
    """Reload voice config without restarting server."""
    from src.transports.piper_tts import get_voice_manager
    get_voice_manager().reload_config()
    return {"ok": True, "config": get_voice_manager()._config}
