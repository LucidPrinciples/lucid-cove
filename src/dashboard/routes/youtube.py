"""
YouTube API routes — upload, scheduling, and queue management.

Handles:
  Upload:
    1. POST /api/youtube/upload        → Upload video with metadata
    2. POST /api/youtube/schedule      → Update a private video's publishAt date

  Queue:
    3. POST /api/youtube/queue         → Add post to upload queue
    4. GET  /api/youtube/queue         → List queued posts
    5. PATCH /api/youtube/queue/{id}   → Update a queued post
    6. DELETE /api/youtube/queue/{id}  → Cancel a queued post

  Info:
    7. POST /api/youtube/process-queue → Manually trigger queue processor
    8. GET  /api/youtube/uploads       → List recent channel uploads

Two-stage scheduling pattern:
  - Upload as PRIVATE on day X (via /upload with privacy=private)
  - Schedule to go PUBLIC on day Y (via /schedule with publishAt)
  - Only 1-2 weeks of scheduled posts in YouTube at any time

Auth routes are in youtube_auth.py. Calendar sync is in youtube_calendar.py.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.dashboard.routes.youtube_auth import get_valid_access_token, _get_oauth_config
from src.utils.time_utils import now_app, local_to_utc
from src.dashboard.routes.youtube_calendar import (
    create_youtube_calendar_event,
    delete_youtube_calendar_event,
)

router = APIRouter()


def sanitize_youtube_tags(tags) -> list[str]:
    """Trim a tag list to YouTube's real keyword limit so an upload never 400s on
    'invalid video keywords'. Strips angle brackets (rejected outright), drops
    empties, and caps the TOTAL characters (YouTube counts the quotes it wraps
    around multi-word tags plus the commas between them) to a safe 450. Shared by
    BOTH the interactive upload route and the scheduler queue path so neither can
    send an over-long or malformed keyword set."""
    safe: list[str] = []
    total = 0
    for t in (tags or []):
        t = str(t).replace("<", "").replace(">", "").strip()
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if safe else 0)
        if total + cost > 450:
            break
        safe.append(t)
        total += cost
    return safe



# =========================================================================
# Upload + Scheduling
# =========================================================================

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"

# Content root inside the container (mounted from host)
CONTENT_ROOT = Path("/content")


class UploadRequest(BaseModel):
    """Request body for video upload."""
    file_path: str = Field(
        ...,
        description="Path to video file relative to content root. "
        "E.g. 'Videos/Stories/FMTA/Week10/video.mp4'"
    )
    title: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    privacy: str = Field(
        default="private",
        description="Upload privacy: private, unlisted, or public. "
        "Use private for two-stage scheduling."
    )
    category_id: str = Field(
        default="22",
        description="YouTube category ID. 22=People & Blogs, 27=Education, 10=Music"
    )
    made_for_kids: bool = Field(default=False)
    shorts: bool = Field(
        default=False,
        description="If true, adds #Shorts to title if not present."
    )
    publish_at: str | None = Field(
        default=None,
        description="ISO 8601 datetime for scheduled publish. "
        "Only works with privacy=private. "
        "E.g. '2026-05-22T12:00:00-04:00'"
    )


class ScheduleRequest(BaseModel):
    """Request body for scheduling a previously uploaded video."""
    video_id: str = Field(..., description="YouTube video ID to schedule")
    publish_at: str = Field(
        ...,
        description="ISO 8601 datetime for scheduled publish. "
        "E.g. '2026-05-22T12:00:00-04:00'"
    )


@router.post("/api/youtube/upload")
async def youtube_upload(req: UploadRequest, request: Request):
    """Upload a video to YouTube with metadata.

    Uses resumable upload protocol for reliability. The video file
    is read from the content mount (/content in container).

    Returns the YouTube video ID and URL on success.

    Quota cost: 1,600 units per upload (daily limit ~6 uploads on free tier).
    """
    # Validate file exists (resolver handles vault-relative + content-relative paths)
    from src.utils.content_paths import resolve_content_path
    video_path = resolve_content_path(req.file_path)
    if not video_path:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Video file not found: {req.file_path}",
                "content_root": str(CONTENT_ROOT),
            },
        )

    if not video_path.is_file():
        return JSONResponse(
            status_code=400,
            content={"error": f"Path is not a file: {req.file_path}"},
        )

    file_size = video_path.stat().st_size
    if file_size == 0:
        return JSONResponse(
            status_code=400,
            content={"error": "Video file is empty."},
        )

    # Get valid access token for THIS presence's connected channel
    from src.dashboard.routes.posting_identity import owner_id_from_request, yt_service_key
    _yt_service = yt_service_key(await owner_id_from_request(request))
    try:
        access_token = await get_valid_access_token(_yt_service)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})

    # Build video metadata
    title = req.title
    if req.shorts and "#Shorts" not in title and "#shorts" not in title:
        # Append #Shorts if it fits within the 100-char limit
        if len(title) + 8 <= 100:
            title = f"{title} #Shorts"

    # Build snippet. Sanitize tags to YouTube's real limit: strip angle brackets
    # (rejected outright), drop empties, and cap the TOTAL characters (YouTube counts
    # the quotes it wraps around multi-word tags plus the commas between them), so an
    # over-long or malformed keyword set is trimmed to valid instead of failing upload.
    _safe_tags = sanitize_youtube_tags(req.tags)

    snippet = {
        "title": title,
        "description": req.description,
        "tags": _safe_tags,
        "categoryId": req.category_id,
    }

    # Build status
    status = {
        "privacyStatus": req.privacy,
        "selfDeclaredMadeForKids": req.made_for_kids,
    }

    # publishAt only works with privacy=private
    # YouTube API requires UTC with Z suffix
    if req.publish_at and req.privacy == "private":
        try:
            from datetime import timezone as _tz
            parsed = datetime.fromisoformat(req.publish_at)
            utc_dt = parsed.astimezone(_tz.utc)
            status["publishAt"] = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            status["publishAt"] = req.publish_at  # fallback to raw value

    video_metadata = {
        "snippet": snippet,
        "status": status,
    }

    now = now_app().strftime("%Y-%m-%d %H:%M %Z")

    # ── Step 1: Initiate resumable upload ────────────────────────────────
    # Detect content type from extension
    ext = video_path.suffix.lower()
    content_types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    content_type = content_types.get(ext, "video/mp4")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            init_resp = await client.post(
                YOUTUBE_UPLOAD_URL,
                params={
                    "uploadType": "resumable",
                    "part": "snippet,status",
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Upload-Content-Type": content_type,
                    "X-Upload-Content-Length": str(file_size),
                },
                content=json.dumps(video_metadata),
            )

        if init_resp.status_code not in (200, 308):
            return JSONResponse(
                status_code=init_resp.status_code,
                content={
                    "error": "Failed to initiate upload",
                    "detail": init_resp.text,
                },
            )

        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            return JSONResponse(
                status_code=500,
                content={"error": "No upload URL returned from YouTube."},
            )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Upload initiation failed: {str(e)}"},
        )

    # ── Step 2: Upload the video file ────────────────────────────────────
    # Stream the file in chunks for memory efficiency
    CHUNK_SIZE = 10 * 1024 * 1024  # 10MB chunks

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            with open(video_path, "rb") as f:
                file_data = f.read()  # For shorts (<60s), files are small enough

            upload_resp = await client.put(
                upload_url,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(file_size),
                },
                content=file_data,
            )

        if upload_resp.status_code not in (200, 201):
            return JSONResponse(
                status_code=upload_resp.status_code,
                content={
                    "error": "Video upload failed",
                    "detail": upload_resp.text,
                },
            )

        result = upload_resp.json()
        video_id = result.get("id")
        video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None

        print(
            f"[{now}] [youtube] Upload complete: {video_id} "
            f"({req.file_path}, {file_size // 1024}KB, {req.privacy})"
        )

        return {
            "status": "ok",
            "video_id": video_id,
            "url": video_url,
            "title": title,
            "privacy": req.privacy,
            "publish_at": req.publish_at,
            "file_size_kb": file_size // 1024,
        }

    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={
                "error": "Upload timed out. File may be too large or connection too slow.",
                "file_size_mb": round(file_size / 1024 / 1024, 1),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Upload failed: {str(e)}"},
        )


@router.post("/api/youtube/schedule")
async def youtube_schedule(req: ScheduleRequest, request: Request):
    """Schedule a previously uploaded private video to go public.

    This is stage 2 of the two-stage scheduling pattern:
      Stage 1: Upload as private (via /api/youtube/upload)
      Stage 2: Set publishAt to make it go public on a specific date/time

    The video must currently be private. publishAt sets it to auto-publish
    at the specified time. YouTube requires publishAt to be at least
    15 minutes in the future.
    """
    from src.dashboard.routes.posting_identity import owner_id_from_request, yt_service_key
    _yt_service = yt_service_key(await owner_id_from_request(request))
    try:
        access_token = await get_valid_access_token(_yt_service)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})

    now = now_app().strftime("%Y-%m-%d %H:%M %Z")

    # Update the video's status to scheduled
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                YOUTUBE_API_URL,
                params={"part": "status"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps({
                    "id": req.video_id,
                    "status": {
                        "privacyStatus": "private",
                        "publishAt": local_to_utc(req.publish_at),
                    },
                }),
            )

        if resp.status_code != 200:
            return JSONResponse(
                status_code=resp.status_code,
                content={
                    "error": "Failed to schedule video",
                    "detail": resp.text,
                },
            )

        result = resp.json()
        print(
            f"[{now}] [youtube] Scheduled: {req.video_id} → {req.publish_at}"
        )

        return {
            "status": "ok",
            "video_id": req.video_id,
            "publish_at": req.publish_at,
            "url": f"https://www.youtube.com/watch?v={req.video_id}",
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Schedule failed: {str(e)}"},
        )


@router.post("/api/youtube/process-queue")
async def youtube_process_queue():
    """Manually trigger the YouTube queue processor.

    Checks for queued posts where upload_date has passed and uploads them.
    Same logic as the scheduler's 15-minute check, but on demand.
    """
    # CF-1: left unscoped (processor path) — this is the manual trigger of the
    # Cove-machinery uploader; it must see every presence's queued rows.
    from src.memory.database import get_db

    try:
        # Check YouTube is configured
        config = _get_oauth_config()
    except ValueError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, title, upload_date, publish_date, status
                   FROM youtube_queue
                   WHERE status = 'queued' AND upload_date <= NOW()
                   ORDER BY upload_date ASC"""
            )
            ready = await result.fetchall()

        if not ready:
            return {"status": "ok", "message": "No posts ready for upload.", "ready": 0}

        # Import and run the scheduler's upload logic
        from src.utils.scheduler import AgentScheduler
        scheduler = AgentScheduler()

        results = []
        for post_row in ready:
            # Fetch full post data
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT id, title, description, tags, hashtags, file_path,
                              category_id, made_for_kids, is_short, related_video,
                              playlist_id, publish_date, series
                       FROM youtube_queue WHERE id = %s""",
                    (post_row["id"],),
                )
                post = await result.fetchone()

            if post:
                await scheduler._upload_youtube_post(dict(post))
                # Re-fetch status after upload attempt
                async with get_db() as conn:
                    result = await conn.execute(
                        "SELECT status, youtube_video_id, error_message FROM youtube_queue WHERE id = %s",
                        (post_row["id"],),
                    )
                    updated = await result.fetchone()
                results.append({
                    "id": post_row["id"],
                    "title": post_row["title"],
                    "status": updated["status"] if updated else "unknown",
                    "video_id": updated["youtube_video_id"] if updated else None,
                    "error": updated["error_message"] if updated else None,
                })

        return {"status": "ok", "processed": len(results), "results": results}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Queue processing failed: {str(e)}"})


@router.get("/api/youtube/uploads")
async def youtube_uploads(max_results: int = 10):
    """List recent uploads on the channel.

    Useful for verifying uploads went through and checking scheduled dates.
    Quota cost: ~3 units per call.
    """
    try:
        access_token = await get_valid_access_token("youtube")
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # First get the uploads playlist ID
            ch_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "contentDetails", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if ch_resp.status_code != 200:
                return JSONResponse(
                    status_code=ch_resp.status_code,
                    content={"error": "Failed to get channel info", "detail": ch_resp.text},
                )

            ch_data = ch_resp.json()
            items = ch_data.get("items", [])
            if not items:
                return {"uploads": [], "message": "No channel found."}

            uploads_playlist = (
                items[0]
                .get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )

            if not uploads_playlist:
                return {"uploads": [], "message": "No uploads playlist found."}

            # Get recent uploads from the playlist
            pl_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={
                    "part": "snippet,status",
                    "playlistId": uploads_playlist,
                    "maxResults": min(max_results, 50),
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if pl_resp.status_code != 200:
                return JSONResponse(
                    status_code=pl_resp.status_code,
                    content={"error": "Failed to list uploads", "detail": pl_resp.text},
                )

            pl_data = pl_resp.json()
            uploads = []
            for item in pl_data.get("items", []):
                snippet = item.get("snippet", {})
                status = item.get("status", {})
                video_id = snippet.get("resourceId", {}).get("videoId")
                uploads.append({
                    "video_id": video_id,
                    "title": snippet.get("title"),
                    "published_at": snippet.get("publishedAt"),
                    "privacy": status.get("privacyStatus"),
                    "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                })

            return {"uploads": uploads, "total": pl_data.get("pageInfo", {}).get("totalResults", 0)}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to list uploads: {str(e)}"},
        )


# =========================================================================
# YouTube Queue — Two-layer scheduling
# =========================================================================
# Posts are saved to the queue from the action page. Stuart's job runner
# picks them up when upload_date arrives. After upload, follow-up tasks
# are created for Studio-only actions (related video, altered content, etc.)

class QueueRequest(BaseModel):
    """Request body for adding a post to the YouTube queue."""
    title: str = Field(..., max_length=100)
    description: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    hashtags: str = Field(default="")
    file_path: str = Field(
        ..., description="Path to video file relative to content root."
    )
    category_id: str = Field(default="22")
    made_for_kids: bool = Field(default=False)
    is_short: bool = Field(default=False)
    related_video: str | None = Field(
        default=None,
        description="Long-form video title/URL to link on the short (Studio-only)."
    )
    playlist_id: str | None = Field(default=None)
    thumbnail_path: str | None = Field(default=None)
    upload_date: str = Field(
        ...,
        description="ISO 8601 datetime — when Stuart uploads to YouTube. "
        "E.g. '2026-05-18T09:00:00-04:00'"
    )
    publish_date: str = Field(
        ...,
        description="ISO 8601 datetime — when YouTube makes it public. "
        "E.g. '2026-05-20T09:00:00-04:00'"
    )
    series: str | None = Field(default=None)
    card_id: str | None = Field(default=None)


class QueueUpdateRequest(BaseModel):
    """Request body for updating a queued post. All fields optional."""
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    hashtags: str | None = None
    file_path: str | None = None
    category_id: str | None = None
    made_for_kids: bool | None = None
    is_short: bool | None = None
    related_video: str | None = None
    playlist_id: str | None = None
    thumbnail_path: str | None = None
    upload_date: str | None = None
    publish_date: str | None = None
    series: str | None = None
    status: str | None = Field(
        default=None,
        description="New status: draft or queued."
    )


@router.post("/api/youtube/queue")
async def youtube_queue_add(req: QueueRequest, request: Request):
    """Add a post to the YouTube upload queue.

    The post is saved with status 'queued'. Stuart's scheduled job runner
    will upload it to YouTube when upload_date arrives.
    """
    from src.memory.database import get_db
    # CF-1: strict self-scope — stamp the acting presence in multi mode
    # (NULL in single mode; behavior there is unchanged).
    from src.dashboard.routes.action_board import _acting_presence_id
    pid = await _acting_presence_id(request)

    now = now_app().strftime("%Y-%m-%d %H:%M %Z")

    # Validate file exists (resolver handles vault-relative + content-relative paths)
    from src.utils.content_paths import resolve_content_path
    video_path = resolve_content_path(req.file_path)
    if not video_path:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Video file not found: {req.file_path}",
            },
        )

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """
                INSERT INTO youtube_queue
                    (title, description, tags, hashtags, file_path, category_id,
                     made_for_kids, is_short, related_video, playlist_id,
                     thumbnail_path, upload_date, publish_date, series, card_id,
                     presence_id)
                VALUES
                    (%(title)s, %(description)s, %(tags)s::jsonb, %(hashtags)s,
                     %(file_path)s, %(category_id)s, %(made_for_kids)s, %(is_short)s,
                     %(related_video)s, %(playlist_id)s, %(thumbnail_path)s,
                     %(upload_date)s, %(publish_date)s, %(series)s, %(card_id)s,
                     %(presence_id)s)
                RETURNING id, status, created_at
                """,
                {
                    # CF-1: NULL in single mode; acting presence in multi
                    "presence_id": pid if pid else None,
                    "title": req.title,
                    "description": req.description,
                    "tags": json.dumps(req.tags),
                    "hashtags": req.hashtags,
                    "file_path": req.file_path,
                    "category_id": req.category_id,
                    "made_for_kids": req.made_for_kids,
                    "is_short": req.is_short,
                    "related_video": req.related_video,
                    "playlist_id": req.playlist_id,
                    "thumbnail_path": req.thumbnail_path,
                    "upload_date": local_to_utc(req.upload_date) if req.upload_date else None,
                    "publish_date": local_to_utc(req.publish_date) if req.publish_date else None,
                    "series": req.series,
                    "card_id": req.card_id,
                },
            )
            row = await result.fetchone()

        print(f"[{now}] [youtube] Queued: #{row['id']} '{req.title}' → upload {req.upload_date}, publish {req.publish_date}")

        return {
            "status": "queued",
            "id": row["id"],
            "title": req.title,
            "upload_date": req.upload_date,
            "publish_date": req.publish_date,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to queue post: {str(e)}"},
        )


@router.get("/api/youtube/queue")
async def youtube_queue_list(
    request: Request,
    status: str | None = None,
    limit: int = 50,
):
    """List queued YouTube posts.

    Optional filter by status: queued, uploading, uploaded, published, failed, cancelled.
    Returns newest first by default.
    """
    from src.memory.database import get_db
    # CF-1: strict self-scope — browser-facing list (action board / post pages)
    from src.dashboard.routes.action_board import _acting_presence_id
    pid = await _acting_presence_id(request)
    if pid == "":
        return {"posts": [], "count": 0}
    scope_sql = "" if pid is None else " AND presence_id = %s"
    scope_args = () if pid is None else (pid,)

    try:
        async with get_db() as conn:
            if status:
                result = await conn.execute(
                    f"""
                    SELECT * FROM youtube_queue
                    WHERE status = %s{scope_sql}
                    ORDER BY publish_date ASC
                    LIMIT %s
                    """,
                    (status,) + scope_args + (limit,),
                )
            else:
                result = await conn.execute(
                    f"""
                    SELECT * FROM youtube_queue
                    WHERE TRUE{scope_sql}
                    ORDER BY publish_date ASC
                    LIMIT %s
                    """,
                    scope_args + (limit,),
                )
            rows = await result.fetchall()

        posts = []
        for row in rows:
            post = dict(row)
            # Convert datetimes to ISO strings for JSON
            for key in ("upload_date", "publish_date", "created_at", "updated_at", "uploaded_at", "published_at"):
                if post.get(key):
                    post[key] = post[key].isoformat()
            posts.append(post)

        return {"posts": posts, "count": len(posts)}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to list queue: {str(e)}"},
        )


@router.patch("/api/youtube/queue/{queue_id}")
async def youtube_queue_update(queue_id: int, req: QueueUpdateRequest, request: Request):
    """Update a queued post. Only editable while status is 'queued'.

    Send only the fields you want to change.
    """
    from src.memory.database import get_db
    # CF-1: strict self-scope — the ownership gate is this status SELECT;
    # the UPDATE below runs on the same verified id in the same transaction.
    from src.dashboard.routes.action_board import _acting_presence_id
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse(status_code=404, content={"error": "Queue entry not found"})
    scope_sql = "" if pid is None else " AND presence_id = %s"
    scope_args = () if pid is None else (pid,)

    try:
        async with get_db() as conn:
            # Check current status
            result = await conn.execute(
                f"SELECT status FROM youtube_queue WHERE id = %s{scope_sql}",
                (queue_id,) + scope_args,
            )
            row = await result.fetchone()

            if not row:
                return JSONResponse(status_code=404, content={"error": "Queue entry not found"})

            old_status = row["status"]

            if old_status not in ("draft", "queued", "failed"):
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": f"Cannot edit — status is '{old_status}'. Only 'draft', 'queued', or 'failed' posts can be edited.",
                    },
                )

            # Build dynamic UPDATE from provided fields
            updates = {}
            for field, value in req.model_dump(exclude_unset=True).items():
                if field == "tags":
                    updates["tags"] = json.dumps(value)
                elif field in ("upload_date", "publish_date") and value:
                    converted = local_to_utc(value)
                    print(f"[youtube] PATCH date debug: {field} raw='{value}' → converted='{converted}'")
                    updates[field] = converted
                else:
                    updates[field] = value

            if not updates:
                return {"status": "no_changes", "id": queue_id}

            # Re-queue of a FAILED post = a retry: clear the old error and any
            # stale upload marker so the processor picks it up cleanly. Failed
            # rows used to be a dead end (409 on edit) — the operator's only
            # path after a fixed misconfig was recreating the whole card.
            if old_status == "failed" and updates.get("status") == "queued":
                updates["error_message"] = None
                updates["uploaded_at"] = None

            set_clause = ", ".join(f"{k} = %({k})s" for k in updates)
            updates["id"] = queue_id

            await conn.execute(
                f"UPDATE youtube_queue SET {set_clause} WHERE id = %(id)s",
                updates,
            )

            # Calendar sync — create/update on queued, delete on draft
            new_status = updates.get("status", old_status)
            dates_changed = "upload_date" in updates or "publish_date" in updates

            if new_status == "queued" and (old_status != "queued" or dates_changed):
                # Create or update calendar event (PUT is idempotent — overwrites if exists)
                result = await conn.execute(
                    "SELECT title, upload_date, publish_date, series, presence_id "
                    "FROM youtube_queue WHERE id = %s",
                    (queue_id,),
                )
                post = await result.fetchone()
                if post and post["upload_date"]:
                    await create_youtube_calendar_event(
                        queue_id, post["title"], post["upload_date"],
                        post["publish_date"], post["series"] or "",
                        presence_id=post.get("presence_id"),
                    )
            elif new_status == "draft" and old_status == "queued":
                _r = await conn.execute(
                    "SELECT presence_id FROM youtube_queue WHERE id = %s", (queue_id,))
                _row = await _r.fetchone()
                await delete_youtube_calendar_event(
                    queue_id, presence_id=(_row or {}).get("presence_id"))

        now = now_app().strftime("%Y-%m-%d %H:%M %Z")
        print(f"[{now}] [youtube] Queue updated: #{queue_id} fields={list(updates.keys())}")

        return {"status": "updated", "id": queue_id, "fields": list(updates.keys())}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to update queue entry: {str(e)}"},
        )


@router.delete("/api/youtube/queue/{queue_id}")
async def youtube_queue_cancel(queue_id: int, request: Request):
    """Cancel a queued post. Sets status to 'cancelled'.

    Only works on posts with status 'queued'. Already uploaded posts
    must be managed in YouTube Studio.
    """
    from src.memory.database import get_db
    # CF-1: strict self-scope — the ownership gate is this status SELECT;
    # the UPDATE below runs on the same verified id in the same transaction.
    from src.dashboard.routes.action_board import _acting_presence_id
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse(status_code=404, content={"error": "Queue entry not found"})
    scope_sql = "" if pid is None else " AND presence_id = %s"
    scope_args = () if pid is None else (pid,)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                f"SELECT status, presence_id FROM youtube_queue WHERE id = %s{scope_sql}",
                (queue_id,) + scope_args,
            )
            row = await result.fetchone()

            if not row:
                return JSONResponse(status_code=404, content={"error": "Queue entry not found"})

            if row["status"] not in ("draft", "queued"):
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": f"Cannot cancel — status is '{row['status']}'. Only 'draft' or 'queued' posts can be cancelled.",
                    },
                )

            await conn.execute(
                "UPDATE youtube_queue SET status = 'cancelled' WHERE id = %s",
                (queue_id,),
            )

        # Remove calendar event if it existed
        await delete_youtube_calendar_event(
            queue_id, presence_id=(row.get("presence_id") if hasattr(row, "get") else None))

        now = now_app().strftime("%Y-%m-%d %H:%M %Z")
        print(f"[{now}] [youtube] Queue cancelled: #{queue_id}")

        return {"status": "cancelled", "id": queue_id}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to cancel queue entry: {str(e)}"},
        )
