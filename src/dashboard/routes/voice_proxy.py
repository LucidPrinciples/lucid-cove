"""Same-origin voice proxy — browser TTS without reaching voice.{domain} directly.

Chat TTS used to fetch https://voice.{domain}/api/tts from the browser. On mesh,
LAN, or a laptop that can't resolve/route that host, fetch hung with no timeout
and the UI sat on Stop. Jules already proxies STT through the app; TTS needs the
same pattern so speak rides the Cove origin the user already has open.

Browser → POST /api/voice/tts → VOICE_INTERNAL_URL /api/tts (Piper).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()
log = logging.getLogger(__name__)

# Hard cap on upstream synthesis — client also times out; this bounds server work.
_TTS_UPSTREAM_TIMEOUT = 20.0
_MAX_TEXT_CHARS = 5000


def _voice_internal() -> str:
    """Internal pipecat base URL (compose service name or host). Empty if voice off."""
    try:
        from src.config import resolve_voice_urls
        return (resolve_voice_urls().get("internal") or "").rstrip("/")
    except Exception:
        from src.env import env
        return (env("VOICE_INTERNAL_URL", "") or "").rstrip("/")


@router.post("/api/voice/tts")
async def proxy_tts(request: Request):
    """Proxy text-to-speech to the Cove voice container. Returns audio/wav.

    Body: { "text": "...", "agent": "cyrus", "voice": "" }
    Same contract as pipecat /api/tts so the browser can swap URL with no payload change.
    """
    internal = _voice_internal()
    if not internal:
        return JSONResponse({"error": "Voice is disabled for this Cove"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "No text provided"}, status_code=400)
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]

    agent = (body.get("agent") or "").strip() or None
    voice = (body.get("voice") or "").strip() or None
    payload = {"text": text}
    if agent:
        payload["agent"] = agent
    if voice:
        payload["voice"] = voice

    try:
        async with httpx.AsyncClient(timeout=_TTS_UPSTREAM_TIMEOUT) as client:
            resp = await client.post(f"{internal}/api/tts", json=payload)
    except httpx.TimeoutException:
        log.warning("voice TTS proxy timed out after %.0fs (%s)", _TTS_UPSTREAM_TIMEOUT, internal)
        return JSONResponse({"error": "Voice timed out"}, status_code=504)
    except Exception as e:
        log.error("voice TTS proxy failed: %s", e)
        return JSONResponse({"error": f"Voice unavailable: {e}"}, status_code=502)

    if resp.status_code != 200:
        # Pass through upstream JSON error when present; else generic.
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"error": f"TTS upstream HTTP {resp.status_code}"}
        return JSONResponse(err_body, status_code=resp.status_code)

    media = resp.headers.get("content-type") or "audio/wav"
    return Response(content=resp.content, media_type=media)


@router.get("/api/voice/tts/voices")
async def proxy_tts_voices():
    """List Piper voices via the same-origin proxy (Settings voice picker)."""
    internal = _voice_internal()
    if not internal:
        return JSONResponse({"error": "Voice is disabled for this Cove"}, status_code=503)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{internal}/api/tts/voices")
        if resp.status_code != 200:
            return JSONResponse({"error": f"Upstream HTTP {resp.status_code}"}, status_code=resp.status_code)
        return Response(content=resp.content, media_type="application/json")
    except Exception as e:
        log.error("voice voices proxy failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=502)
