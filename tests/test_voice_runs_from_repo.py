"""Voice runs its code from the repo (bind mount of voice/src), like the app does from /cove-core.

A voice change is then a pull and a restart on every install, not an image rebuild.
"""
from pathlib import Path

import provision.centralized as cz

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")


def _compose(voice_local=True, voice_gpu=False):
    cove = {"id": "lucidcove-test", "name": "Test", "_app_port": 8200}
    deploy = {"nextcloud_port": 8080, "matrix_port": 8008, "voice_port": 8301,
              "umami_port": 3000, "lucid_cove_path": "/srv/lucid-cove"}
    return cz.build_compose(cove, deploy, matrix_on=False, voice_local=voice_local,
                            umami_on=False, voice_gpu=voice_gpu)


def _voice_block(out: str) -> str:
    start = out.index("\n  voice:\n")
    rest = out[start + 1:]
    lines = rest.splitlines()
    block = [lines[0]]
    for line in lines[1:]:
        if line and not line.startswith("    ") and not line.startswith("  #") and line.strip():
            break
        block.append(line)
    return "\n".join(block)


def test_voice_service_mounts_repo_src_read_only():
    for gpu in (False, True):
        block = _voice_block(_compose(voice_gpu=gpu))
        assert "- /srv/lucid-cove/voice/src:/app/src:ro" in block
        # the model cache and the Nextcloud tree are still mounted
        assert ":/root/.cache" in block
        assert ":/var/www/html" in block


def test_no_voice_mount_when_voice_is_not_local():
    out = _compose(voice_local=False)
    assert "/voice/src:/app/src" not in out


def test_voice_pause_message_has_no_diagnostic_tag():
    assert "'Voice paused' + tag" not in VOICE_JS
    assert "Voice paused. Send or edit" in VOICE_JS
    assert "Voice paused. Nothing was captured. Tap to talk." in VOICE_JS
