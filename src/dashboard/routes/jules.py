# =============================================================================
# jules.py — MC-served jules (voice transcription tool)
#
# Serves the jules page from the MC itself, with save endpoints that use
# per-Presence NC credentials via get_nc_creds(). WebSocket for real-time
# STT connects directly to the pipecat-voice server (cross-origin OK for WS).
# =============================================================================

import os
import re
from src.env import env
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse

from src.dashboard.routes.nextcloud import get_nc_creds
from src.config import get_instance, get_compute_config, resolve_voice_urls

log = logging.getLogger(__name__)

router = APIRouter()


def collapse_consecutive_duplicates(text: str) -> str:
    """B13: the live STT path re-transcribes the GROWING audio buffer every 5s, so the same
    sentence can land many times in a row (run-3: one line ~18×). Collapse CONSECUTIVE
    duplicate segments (paragraphs split on blank lines), comparing normalized
    (case- + whitespace-insensitive). A non-consecutive repeat is left alone — a real
    transcript can legitimately say the same thing again later. Order preserved; a segment
    that merely GREW (the previous is a prefix of this one) keeps the longer version."""
    if not text:
        return text
    parts = re.split(r"\n\s*\n", text)
    out = []
    prev_norm = None
    for p in parts:
        norm = re.sub(r"\s+", " ", p).strip().lower()
        if norm and prev_norm is not None:
            if norm == prev_norm:
                continue                      # exact consecutive duplicate → drop
            if norm.startswith(prev_norm) and len(prev_norm) >= 8:
                out[-1] = p                   # same sentence that grew → keep the longer one
                prev_norm = norm
                continue
        out.append(p)
        if norm:
            prev_norm = norm
    return "\n\n".join(out)

# Default local voice server (a pipecat-voice on this Cove's host). The Admin Presence
# can repoint voice to an external box (e.g. the P620 GPU) via the `compute.voice` setting.
_LOCAL_VOICE_INTERNAL = env("VOICE_INTERNAL_URL", "http://host.docker.internal:8300")


def _voice() -> dict:
    """Resolved voice backend for jules. Thin adapter over the single source of truth
    (config.resolve_voice_urls) so jules.py and the frontend /api/config never diverge.

    Returns {'public': <wss url or ''>, 'internal': <http url or ''>} for back-compat
    with the two call sites below (serve page + transcribe proxy).
    """
    v = resolve_voice_urls()
    return {"public": v["ws"], "internal": v["internal"]}

# NC target folder for jules
JULES_NC_PATH = "AgentSkills/Inbox"


# =============================================================================
# Serve jules page
# =============================================================================

@router.get("/jules", response_class=HTMLResponse)
async def serve_jules(request: Request):
    """Serve the jules page with voice server URL injected."""
    static_dir = Path(__file__).parent.parent / "static"
    jules_path = static_dir / "jules.html"
    if not jules_path.exists():
        return HTMLResponse("<h1>jules not found</h1>", status_code=404)

    html = jules_path.read_text()
    # Resolve the realtime-STT WebSocket base (honors compute.voice). For a no-domain
    # Cove the voice server runs on this host's published voice port (a DIFFERENT port
    # than the app), and the resolver can't know the public host — so fill it in here
    # from the request Host header. Without this the page falls back to the app origin
    # (:8204/ws) and the socket fails. Domain Coves already resolve to wss://voice.{domain}.
    # The voice URL must follow the host you're VIEWING from, so setting a public address
    # never breaks jules on the box. Viewing over localhost/IP/http → use the same-host
    # voice port; viewing on the real https domain → wss://voice.{domain}. (Mirrors the
    # MC's client-side MC.voiceUrl resolver.)
    v = resolve_voice_urls()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]
    xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    is_https = (request.url.scheme == "https") or (xfp == "https")
    is_ip = host.count(".") == 3 and host.replace(".", "").isdigit()
    localish = (not is_https) or host in ("localhost", "127.0.0.1") or is_ip
    ws_url = ""
    if v.get("same_host_port") and localish and host:
        scheme = "wss" if is_https else "ws"
        ws_url = f"{scheme}://{host}:{v['same_host_port']}"
    if not ws_url:
        ws_url = v.get("ws") or ""
    if not ws_url and v.get("same_host_port") and host:           # last-resort same-host
        scheme = "wss" if is_https else "ws"
        ws_url = f"{scheme}://{host}:{v['same_host_port']}"
    html = html.replace("__VOICE_SERVER_URL__", ws_url)
    return HTMLResponse(html)


# =============================================================================
# Save transcript text to NC via WebDAV
# =============================================================================

@router.post("/api/jules/save")
async def jules_save(request: Request):
    """Save transcript text to Nextcloud via WebDAV."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    text = body.get("text", "").strip()
    filename = body.get("filename", "")

    if not text:
        return JSONResponse({"ok": False, "error": "Empty transcript"}, status_code=400)

    # B13: safety net — collapse consecutive duplicate segments before persisting (the live
    # STT chunking could stack the same sentence ~18×). The client also guards live, but this
    # catches anything that slipped through so what lands in NC is clean.
    text = collapse_consecutive_duplicates(text)

    if not filename:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}"

    # Ensure .md extension
    if not filename.endswith(".md"):
        filename = filename + ".md"

    # Add jules header
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = f"jules by Julian — {now} (transcribed from voice)\n\n---\n\n{text}"

    # Get per-Presence NC credentials
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"ok": False, "error": "Nextcloud not configured"}, status_code=500)

    # Save to NC via WebDAV
    ok, error = await _save_to_nc(nc_url, nc_user, nc_pass, filename, content.encode("utf-8"), "text/markdown")
    if ok:
        try:  # auto-process into the backlog (best-effort, never breaks the save)
            from src.dashboard.routes.jules_process import schedule_auto_process
            schedule_auto_process(nc_url, nc_user, nc_pass)
        except Exception:
            pass
        return JSONResponse({"ok": True, "path": f"{JULES_NC_PATH}/{filename}"})
    return JSONResponse({"ok": False, "error": error}, status_code=500)


# =============================================================================
# Save audio to NC via WebDAV
# =============================================================================

@router.post("/api/jules/save-audio")
async def jules_save_audio(
    request: Request,
    audio: UploadFile = File(...),
    filename: str = Form(""),
):
    """Save audio file to Nextcloud via WebDAV."""
    if not filename:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}.webm"

    # Ensure extension
    if not filename.endswith(".webm"):
        filename = filename + ".webm"

    content = await audio.read()
    if not content:
        return JSONResponse({"ok": False, "error": "Empty audio"}, status_code=400)

    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"ok": False, "error": "Nextcloud not configured"}, status_code=500)

    content_type = audio.content_type or "audio/webm"
    ok, error = await _save_to_nc(nc_url, nc_user, nc_pass, filename, content, content_type)
    if ok:
        return JSONResponse({"ok": True, "path": f"{JULES_NC_PATH}/{filename}"})
    return JSONResponse({"ok": False, "error": error}, status_code=500)


# =============================================================================
# Transcribe + save — proxy audio to pipecat-voice, then save to NC
# =============================================================================

@router.post("/api/jules/transcribe-and-save")
async def jules_transcribe_and_save(
    request: Request,
    audio: UploadFile = File(...),
    filename: str = Form(""),
):
    """Proxy audio to pipecat-voice for Whisper transcription, then save to NC."""
    if not filename:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}"

    audio_content = await audio.read()
    if not audio_content:
        return JSONResponse({"ok": False, "error": "Empty audio"}, status_code=400)

    # Get NC creds first
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"ok": False, "error": "Nextcloud not configured"}, status_code=500)

    # Resolve the voice backend (honors compute.voice — local pipecat or an external GPU box).
    voice_internal = _voice()["internal"]
    if not voice_internal:
        return JSONResponse({"ok": False, "error": "Voice transcription is disabled for this Cove"}, status_code=503)

    # Proxy to pipecat-voice for transcription
    # Send WITHOUT presence_id so pipecat-voice doesn't try to save to NC
    # (it'll fall back to filesystem, which is fine as a backup)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            files = {"audio": ("recording.webm", audio_content, audio.content_type or "audio/webm")}
            data = {"filename": filename}
            resp = await client.post(
                f"{voice_internal}/api/transcribe-and-save",
                files=files,
                data=data,
            )
            voice_result = resp.json()
    except Exception as e:
        log.error("Pipecat-voice transcription proxy failed: %s", e)
        return JSONResponse({"ok": False, "error": f"Transcription failed: {e}"}, status_code=502)

    if not voice_result.get("ok"):
        return JSONResponse({"ok": False, "error": voice_result.get("error", "Transcription failed")}, status_code=502)

    # Extract transcript from voice result (the voice endpoint returns it as `text`;
    # accept `transcript` too for forward-compat).
    transcript = (voice_result.get("transcript") or voice_result.get("text") or "").strip()
    if not transcript:
        return JSONResponse({"ok": False, "error": "Empty transcription"}, status_code=502)

    # Save transcript to NC
    md_filename = filename if filename.endswith(".md") else filename + ".md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md_content = f"jules by Julian — {now} (transcribed from voice)\n\n---\n\n{transcript}"

    ok_md, err_md = await _save_to_nc(nc_url, nc_user, nc_pass, md_filename, md_content.encode("utf-8"), "text/markdown")

    # Save audio to NC
    webm_filename = filename.replace(".md", "") + ".webm"
    ok_audio, err_audio = await _save_to_nc(nc_url, nc_user, nc_pass, webm_filename, audio_content, "audio/webm")

    if ok_md:
        try:  # auto-process into the backlog (best-effort, never breaks the save)
            from src.dashboard.routes.jules_process import schedule_auto_process
            schedule_auto_process(nc_url, nc_user, nc_pass)
        except Exception:
            pass
        return JSONResponse({
            "ok": True,
            "transcript": transcript,
            "path": f"{JULES_NC_PATH}/{md_filename}",
            "audio_saved": ok_audio,
        })
    return JSONResponse({"ok": False, "error": err_md or "Save failed"}, status_code=500)


# =============================================================================
# WebDAV helper
# =============================================================================

async def _save_to_nc(nc_url: str, nc_user: str, nc_pass: str,
                      filename: str, content: bytes, content_type: str) -> tuple:
    """Save a file to NC via WebDAV. Returns (success: bool, error: str|None)."""
    webdav_url = (
        f"{nc_url}/remote.php/dav/files/{nc_user}"
        f"/{JULES_NC_PATH}/{quote(filename, safe='')}"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                webdav_url,
                auth=(nc_user, nc_pass),
                content=content,
                headers={"Content-Type": content_type},
            )
            if resp.status_code in (200, 201, 204):
                log.info("Jules saved to NC: %s/%s", JULES_NC_PATH, filename)
                return True, None
            log.error("Jules NC save failed: HTTP %s — %s", resp.status_code, resp.text[:200])
            return False, f"Nextcloud HTTP {resp.status_code}"
    except Exception as e:
        log.error("Jules NC save error: %s", e)
        return False, str(e)
