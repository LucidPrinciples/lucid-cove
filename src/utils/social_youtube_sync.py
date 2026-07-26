"""Keep Action Board social_queue in step with youtube_queue.

Board cards live in social_queue. The real YouTube uploader writes
youtube_queue only. After a successful upload (or Mark Published) the
social row used to stay status=queued forever — the watcher then fired
"stuck past its upload time" even though Studio already had the video.

Link key: social_queue.platform_data.youtube_queue_id → youtube_queue.id
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Terminal / in-flight youtube states that mean social must not stay "queued".
_YT_DONE = frozenset({"uploaded", "published", "failed", "cancelled"})


def _pdata(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def sync_social_for_youtube_queue(
    conn,
    youtube_queue_id: int,
    *,
    status: str,
    youtube_video_id: str | None = None,
    youtube_url: str | None = None,
    error_message: str | None = None,
) -> int:
    """Mirror a youtube_queue terminal/progress state onto linked social rows.

    Returns number of social_queue rows updated.
    """
    if not youtube_queue_id or not status:
        return 0
    status = str(status).strip().lower()
    if status not in ("uploading", "uploaded", "published", "failed", "cancelled"):
        return 0

    res = await conn.execute(
        """SELECT id, status, platform_data FROM social_queue
           WHERE platform = 'youtube'
             AND status NOT IN ('cancelled')
             AND (platform_data->>'youtube_queue_id') = %s""",
        (str(int(youtube_queue_id)),),
    )
    rows = await res.fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        pdata = _pdata(row.get("platform_data"))
        if youtube_video_id:
            pdata["youtube_video_id"] = youtube_video_id
        if youtube_url:
            pdata["youtube_url"] = youtube_url
        if youtube_queue_id:
            pdata["youtube_queue_id"] = int(youtube_queue_id)

        sets = ["status = %s", "platform_data = %s::jsonb", "updated_at = NOW()"]
        args: list = [status, json.dumps(pdata)]

        if status == "uploading":
            pass
        elif status == "uploaded":
            sets.append("uploaded_at = COALESCE(uploaded_at, NOW())")
            sets.append("error_message = NULL")
        elif status == "published":
            sets.append("uploaded_at = COALESCE(uploaded_at, NOW())")
            sets.append("published_at = COALESCE(published_at, NOW())")
            sets.append("error_message = NULL")
        elif status == "failed":
            sets.append("error_message = %s")
            args.append((error_message or "")[:500] or "youtube upload failed")
        elif status == "cancelled":
            pass

        # Don't regress a published social row back to uploaded.
        cur = (row.get("status") or "").lower()
        if cur == "published" and status in ("uploaded", "uploading"):
            continue
        if cur == status and status != "failed":
            # Still refresh platform_data (ids/urls) if needed
            await conn.execute(
                f"UPDATE social_queue SET platform_data = %s::jsonb, updated_at = NOW() WHERE id = %s",
                (json.dumps(pdata), row["id"]),
            )
            updated += 1
            continue

        args.append(row["id"])
        await conn.execute(
            f"UPDATE social_queue SET {', '.join(sets)} WHERE id = %s",
            tuple(args),
        )
        updated += 1
        logger.info(
            "social_youtube_sync: social #%s %s → %s (yt_queue #%s)",
            row["id"], cur, status, youtube_queue_id,
        )
    return updated


async def heal_orphaned_social_youtube(conn) -> int:
    """One-shot style heal: social youtube still queued/uploading while linked
    youtube_queue already finished (uploaded/published/failed/cancelled).

    Also catches rows whose linked yt id is set but social never flipped.
    Returns rows healed.
    """
    res = await conn.execute(
        """SELECT s.id AS social_id, s.status AS social_status, s.platform_data,
                  y.id AS yt_id, y.status AS yt_status,
                  y.youtube_video_id, y.youtube_url, y.error_message
           FROM social_queue s
           JOIN youtube_queue y
             ON (s.platform_data->>'youtube_queue_id') = y.id::text
           WHERE s.platform = 'youtube'
             AND s.status IN ('queued', 'uploading')
             AND y.status IN ('uploaded', 'published', 'failed', 'cancelled')"""
    )
    rows = await res.fetchall()
    n = 0
    for row in rows:
        n += await sync_social_for_youtube_queue(
            conn,
            int(row["yt_id"]),
            status=row["yt_status"],
            youtube_video_id=row.get("youtube_video_id"),
            youtube_url=row.get("youtube_url"),
            error_message=row.get("error_message"),
        )
    return n
