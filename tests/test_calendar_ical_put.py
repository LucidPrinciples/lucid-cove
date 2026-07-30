"""CAL415 — MC calendar create/update emit CRLF + escaped TEXT for Sabre."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_helpers():
    src = (ROOT / "src/dashboard/routes/home.py").read_text()
    start = src.index("def _ical_escape_text")
    end = src.index("async def create_calendar_event")
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["_ical_escape_text"], ns["_ical_crlf"]


def test_ical_escape_and_crlf():
    esc, crlf = _load_helpers()
    assert esc("Dr French") == "Dr French"
    assert esc("A;B,C") == "A\\;B\\,C"
    assert "\\n" in esc("a\nb")
    body = crlf("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Hi\nEND:VEVENT\nEND:VCALENDAR\n")
    assert body.endswith(b"\r\n")
    assert b"\r\n" in body
    # no bare LF left
    assert b"\n" not in body.replace(b"\r\n", b"")


def test_create_update_use_bytes_helpers():
    src = (ROOT / "src/dashboard/routes/home.py").read_text()
    assert "def _ical_escape_text" in src
    assert "def _ical_crlf" in src
    assert src.count("content=vcal_bytes") >= 2
    assert "content=vcal," not in src
