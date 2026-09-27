"""Host-side sign-in recovery (`python -m src.cli.door` / cove-lifecycle.sh door)."""
from pathlib import Path

from src.cli.door import build_door_url

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (ROOT / "provision" / "cove-lifecycle.sh").read_text()
CENTRAL = (ROOT / "provision" / "centralized.py").read_text()
QUICK = (ROOT / "QUICKSTART.md").read_text()
DOOR = (ROOT / "src" / "cli" / "door.py").read_text()


def test_build_door_url_uses_claimed_domain():
    url = build_door_url("tokABC", domain="example.lucidcove.org", port="8200")
    assert url == "https://example.lucidcove.org/p/tokABC"


def test_build_door_url_falls_back_to_localhost_port():
    url = build_door_url("tokABC", domain="", port="8210")
    assert url == "http://localhost:8210/p/tokABC"


def test_build_door_url_always_includes_p_path():
    assert "/p/" in build_door_url("x", domain="cove.example.com")
    assert "/p/" in build_door_url("x", domain="")


def test_lifecycle_has_door_command():
    assert "python -m src.cli.door" in LIFECYCLE
    assert 'cmd="${1:-help}"' in LIFECYCLE or "door)" in LIFECYCLE
    assert "host-recovery" in DOOR
    assert "device_label" in DOOR or '"host-recovery"' in DOOR


def test_door_prints_to_terminal_only():
    assert "never logs the token" in DOOR.lower() or "terminal only" in LIFECYCLE.lower()
    assert "print(url)" in DOOR
    assert "write_text" not in DOOR
    assert "open(" not in DOOR


def test_generated_cove_folder_gets_door_script():
    assert "COVE_DOOR_SH" in CENTRAL
    assert 'root / "cove-lifecycle.sh"' in CENTRAL
    assert "./cove-lifecycle.sh door" in CENTRAL
    assert "Lost access" in QUICK
    assert "./cove-lifecycle.sh door" in QUICK
