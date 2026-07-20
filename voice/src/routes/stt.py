"""STT routes — batch video transcription with Qwen3-ASR."""
import asyncio
import json
import logging
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.voice_common import (
    VIDEO_MOUNT, NC_VIDEO_PATH, NC_HTML_ROOT,
    NEXTCLOUD_URL, NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD,
    _nc_scan, _get_qwen_asr,
    NCSession, resolve_video_source, publish_video_output,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _gpu_auth_ok(request: Request) -> bool:
    """Authorize an incoming GPU transcription job (cross-Cove GPU share).

    OPEN by default (legacy behavior) unless this pipecat is configured to gate — i.e.
    PIPECAT_INTERNAL_SECRET or GPU_GRANT_VERIFY_URL is set. Once gating is on, accept EITHER:
      - the Cove's OWN app, via X-Pipecat-Secret == PIPECAT_INTERNAL_SECRET (local jobs), or
      - a renter's grant token X-Cove-GPU-Token, verified against the provider Cove app's
        /api/gpu/verify (GPU_GRANT_VERIFY_URL).
    Anything else → denied. Headers only; no body read."""
    secret = (os.getenv("PIPECAT_INTERNAL_SECRET") or "").strip()
    verify_url = (os.getenv("GPU_GRANT_VERIFY_URL") or "").strip().rstrip("/")
    if not secret and not verify_url:
        return True  # gating not configured → legacy open behavior (zero regression)
    if secret and request.headers.get("X-Pipecat-Secret") == secret:
        return True
    token = (request.headers.get("X-Cove-GPU-Token") or "").strip()
    if token and verify_url:
        try:
            import httpx
            hdrs = {"X-Pipecat-Secret": secret} if secret else {}
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{verify_url}/api/gpu/verify",
                                 json={"token": token}, headers=hdrs)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                return True
            logger.warning(f"GPU grant verify rejected token ({r.status_code})")
        except Exception as e:
            logger.warning(f"GPU grant verify error: {e}")
    return False


def _resolve_asr_engine(body: dict):
    """Pick the batch ASR engine: 'qwen' (GPU, default) | 'cloud' (BYOK API) |
    'whisper'/'local' (faster-whisper, CPU-OK). cove-core passes `engine` from
    the Cove's compute.video_asr setting; falls back to the ASR_ENGINE env."""
    engine = (body.get("engine") or os.getenv("ASR_ENGINE") or "qwen").lower()
    if engine == "cloud":
        from src.transports.cloud_asr import get_cloud_asr
        # asr_key = the Cove's saved pipeline key, forwarded per-job (env fallback inside).
        return get_cloud_asr(body.get("asr_provider"), api_key=body.get("asr_key"))
    if engine in ("whisper", "local", "faster-whisper"):
        from src.transports.whisper_stt import get_whisper_file_asr
        return get_whisper_file_asr(body.get("asr_model"))
    return _get_qwen_asr()


@router.post("/api/stt/video")
async def transcribe_video(request: Request):
    """Batch transcribe a video/audio file with Qwen3-ASR-1.7B.

    Loads model on-demand, transcribes with word-level timestamps,
    writes transcript to transcripts/ folder, then unloads GPU.

    Body: { "file_path": "/video/inbox/IMG_7129.MOV", "timestamps": true }
    Or just: { "filename": "IMG_7129.MOV" } (looks in VIDEO_MOUNT/inbox/)

    Pipeline flow (all file ops via NC WebDAV — replicable pattern):
    1. Moves video from inbox/ → processing/ via NC MOVE
    2. Transcribes with Qwen3-ASR + ForcedAligner (reads from local mount)
    3. Writes JSON + TXT to transcripts/ via NC PUT
    4. Video stays in processing/ for next pipeline step (shorts)

    Returns: { "text": "...", "segments": [...], "transcript_path": "...", ... }
    """
    if not await _gpu_auth_ok(request):
        return JSONResponse(
            {"error": "GPU access denied — missing or invalid credential"},
            status_code=401,
        )
    body = await request.json()
    file_path = body.get("file_path", "").strip()
    filename = body.get("filename", "").strip()
    use_timestamps = body.get("timestamps", True)

    # Per-presence NC session (cove-core injects X-NC-* headers); None = local mount.
    nc = NCSession.from_request(request, body)

    # Whether the inbox→processing/ copy actually landed. The NC (WebDAV) path can
    # report False WITHOUT raising (a big PUT that times out); the local-mount path
    # raises on failure. Default True; the NC branch below sets it honestly.
    processing_copy_ok = True

    if nc is not None:
        # No local mount — pull the source from the presence's NC into scratch.
        # Derive the bare filename from either filename or file_path.
        nc_name = filename or os.path.basename(file_path)
        if not nc_name:
            return JSONResponse({"error": "No file_path or filename provided"}, status_code=400)
        video_name = os.path.basename(nc_name)
        stem = os.path.splitext(video_name)[0]
        # Source lives in inbox/ (or processing/raw) on the presence's NC.
        processing_path = await resolve_video_source(video_name, nc)
        if not processing_path:
            return JSONResponse({"error": f"File not found: {video_name}"}, status_code=404)
        # Step 1: Prefer a true WebDAV MOVE inbox→processing (one object, no copy+delete).
        # Fall back to copy only when MOVE can't run (source already outside inbox/, or
        # MOVE rejected). Never DELETE the inbox original after a successful copy —
        # operator policy 2026-07-20: move or leave; dual copies are cleaned by lifecycle.
        try:
            moved = False
            src_norm = processing_path.replace(os.sep, "/")
            if "/inbox/" in src_norm:
                moved = bool(await nc.move(f"inbox/{video_name}", f"processing/{video_name}"))
                if moved:
                    logger.info(f"WebDAV MOVE inbox/{video_name} → processing/")
                    # Scratch still holds the bytes under inbox/; also place under
                    # processing/ so later resolve hits local without re-download.
                    try:
                        import shutil
                        scratch_proc = os.path.join(
                            os.path.dirname(os.path.dirname(processing_path)),
                            "processing",
                            video_name,
                        )
                        # processing_path is .../user/inbox/name or .../user/sub/name
                        # Prefer sibling swap: replace /inbox/ with /processing/ in path.
                        alt = src_norm.replace("/inbox/", "/processing/")
                        if alt != src_norm:
                            os.makedirs(os.path.dirname(alt), exist_ok=True)
                            if os.path.isfile(processing_path) and not os.path.isfile(alt):
                                shutil.copy2(processing_path, alt)
                                processing_path = alt
                    except Exception as scratch_err:
                        logger.warning(f"scratch realign after MOVE: {scratch_err}")
            if not moved:
                # Copy up (large PUT). Keep inbox original — no delete.
                processing_copy_ok = bool(await publish_video_output(
                    processing_path, f"processing/{video_name}", nc, "video/mp4"))
                if processing_copy_ok:
                    logger.info(
                        f"Copied {video_name} to processing/ (inbox original kept — no delete)"
                    )
                else:
                    processing_copy_ok = False
                    logger.error(
                        f"processing/ copy of {video_name} did NOT land "
                        f"(publish_video_output returned False — the PUT likely timed out). "
                        f"Continuing with transcription; keeping the inbox original.")
            else:
                processing_copy_ok = True
        except Exception as move_err:
            return JSONResponse(
                {"error": f"Failed to move {video_name} to processing/: {move_err}"},
                status_code=500,
            )
    else:
        # Resolve file path (local mount for reading)
        if not file_path and filename:
            file_path = os.path.join(VIDEO_MOUNT, "inbox", filename)
        if not file_path:
            return JSONResponse({"error": "No file_path or filename provided"}, status_code=400)
        if not os.path.isfile(file_path):
            return JSONResponse({"error": f"File not found: {file_path}"}, status_code=404)

        video_name = os.path.basename(file_path)
        stem = os.path.splitext(video_name)[0]

        # Step 1: Move to processing/ via filesystem (direct mount)
        import shutil
        processing_dir = os.path.join(VIDEO_MOUNT, "processing")
        os.makedirs(processing_dir, exist_ok=True)
        processing_path = os.path.join(processing_dir, video_name)
        try:
            shutil.move(file_path, processing_path)
            _nc_scan("AgentSkills/Content/video")
            logger.info(f"Moved {video_name} to processing/")
        except Exception as move_err:
            return JSONResponse(
                {"error": f"Failed to move {video_name} to processing/: {move_err}"},
                status_code=500,
            )

    asr = _resolve_asr_engine(body)

    try:
        # Step 2: Transcribe (reads from local mount — fast)
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: asr.transcribe(
                file_path=processing_path,
                use_timestamps=use_timestamps,
                work_dir="/tmp",
            ),
        )

        # Step 3: Write transcript files (NC WebDAV per-presence, or direct mount)
        import json as json_mod
        import shutil

        # Build content first (identical for both paths).
        json_content = json_mod.dumps(transcript, indent=2, ensure_ascii=False)

        # TXT — human-readable for review
        txt_lines = []
        txt_lines.append(f"# Transcript: {stem}")
        txt_lines.append(f"# Model: {transcript.get('model', 'Qwen3-ASR-1.7B')}")
        duration = transcript.get("audio_duration_seconds")
        if duration:
            mins = int(duration // 60)
            secs = int(duration % 60)
            txt_lines.append(f"# Duration: {mins}:{secs:02d}")
        txt_lines.append(f"# Language: {transcript.get('language', 'unknown')}")
        txt_lines.append("")

        if "segments" in transcript:
            for seg in transcript["segments"]:
                start = seg.get("start", "?")
                end = seg.get("end", "?")
                txt_lines.append(f"[{start} → {end}] {seg['text']}")
        else:
            txt_lines.append(transcript.get("text", ""))

        txt_content = "\n".join(txt_lines)

        json_name = f"{stem}-transcript.json"
        txt_name = f"{stem}-transcript.txt"

        if nc is not None:
            # Stage locally, then publish each to the presence's NC via WebDAV.
            local_dir = os.path.join("/tmp/cove-video", nc.user, "transcripts")
            os.makedirs(local_dir, exist_ok=True)
            json_path = os.path.join(local_dir, json_name)
            txt_path = os.path.join(local_dir, txt_name)
            with open(json_path, "w") as jf:
                jf.write(json_content)
            with open(txt_path, "w") as tf:
                tf.write(txt_content)
            await publish_video_output(json_path, f"transcripts/{json_name}", nc, "application/json")
            await publish_video_output(txt_path, f"transcripts/{txt_name}", nc, "text/plain")
            logger.info(f"Transcripts published to NC: {json_name}, {txt_name}")
        else:
            transcripts_dir = os.path.join(VIDEO_MOUNT, "transcripts")
            os.makedirs(transcripts_dir, exist_ok=True)

            json_path = os.path.join(transcripts_dir, json_name)
            with open(json_path, "w") as jf:
                jf.write(json_content)

            txt_path = os.path.join(transcripts_dir, txt_name)
            with open(txt_path, "w") as tf:
                tf.write(txt_content)

            _nc_scan("AgentSkills/Content/video/transcripts")
            logger.info(f"Transcripts written to filesystem: {json_path}, {txt_path}")

        # Preview is NOT generated here — it's built from trimmed segments
        # after the user edits in the transcript editor. See /api/video/preview.

        # Add paths to response
        transcript["transcript_json"] = f"{NC_VIDEO_PATH}/transcripts/{stem}-transcript.json"
        transcript["transcript_txt"] = f"{NC_VIDEO_PATH}/transcripts/{stem}-transcript.txt"
        transcript["video_location"] = f"{NC_VIDEO_PATH}/processing/{video_name}"
        transcript["pipeline_stage"] = "transcribed"
        # Surface the copy outcome so callers know the video's processing/ copy is
        # missing (transcript still valid; the source is preserved in inbox/).
        transcript["processing_copy_ok"] = processing_copy_ok

        return JSONResponse(transcript)

    except Exception as e:
        logger.error(f"Video transcription error: {e}")
        # Move back to inbox on failure so it can be retried (founder mount only;
        # with nc the source still sits in the presence's NC inbox, untouched).
        if nc is None:
            try:
                if os.path.isfile(processing_path):
                    shutil.move(processing_path, file_path)
                    _nc_scan("AgentSkills/Content/video")
                    logger.info(f"Moved {video_name} back to inbox/ after error")
            except Exception:
                pass
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/stt/video/status")
async def video_transcription_status():
    """Check if Qwen3-ASR is currently loaded (transcription in progress)."""
    asr = _get_qwen_asr()
    nc_html_ok = bool(NC_HTML_ROOT) and os.path.isdir(NC_HTML_ROOT)
    return {
        "model": asr.ASR_MODEL,
        "loaded": asr.is_loaded,
        "video_mount": os.path.isdir(VIDEO_MOUNT),
        "nc_html_root": NC_HTML_ROOT or "",
        "nc_html_mounted": nc_html_ok,
        "inbox_files": os.listdir(f"{VIDEO_MOUNT}/inbox") if os.path.isdir(f"{VIDEO_MOUNT}/inbox") else [],
    }
