"""Video session open-work model (#VP-SESS-LIFE1).

One original recording = one session (keyed by master stem). This module is the
shared definition of "what still needs attention" — folder phase, plan progress,
and publish/queue buckets — without moving files.

Operator lifecycle (locked 2026-08-08):
  inbox/       until transcribed AND has a moments plan (or explicit skip-moments)
  processing/  until every cared-about moment is processed AND publish settled
  raw/         only when the session is fully done
  History      only when the session is clear

Folder moves stay gated until callers opt in with this done definition.
Graduate-on-first-short and hide-because-shorts are out.
"""
from __future__ import annotations

from typing import Any


# Terminal publish statuses that no longer need operator attention for that row.
_SETTLED_STATUSES = frozenset({"published", "cancelled"})
# Still in flight on the board (not History).
_OPEN_QUEUE_STATUSES = frozenset(
    {"draft", "queued", "uploading", "uploaded", "failed"}
)


def moments_plan_progress(moments_data: Any) -> dict:
    """Derive clip counts from a moments.json dict (shared list/session rule).

    Skipped clips do not count as left. Empty/unreadable → incomplete.
    """
    if not isinstance(moments_data, dict):
        return {
            "clip_count": None,
            "clips_done": None,
            "clips_left": None,
            "clips_skipped": None,
            "moments_complete": False,
        }
    clip_count = left = done = skipped = 0
    for moment in moments_data.get("moments") or []:
        if not isinstance(moment, dict):
            continue
        for clip in moment.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            clip_count += 1
            if clip.get("skipped"):
                skipped += 1
                continue
            if clip.get("processed"):
                done += 1
            else:
                left += 1
    return {
        "clip_count": clip_count,
        "clips_done": done,
        "clips_left": left,
        "clips_skipped": skipped,
        "moments_complete": bool(clip_count > 0 and left == 0),
    }


def shorts_belong_to_stem(stem: str, filename: str) -> bool:
    """Reject dual-stem pollution (e.g. IMG_7171-clean-* under parent IMG_7171)."""
    if not stem or not filename or not filename.startswith(f"{stem}-"):
        return False
    rest = filename[len(stem) + 1 :]
    if rest.startswith("clean-") or rest.startswith("clean."):
        return False
    return True


def has_processed_outputs(stem: str, short_names: set[str] | list[str]) -> bool:
    """True when shorts/ has a non-preview product for this stem (badge only)."""
    names = set(short_names or [])
    if f"{stem}-moments-processed.json" in names:
        return True
    for n in names:
        if not shorts_belong_to_stem(stem, n):
            continue
        if "-preview" in n:
            continue
        if n.endswith(".mp4"):
            return True
    return False


def folder_phase(
    *,
    in_inbox: bool,
    in_processing: bool,
    in_raw: bool,
) -> str:
    """Where the master currently lives (processing wins over dual inbox)."""
    if in_processing:
        return "processing"
    if in_inbox:
        return "inbox"
    if in_raw:
        return "raw"
    return "none"


def session_phase(
    *,
    folder: str,
    has_transcript: bool,
    has_moments: bool,
    skip_moments: bool,
    moments_complete: bool,
    clips_left: int | None,
    queue_open: int,
    queue_uploaded: int,
    queue_scheduled: int,
) -> str:
    """Coarse session stage for Open Work / list copy.

    Not the same as folder_phase — a stem can sit in raw/ with clips still left.
    """
    if not has_transcript:
        if folder == "inbox":
            return "needs_transcript"
        if folder in ("processing", "raw", "none"):
            return "needs_transcript"
        return "needs_transcript"

    if skip_moments and not has_moments:
        # Whole-video path: plan is the single full clip once cropped.
        if not moments_complete and (clips_left is None or clips_left > 0):
            if folder == "inbox":
                return "ready_to_crop"
            return "crop_or_caption"

    if not has_moments and not skip_moments:
        return "needs_moments_plan"

    if has_moments and not moments_complete:
        return "clips_remaining"

    # Plan complete (or skip path finished products) — publish tail
    if queue_scheduled > 0:
        return "scheduled"
    if queue_uploaded > 0:
        return "uploaded_awaiting_publish"
    if queue_open > 0:
        return "drafts_open"

    if moments_complete or (skip_moments and has_processed_hint(clips_left, moments_complete)):
        return "clear"

    return "in_progress"


def has_processed_hint(clips_left: int | None, moments_complete: bool) -> bool:
    """Internal: skip-moments with no plan file still needs a complete signal."""
    if moments_complete:
        return True
    return clips_left == 0


def is_session_clear(
    *,
    has_transcript: bool,
    has_moments: bool,
    skip_moments: bool,
    moments_complete: bool,
    queue_open: int,
    queue_uploaded: int,
    queue_scheduled: int,
) -> bool:
    """True when History is appropriate and folder may graduate to raw/.

    Requires transcript + (moments plan complete OR skip-moments finished) and
    no open/scheduled/uploaded-not-published queue rows for the stem.
    """
    if queue_open or queue_uploaded or queue_scheduled:
        return False
    if not has_transcript:
        return False
    if skip_moments and not has_moments:
        # Skip path without a moments.json: treat as clear only when caller
        # already marked moments_complete (e.g. whole clip processed).
        return bool(moments_complete)
    if not has_moments:
        return False
    return bool(moments_complete)


def may_graduate_to_raw(
    *,
    has_transcript: bool,
    has_moments: bool,
    skip_moments: bool,
    moments_complete: bool,
    queue_open: int,
    queue_uploaded: int,
    queue_scheduled: int,
    in_processing: bool,
) -> bool:
    """Folder move gate — processing → raw only when session is clear."""
    if not in_processing:
        return False
    return is_session_clear(
        has_transcript=has_transcript,
        has_moments=has_moments,
        skip_moments=skip_moments,
        moments_complete=moments_complete,
        queue_open=queue_open,
        queue_uploaded=queue_uploaded,
        queue_scheduled=queue_scheduled,
    )


def classify_queue_row(status: str | None) -> str:
    """Map a queue status into open-work buckets."""
    st = (status or "").strip().lower()
    if st in _SETTLED_STATUSES:
        return "settled"
    if st == "uploaded":
        return "uploaded"
    if st in ("queued", "uploading"):
        return "scheduled"
    if st in ("draft", "failed"):
        return "open"
    if st in _OPEN_QUEUE_STATUSES:
        return "open"
    return "other"


def build_session_snapshot(
    *,
    stem: str,
    in_inbox: bool = False,
    in_processing: bool = False,
    in_raw: bool = False,
    has_transcript: bool = False,
    has_edits: bool = False,
    has_moments: bool = False,
    skip_moments: bool = False,
    moments_data: Any = None,
    has_processed: bool = False,
    queue_rows: list[dict] | None = None,
) -> dict:
    """Aggregate one session for Open Work API / Actions UI.

    queue_rows: optional list of {status, title?, id?, platform?, session_role?}
    """
    prog = moments_plan_progress(moments_data if has_moments else None)
    # Unreadable moments file → keep incomplete
    if has_moments and moments_data is None:
        prog = {
            "clip_count": None,
            "clips_done": None,
            "clips_left": None,
            "clips_skipped": None,
            "moments_complete": False,
        }

    # skip-moments / whole video: no multi-clip plan; complete when processed badge
    # and caller set skip_moments (UI whole=1 path).
    moments_complete = bool(prog.get("moments_complete"))
    if skip_moments and not has_moments:
        moments_complete = bool(has_processed)

    buckets = {
        "open": 0,
        "scheduled": 0,
        "uploaded": 0,
        "settled": 0,
        "other": 0,
    }
    for row in queue_rows or []:
        if not isinstance(row, dict):
            continue
        bucket = classify_queue_row(row.get("status"))
        buckets[bucket] = buckets.get(bucket, 0) + 1

    folder = folder_phase(
        in_inbox=in_inbox, in_processing=in_processing, in_raw=in_raw
    )
    queue_open = buckets["open"]
    queue_scheduled = buckets["scheduled"]
    queue_uploaded = buckets["uploaded"]

    clear = is_session_clear(
        has_transcript=has_transcript,
        has_moments=has_moments,
        skip_moments=skip_moments,
        moments_complete=moments_complete,
        queue_open=queue_open,
        queue_uploaded=queue_uploaded,
        queue_scheduled=queue_scheduled,
    )
    phase = session_phase(
        folder=folder,
        has_transcript=has_transcript,
        has_moments=has_moments,
        skip_moments=skip_moments,
        moments_complete=moments_complete,
        clips_left=prog.get("clips_left"),
        queue_open=queue_open,
        queue_uploaded=queue_uploaded,
        queue_scheduled=queue_scheduled,
    )
    if clear:
        phase = "clear"

    graduate_ok = may_graduate_to_raw(
        has_transcript=has_transcript,
        has_moments=has_moments,
        skip_moments=skip_moments,
        moments_complete=moments_complete,
        queue_open=queue_open,
        queue_uploaded=queue_uploaded,
        queue_scheduled=queue_scheduled,
        in_processing=in_processing,
    )

    # Next operator action + deep links (relative MC paths)
    next_action, links = _next_action_and_links(
        stem=stem,
        phase=phase,
        has_edits=has_edits,
        has_moments=has_moments,
        skip_moments=skip_moments,
        clips_left=prog.get("clips_left"),
        moments_complete=moments_complete,
        queue_open=queue_open,
        queue_uploaded=queue_uploaded,
        queue_scheduled=queue_scheduled,
    )

    return {
        "stem": stem,
        "folder": folder,
        "phase": phase,
        "has_transcript": has_transcript,
        "has_edits": has_edits,
        "has_moments": has_moments,
        "skip_moments": bool(skip_moments),
        "has_processed": bool(has_processed),
        "clip_count": prog.get("clip_count"),
        "clips_done": prog.get("clips_done"),
        "clips_left": prog.get("clips_left"),
        "clips_skipped": prog.get("clips_skipped"),
        "moments_complete": moments_complete,
        "queue": {
            "open": queue_open,
            "scheduled": queue_scheduled,
            "uploaded": queue_uploaded,
            "settled": buckets["settled"],
        },
        "is_clear": clear,
        "may_graduate_to_raw": graduate_ok,
        "on_open_work": not clear,
        "next_action": next_action,
        "links": links,
    }


def _next_action_and_links(
    *,
    stem: str,
    phase: str,
    has_edits: bool,
    has_moments: bool,
    skip_moments: bool,
    clips_left: int | None,
    moments_complete: bool,
    queue_open: int,
    queue_uploaded: int,
    queue_scheduled: int,
) -> tuple[str, dict]:
    """Human next step + deep-link map for Actions Open Work cards."""
    param = f"stem={stem}"
    pipeline = f"/static/action-board/full-video-pipeline.html?{param}"
    edit = f"/static/action-board/video-transcript-editor.html?{param}"
    moments = f"/static/action-board/video-moments-review.html?{param}"
    # plan=1: Crop loads remaining unprocessed plan clips (no Moments approve gate).
    crop = f"/static/action-board/video-crop-position.html?{param}&plan=1"
    crop_whole = f"/static/action-board/video-crop-position.html?{param}&whole=1"

    links = {
        "pipeline": pipeline,
        "edit": edit,
        "moments": moments,
        "crop": crop,
        "crop_whole": crop_whole,
    }

    if phase == "needs_transcript":
        return "Transcribe", {"primary": pipeline, **links}
    if phase == "needs_moments_plan":
        if not has_edits:
            return "Edit transcript", {"primary": edit, **links}
        return "Find moments", {"primary": moments, **links}
    if phase in ("ready_to_crop", "crop_or_caption") and skip_moments:
        return "Crop full video", {"primary": crop_whole, **links}
    if phase == "clips_remaining":
        left = clips_left if clips_left is not None else "?"
        return f"Cut remaining clips ({left} left)", {"primary": crop, **links}
    if phase == "drafts_open" or queue_open:
        return "Finish drafts", {"primary": None, **links}
    if phase == "scheduled" or queue_scheduled:
        return "Waiting on schedule", {"primary": None, **links}
    if phase == "uploaded_awaiting_publish" or queue_uploaded:
        return "Mark published", {"primary": None, **links}
    if phase == "clear" or moments_complete:
        return "Session clear", {"primary": pipeline, **links}
    return "Continue pipeline", {"primary": pipeline, **links}


def summarize_open_work(sessions: list[dict]) -> dict:
    """Roll-up counts for the Open Work tab badge / empty states."""
    open_sessions = [s for s in sessions if s.get("on_open_work")]
    by_phase: dict[str, int] = {}
    for s in open_sessions:
        p = s.get("phase") or "in_progress"
        by_phase[p] = by_phase.get(p, 0) + 1
    return {
        "open_count": len(open_sessions),
        "clear_count": sum(1 for s in sessions if s.get("is_clear")),
        "total": len(sessions),
        "by_phase": by_phase,
        "clips_left_total": sum(
            int(s["clips_left"])
            for s in open_sessions
            if s.get("clips_left") is not None
        ),
    }


def apply_moments_plan_updates(moments_data: Any, updates: list) -> dict:
    """Apply Moments editor Save rows onto a moments.json dict (MOMSAVE1).

    Mutates moments_data in place. Returns counts: changed, approved, skipped.
    """
    if not isinstance(moments_data, dict):
        return {"changed": 0, "approved": 0, "skipped": 0}
    by_key: dict = {}
    for row in updates or []:
        if not isinstance(row, dict):
            continue
        mid = row.get("moment_id", row.get("momentId"))
        ctype = row.get("clip_type", row.get("type"))
        if mid is None or not ctype:
            continue
        by_key[(mid, ctype)] = row
    if not by_key:
        return {"changed": 0, "approved": 0, "skipped": 0}

    changed = approved_n = skipped_n = 0
    for moment in moments_data.get("moments") or []:
        if not isinstance(moment, dict):
            continue
        mid = moment.get("id")
        for clip in moment.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            row = by_key.get((mid, clip.get("type")))
            if not row:
                continue
            if clip.get("processed") and not clip.get("skipped"):
                continue
            skipped = bool(row.get("skipped"))
            approved = bool(row.get("approved")) and not skipped
            if skipped:
                clip["skipped"] = True
                clip["processed"] = True
                clip["approved"] = False
                skipped_n += 1
            else:
                if clip.get("skipped"):
                    clip["skipped"] = False
                    if clip.get("processed") and not row.get("keep_processed"):
                        clip["processed"] = False
                clip["approved"] = approved
                if approved:
                    approved_n += 1
            start = row.get("start_seconds", row.get("start"))
            end = row.get("end_seconds", row.get("end"))
            try:
                if start is not None:
                    clip["start"] = float(start)
                    clip["start_seconds"] = float(start)
                if end is not None:
                    clip["end"] = float(end)
                    clip["end_seconds"] = float(end)
                if start is not None and end is not None:
                    clip["duration_seconds"] = round(float(end) - float(start), 1)
            except (TypeError, ValueError):
                pass
            changed += 1
    return {"changed": changed, "approved": approved_n, "skipped": skipped_n}

