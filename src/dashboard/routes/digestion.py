"""
Archive digestion routes — Stuart-specific endpoints for managing the
archive digestion pipeline (manual trigger + status check).

Registered as an additional route in agent.yaml, NOT as an overlay of system.py.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/system/archive-digestion")
async def trigger_archive_digestion():
    """Manually trigger archive digestion pipeline.

    Normally runs Sundays at 8:30pm after Memory consolidation.
    This endpoint allows manual trigger for testing or immediate processing.
    """
    try:
        from src.memory.archive_digestion import run_archive_digestion
        result = await run_archive_digestion()
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/system/archive-digestion/cancel")
async def cancel_archive_digestion():
    """Cancel a running archive digestion after the current session finishes."""
    try:
        from src.memory.archive_digestion import request_cancel
        request_cancel()
        return {"status": "cancelled", "message": "Digestion will stop after current session"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/api/system/archive-digestion")
async def archive_digestion_status():
    """Get archive digestion status — what's been digested vs available."""
    try:
        from src.memory.archive_digestion import (
            ARCHIVE_PATH, _extract_session_entries, _get_digested_sessions
        )

        if not ARCHIVE_PATH.exists():
            return {"status": "error", "error": "Archive file not found"}

        archive_text = ARCHIVE_PATH.read_text(encoding="utf-8")
        sessions = _extract_session_entries(archive_text)
        digested = await _get_digested_sessions()

        return {
            "status": "ok",
            "archive_path": str(ARCHIVE_PATH),
            "total_sessions": len(sessions),
            "digested_sessions": len(digested),
            "undigested_sessions": len(sessions) - len(digested),
            "session_numbers": [s["session_num"] for s in sessions],
            "digested_numbers": sorted(list(digested)),
            "pending_numbers": sorted([s["session_num"] for s in sessions
                                        if s["session_num"] not in digested]),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
