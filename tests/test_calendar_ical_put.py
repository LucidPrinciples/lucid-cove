"""CAL415 — MC calendar create/update emit CRLF + escaped TEXT for Sabre.

Also guards the PR #321 misbind: helpers must not sit under @router.post,
or FastAPI treats POST /api/calendar/events as _ical_escape_text(value=query)
and the Calendar UI gets HTTP 422 on every save.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PY = ROOT / "src/dashboard/routes/home.py"


def _load_helpers():
    src = HOME_PY.read_text()
    start = src.index("def _ical_escape_text")
    # Helpers end at _ical_crlf; decorator may sit between helpers and create_.
    end = src.index("def _ical_crlf")
    # include full _ical_crlf function body through its blank line before @router
    crlf_chunk = src[end:]
    # find end of _ical_crlf by next top-level def/decorator after its body
    lines = crlf_chunk.splitlines(keepends=True)
    # first line is def _ical_crlf...
    body = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith("@") or (ln.startswith("def ") or ln.startswith("async def ")):
            break
        body.append(ln)
    chunk = src[start:end] + "".join(body)
    ns: dict = {}
    exec(chunk, ns)
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
    src = HOME_PY.read_text()
    assert "def _ical_escape_text" in src
    assert "def _ical_crlf" in src
    assert src.count("content=vcal_bytes") >= 2
    assert "content=vcal," not in src


def test_post_decorator_binds_create_not_helper():
    """@router.post must decorate create_calendar_event, not _ical_escape_text."""
    lines = HOME_PY.read_text().splitlines()
    post_idxs = [i for i, ln in enumerate(lines) if ln.strip() == '@router.post("/api/calendar/events")']
    assert post_idxs, "POST /api/calendar/events decorator missing"
    for idx in post_idxs:
        # skip blank lines after decorator
        j = idx + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        assert j < len(lines), "decorator has no following def"
        following = lines[j].strip()
        assert following.startswith("async def create_calendar_event"), (
            f"POST decorator binds {following!r}, expected create_calendar_event"
        )
    # helper must not be immediately under any router decorator
    for i, ln in enumerate(lines):
        if ln.strip().startswith("def _ical_escape_text"):
            prev = next((lines[k].strip() for k in range(i - 1, -1, -1) if lines[k].strip()), "")
            assert not prev.startswith("@router."), f"_ical_escape_text still decorated: {prev}"
            break
    else:
        raise AssertionError("_ical_escape_text not found")
