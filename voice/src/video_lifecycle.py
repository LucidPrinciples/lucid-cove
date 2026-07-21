"""Video file lifecycle — C4 graduation (batch8 #1, DECISION LOCKED 2026-07-03).

The state machine:
  inbox/       untouched originals
  processing/  the original, from transcribe-time until the pipeline finishes
  raw/         FINISHED originals — the original GRADUATES processing → raw when
               captioned-full completes successfully for that stem
  shorts/ + transcripts/   outputs (unchanged)

processed/, done/, captioned/, clips/ are RETIRED FROM WRITES — the resolver keeps
READING them for legacy files, but nothing writes them again. (Audit 2026-07-03:
no current writer targets any of the four; only shorts/, transcripts/, and
processing/ are written. So there was nothing to stop — this module only adds the
graduation half.)

Graduation is BEST-EFFORT: it logs and never fails the render. The original is
family source material — it is MOVED, never auto-deleted. Covers both the NC path
(WebDAV MOVE) and the local-mount path (rename processing → raw).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Source-video extensions the pipeline accepts, in probe order.
_VIDEO_EXTS = (".MOV", ".mov", ".mp4", ".MP4", ".m4v", ".M4V", ".avi", ".mkv")


def _video_mount() -> str:
    return os.environ.get("VIDEO_MOUNT", "/video")


async def graduate_processing_to_raw(stem: str, nc=None, video_mount: str = None) -> bool:
    """Move the finished original {stem}.<ext> from processing/ → raw/. Best-effort:
    returns True on a successful graduation, False otherwise, and NEVER raises — a
    graduation failure must not fail the render that triggered it.

    nc set   → WebDAV MOVE within the presence's video tree.
    nc None  → local VIDEO_MOUNT rename (founder mount).
    """
    try:
        if nc is not None:
            for ext in _VIDEO_EXTS:
                name = f"{stem}{ext}"
                # Only try to move what's actually in processing/ (pull-probe is too
                # heavy here); MOVE of a missing source just returns non-2xx, which we
                # treat as "not this ext" and keep looking.
                if await nc.move(f"processing/{name}", f"raw/{name}"):
                    logger.info(f"[lifecycle] graduated processing/{name} → raw/ (NC)")
                    return True
            logger.info(f"[lifecycle] no processing/ original to graduate for stem {stem} (NC)")
            return False

        base = video_mount or _video_mount()
        proc_dir = os.path.join(base, "processing")
        raw_dir = os.path.join(base, "raw")
        for ext in _VIDEO_EXTS:
            name = f"{stem}{ext}"
            src = os.path.join(proc_dir, name)
            if os.path.isfile(src):
                os.makedirs(raw_dir, exist_ok=True)
                os.replace(src, os.path.join(raw_dir, name))
                logger.info(f"[lifecycle] graduated processing/{name} → raw/ (local mount)")
                return True
        logger.info(f"[lifecycle] no processing/ original to graduate for stem {stem} (local)")
        return False
    except Exception as e:
        # NEVER fail the render on a graduation hiccup.
        logger.warning(f"[lifecycle] graduation skipped for stem {stem}: {e}")
        return False


# ── To-Delete retirement (no hard-delete of user content) ──────────
#
# Operator policy 2026-07-20: never destroy family/source material in place.
# Anything the product would have deleted is MOVED under to-delete/ so the
# operator can offload to external backup or empty later when notified of size.
# Temp ffmpeg scratch (ass/preview under /tmp) may still os.remove — not user files.

TO_DELETE_DIR = "to-delete"


def _safe_retire_name(original_subpath: str) -> str:
    """Flatten a video-tree subpath into to-delete/<stamp>__<rel> so collisions
    and nested names stay recoverable. Keeps extension."""
    import time
    rel = (original_subpath or "file").replace("\\", "/").lstrip("/")
    parts = [seg for seg in rel.split("/") if seg and seg != ".."]
    rel = "/".join(parts) or "file"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = rel.replace("/", "__")
    return f"{TO_DELETE_DIR}/{stamp}__{base}"


async def retire_to_delete(
    subpath: str,
    nc=None,
    video_mount: str = None,
) -> dict:
    """Move <video-tree>/<subpath> → to-delete/ instead of destroying it.

    Returns {ok, method, dest, reason?}. Never raises.
    nc set  → WebDAV MOVE inside the presence video tree.
    nc None → local rename under VIDEO_MOUNT.
    """
    try:
        rel = (subpath or "").replace("\\", "/").lstrip("/")
        parts = [seg for seg in rel.split("/") if seg and seg != ".."]
        rel = "/".join(parts)
        if not rel or rel.startswith(TO_DELETE_DIR + "/"):
            return {"ok": False, "method": "none", "dest": "", "reason": "invalid subpath"}

        dest_rel = _safe_retire_name(rel)

        if nc is not None:
            ok = await nc.move(rel, dest_rel)
            if ok:
                logger.info(f"[lifecycle] retired {rel} → {dest_rel} (NC MOVE)")
                return {"ok": True, "method": "nc_move", "dest": dest_rel}
            # Fallback: WebDAV DELETE lands in NC trashbin — still recoverable.
            try:
                deleted = await nc.delete(rel)
            except Exception:
                deleted = False
            if deleted:
                logger.warning(
                    f"[lifecycle] MOVE failed for {rel}; WebDAV DELETE → NC trash"
                )
                return {"ok": True, "method": "nc_trash", "dest": ""}
            return {
                "ok": False,
                "method": "none",
                "dest": "",
                "reason": "nc move/delete failed",
            }

        base = video_mount or _video_mount()
        src = os.path.join(base, rel)
        if not os.path.isfile(src) and not os.path.isdir(src):
            return {"ok": False, "method": "none", "dest": "", "reason": "not found"}
        dest = os.path.join(base, dest_rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(src, dest)
        logger.info(f"[lifecycle] retired {rel} → {dest_rel} (local mount)")
        return {"ok": True, "method": "local_move", "dest": dest_rel}
    except Exception as e:
        logger.warning(f"[lifecycle] retire_to_delete failed for {subpath}: {e}")
        return {"ok": False, "method": "none", "dest": "", "reason": str(e)}


def to_delete_total_bytes(video_mount: str = None) -> int:
    """Sum size of files under VIDEO_MOUNT/to-delete (local mount only)."""
    base = os.path.join(video_mount or _video_mount(), TO_DELETE_DIR)
    total = 0
    if not os.path.isdir(base):
        return 0
    for root, _dirs, files in os.walk(base):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


async def ensure_inbox_cleared_after_processing(
    video_name: str,
    nc=None,
    video_mount: str = None,
    *,
    min_size_ratio: float = 0.95,
) -> dict:
    """After a successful transcript, the original should live in processing/ only.

    Prefer WebDAV/local MOVE inbox → processing. If processing already has a
    full-size copy (copy fallback path), remove the inbox dual so the board
    stops listing the file as still-in-inbox.

    Never deletes inbox unless processing has a verified file of comparable size
    (or inbox is already gone). Best-effort; never raises.
    """
    out = {
        "ok": False,
        "method": "none",
        "inbox_cleared": False,
        "in_processing": False,
        "reason": "",
    }
    try:
        name = os.path.basename(video_name or "")
        if not name:
            out["reason"] = "empty name"
            return out

        if nc is not None:
            inbox_rel = f"inbox/{name}"
            proc_rel = f"processing/{name}"
            inbox_meta = await nc.file_meta(inbox_rel)
            proc_meta = await nc.file_meta(proc_rel)

            if proc_meta.get("exists") and not inbox_meta.get("exists"):
                out.update(ok=True, method="already_clean", inbox_cleared=True, in_processing=True)
                return out

            if inbox_meta.get("exists") and not proc_meta.get("exists"):
                moved = await nc.move(inbox_rel, proc_rel)
                if moved:
                    out.update(ok=True, method="nc_move", inbox_cleared=True, in_processing=True)
                    logger.info(f"[lifecycle] MOVE {inbox_rel} → {proc_rel}")
                    return out
                out["reason"] = "nc move failed and no processing copy"
                return out

            if inbox_meta.get("exists") and proc_meta.get("exists"):
                # Dual copy — clear inbox only if processing size is credible.
                isz = inbox_meta.get("size") or -1
                psz = proc_meta.get("size") or -1
                size_ok = psz > 0 and (isz <= 0 or psz >= int(isz * min_size_ratio))
                if not size_ok:
                    out.update(
                        in_processing=True,
                        reason=f"dual copy size mismatch inbox={isz} processing={psz}",
                    )
                    return out
                # Prefer MOVE overwrite (atomic-ish) then delete inbox if still there
                moved = await nc.move(inbox_rel, proc_rel)
                if moved:
                    out.update(ok=True, method="nc_move_overwrite", inbox_cleared=True, in_processing=True)
                    logger.info(f"[lifecycle] MOVE overwrite dual {inbox_rel} → {proc_rel}")
                    return out
                deleted = await nc.delete(inbox_rel)
                if deleted:
                    out.update(ok=True, method="nc_delete_inbox_dual", inbox_cleared=True, in_processing=True)
                    logger.info(f"[lifecycle] deleted inbox dual after verified processing/{name}")
                    return out
                out.update(in_processing=True, reason="dual present; clear inbox failed")
                return out

            if not inbox_meta.get("exists") and not proc_meta.get("exists"):
                out["reason"] = "missing in both inbox and processing"
                return out

            out["reason"] = "unhandled nc state"
            return out

        # Local mount path
        base = video_mount or _video_mount()
        src = os.path.join(base, "inbox", name)
        dst = os.path.join(base, "processing", name)
        if os.path.isfile(dst) and not os.path.isfile(src):
            out.update(ok=True, method="already_clean", inbox_cleared=True, in_processing=True)
            return out
        if os.path.isfile(src) and not os.path.isfile(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            out.update(ok=True, method="local_move", inbox_cleared=True, in_processing=True)
            logger.info(f"[lifecycle] local MOVE inbox/{name} → processing/")
            return out
        if os.path.isfile(src) and os.path.isfile(dst):
            isz = os.path.getsize(src)
            psz = os.path.getsize(dst)
            if psz > 0 and psz >= int(isz * min_size_ratio):
                os.remove(src)
                out.update(ok=True, method="local_delete_inbox_dual", inbox_cleared=True, in_processing=True)
                logger.info(f"[lifecycle] removed local inbox dual for {name}")
                return out
            out.update(in_processing=True, reason=f"local dual size mismatch {isz}/{psz}")
            return out
        out["reason"] = "local missing both"
        return out
    except Exception as e:
        logger.warning(f"[lifecycle] ensure_inbox_cleared_after_processing failed: {e}")
        out["reason"] = str(e)
        return out
