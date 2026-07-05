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
