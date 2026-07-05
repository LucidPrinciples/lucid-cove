"""
X (Twitter) posting routes — media upload, posting, and social_queue processing.

Posts video clips from the video pipeline to the configured X account.
Credentials are OAuth 1.0a user-context, loaded from environment (X_* vars).

Handles:
    1. GET  /api/x/status         → Credential + connection check
    2. POST /api/x/post           → Text-only post
    3. POST /api/x/upload         → Media post (video/image from /content mount)
    4. POST /api/x/process-queue  → Process queued social_queue rows (platform='x')

Cost notes (pay-per-use, April 2026 pricing):
    - Standard write (text or media, NO URL): ~$0.015
    - Post containing a URL: ~$0.20 (13x). Captions built here never include URLs.

Media upload uses X API v2 chunked upload (initialize/append/finalize),
falling back to command-style endpoints if the deployed API differs.
Unicode normalization and 280-char handling ported from LT's x_posting
tool on Socrates (the single source for that behavior on VPS).

X_DRY_RUN=true (default if unset: false) skips all live API calls.
"""

import asyncio
import json
import os
from src.env import env, env_bool
import re
import time
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.dashboard.routes.posting_identity import resolve_x_creds

router = APIRouter()

# Content root inside the container (NC AgentSkills/Content mount)
CONTENT_ROOT = Path("/content")

# X API v2
X_API_BASE = "https://api.x.com/2"
X_UPLOAD_V11 = "https://upload.twitter.com/1.1/media/upload.json"
VERIFY_V11 = "https://api.twitter.com/1.1/account/verify_credentials.json"

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB — under the 5MB per-append limit
PROCESSING_TIMEOUT = 300       # max seconds to wait for X video processing

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


# =========================================================================
# Text handling (ported from LT's x_posting.py — keep behavior identical)
# =========================================================================

def normalize_unicode(text: str) -> str:
    """Normalize Unicode characters to prevent X API encoding issues."""
    if not text:
        return text
    replacements = [
        (chr(0x2014), '-'),    # em dash
        (chr(0x2013), '-'),    # en dash
        (chr(0x201C), '"'),    # left double quote
        (chr(0x201D), '"'),    # right double quote
        (chr(0x2018), "'"),    # left single quote
        (chr(0x2019), "'"),    # right single quote
        (chr(0x2026), '...'),  # ellipsis
        (chr(0x00A0), ' '),    # non-breaking space
    ]
    for char, replacement in replacements:
        text = text.replace(char, replacement)
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    return text


def x_length(text: str) -> int:
    """X effective character length. URLs count as 23 chars."""
    text_no_urls = re.sub(r'https?://\S+', '', text)
    url_count = len(re.findall(r'https?://\S+', text))
    return len(text_no_urls) + (url_count * 23)


def contains_url(text: str) -> bool:
    return bool(re.search(r'https?://\S+', text or ""))


def build_caption(title: str, hashtags: str = "", description: str = "") -> str:
    """Build a post caption, fit to 280, never a URL.

    For X, the AI template writes the actual post text into `description` —
    prefer it over the title when present. URLs cost 13x per post on
    pay-per-use — strip them defensively.
    """
    base = (description or "").strip() or (title or "").strip()
    base = re.sub(r'https?://\S+', '', base).strip()
    hashtags = re.sub(r'https?://\S+', '', hashtags or "").strip()
    caption = f"{base}\n\n{hashtags}" if (hashtags and hashtags not in base) else base
    caption = normalize_unicode(caption)
    if x_length(caption) > 280:
        # Drop hashtags first, then truncate
        caption = normalize_unicode(base)
        if x_length(caption) > 280:
            caption = caption[:277] + "..."
    return caption


# =========================================================================
# Credentials / client
# =========================================================================

def _dry_run() -> bool:
    return env_bool("X_DRY_RUN", "false")


def _get_session(creds: dict | None):
    """Create an OAuth1 signed requests session from resolved per-presence creds.

    Returns (session, error). session is None on failure. `creds` is the dict
    returned by posting_identity.resolve_x_creds (api_key/api_secret/
    access_token/access_token_secret).
    """
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        return None, "requests-oauthlib not installed (add to requirements.lock + rebuild)"
    if not creds:
        return None, "X API credentials not set for this presence."
    session = OAuth1Session(
        creds["api_key"],
        client_secret=creds["api_secret"],
        resource_owner_key=creds["access_token"],
        resource_owner_secret=creds["access_token_secret"],
    )
    return session, None


# =========================================================================
# Sync API internals (run via asyncio.to_thread from routes)
# =========================================================================

def _verify_credentials_sync(creds: dict) -> dict:
    """Check credentials. Tries v1.1 (returns access level), falls back to v2."""
    session, error = _get_session(creds)
    if not session:
        return {"ok": False, "error": error}

    r = session.get(VERIFY_V11, timeout=15)
    if r.ok:
        return {
            "ok": True,
            "account": r.json().get("screen_name"),
            "access_level": r.headers.get("x-access-level"),
            "endpoint": "v1.1",
        }

    # v1.1 may be unavailable on newer tiers — try v2
    r2 = session.get(f"{X_API_BASE}/users/me", timeout=15)
    if r2.ok:
        return {
            "ok": True,
            "account": r2.json().get("data", {}).get("username"),
            "access_level": r2.headers.get("x-access-level") or "unknown (v2)",
            "endpoint": "v2",
        }
    return {"ok": False, "error": f"v1.1: HTTP {r.status_code}; v2: HTTP {r2.status_code} {r2.text[:200]}"}


def _upload_media_chunked_sync(session, file_path: Path, media_category: str) -> str:
    """Upload media via X API v2 chunked upload. Returns media_id.

    Tries the path-style v2 endpoints (initialize/append/finalize) first,
    then command-style v2, then v1.1 command-style. Raises RuntimeError
    with detail on failure.
    """
    total_bytes = file_path.stat().st_size
    media_type = MEDIA_TYPES.get(file_path.suffix.lower(), "video/mp4")

    # ── INIT ──────────────────────────────────────────────────────────
    init_body = {
        "media_type": media_type,
        "total_bytes": total_bytes,
        "media_category": media_category,
    }
    style = "v2-path"
    r = session.post(f"{X_API_BASE}/media/upload/initialize", json=init_body, timeout=30)
    if r.status_code == 404:
        style = "v2-command"
        r = session.post(
            f"{X_API_BASE}/media/upload",
            data={"command": "INIT", "media_type": media_type,
                  "total_bytes": total_bytes, "media_category": media_category},
            timeout=30,
        )
    if r.status_code == 404:
        style = "v1.1"
        r = session.post(
            X_UPLOAD_V11,
            data={"command": "INIT", "media_type": media_type,
                  "total_bytes": total_bytes, "media_category": media_category},
            timeout=30,
        )
    if not r.ok:
        raise RuntimeError(f"media INIT failed ({style}): HTTP {r.status_code} {r.text[:300]}")

    body = r.json()
    media_id = (
        body.get("data", {}).get("id")
        or body.get("media_id_string")
        or str(body.get("media_id", ""))
    )
    if not media_id:
        raise RuntimeError(f"media INIT returned no media_id: {body}")

    # ── APPEND chunks ─────────────────────────────────────────────────
    segment = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            if style == "v2-path":
                r = session.post(
                    f"{X_API_BASE}/media/upload/{media_id}/append",
                    data={"segment_index": segment},
                    files={"media": chunk},
                    timeout=120,
                )
            else:
                url = f"{X_API_BASE}/media/upload" if style == "v2-command" else X_UPLOAD_V11
                r = session.post(
                    url,
                    data={"command": "APPEND", "media_id": media_id, "segment_index": segment},
                    files={"media": chunk},
                    timeout=120,
                )
            if not r.ok and r.status_code != 204:
                raise RuntimeError(
                    f"media APPEND segment {segment} failed ({style}): HTTP {r.status_code} {r.text[:300]}"
                )
            segment += 1

    # ── FINALIZE ──────────────────────────────────────────────────────
    if style == "v2-path":
        r = session.post(f"{X_API_BASE}/media/upload/{media_id}/finalize", timeout=30)
    else:
        url = f"{X_API_BASE}/media/upload" if style == "v2-command" else X_UPLOAD_V11
        r = session.post(url, data={"command": "FINALIZE", "media_id": media_id}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"media FINALIZE failed ({style}): HTTP {r.status_code} {r.text[:300]}")

    body = r.json()
    info = body.get("data", {}).get("processing_info") or body.get("processing_info")

    # ── Poll processing (video transcode) ─────────────────────────────
    waited = 0
    while info and info.get("state") in ("pending", "in_progress"):
        wait = min(info.get("check_after_secs", 3), 15)
        time.sleep(wait)
        waited += wait
        if waited > PROCESSING_TIMEOUT:
            raise RuntimeError(f"media processing timed out after {waited}s (media_id {media_id})")
        if style == "v1.1":
            r = session.get(X_UPLOAD_V11, params={"command": "STATUS", "media_id": media_id}, timeout=30)
        else:
            r = session.get(f"{X_API_BASE}/media/upload",
                            params={"command": "STATUS", "media_id": media_id}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"media STATUS failed: HTTP {r.status_code} {r.text[:300]}")
        body = r.json()
        info = body.get("data", {}).get("processing_info") or body.get("processing_info")

    if info and info.get("state") == "failed":
        raise RuntimeError(f"media processing failed: {info.get('error', info)}")

    return media_id


def _create_post_sync(creds: dict, text: str, media_ids: list[str] | None = None) -> dict:
    """Create a post via POST /2/tweets. Returns {tweet_id, url}."""
    session, error = _get_session(creds)
    if not session:
        raise RuntimeError(error)
    payload: dict = {"text": text}
    if media_ids:
        payload["media"] = {"media_ids": [str(m) for m in media_ids]}
    r = session.post(f"{X_API_BASE}/tweets", json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"create post failed: HTTP {r.status_code} {r.text[:300]}")
    tweet_id = str(r.json().get("data", {}).get("id"))
    return {"tweet_id": tweet_id, "url": f"https://x.com/i/web/status/{tweet_id}"}


def _post_media_sync(creds: dict, file_path: Path, text: str, media_category: str) -> dict:
    """Full media post: chunked upload + create post."""
    session, error = _get_session(creds)
    if not session:
        raise RuntimeError(error)
    media_id = _upload_media_chunked_sync(session, file_path, media_category)
    return _create_post_sync(creds, text, media_ids=[media_id])


# =========================================================================
# Path resolution
# =========================================================================

# Shared resolver — single source of truth for every platform poster
from src.utils.content_paths import resolve_content_path  # noqa: E402,F401


# =========================================================================
# Routes
# =========================================================================

@router.get("/api/x/status")
async def x_status(request: Request):
    """Credential + connection check for the current presence. ~1 read ($0.005) live."""
    creds, error = await resolve_x_creds(request=request)
    if not creds:
        return {"configured": False, "dry_run": _dry_run(), "error": error}
    result = await asyncio.to_thread(_verify_credentials_sync, creds)
    return {
        "configured": True,
        "dry_run": _dry_run(),
        "connection": result,
    }


class PostRequest(BaseModel):
    text: str = Field(..., description="Post text (max 280 effective chars)")


@router.post("/api/x/post")
async def x_post(req: PostRequest, request: Request):
    """Text-only post. URLs are flagged (13x cost) but not blocked here."""
    text = normalize_unicode(req.text)
    length = x_length(text)
    if length > 280:
        return JSONResponse(status_code=400, content={
            "error": f"Post too long: {length} chars (max 280). {length - 280} over."})

    if _dry_run():
        return {"status": "dry_run", "would_post": text, "length": length,
                "url_warning": contains_url(text)}

    creds, error = await resolve_x_creds(request=request)
    if not creds:
        return JSONResponse(status_code=400, content={"error": error})
    try:
        result = await asyncio.to_thread(_create_post_sync, creds, text, None)
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return {"status": "ok", **result, "url_cost_applied": contains_url(text)}


class UploadRequest(BaseModel):
    file_path: str = Field(..., description="NC path or /content-relative path to media file")
    text: str = Field(default="", description="Caption. If empty, built from title+hashtags")
    title: str = Field(default="", description="Used to build caption when text empty")
    hashtags: str = Field(default="", description="Used to build caption when text empty")
    media_category: str = Field(
        default="",
        description="Override: tweet_video, tweet_image, amplify_video (long-form, Premium). "
        "Auto-detected from extension when empty.",
    )


@router.post("/api/x/upload")
async def x_upload(req: UploadRequest, request: Request):
    """Upload media from /content and post it with a caption."""
    video_path = resolve_content_path(req.file_path)
    if not video_path:
        return JSONResponse(status_code=404, content={
            "error": f"Media file not found under /content: {req.file_path}"})

    text = normalize_unicode(req.text) if req.text else build_caption(req.title, req.hashtags)
    length = x_length(text)
    if length > 280:
        return JSONResponse(status_code=400, content={
            "error": f"Caption too long: {length} chars (max 280)."})

    if req.media_category:
        media_category = req.media_category
    else:
        is_video = video_path.suffix.lower() in (".mp4", ".mov", ".webm")
        media_category = "tweet_video" if is_video else "tweet_image"

    size_mb = round(video_path.stat().st_size / 1024 / 1024, 1)

    if _dry_run():
        return {"status": "dry_run", "would_post": text,
                "file": str(video_path), "size_mb": size_mb,
                "media_category": media_category}

    creds, error = await resolve_x_creds(request=request)
    if not creds:
        return JSONResponse(status_code=400, content={"error": error})
    try:
        result = await asyncio.to_thread(_post_media_sync, creds, video_path, text, media_category)
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})

    return {"status": "ok", **result, "file": str(video_path),
            "size_mb": size_mb, "caption": text}


async def process_queued_x_posts() -> dict:
    """Process social_queue rows: platform='x', status='queued', due now.

    Rows with no upload_date are treated as due immediately.
    Status flow: queued → uploading → published (X posts go live instantly),
    or → failed with error_message.

    Shared by the /api/x/process-queue route and the 15-minute scheduler check.
    """
    # CF-1: left unscoped (processor path) — Cove machinery posts every
    # presence's due rows; per-presence identity comes from agent_id.
    from src.memory.database import get_db

    async with get_db() as conn:
        # clip_type='full' and >140s clips are excluded: X API caps video at
        # 140s on standard accounts. Full-length posts go through the manual
        # card flow (native upload, Premium). They stay on the board untouched.
        # agent_id = the owning presence — each row posts from its OWN account.
        result = await conn.execute(
            """SELECT id, title, description, hashtags, file_path, duration_seconds, agent_id
               FROM social_queue
               WHERE platform = 'x' AND status = 'queued'
                 AND (upload_date IS NULL OR upload_date <= NOW())
                 AND COALESCE(clip_type, '') != 'full'
                 AND COALESCE(duration_seconds, 0) <= 140
               ORDER BY created_at ASC"""
        )
        ready = await result.fetchall()

    if not ready:
        return {"status": "ok", "message": "No X posts ready.", "ready": 0}

    creds_cache: dict = {}  # owner_id -> creds (resolve once per presence)
    results = []
    for row in ready:
        qid = row["id"]
        caption = build_caption(row["title"], row["hashtags"], row.get("description", ""))
        video_path = resolve_content_path(row["file_path"])

        # Resolve THIS row's presence credentials (env fallback for legacy NULL rows).
        owner_id = row.get("agent_id")
        if owner_id not in creds_cache:
            creds_cache[owner_id], _ = await resolve_x_creds(owner_id=owner_id)
        row_creds = creds_cache[owner_id]
        if not row_creds:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE social_queue SET status='failed', error_message=%s WHERE id=%s",
                    ("X credentials not set for this presence.", qid))
            results.append({"id": qid, "status": "failed", "error": "no X credentials"})
            continue

        if not video_path:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE social_queue SET status='failed', error_message=%s WHERE id=%s",
                    (f"File not found under /content: {row['file_path']}", qid))
            results.append({"id": qid, "status": "failed", "error": "file not found"})
            continue

        if _dry_run():
            results.append({"id": qid, "status": "dry_run", "would_post": caption,
                            "file": str(video_path)})
            continue

        async with get_db() as conn:
            await conn.execute(
                "UPDATE social_queue SET status='uploading' WHERE id=%s", (qid,))

        try:
            post = await asyncio.to_thread(
                _post_media_sync, row_creds, video_path, caption, "tweet_video")
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE social_queue
                       SET status='published', published_at=NOW(), uploaded_at=NOW(),
                           error_message=NULL, platform_data=%s
                       WHERE id=%s""",
                    (json.dumps(post), qid))
            results.append({"id": qid, "status": "published", **post})
        except Exception as e:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE social_queue SET status='failed', error_message=%s WHERE id=%s",
                    (str(e)[:500], qid))
            results.append({"id": qid, "status": "failed", "error": str(e)[:300]})

    return {"status": "ok", "processed": len(results),
            "dry_run": _dry_run(), "results": results}


@router.post("/api/x/process-queue")
async def x_process_queue():
    """Manually trigger the X queue processor (same logic as the scheduler)."""
    result = await process_queued_x_posts()
    if result.get("status") == "error":
        return JSONResponse(status_code=500, content={"error": result.get("error")})
    return result
