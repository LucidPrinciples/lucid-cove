"""CAL415 — MC calendar create/update ICS shape for Sabre/Nextcloud.

Guards:
- CRLF line endings + RFC 5545 TEXT escape
- POST decorator binds create_calendar_event (not a helper)
- Timed events from the Calendar UI keep floating local HHMMSS (no false Z)
- No double-seconds append (``143000`` must not become ``14300000``)
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PY = ROOT / "src/dashboard/routes/home.py"


def _load_helpers():
    src = HOME_PY.read_text()
    start = src.index("def _ical_escape_text")
    # Helpers run through _ical_vevent_bytes; stop at @router.post create
    end = src.index('@router.post("/api/calendar/events")')
    chunk = src[start:end]
    ns: dict = {}
    exec(chunk, ns)
    return ns


def test_ical_escape_and_crlf():
    ns = _load_helpers()
    esc, crlf = ns["_ical_escape_text"], ns["_ical_crlf"]
    assert esc("Dr French") == "Dr French"
    assert esc("A;B,C") == "A\\;B\\,C"
    assert "\\n" in esc("a\nb")
    body = crlf("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Hi\nEND:VEVENT\nEND:VCALENDAR\n")
    assert body.endswith(b"\r\n")
    assert b"\r\n" in body
    assert b"\n" not in body.replace(b"\r\n", b"")


def test_local_dt_token_from_ui_form():
    """UI sends dateTtime:00 — must become YYYYMMDDTHHMMSS floating, not ...00Z double-seconds."""
    tok = _load_helpers()["_ical_local_dt_token"]
    assert tok("2026-08-05T14:30:00") == "20260805T143000"
    assert tok("2026-08-05T14:30") == "20260805T143000"
    assert tok("2026-08-05T09:05:00") == "20260805T090500"
    assert tok("2026-08-05T14:30:00Z") == "20260805T143000"
    # never 8-digit time
    assert not tok("2026-08-05T14:30:00").endswith("0000") or tok("2026-08-05T14:30:00") == "20260805T143000"


def test_vevent_bytes_timed_floating_local():
    ns = _load_helpers()
    raw = ns["_ical_vevent_bytes"](
        uid="test-uid-1",
        summary="Dr French",
        start_str="2026-08-05T14:30:00",
        end_str="2026-08-05T15:00:00",
        all_day=False,
        location="Office",
        description="Checkup",
    )
    assert isinstance(raw, bytes)
    assert raw.endswith(b"\r\n")
    text = raw.decode()
    assert "DTSTART:20260805T143000" in text
    assert "DTEND:20260805T150000" in text
    assert "DTSTART:20260805T143000Z" not in text
    assert "14300000" not in text
    assert "SUMMARY:Dr French" in text
    assert "LOCATION:Office" in text


def test_vevent_bytes_all_day():
    ns = _load_helpers()
    raw = ns["_ical_vevent_bytes"](
        uid="test-uid-2",
        summary="Holiday",
        start_str="2026-08-05",
        end_str="",
        all_day=True,
    )
    text = raw.decode()
    assert "DTSTART;VALUE=DATE:20260805" in text
    assert "DTEND;VALUE=DATE:20260806" in text


def test_create_update_use_shared_builder():
    src = HOME_PY.read_text()
    assert "def _ical_vevent_bytes" in src
    assert src.count("_ical_vevent_bytes(") >= 2
    assert src.count("content=vcal_bytes") >= 2
    assert "content=vcal," not in src
    # old double-seconds create pattern gone
    assert "s}00Z" not in src
    assert 'f"DTSTART:{s}Z"' not in src


def test_post_decorator_binds_create_not_helper():
    """@router.post must decorate create_calendar_event (not a helper)."""
    src = HOME_PY.read_text()
    marker = '@router.post("/api/calendar/events")'
    i = src.index(marker)
    window = src[i : i + 200]
    assert "async def create_calendar_event" in window
    assert "def _ical_escape_text" not in window
    assert "def _ical_vevent_bytes" not in window
