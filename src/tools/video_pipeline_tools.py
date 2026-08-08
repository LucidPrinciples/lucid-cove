"""Presence video-pipeline tools (#VP-ATLAS1).

Read-only state for personal agents (Atlas and every Cove personal agent) so they
can diagnose and operate their own content pipeline without host Docker or raw
ad-hoc SQL.

Loaded for every presence via _PRESENCE_DEFAULT_MODULES (same upgrade path as
project_tools / research_tools). Chat already binds the acting presence\'s NC
creds and project presence_id; these tools reuse that scope.

No publish/write actions here — diagnose + report first.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from langchain_core.tools import tool

from src.tools.approval import auto
from src.video_stems import stem_from_transcript_name

logger = logging.getLogger(__name__)

_VIDEO_ROOT = "AgentSkills/Content/video"
_MASTER_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".mkv")


def _acting_presence_id() -> str | None:
    """Reuse project-tools presence binding (set on personal-agent chat turns)."""
    try:
        from src.tools.project_tools import _acting_presence_id as _pid

        return _pid()
    except Exception:
        return None


def _stem_from_master(filename: str) -> str:
    name = filename or ""
    lower = name.lower()
    for ext in _MASTER_EXTS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


async def _nc_list(path: str) -> list[str]:
    """List basenames under a Nextcloud path using the bound presence creds."""
    try:
        from src.tools import nextcloud_tools as nc

        fn = nc.nextcloud_list
        raw = await (fn.coroutine if hasattr(fn, "coroutine") else fn)(path=path)
    except Exception as e:
        return [f"__error__:{e}"]
    if not isinstance(raw, str):
        return []
    if raw.startswith("Error") or raw.startswith("Access denied"):
        return [f"__error__:{raw}"]
    names: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("Empty") or line.startswith("("):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0] in ("📄", "📁", "📃", "📦", "-", "*"):
            name = parts[1].strip().rstrip("/")
        elif line.startswith("[") and "]" in line:
            name = line.split("]", 1)[-1].strip().rstrip("/")
        else:
            name = line.rstrip("/")
        # nextcloud_list appends " (1,234 bytes)"
        if " (" in name and name.endswith(")"):
            name = name.rsplit(" (", 1)[0].strip()
        # Skip the "Contents of path:" header line
        if name.lower().startswith("contents of "):
            continue
        if name.endswith(" is empty"):
            continue
        names.append(name)
    return [n for n in names if n and not n.startswith("__error__")]


async def _nc_list_or_err(subdir: str) -> tuple[list[str], str | None]:
    path = f"{_VIDEO_ROOT}/{subdir}"
    names = await _nc_list(path)
    errs = [n for n in names if n.startswith("__error__:")]
    if errs:
        return [], errs[0].split(":", 1)[-1]
    clean = [n for n in names if n not in (".", "..") and not n.startswith("__error__")]
    return clean, None


async def _nc_read_json(rel_under_video: str) -> dict | None:
    path = f"{_VIDEO_ROOT}/{rel_under_video}"
    try:
        from src.tools import nextcloud_tools as nc

        fn = nc.nextcloud_read
        raw = await (fn.coroutine if hasattr(fn, "coroutine") else fn)(path=path)
    except Exception as e:
        logger.warning("video tool read %s failed: %s", path, e)
        return None
    if not isinstance(raw, str) or raw.startswith("Error") or raw.startswith("Access denied"):
        return None
    text = raw.strip()
    if text.startswith("{"):
        body = text
    else:
        idx = text.find("{")
        if idx < 0:
            return None
        body = text[idx:]
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _has_processed(stem: str, short_names: set[str]) -> bool:
    if f"{stem}-moments-processed.json" in short_names:
        return True
    prefix = f"{stem}-"
    for n in short_names:
        if not n.startswith(prefix):
            continue
        if "-preview" in n:
            continue
        if n.endswith(".mp4"):
            return True
    return False


def _on_active_pipeline_list(
    *,
    in_inbox: bool,
    in_processing: bool,
    has_transcript: bool,
    has_processed: bool,
) -> bool:
    """Mirror full-video-pipeline.html merge rules (as of #VP-ATLAS1)."""
    if in_processing or in_inbox:
        return True
    if has_transcript and not has_processed:
        return True
    return False


@auto
@tool
async def video_pipeline_status(include_hidden: str = "true") -> str:
    """Snapshot this presence\'s video pipeline folders and active-list visibility.

    Use when diagnosing pending list gaps, History vs pipeline disagreement, or
    "where did this stem go?" for the logged-in presence only.

    Args:
        include_hidden: \'true\' (default) also lists transcript stems the UI hides
            after any short exists (has_processed). \'false\' = active-list only.
    """
    show_hidden = str(include_hidden or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    pid = _acting_presence_id()
    folders: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for sub in ("inbox", "processing", "raw", "transcripts", "shorts", "moments", "to-delete"):
        names, err = await _nc_list_or_err(sub)
        if err:
            errors[sub] = err
            folders[sub] = []
        else:
            folders[sub] = names

    inbox_stems: dict[str, str] = {}
    for n in folders.get("inbox", []):
        if n.lower().endswith(_MASTER_EXTS) or (not n.endswith(".json") and "." in n):
            inbox_stems[_stem_from_master(n)] = n

    proc_stems: dict[str, str] = {}
    for n in folders.get("processing", []):
        if n.lower().endswith(_MASTER_EXTS) or (not n.endswith(".json") and "." in n):
            proc_stems[_stem_from_master(n)] = n

    raw_stems: dict[str, str] = {}
    for n in folders.get("raw", []):
        if n.lower().endswith(_MASTER_EXTS) or (not n.endswith(".json") and "." in n):
            raw_stems[_stem_from_master(n)] = n

    short_names = set(folders.get("shorts", []) or [])
    tnames = folders.get("transcripts", []) or []
    transcript_stems: set[str] = set()
    moments_stems: set[str] = set()
    edited_stems: set[str] = set()
    for n in tnames:
        if n.endswith("-transcript.json") and not n.endswith("-edited.json"):
            transcript_stems.add(stem_from_transcript_name(n))
        elif n.endswith("-moments.json") and not n.endswith("-moments-processed.json"):
            moments_stems.add(n[: -len("-moments.json")])
        elif n.endswith("-transcript-edited.json"):
            edited_stems.add(n[: -len("-transcript-edited.json")])

    all_stems = sorted(
        set(inbox_stems) | set(proc_stems) | set(raw_stems) | transcript_stems | moments_stems
    )

    rows = []
    active = 0
    hidden = 0
    for stem in all_stems:
        in_inbox = stem in inbox_stems
        in_proc = stem in proc_stems
        in_raw = stem in raw_stems
        has_t = stem in transcript_stems
        has_m = stem in moments_stems
        has_e = stem in edited_stems
        has_p = _has_processed(stem, short_names)
        on_list = _on_active_pipeline_list(
            in_inbox=in_inbox,
            in_processing=in_proc,
            has_transcript=has_t,
            has_processed=has_p,
        )
        if on_list:
            active += 1
        elif has_t or has_m or has_p:
            hidden += 1
        if not show_hidden and not on_list:
            continue
        folder = (
            "processing"
            if in_proc
            else "inbox"
            if in_inbox
            else "raw"
            if in_raw
            else "transcripts"
            if has_t
            else "unknown"
        )
        rows.append(
            {
                "stem": stem,
                "folder": folder,
                "master": proc_stems.get(stem) or inbox_stems.get(stem) or raw_stems.get(stem),
                "in_inbox": in_inbox,
                "in_processing": in_proc,
                "in_raw": in_raw,
                "has_transcript": has_t,
                "has_moments_json": has_m,
                "has_edits": has_e,
                "has_processed": has_p,
                "on_active_pipeline_list": on_list,
                "ui_hidden_reason": (
                    None
                    if on_list
                    else (
                        "has_processed_transcript_only"
                        if (has_t and has_p and not in_inbox and not in_proc)
                        else "no_master_no_transcript"
                        if not has_t and not in_inbox and not in_proc
                        else "not_on_list"
                    )
                ),
            }
        )

    payload = {
        "presence_id": pid,
        "video_root": _VIDEO_ROOT,
        "folder_counts": {k: len(v) for k, v in folders.items()},
        "folder_errors": errors or None,
        "active_pipeline_count": active,
        "hidden_processed_count": hidden,
        "stems": rows,
        "product_rule": (
            "Active Video Pipeline list = masters in inbox/ or processing/, plus "
            "transcript-only stems where has_processed is false. Once any short "
            "exists and the master left inbox/processing, the stem drops off the "
            "active list even if many moments clips remain."
        ),
    }
    return json.dumps(payload, indent=2, default=str)


@auto
@tool
async def video_moments_map(stem: str) -> str:
    """Load the moments plan map for one stem (done / left / skipped per clip).

    Reads AgentSkills/Content/video/transcripts/{stem}-moments.json for this
    presence. Use when Moments UI says "No moments" or History map counts look wrong.

    Args:
        stem: Master stem, e.g. IMG_7168 (no extension).
    """
    stem = (stem or "").strip()
    if not stem or not all(c.isalnum() or c in ("_", "-") for c in stem) or len(stem) > 80:
        return json.dumps({"error": "invalid stem", "stem": stem})
    data = await _nc_read_json(f"transcripts/{stem}-moments.json")
    try:
        from src.dashboard.routes.action_board import _summarize_moments_map
    except Exception as e:
        return json.dumps({"error": f"summarize unavailable: {e}", "stem": stem})
    summary = _summarize_moments_map(data, stem)
    if not summary.get("has_map"):
        summary["hint"] = (
            f"No transcripts/{stem}-moments.json (or unreadable). "
            "UI Moments shows \'No moments\'. Run Analyze or check analyze job failures."
        )
    short_names, _ = await _nc_list_or_err("shorts")
    summary["has_processed_marker"] = _has_processed(stem, set(short_names or []))
    summary["shorts_matching_prefix"] = sorted(
        n for n in (short_names or []) if n.startswith(f"{stem}-")
    )[:40]
    return json.dumps(summary, indent=2, default=str)


def _row_to_dict(row: Any) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


@auto
@tool
async def video_queue_status(
    source_stem: str = "",
    platform: str = "",
    status: str = "",
    limit: int = 40,
) -> str:
    """List this presence\'s youtube_queue and social_queue rows (read-only).

    Scoped to the acting presence_id from the chat session. Use for History vs
    board forensics and publish-state checks — not for other people\'s queues.

    Args:
        source_stem: Optional master stem filter (exact).
        platform: Optional social platform filter (youtube, x, tiktok, ...).
        status: Optional status filter (draft, queued, uploaded, published, failed, ...).
        limit: Max rows per table (default 40, max 100).
    """
    pid = _acting_presence_id()
    if not pid:
        return json.dumps(
            {
                "error": "no_presence_scope",
                "hint": (
                    "video_queue_status needs the personal-agent chat presence binding. "
                    "Use this from Atlas (or another personal agent) while logged in as "
                    "that presence — not from an unbound background turn."
                ),
            }
        )
    lim = max(1, min(int(limit or 40), 100))
    stem = (source_stem or "").strip() or None
    plat = (platform or "").strip().lower() or None
    st = (status or "").strip().lower() or None

    yt_rows: list[dict] = []
    soc_rows: list[dict] = []
    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            yt_sql = """
                SELECT id, title, status, source_stem, file_path, series,
                       upload_date, publish_date, youtube_video_id, youtube_url,
                       error_message, is_short, presence_id, created_at, updated_at
                FROM youtube_queue
                WHERE presence_id::text = %s
            """
            yt_params: list[Any] = [str(pid)]
            if stem:
                yt_sql += " AND source_stem = %s"
                yt_params.append(stem)
            if st:
                yt_sql += " AND status = %s"
                yt_params.append(st)
            yt_sql += " ORDER BY COALESCE(updated_at, created_at) DESC NULLS LAST LIMIT %s"
            yt_params.append(lim)
            res = await conn.execute(yt_sql, tuple(yt_params))
            for row in await res.fetchall():
                yt_rows.append(_row_to_dict(row))

            sq_sql = """
                SELECT id, platform, title, status, source_stem, file_path, series,
                       clip_type, clip_label, moment_id, upload_date, publish_date,
                       error_message, presence_id, agent_id, created_at, updated_at
                FROM social_queue
                WHERE (presence_id::text = %s OR agent_id::text = %s)
            """
            sq_params: list[Any] = [str(pid), str(pid)]
            if stem:
                sq_sql += " AND source_stem = %s"
                sq_params.append(stem)
            if plat:
                sq_sql += " AND platform = %s"
                sq_params.append(plat)
            if st:
                sq_sql += " AND status = %s"
                sq_params.append(st)
            sq_sql += " ORDER BY COALESCE(updated_at, created_at) DESC NULLS LAST LIMIT %s"
            sq_params.append(lim)
            res = await conn.execute(sq_sql, tuple(sq_params))
            for row in await res.fetchall():
                soc_rows.append(_row_to_dict(row))
    except Exception as e:
        return json.dumps({"error": str(e), "presence_id": pid})

    def _tally(rows: list[dict], key: str = "status") -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for r in rows:
            out[str(r.get(key) or "?")] += 1
        return dict(out)

    return json.dumps(
        {
            "presence_id": pid,
            "filters": {"source_stem": stem, "platform": plat, "status": st, "limit": lim},
            "youtube_queue": {"count": len(yt_rows), "by_status": _tally(yt_rows), "rows": yt_rows},
            "social_queue": {"count": len(soc_rows), "by_status": _tally(soc_rows), "rows": soc_rows},
        },
        indent=2,
        default=str,
    )


@auto
@tool
async def video_jobs_recent(limit: int = 25, state: str = "") -> str:
    """Recent video_jobs registry rows (transcribe/analyze/render) — read-only.

    Cove-wide job table (not presence-columned). Useful when a Moments run or
    render failed and the UI only showed a spinner. Prefer pairing with
    video_pipeline_status for stem truth.

    Args:
        limit: Max jobs (default 25, max 80).
        state: Optional filter: queued, running, done, failed.
    """
    lim = max(1, min(int(limit or 25), 80))
    st = (state or "").strip().lower() or None
    try:
        from src.memory.database import get_db

        async with get_db() as conn:
            sql = """
                SELECT job_id, kind, state, phase, error,
                       created_at, started_at, finished_at, updated_at
                FROM video_jobs
            """
            params: list[Any] = []
            if st:
                sql += " WHERE state = %s"
                params.append(st)
            sql += " ORDER BY COALESCE(updated_at, to_timestamp(created_at)) DESC NULLS LAST LIMIT %s"
            params.append(lim)
            res = await conn.execute(sql, tuple(params))
            rows = [_row_to_dict(r) for r in await res.fetchall()]
    except Exception as e:
        return json.dumps({"error": str(e), "hint": "video_jobs table may be missing on older DBs"})
    return json.dumps({"count": len(rows), "jobs": rows}, indent=2, default=str)


TOOLS = [
    video_pipeline_status,
    video_moments_map,
    video_queue_status,
    video_jobs_recent,
]
ALL_VIDEO_PIPELINE_TOOLS = TOOLS


def get_tools() -> list:
    return list(TOOLS)
