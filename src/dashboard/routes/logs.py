"""
Log viewer routes — reads from persistent daily log files on disk.
Includes SSE endpoint for live tail streaming.

Ported from SocratesArcher-LG with agent_id filtering for multi-agent readiness.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from src.env import env

router = APIRouter()

LOG_DIR = Path("/app/data/logs")


def _read_log_file(path: Path, max_lines: int) -> list[str]:
    """Read up to max_lines from the tail of a log file."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(errors="replace").splitlines()
        return [l for l in raw[-max_lines:] if l.strip()]
    except Exception:
        return []


def _read_log_file_full(path: Path) -> str:
    """Read entire log file as raw text."""
    if not path.exists():
        return ""
    try:
        return path.read_text(errors="replace")
    except Exception:
        return ""


@router.get("/api/logs/stream")
async def stream_logs(request: Request, filter: str = None):
    """SSE endpoint — streams live log lines, like tail -f.

    Sends the last 50 lines immediately, then watches the file for new
    content. Handles midnight log rotation automatically.

    Client connects via EventSource. Each event is JSON: {"text": "..."}
    """
    import os
    # C2: read the config cascade, not the env — APP_TIMEZONE was only emitted
    # by the legacy provisioner, so log dates ran Eastern on non-Eastern Coves.
    from src.utils.time_utils import app_tz
    tz = app_tz()

    async def event_generator():
        yield ": keepalive\n\n"

        now_local = datetime.now(tz)
        log_path = LOG_DIR / f"app-{now_local.strftime('%Y-%m-%d')}.log"

        # Send last 50 lines as initial burst
        if log_path.exists():
            initial = _read_log_file(log_path, 50)
            for line in initial:
                if filter and filter.lower() not in line.lower():
                    continue
                yield f"data: {json.dumps({'text': line})}\n\n"

        last_size = log_path.stat().st_size if log_path.exists() else 0
        keepalive_counter = 0

        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(1)
            keepalive_counter += 1
            if keepalive_counter % 15 == 0:
                yield ": keepalive\n\n"

            # Handle midnight log rotation
            now_local = datetime.now(tz)
            current_path = LOG_DIR / f"app-{now_local.strftime('%Y-%m-%d')}.log"
            if current_path != log_path:
                log_path = current_path
                last_size = 0

            if not log_path.exists():
                continue

            current_size = log_path.stat().st_size
            if current_size > last_size:
                try:
                    with open(log_path, "r", errors="replace") as f:
                        f.seek(last_size)
                        new_content = f.read()
                    last_size = current_size
                    for line in new_content.splitlines():
                        if not line.strip():
                            continue
                        if filter and filter.lower() not in line.lower():
                            continue
                        yield f"data: {json.dumps({'text': line})}\n\n"
                except Exception:
                    pass
            elif current_size < last_size:
                last_size = 0

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/logs/dates")
async def get_log_dates():
    """List available log file dates (most recent first), plus file sizes."""
    if not LOG_DIR.exists():
        return {"dates": [], "note": "No log directory found"}
    dates = []
    for f in sorted(LOG_DIR.glob("app-*.log"), reverse=True):
        try:
            date_str = f.stem.replace("app-", "")
            datetime.strptime(date_str, "%Y-%m-%d")
            size_kb = round(f.stat().st_size / 1024, 1)
            dates.append({"date": date_str, "filename": f.name, "size_kb": size_kb})
        except ValueError:
            pass
    return {"dates": dates}


@router.get("/api/logs/raw")
async def get_logs_raw(date: str = None, filter: str = None):
    """Return raw text of a log file for the full-page viewer.

    date: YYYY-MM-DD (defaults to today).
    filter: optional substring filter applied line-by-line.
    """
    import os
    # C2: read the config cascade, not the env — APP_TIMEZONE was only emitted
    # by the legacy provisioner, so log dates ran Eastern on non-Eastern Coves.
    from src.utils.time_utils import app_tz
    tz = app_tz()
    now_local = datetime.now(tz)

    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"error": "Invalid date format", "text": "", "lines": 0}
        log_path = LOG_DIR / f"app-{date}.log"
    else:
        date = now_local.strftime("%Y-%m-%d")
        log_path = LOG_DIR / f"app-{date}.log"

    raw = _read_log_file_full(log_path)

    if not raw and not log_path.exists():
        has_any = LOG_DIR.exists() and any(LOG_DIR.glob("app-*.log"))
        if not has_any:
            return {"text": "[No log files found yet.]\n", "date": date, "lines": 0, "size_kb": 0}
        return {"text": f"[No log file for {date}]\n", "date": date, "lines": 0, "size_kb": 0}

    lines = raw.splitlines()
    if filter:
        lines = [l for l in lines if filter.lower() in l.lower()]
    text = "\n".join(lines)
    size_kb = round(log_path.stat().st_size / 1024, 1) if log_path.exists() else 0

    return {"text": text, "date": date, "lines": len(lines), "size_kb": size_kb}


@router.get("/api/logs")
async def get_logs(agent_id: str = None, limit: int = 200):
    """Return recent log lines with optional agent_id filter.

    Used by agent detail pages. For full log viewer, use /api/logs/raw.
    """
    import os
    # C2: read the config cascade, not the env — APP_TIMEZONE was only emitted
    # by the legacy provisioner, so log dates ran Eastern on non-Eastern Coves.
    from src.utils.time_utils import app_tz
    tz = app_tz()
    now_local = datetime.now(tz)

    today_path = LOG_DIR / f"app-{now_local.strftime('%Y-%m-%d')}.log"
    yesterday_path = LOG_DIR / f"app-{(now_local - timedelta(days=1)).strftime('%Y-%m-%d')}.log"

    raw_pool: list[str] = []

    if LOG_DIR.exists() and any(LOG_DIR.glob("app-*.log")):
        read_limit = limit * 8
        today_lines = _read_log_file(today_path, read_limit)
        if len(today_lines) < limit:
            need = limit - len(today_lines)
            yesterday_lines = _read_log_file(yesterday_path, need * 4)
            raw_pool = yesterday_lines + today_lines
        else:
            raw_pool = today_lines
    else:
        return {"lines": [], "agent_id": agent_id, "count": 0, "source": "none"}

    ts = datetime.now(timezone.utc).isoformat()
    lines = []
    for text in raw_pool:
        if agent_id and agent_id.lower() not in text.lower():
            continue
        lines.append({"ts": ts, "stream": "stdout", "text": text})

    lines = lines[-limit:]
    return {"lines": lines, "agent_id": agent_id, "count": len(lines), "source": "file"}
