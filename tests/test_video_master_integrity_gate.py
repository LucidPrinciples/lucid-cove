"""Master integrity gate — never ASR / promote ghost MOVs (IMG_7159 2026-07-22)."""
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
STT_PATH = ROOT / "voice/src/routes/stt.py"
STT_SRC = STT_PATH.read_text(encoding="utf-8")


def test_stt_no_continue_on_failed_processing_copy():
    """Failed publish must hard-return, not log-and-ASR."""
    assert "ABORTING transcription" in STT_SRC
    # The old failure mode string must not remain as live log+continue behavior.
    # (A historical mention inside a comment about the bug is OK.)
    live = "\n".join(
        ln for ln in STT_SRC.splitlines()
        if "Continuing with transcription" in ln and not ln.lstrip().startswith("#")
    )
    assert live == "", f"live continue-on-failure still present:\n{live}"
    assert "status_code=500" in STT_SRC
    assert "assert_video_master_readable" in STT_SRC


def test_helper_rejects_tiny_and_missing(tmp_path, monkeypatch):
    # Load just the helper without importing the full FastAPI voice app.
    sys.path.insert(0, str(ROOT / "voice"))
    # Minimal stub package so we can exec the helper.
    ns: dict = {"os": __import__("os")}
    # Extract helper source
    start = STT_SRC.index("def assert_video_master_readable")
    end = STT_SRC.index("\nasync def _gpu_auth_ok") if "\nasync def _gpu_auth_ok" in STT_SRC else STT_SRC.index("\n@router.post")
    # helper sits before _gpu_auth_ok after our edit — find next top-level def after helper
    rest = STT_SRC[start:]
    # next def at column 0 after first line
    lines = rest.splitlines(True)
    body = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith("async def ") or (ln.startswith("def ") and "assert_video" not in ln):
            break
        if ln.startswith("@router"):
            break
        body.append(ln)
    code = "".join(body)
    exec(compile(code, "stt_helper", "exec"), ns)
    fn = ns["assert_video_master_readable"]

    try:
        fn(str(tmp_path / "nope.MOV"))
        assert False, "expected missing"
    except RuntimeError as e:
        assert "missing" in str(e).lower()

    tiny = tmp_path / "tiny.MOV"
    tiny.write_bytes(b"x" * 100)
    try:
        fn(str(tiny))
        assert False, "expected too small"
    except RuntimeError as e:
        assert "too small" in str(e).lower()


def test_helper_uses_ffprobe_failure(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "voice"))
    ns: dict = {"os": __import__("os")}
    start = STT_SRC.index("def assert_video_master_readable")
    rest = STT_SRC[start:]
    lines = rest.splitlines(True)
    body = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith("async def ") or (ln.startswith("def ") and "assert_video" not in ln) or ln.startswith("@router"):
            break
        body.append(ln)
    exec(compile("".join(body), "stt_helper", "exec"), ns)
    fn = ns["assert_video_master_readable"]

    junk = tmp_path / "junk.MOV"
    junk.write_bytes(b"not a real mov" * 200)

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "moov atom not found"

    def fake_run(*a, **k):
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        fn(str(junk))
        assert False, "expected moov fail"
    except RuntimeError as e:
        assert "moov" in str(e).lower() or "unreadable" in str(e).lower()
