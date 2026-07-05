"""
Action Board API — serves individual action items for the Actions tab.

Each action item is one clickable card. Sources:
  - youtube_queue (draft/queued shorts to process)
  - tasks table (follow-up items from completed uploads)
  - wizard providers (any tool/flow with multi-step wizard progress)

Cards only appear when items exist for that category.

## Wizard Provider System

Any cove-core tool or Creation Flow can show progress cards on the Actions tab
by registering a wizard provider. A provider is an async function that returns
a list of wizard action dicts. Each dict includes steps with per-step page URLs
so the frontend routes generically — no tool-specific JS needed.

Register with: `register_wizard_provider(name, async_fn)`

Provider function signature: `async def my_provider(request) -> list[dict]`

Each dict must include:
  - id: unique string
  - title: display name
  - source: provider name (for grouping)
  - icon: emoji
  - steps: list of {id, label, done, page} — page is full URL with params

The `page` field on each step is what makes this generic. The frontend opens
whatever URL the step says. No FLOW_PAGES lookup, no tool-specific routing.
"""

import json
import logging
import os
from src.env import env, env_bool
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _acting_presence_id(request) -> str | None:
    """CF-1 strict self-scope helper for youtube_queue / social_queue UI surfaces.

    Every UI list/edit of the two queues shows ONLY the acting presence's
    rows — admin and stewards are just presences with their own queues.
    Background processors are Cove machinery and never call this.

    Returns:
      None -> single-Cove mode: no scoping (behave exactly as today)
      ''   -> multi mode but no resolvable presence: match NOTHING
      id   -> scope every queue read/write to this presence
    """
    if env("COVE_MODE", "single") != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
    except Exception:
        return ""
    return str(p["id"]) if p and p.get("id") else ""


def _scope_clause(pid):
    """(sql_suffix, extra_args) for a CF-1 scoped query. pid=None -> no-op."""
    if pid is None:
        return "", ()
    return " AND presence_id = %s", (pid,)


# ── Wizard Provider Registry ─────────────────────────────────────────
# Tools register here. The action board calls all providers on every load.

_wizard_providers: dict[str, callable] = {}


def register_wizard_provider(name: str, provider_fn):
    """Register a wizard provider function.

    provider_fn: async def(request) -> list[dict]
    Each dict: {id, title, description, urgency, source, icon, steps[]}
    Each step: {id, label, done, page}
    """
    _wizard_providers[name] = provider_fn
    logger.info(f"Wizard provider registered: {name}")


# ── Built-in providers ────────────────────────────────────────────────

async def _site_builder_provider(request: Request) -> list:
    """Site Builder — incomplete wizard cards with step chips."""
    actions = []
    try:
        from src.dashboard.routes.sites import _list_sites_internal
        sites = await _list_sites_internal(request)

        STEPS = [
            {"id": "domain",             "label": "Domain"},
            {"id": "site-type",          "label": "Type"},
            {"id": "create-site",        "label": "Create"},
            {"id": "site-structure",     "label": "Structure"},
            {"id": "logo",               "label": "Logo"},
            {"id": "visual-design",      "label": "Design"},
            {"id": "connect-github",     "label": "GitHub"},
            {"id": "connect-cloudflare", "label": "Cloudflare"},
        ]
        BASE_URL = "/static/action-board/site-builder.html"

        for site in sites:
            if site.get("status") != "setup":
                continue
            ws = site.get("wizard_state") or {}
            completed = set(ws.get("completed", []))
            param = f"domain={site['domain']}"

            steps = []
            for s in STEPS:
                steps.append({
                    "id": s["id"],
                    "label": s["label"],
                    "done": s["id"] in completed,
                    "page": f"{BASE_URL}?{param}&step={s['id']}",
                })

            remaining = len([s for s in steps if not s["done"]])
            # Card click goes to first incomplete step
            first_pending = next((s for s in steps if not s["done"]), steps[0])

            actions.append({
                "id": f"wizard-site-{site['domain']}",
                "title": site["domain"],
                "description": f"{remaining} steps remaining",
                "urgency": "high",
                "source": "site-builder",
                "category": "wizard",
                "icon": "🔧",
                "type": "wizard-resume",
                "default_page": first_pending["page"],
                "steps": steps,
            })
    except Exception:
        pass
    return actions


async def _video_pipeline_provider(request: Request) -> list:
    """Video Pipeline — transcribed videos with step chips."""
    actions = []
    try:
        video_base = env("VIDEO_BASE_PATH", "/vault/AgentSkills/Content/video")
        tdir = os.path.join(video_base, "transcripts")
        if not os.path.isdir(tdir):
            return actions

        files = set(os.listdir(tdir))
        shorts_dir = os.path.join(video_base, "shorts")
        shorts_files = set(os.listdir(shorts_dir)) if os.path.isdir(shorts_dir) else set()

        for f in sorted(files):
            if not f.endswith("-transcript.json"):
                continue
            stem = f.replace("-transcript.json", "")
            has_edits = f"{stem}-transcript-edited.json" in files
            has_moments = f"{stem}-moments.json" in files
            has_clips = f"{stem}-moments-processed.json" in shorts_files

            # Check if all moments are fully processed (no unprocessed clips remain)
            all_processed = False
            if has_moments:
                try:
                    import json as json_mod
                    moments_path = os.path.join(tdir, f"{stem}-moments.json")
                    with open(moments_path) as mf:
                        mdata = json_mod.load(mf)
                    unprocessed = 0
                    for m in mdata.get("moments", []):
                        for c in m.get("clips", []):
                            if not c.get("processed"):
                                unprocessed += 1
                    all_processed = unprocessed == 0 and len(mdata.get("moments", [])) > 0
                except Exception:
                    pass

            # Fully done — all clips processed, hide the card
            if all_processed:
                continue

            param = f"stem={stem}&presence=operator"
            steps = [
                {"id": "transcribe", "label": "Transcribe",     "done": True,
                 "page": None},
                {"id": "edit",       "label": "Edit",            "done": has_edits,
                 "page": f"/static/action-board/video-transcript-editor.html?{param}"},
                {"id": "moments",    "label": "Moments",         "done": has_moments,
                 "page": f"/static/action-board/video-moments-review.html?stem={stem}"},
                {"id": "crop",       "label": "Crop & Caption",  "done": has_clips,
                 "page": f"/static/action-board/video-crop-position.html?stem={stem}"},
                {"id": "process",    "label": "Process",         "done": has_clips,
                 "page": None},
            ]

            remaining = len([s for s in steps if not s["done"]])
            first_pending = next(
                (s for s in steps if not s["done"] and s["page"]),
                steps[2],  # moments review as re-enter point
            )

            actions.append({
                "id": f"video-pipeline-{stem}",
                "title": stem,
                "description": "Moments remaining" if has_clips else f"{remaining} steps remaining",
                "urgency": "normal",
                "source": "video-pipeline",
                "category": "wizard",
                "icon": "🎬",
                "type": "wizard-resume",
                "default_page": first_pending["page"],
                "steps": steps,
            })
    except Exception:
        pass
    return actions


async def _video_shorts_provider(request: Request) -> list:
    """Processed video clips ready for review and scheduling."""
    import json as json_mod
    actions = []
    try:
        video_base = env("VIDEO_BASE_PATH", "/vault/AgentSkills/Content/video")
        shorts_dir = os.path.join(video_base, "shorts")
        if not os.path.isdir(shorts_dir):
            return actions

        for f in sorted(os.listdir(shorts_dir)):
            if not f.endswith("-moments-processed.json"):
                continue

            manifest_path = os.path.join(shorts_dir, f)
            try:
                with open(manifest_path) as mf:
                    manifest = json_mod.load(mf)
            except Exception:
                continue

            stem = manifest.get("stem", f.replace("-moments-processed.json", ""))
            processed = manifest.get("processed", [])

            FORMAT_LABELS_MANIFEST = {"vertical": "9:16", "horizontal": "16:9", "square": "1:1"}

            for clip in processed:
                clip_file = clip.get("filename", "")
                preview_file = clip.get("preview_filename", "")
                clip_type = clip.get("clip_type", "clip")
                label = clip.get("label", clip_file)
                duration = clip.get("duration_seconds", 0)
                fmt = clip.get("format", "vertical" if clip.get("vertical", True) else "horizontal")
                moment_id = clip.get("moment_id", 0)

                # Preview link for review (falls back to full-res if no preview)
                review_file = preview_file or clip_file
                preview_url = f"/api/video/proxy/stream?filename={review_file}" if review_file else ""

                # Type badge color
                type_colors = {"quote": "#5ce1e6", "thought": "#e6b43c", "story": "#a064e6"}
                badge_color = type_colors.get(clip_type, "#888")

                dur_label = f"{int(duration)}s" if duration < 60 else f"{int(duration//60)}m {int(duration%60)}s"
                orientation = FORMAT_LABELS_MANIFEST.get(fmt, fmt)

                actions.append({
                    "id": f"short-{stem}-m{moment_id}-{clip_type}-{fmt}",
                    "title": label,
                    "description": f"{clip_type.capitalize()} · {dur_label} · {orientation}",
                    "urgency": "normal",
                    "source": "video-shorts",
                    "category": "video",
                    "icon": "📹",
                    "type": "link",
                    "url": preview_url,
                    "metadata": {
                        "stem": stem,
                        "clip_file": clip_file,
                        "preview_file": preview_file,
                        "clip_type": clip_type,
                        "duration": duration,
                        "format": fmt,
                        "badge_color": badge_color,
                    },
                })

    except Exception:
        pass
    return actions


# Register built-in providers
register_wizard_provider("site-builder", _site_builder_provider)
register_wizard_provider("video-pipeline", _video_pipeline_provider)


# ── Helpers ──────────────────────────────────────────────────────────

def _fmt_date(dt) -> str:
    """Format a datetime for display in the Presence's timezone."""
    if not dt:
        return ""
    if hasattr(dt, "strftime"):
        from src.utils.time_utils import utc_to_local
        local_dt = utc_to_local(dt)
        return local_dt.strftime("%b %d, %I:%M %p")
    return str(dt)


# ── Main actions endpoint ────────────────────────────────────────────

def _is_public_app() -> bool:
    """The shared multi-tenant app (registry master) has no agents/video pipeline.
    Its YouTube/social Action Board sections are a Cove feature — gate them off
    here (#leak), but keep all human-driven actions/tools/Connect/Market."""
    import os
    return env_bool("LP_REGISTRY_MASTER")


class _SkipPublic(Exception):
    """Internal: bail out of a Cove-only Action Board section on the public app."""


async def _get_presence_id(request):
    """Resolve the caller's presence id in multi mode. Returns None in single
    mode (behavior then unchanged — no extra scoping)."""
    if env("COVE_MODE", "single") != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        return p["id"] if p else None
    except Exception:
        return None


@router.get("/api/action-board/actions")
async def get_actions(request: Request):
    """Return individual action items for the Actions tab.

    Each item becomes its own clickable card. Grouped by source/category
    but returned as a flat list. Only items that need attention appear.

    Wizard providers are called automatically — any registered tool/flow
    that has in-progress work will show up here with step chips.
    """
    actions = []

    # CF-1: strict self-scope — every queue list below shows only the acting
    # presence's rows in multi mode. None = single mode (no filtering).
    pid = await _acting_presence_id(request)
    scope_sql, scope_args = _scope_clause(pid)

    # ── YouTube Shorts: only items needing attention (Cove video pipeline) ──
    try:
        from src.memory.database import get_db
        if _is_public_app() or pid == "":
            raise _SkipPublic

        async with get_db() as conn:
            # CF-1: strict self-scope
            result = await conn.execute(
                f"""SELECT id, title, description, tags, hashtags, file_path,
                          series, card_id, upload_date, publish_date, status,
                          related_video, created_at
                   FROM youtube_queue
                   WHERE status = 'draft'{scope_sql}
                   ORDER BY publish_date ASC""",
                scope_args,
            )
            rows = await result.fetchall()

            for row in rows:
                series_labels = {"ras": "RAS", "hltb": "How LT Was Built", "hltagb": "How LT Got Built"}
                actions.append({
                    "id": f"yt-short-{row['id']}",
                    "queue_id": row["id"],
                    "title": row["title"],
                    "description": f"Draft — {_fmt_date(row['publish_date'])}",
                    "urgency": "normal",
                    "source": "youtube",
                    "category": "youtube-short",
                    "icon": "📺",
                    "series": series_labels.get(row["series"], row["series"] or ""),
                    "status": row["status"],
                    "type": "youtube-short",
                })

            # CF-1: strict self-scope
            result = await conn.execute(
                f"""SELECT id, title, error_message FROM youtube_queue
                   WHERE status = 'failed'{scope_sql} ORDER BY updated_at DESC""",
                scope_args,
            )
            for row in await result.fetchall():
                actions.append({
                    "id": f"yt-failed-{row['id']}",
                    "queue_id": row["id"],
                    "title": row["title"],
                    "description": f"Failed: {row['error_message'] or 'unknown error'}",
                    "urgency": "high",
                    "source": "youtube",
                    "category": "youtube-short",
                    "icon": "⚠",
                    "status": "failed",
                    "type": "youtube-short",
                })
    except Exception:
        pass

    # ── Social Queue: draft moments ready for review/scheduling (Cove pipeline) ──
    try:
        from src.memory.database import get_db
        if _is_public_app() or pid == "":  # CF-1: no presence in multi mode -> nothing
            raise _SkipPublic

        platform_meta = {
            "youtube":   {"category": "youtube-short", "icon": "📺"},
            "tiktok":    {"category": "tiktok", "icon": "🎵"},
            "x":         {"category": "x-post", "icon": "𝕏"},
            "instagram": {"category": "instagram", "icon": "📸"},
            "facebook":  {"category": "facebook", "icon": "📘"},
        }

        async with get_db() as conn:
            # CF-1: strict self-scope
            result = await conn.execute(
                f"""SELECT id, platform, title, description, file_path, preview_path,
                          source_stem, moment_id, clip_type, clip_label,
                          duration_seconds, is_vertical, format, series, status, created_at
                   FROM social_queue
                   WHERE status IN ('draft', 'failed'){scope_sql}
                   ORDER BY created_at ASC""",
                scope_args,
            )
            rows = await result.fetchall()

            FORMAT_LABELS = {"vertical": "9:16", "horizontal": "16:9", "square": "1:1"}

            for row in rows:
                plat = row["platform"]
                meta = platform_meta.get(plat, {"category": plat, "icon": "📹"})
                dur = row.get("duration_seconds") or 0
                dur_label = f"{int(dur)}s" if dur < 60 else f"{int(dur//60)}m {int(dur%60)}s"
                clip_type = row.get("clip_type") or ""
                is_failed = row["status"] == "failed"
                fmt = row.get("format") or ("vertical" if row.get("is_vertical") else "horizontal")
                fmt_label = FORMAT_LABELS.get(fmt, fmt)

                # Preview link for review
                preview = row.get("preview_path") or row.get("file_path") or ""
                preview_file = preview.rsplit("/", 1)[-1] if preview else ""

                actions.append({
                    "id": f"sq-{plat}-{row['id']}",
                    "queue_id": row["id"],
                    "title": row["title"],
                    "description": f"{clip_type.capitalize()} · {dur_label} · {fmt_label}" if not is_failed else "Failed",
                    "urgency": "high" if is_failed else "normal",
                    "source": f"social-{plat}",
                    "category": meta["category"],
                    "icon": meta["icon"],
                    "status": row["status"],
                    "type": "social",
                    "format": fmt,
                })
    except _SkipPublic:
        pass
    except Exception as e:
        logger.warning(f"social_queue read failed (table may not exist yet): {e}")

    # ── Follow-up tasks (all sources) ───────────────────────────────
    try:
        from src.memory.database import get_db

        presence_id = await _get_presence_id(request)

        async with get_db() as conn:
            if presence_id:
                result = await conn.execute(
                    """SELECT id, title, description, source, notes FROM tasks
                       WHERE status = 'pending' AND presence_id = %s
                       ORDER BY created_at ASC""",
                    (presence_id,),
                )
            else:
                result = await conn.execute(
                    """SELECT id, title, description, source, notes FROM tasks
                       WHERE status = 'pending' ORDER BY created_at ASC"""
                )
            source_meta = {
                "youtube-queue": {"category": "youtube-studio", "icon": "🎬"},
                "wizard":        {"category": "wizard", "icon": "🔧"},
            }
            for row in await result.fetchall():
                src = row.get("source") or "internal"
                meta = source_meta.get(src, {"category": src, "icon": "📋"})
                actions.append({
                    "id": f"task-{row['id']}",
                    "task_id": row["id"],
                    "title": row["title"],
                    "description": row["description"] or "",
                    "urgency": "normal",
                    "source": src,
                    "category": meta["category"],
                    "icon": meta["icon"],
                    "type": "task",
                })
    except Exception:
        pass

    # ── Wizard providers (tools + flows with multi-step progress) ──
    for name, provider_fn in _wizard_providers.items():
        try:
            provider_actions = await provider_fn(request)
            actions.extend(provider_actions)
        except Exception as e:
            logger.warning(f"Wizard provider '{name}' failed: {e}")

    return {"actions": actions}


@router.post("/api/action-board/tasks")
async def create_task(request: Request):
    """Create a follow-up task (used by wizard engine for deferred steps).

    Body: { title, description, source?, notes? }
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO tasks (title, description, status, source, notes, presence_id)
                   VALUES (%s, %s, 'pending', %s, %s, %s)
                   RETURNING id, created_at""",
                (
                    title,
                    body.get("description", ""),
                    body.get("source", "wizard"),
                    body.get("notes", ""),
                    presence_id,
                ),
            )
            row = await result.fetchone()
            return {"id": row["id"], "created_at": row["created_at"].isoformat() if row["created_at"] else None}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/action-board/scheduled")
async def get_scheduled(request: Request):
    """Return items in the YouTube pipeline — queued, uploading, uploaded.

    These are NOT actions (they don't need input). They're in-flight items
    the operator can monitor and cancel if needed before upload_date.
    """
    if _is_public_app():
        return {"scheduled": [], "count": 0}

    # CF-1: strict self-scope
    pid = await _acting_presence_id(request)
    if pid == "":
        return {"scheduled": [], "count": 0}
    scope_sql, scope_args = _scope_clause(pid)
    scheduled = []

    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT id, title, series, status, upload_date, publish_date,
                          youtube_video_id, youtube_url, uploaded_at
                   FROM youtube_queue
                   WHERE status IN ('queued', 'uploading', 'uploaded'){scope_sql}
                   ORDER BY upload_date ASC""",
                scope_args,
            )
            rows = await result.fetchall()

            series_labels = {
                "ras": "RAS",
                "hltb": "How LT Was Built",
                "hltagb": "How LT Got Built",
            }

            for row in rows:
                subtitle = ""
                if row["status"] == "queued":
                    subtitle = f"Uploads {_fmt_date(row['upload_date'])}"
                elif row["status"] == "uploading":
                    subtitle = "Uploading now..."
                elif row["status"] == "uploaded":
                    subtitle = f"Goes public {_fmt_date(row['publish_date'])}"

                scheduled.append({
                    "id": row["id"],
                    "title": row["title"],
                    "series": series_labels.get(row["series"], row["series"] or ""),
                    "status": row["status"],
                    "subtitle": subtitle,
                    "upload_date": row["upload_date"].isoformat() if row["upload_date"] else None,
                    "publish_date": row["publish_date"].isoformat() if row["publish_date"] else None,
                    "youtube_url": row["youtube_url"],
                    "youtube_video_id": row["youtube_video_id"],
                })

    except Exception:
        pass

    return {"scheduled": scheduled, "count": len(scheduled)}


@router.get("/api/action-board/actions/{queue_id}")
async def get_action_detail(queue_id: int, request: Request):
    """Get full detail for a single youtube_queue item (for the action overlay)."""
    if _is_public_app():
        return JSONResponse({"error": "not found"}, status_code=404)

    # CF-1: strict self-scope — a miss on someone else's row is a plain 404
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse({"error": "not found"}, status_code=404)
    scope_sql, scope_args = _scope_clause(pid)
    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT id, title, description, tags, hashtags, file_path,
                          series, card_id, upload_date, publish_date, status,
                          related_video, category_id, made_for_kids, is_short,
                          playlist_id, thumbnail_path, created_at
                   FROM youtube_queue WHERE id = %s{scope_sql}""",
                (queue_id,) + scope_args,
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            return {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "tags": row["tags"] if isinstance(row["tags"], list) else json.loads(row["tags"] or "[]"),
                "hashtags": row["hashtags"],
                "file_path": row["file_path"],
                "series": row["series"],
                "card_id": row["card_id"],
                "upload_date": row["upload_date"].isoformat() if row["upload_date"] else None,
                "publish_date": row["publish_date"].isoformat() if row["publish_date"] else None,
                "status": row["status"],
                "related_video": row["related_video"],
                "category_id": row["category_id"],
                "made_for_kids": row["made_for_kids"],
                "is_short": row["is_short"],
            }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Social Queue CRUD ────────────────────────────────────────────────

@router.get("/api/action-board/social/{item_id}")
async def get_social_detail(item_id: int, request: Request):
    """Get full detail for a social_queue item."""
    if _is_public_app():
        return JSONResponse({"error": "not found"}, status_code=404)

    # CF-1: strict self-scope — a miss on someone else's row is a plain 404
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse({"error": "not found"}, status_code=404)
    scope_sql, scope_args = _scope_clause(pid)
    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT id, platform, title, description, tags, hashtags,
                          file_path, preview_path, source_stem, moment_id,
                          clip_type, clip_label, duration_seconds, is_vertical,
                          format, upload_date, publish_date, status, series,
                          platform_data, created_at
                   FROM social_queue WHERE id = %s{scope_sql}""",
                (item_id,) + scope_args,
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            preview = row.get("preview_path") or row.get("file_path") or ""
            preview_file = preview.rsplit("/", 1)[-1] if preview else ""

            return {
                "id": row["id"],
                "platform": row["platform"],
                "title": row["title"],
                "description": row["description"],
                "tags": row["tags"] if isinstance(row["tags"], list) else json.loads(row["tags"] or "[]"),
                "hashtags": row["hashtags"],
                "file_path": row["file_path"],
                "preview_path": row["preview_path"],
                "preview_file": preview_file,
                "source_stem": row["source_stem"],
                "clip_type": row["clip_type"],
                "duration_seconds": row["duration_seconds"],
                "is_vertical": row["is_vertical"],
                "format": row.get("format") or ("vertical" if row["is_vertical"] else "horizontal"),
                "upload_date": row["upload_date"].isoformat() if row["upload_date"] else None,
                "publish_date": row["publish_date"].isoformat() if row["publish_date"] else None,
                "status": row["status"],
                "series": row["series"],
                "platform_data": row["platform_data"] if isinstance(row["platform_data"], dict) else json.loads(row["platform_data"] or "{}"),
            }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/action-board/social/{item_id}")
async def update_social_item(item_id: int, request: Request):
    """Update a social_queue item — edit metadata, schedule, change status."""
    if _is_public_app():
        return JSONResponse({"ok": False, "error": "unavailable"}, status_code=404)

    # CF-1: strict self-scope — only the acting presence's row is editable
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse({"error": "not found"}, status_code=404)
    scope_sql, scope_args = _scope_clause(pid)
    try:
        body = await request.json()
        from src.memory.database import get_db

        allowed = {
            "title", "description", "hashtags", "tags", "upload_date",
            "publish_date", "status", "platform_data",
        }
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = json.dumps(updates["tags"])
        if "platform_data" in updates and isinstance(updates["platform_data"], dict):
            updates["platform_data"] = json.dumps(updates["platform_data"])

        # Dates from the board are the Presence's LOCAL time. Convert to UTC the
        # same way the YouTube path does (resolves tz via the Presence cascade —
        # cove-core default → Stuart → presence override). Skipping this stored
        # local time as if it were UTC, throwing everything off by the tz offset.
        from src.utils.time_utils import local_to_utc
        for _df in ("upload_date", "publish_date"):
            if updates.get(_df):
                updates[_df] = local_to_utc(updates[_df])

        set_parts = [f"{k} = %s" for k in updates]
        values = list(updates.values()) + [item_id]

        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE social_queue SET {', '.join(set_parts)} WHERE id = %s{scope_sql}",
                tuple(values) + scope_args,
            )
            if pid is not None and result.rowcount == 0:
                # CF-1: not this presence's row (or missing) -> not found
                return JSONResponse({"error": "not found"}, status_code=404)

            # ── Promote a scheduled YouTube post into youtube_queue ──────────
            # The board only writes social_queue. The uploader, calendar event,
            # and post-upload follow-up tasks all hang off youtube_queue, so a
            # scheduled youtube post must be mirrored there or nothing fires.
            # Idempotent: the youtube_queue id is stored back in platform_data
            # so re-scheduling updates the same row instead of duplicating.
            promoted_id = await _promote_youtube_post(conn, item_id, pid)

        result = {"ok": True, "updated": list(updates.keys())}
        if promoted_id:
            result["youtube_queue_id"] = promoted_id
            result["queued"] = True
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _promote_youtube_post(conn, item_id: int, presence_id: str | None = None):
    """If a social_queue item is a YouTube post now queued with both dates,
    mirror it into youtube_queue + create the calendar event. Returns the
    youtube_queue id, or None if not applicable. Safe to call on every update.

    CF-1: the caller has already verified the social_queue row belongs to the
    acting presence; the mirrored youtube_queue row is stamped with the same
    presence_id so it stays visible to its owner in multi mode.
    """
    res = await conn.execute(
        """SELECT platform, title, description, tags, hashtags, file_path,
                  is_vertical, format, thumbnail_path, series, upload_date,
                  publish_date, status, platform_data
           FROM social_queue WHERE id = %s""",
        (item_id,),
    )
    s = await res.fetchone()
    if not s or s["platform"] != "youtube" or s["status"] != "queued":
        return None
    if not s["upload_date"] or not s["publish_date"]:
        return None

    pdata = s["platform_data"] if isinstance(s["platform_data"], dict) \
        else json.loads(s["platform_data"] or "{}")
    yq_id = pdata.get("youtube_queue_id")
    tags_val = s["tags"] if isinstance(s["tags"], str) else json.dumps(s["tags"] or [])
    is_short = (s["format"] == "vertical") or bool(s["is_vertical"])

    # The uploader's CONTENT_ROOT is /content (the Content folder itself), but
    # social_queue stores vault-relative paths ("AgentSkills/Content/..."). Strip
    # that prefix so the file resolves under CONTENT_ROOT.
    fpath = s["file_path"] or ""
    for _pfx in ("AgentSkills/Content/", "Content/"):
        if fpath.startswith(_pfx):
            fpath = fpath[len(_pfx):]
            break

    if yq_id:
        # Reschedule / update the existing queue row
        await conn.execute(
            """UPDATE youtube_queue
               SET title=%s, description=%s, tags=%s::jsonb, hashtags=%s,
                   file_path=%s, is_short=%s, thumbnail_path=%s,
                   upload_date=%s, publish_date=%s, series=%s, status='queued'
               WHERE id=%s""",
            (s["title"], s["description"], tags_val, s["hashtags"], fpath,
             is_short, s["thumbnail_path"], s["upload_date"], s["publish_date"],
             s["series"], yq_id),
        )
    else:
        res2 = await conn.execute(
            """INSERT INTO youtube_queue
                  (title, description, tags, hashtags, file_path, category_id,
                   made_for_kids, is_short, thumbnail_path, upload_date,
                   publish_date, series, status, presence_id)
               VALUES (%s,%s,%s::jsonb,%s,%s,'22',false,%s,%s,%s,%s,%s,'queued',%s)
               RETURNING id""",
            (s["title"], s["description"], tags_val, s["hashtags"], fpath,
             is_short, s["thumbnail_path"], s["upload_date"], s["publish_date"],
             s["series"], presence_id or None),
        )
        yq_id = (await res2.fetchone())["id"]
        pdata["youtube_queue_id"] = yq_id
        await conn.execute(
            "UPDATE social_queue SET platform_data=%s WHERE id=%s",
            (json.dumps(pdata), item_id),
        )

    try:
        from src.dashboard.routes.youtube_calendar import create_youtube_calendar_event
        await create_youtube_calendar_event(
            yq_id, s["title"], s["upload_date"], s["publish_date"], s["series"] or "",
        )
    except Exception as e:
        logger.warning(f"Promote {item_id}: queued #{yq_id} but calendar event failed: {e}")

    logger.info(f"Promoted social_queue #{item_id} → youtube_queue #{yq_id} (queued)")
    return yq_id


@router.delete("/api/action-board/social/{item_id}")
async def delete_social_item(item_id: int, request: Request):
    """Cancel/remove a social_queue item."""
    if _is_public_app():
        return JSONResponse({"ok": False, "error": "unavailable"}, status_code=404)

    # CF-1: strict self-scope — only the acting presence's row is cancellable
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    scope_sql, scope_args = _scope_clause(pid)
    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE social_queue SET status = 'cancelled' WHERE id = %s{scope_sql}",
                (item_id,) + scope_args,
            )
            if pid is not None and result.rowcount == 0:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/action-board/tasks/{task_id}")
async def complete_task(task_id: int, request: Request):
    """Mark a Studio follow-up task as done.

    Transitions status from 'pending' to 'done'. The task stops appearing
    in the Actions tab on next refresh.
    """
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            if presence_id:
                result = await conn.execute(
                    """UPDATE tasks SET status = 'done'
                       WHERE id = %s AND status = 'pending' AND presence_id = %s
                       RETURNING id""",
                    (task_id, presence_id),
                )
            else:
                result = await conn.execute(
                    """UPDATE tasks SET status = 'done'
                       WHERE id = %s AND status = 'pending'
                       RETURNING id""",
                    (task_id,),
                )
            row = await result.fetchone()

            if not row:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Task not found or already completed."},
                )

        return {"status": "done", "id": task_id}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to complete task: {str(e)}"},
        )


@router.patch("/api/action-board/scheduled/{queue_id}/publish")
async def mark_published(queue_id: int, request: Request):
    """Mark an uploaded video as published.

    Clears it from the Scheduled section. Only works on items
    with status 'uploaded' — the video is already on YouTube,
    this just acknowledges it went public.
    """
    if _is_public_app():
        return JSONResponse({"error": "not found"}, status_code=404)
    from src.memory.database import get_db

    # CF-1: strict self-scope — a miss on someone else's row is the 404 below
    pid = await _acting_presence_id(request)
    if pid == "":
        return JSONResponse(
            status_code=404,
            content={"error": "Item not found or not in 'uploaded' status."},
        )
    scope_sql, scope_args = _scope_clause(pid)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                f"""UPDATE youtube_queue
                   SET status = 'published', published_at = NOW()
                   WHERE id = %s AND status = 'uploaded'{scope_sql}
                   RETURNING id""",
                (queue_id,) + scope_args,
            )
            row = await result.fetchone()

            if not row:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Item not found or not in 'uploaded' status."},
                )

        return {"status": "published", "id": queue_id}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to mark published: {str(e)}"},
        )


# =============================================================================
# Links board — per-operator editable link cards
# =============================================================================
# A card = {"id", "title", "url", "note", "icon", "group"}. Stored as
# {"cards": [...]}. Multi mode -> accounts.preferences["action_links"]
# (per-operator). Single mode (Cove) -> DATA_DIR/action-links.json (cove-wide).
# Agents can write the same store later via the same endpoint.

from pathlib import Path as _Path

_LINKS_KEY = "action_links"
_LINKS_FILE = _Path(env("DATA_DIR", "/app/data")) / "action-links.json"


def _new_link_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def _sanitize_links(payload) -> dict:
    """Coerce arbitrary input into the safe {cards:[...]} shape (XSS-safe URLs)."""
    raw = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raw = []
    cards = []
    for c in raw[:200]:  # hard cap
        if not isinstance(c, dict):
            continue
        title = str(c.get("title", "") or "").strip()[:120]
        url = str(c.get("url", "") or "").strip()[:1000]
        note = str(c.get("note", "") or "").strip()[:200]
        icon = str(c.get("icon", "") or "").strip()[:8]
        group = str(c.get("group", "") or "").strip()[:60]
        if not (title or url):
            continue
        low = url.lower()
        if low.startswith("javascript:") or low.startswith("data:") or low.startswith("vbscript:"):
            url = ""
        elif low and not (low.startswith("http://") or low.startswith("https://") or low.startswith("/")):
            url = "https://" + url
        cid = str(c.get("id", "") or "").strip()[:40] or _new_link_id()
        cards.append({"id": cid, "title": title, "url": url, "note": note, "icon": icon, "group": group})
    return {"cards": cards}


def _default_links() -> list:
    """Primary links every Cove starts with (the operator can edit/remove). The
    Backlog is the catch-all driver (everything to organize, distinct from the
    time-sensitive Attention board); jules feeds it; Cloud is the files home."""
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".")
    except Exception:
        dom = ""
    return [
        {"id": "backlog", "title": "Backlog", "url": "/backlog",
         "note": "Everything to organize, by workflow", "icon": "🗂", "group": ""},
        {"id": "jules", "title": "jules", "url": "/jules",
         "note": "Capture by voice → your Inbox", "icon": "🎙", "group": ""},
        {"id": "cloud", "title": "Cloud", "url": (f"https://cloud.{dom}" if dom else "/files"),
         "note": "Your files", "icon": "☁", "group": ""},
    ]


@router.get("/api/action-board/links")
async def get_links(request: Request):
    """Return the operator's link cards (cove-wide in single mode)."""
    try:
        from src.dashboard.routes.presence import get_current_presence, COVE_MODE
        if COVE_MODE == "multi":
            presence = await get_current_presence(request)
            if not presence:
                return {"cards": [], "editable": False}
            prefs = presence.get("preferences") or {}
            if isinstance(prefs, str):
                try:
                    prefs = json.loads(prefs)
                except Exception:
                    prefs = {}
            cards = _sanitize_links(prefs.get(_LINKS_KEY) or {})["cards"] or _default_links()
            return {"cards": cards, "editable": True}
        # single-mode Cove: file-backed, cove-wide
        data = {}
        if _LINKS_FILE.exists():
            try:
                data = json.loads(_LINKS_FILE.read_text())
            except Exception:
                data = {}
        return {"cards": _sanitize_links(data)["cards"] or _default_links(), "editable": True}
    except Exception as e:
        logger.warning("[links] get failed: %s", e)
        return {"cards": [], "editable": True}


@router.put("/api/action-board/links")
async def save_links(request: Request):
    """Save the operator's link cards. Replaces the full set."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    clean = _sanitize_links(body)
    try:
        from src.dashboard.routes.presence import get_current_presence, COVE_MODE
        from src.memory.database import get_db
        if COVE_MODE == "multi":
            presence = await get_current_presence(request)
            if not presence:
                return JSONResponse(status_code=401, content={"error": "Not authenticated"})
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE accounts
                       SET preferences = COALESCE(preferences, '{}'::jsonb) || %s::jsonb,
                           updated_at = NOW()
                       WHERE id = %s""",
                    (json.dumps({_LINKS_KEY: clean}), presence["id"]),
                )
            return {"ok": True, "count": len(clean["cards"])}
        # single-mode Cove: write the cove-wide file
        _LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LINKS_FILE.write_text(json.dumps(clean, indent=2))
        return {"ok": True, "count": len(clean["cards"])}
    except Exception as e:
        logger.error("[links] save failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Failed to save links"})
