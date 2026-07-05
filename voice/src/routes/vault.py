"""Vault inbox routes — save transcripts and audio to filesystem or Nextcloud."""
import asyncio
import os
import tempfile
import logging
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.voice_common import (
    VAULT_INBOX, NEXTCLOUD_URL,
    _save_to_nextcloud_vault, _transcribe_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Vault Inbox save (filesystem, synced via Syncthing) ──────────

@router.post("/api/save-to-vault")
async def save_to_vault(request: Request):
    """Save transcript text to vault Inbox folder.

    Routes to either:
    - Filesystem (single-Presence / operator mode) — synced via Syncthing
    - Nextcloud WebDAV (multi-Presence mode) — each Presence's vault

    Body params:
        text: Transcript text
        filename: Optional filename
        operator: Operator/Presence name for header
        presence_id: Optional Nextcloud username — triggers per-Presence routing
    """
    body = await request.json()
    text = body.get("text", "").strip()
    filename = body.get("filename", "")
    operator = body.get("operator", "Chords")
    presence_id = body.get("presence_id", "")

    if not text:
        return JSONResponse({"error": "No transcript text"}, status_code=400)

    if not filename:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}.md"

    # Ensure .md extension
    if not filename.endswith(".md"):
        filename += ".md"

    # Prepend jules header for identification
    header_ts = datetime.now().strftime("%Y-%m-%d %-I:%M %p EDT")
    header = f"jules by Julian — {operator} — {header_ts} (transcribed from voice)\n\n"
    content = header + text

    # Per-Presence routing via Nextcloud WebDAV
    if presence_id and NEXTCLOUD_URL:
        result = await _save_to_nextcloud_vault(
            nc_user=presence_id,
            filename=filename,
            content=content.encode("utf-8"),
            content_type="text/markdown",
        )
        if "error" in result:
            return JSONResponse(result, status_code=500)
        result["location"] = "nextcloud"
        return result

    # Filesystem fallback (single-Presence / operator mode)
    if not os.path.isdir(VAULT_INBOX):
        return JSONResponse(
            {"error": f"Vault inbox not mounted at {VAULT_INBOX}"},
            status_code=503,
        )

    filepath = os.path.join(VAULT_INBOX, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Saved transcript to vault inbox: {filename}")
        return {"ok": True, "path": f"Inbox/{filename}", "location": "vault"}
    except Exception as e:
        logger.error(f"Vault save error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Save audio file to vault inbox ───────────────────────────────

@router.post("/api/save-audio")
async def save_audio(
    audio: UploadFile = File(...),
    filename: str = Form(""),
    presence_id: str = Form(""),
):
    """Save an audio file to vault inbox. Used alongside text save for live recordings."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        return JSONResponse({"error": "Empty audio file"}, status_code=400)

    if not filename:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}.webm"

    # Per-Presence routing via Nextcloud WebDAV
    if presence_id and NEXTCLOUD_URL:
        result = await _save_to_nextcloud_vault(
            nc_user=presence_id,
            filename=filename,
            content=audio_bytes,
            content_type="audio/webm",
        )
        if "error" in result:
            return JSONResponse(result, status_code=500)
        return result

    # Filesystem fallback
    if not os.path.isdir(VAULT_INBOX):
        return JSONResponse({"error": "Vault inbox not mounted"}, status_code=503)

    filepath = os.path.join(VAULT_INBOX, filename)
    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    logger.info(f"Saved audio to vault inbox: {filename} ({len(audio_bytes)} bytes)")
    return {"ok": True, "path": f"Inbox/{filename}"}


# ── Transcribe and save (for queued offline recordings) ──────────

@router.post("/api/transcribe-and-save")
async def transcribe_and_save(
    request: Request,
    audio: UploadFile = File(...),
    filename: str = Form(""),
    operator: str = Form("Chords"),
    presence_id: str = Form(""),
):
    """Accept an audio file, transcribe with Whisper, save text to vault inbox.
    Used by Jules queue system for offline recordings.

    If presence_id is provided, routes to that Presence's Nextcloud vault.
    Otherwise saves to the filesystem vault inbox (operator mode).
    """
    pipeline = request.app.state.pipeline if hasattr(request.app.state, 'pipeline') else None
    # Check for STT directly — don't require full pipeline (TTS may have failed)
    stt = pipeline.stt if pipeline and hasattr(pipeline, 'stt') and pipeline.stt else None
    if not stt:
        return JSONResponse({"error": "STT not available"}, status_code=503)

    # Save uploaded audio to temp file (Whisper needs a file path for non-PCM formats)
    audio_bytes = await audio.read()
    if not audio_bytes:
        return JSONResponse({"error": "Empty audio file"}, status_code=400)

    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Transcribe (faster-whisper handles webm/opus via ffmpeg)
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None, lambda: _transcribe_file(stt, tmp_path)
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not transcript:
        return JSONResponse({"error": "No speech detected"}, status_code=200)

    # Generate base filename
    if not filename:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"jules-{ts}"
    # Strip extension to get base name
    base = filename.rsplit(".", 1)[0] if "." in filename else filename

    # Prepend jules header for identification
    header_ts = datetime.now().strftime("%Y-%m-%d %-I:%M %p EDT")
    header = f"jules by Julian — {operator} — {header_ts} (transcribed from voice)\n\n"
    content = header + transcript
    audio_ext = suffix if suffix.startswith(".") else f".{suffix}"

    # Per-Presence routing via Nextcloud WebDAV
    if presence_id and NEXTCLOUD_URL:
        md_result = await _save_to_nextcloud_vault(
            nc_user=presence_id,
            filename=f"{base}.md",
            content=content.encode("utf-8"),
            content_type="text/markdown",
        )
        audio_result = await _save_to_nextcloud_vault(
            nc_user=presence_id,
            filename=f"{base}{audio_ext}",
            content=audio_bytes,
            content_type="audio/webm",
        )
        logger.info(f"Transcribed and saved to Nextcloud ({presence_id}): {base}")
        return {
            "ok": md_result.get("ok", False),
            "path": md_result.get("path", ""),
            "audio_path": audio_result.get("path", ""),
            "text": transcript,
            "location": "nextcloud",
        }

    # Filesystem save is BEST-EFFORT. The MC's jules proxy calls this WITHOUT a
    # presence_id and saves to Nextcloud itself — it only needs the transcript back —
    # so a missing /vault-inbox mount must NOT fail the request (that was the
    # "Vault inbox not mounted" bug). Save the file when the mount exists; always
    # return the transcript. (Return under both `text` and `transcript` keys so
    # callers reading either work.)
    saved_path = ""
    if os.path.isdir(VAULT_INBOX):
        try:
            md_path = os.path.join(VAULT_INBOX, f"{base}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
            audio_path = os.path.join(VAULT_INBOX, f"{base}{audio_ext}")
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
            saved_path = f"Inbox/{base}.md"
            logger.info(f"Transcribed + saved to vault: {base} ({len(audio_bytes)} bytes -> {len(transcript)} chars)")
        except Exception as e:
            logger.warning(f"Vault file save failed (returning transcript anyway): {e}")
    else:
        logger.info(f"Transcribed (no /vault-inbox mount; returning transcript only): {base}")
    return {"ok": True, "path": saved_path, "text": transcript, "transcript": transcript}
