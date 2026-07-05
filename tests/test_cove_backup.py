# CF-112 — pure-logic tests for the Cove backup (redaction, exclusions, remote
# normalization, token injection, dump rotation). The end-to-end run needs a
# live git remote + pg_dump and is proven in the primary session / run 4.
from pathlib import Path

from src.utils.cove_backup import (
    build_push_url, is_video_file, normalize_remote_url, redact_config_text,
    rotate_dumps, DUMPS_KEPT,
)


def test_redact_hits_secretish_keys_and_keeps_structure():
    text = (
        "cove:\n"
        "  id: muller\n"
        "  operator_token: mBYxF-super-secret\n"
        "  auth:\n"
        "    method: magic_link\n"
        "  compute:\n"
        "    video_asr:\n"
        "      mode: external\n"
        "      token: gpugrant_abc123\n"
        "  api_key: sk-xyz\n"
        "  name: Muller\n"
    )
    out = redact_config_text(text)
    assert "mBYxF-super-secret" not in out
    assert "gpugrant_abc123" not in out
    assert "sk-xyz" not in out
    assert "operator_token: __REDACTED__" in out
    assert "mode: external" in out           # non-secret values untouched
    assert "name: Muller" in out
    assert "method: magic_link" in out       # 'magic_link' value under a non-secret key stays


def test_redact_leaves_empty_and_block_values_alone():
    text = "tokens:\n  rotation_threshold: 30\npassword:\n"
    out = redact_config_text(text)
    assert "rotation_threshold: __REDACTED__" not in out  # nested non-secret key name? it IS secret-ish? no — 'rotation_threshold' has no secret word
    assert "password:\n" in out or out.endswith("password:")  # empty value untouched


def test_video_exclusions():
    assert is_video_file("clip.MP4")
    assert is_video_file("a/b/c/full-render.mov")
    assert not is_video_file("transcript.json")
    assert not is_video_file("notes.md")
    assert not is_video_file("audio.webm.txt")


def test_normalize_remote_url_forms():
    want = "https://github.com/chords/my-cove-backup.git"
    assert normalize_remote_url("https://github.com/chords/my-cove-backup") == want
    assert normalize_remote_url("https://github.com/chords/my-cove-backup.git") == want
    assert normalize_remote_url("chords/my-cove-backup") == want
    assert normalize_remote_url("git@github.com:chords/my-cove-backup.git") == want
    assert normalize_remote_url("") == ""
    assert normalize_remote_url("ftp://nope/x/y") == ""
    assert normalize_remote_url("https://github.com/just-owner") == ""


def test_build_push_url_injects_token_once():
    u = build_push_url("https://github.com/chords/my-cove-backup", "github_pat_ABC")
    assert u == "https://oauth2:github_pat_ABC@github.com/chords/my-cove-backup.git"
    assert build_push_url("", "t") == ""
    assert build_push_url("chords/repo", "") == ""


def test_rotate_dumps_keeps_newest(tmp_path: Path):
    db = tmp_path / "db"
    db.mkdir()
    names = [f"2026-06-{d:02d}_03-30.sql.gz" for d in range(1, DUMPS_KEPT + 6)]
    for n in names:
        (db / n).write_bytes(b"x")
    removed = rotate_dumps(db)
    left = sorted(p.name for p in db.glob("*.sql.gz"))
    assert removed == 5
    assert len(left) == DUMPS_KEPT
    assert left[0] == names[5]      # oldest five gone
    assert left[-1] == names[-1]    # newest kept
