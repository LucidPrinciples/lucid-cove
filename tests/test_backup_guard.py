"""Cove backups must not commit secrets. No network."""

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from src.utils.backup_guard import guard_staged, is_denied

ROOT = Path(__file__).resolve().parents[1]

STRIPE = "sk_live_NEVER_COMMIT_991"
DISCORD = "NEVER_COMMIT_992"


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def _git_env():
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_AUTHOR_NAME"] = "Backup Test"
    env["GIT_AUTHOR_EMAIL"] = "backup@example.com"
    env["GIT_COMMITTER_NAME"] = "Backup Test"
    env["GIT_COMMITTER_EMAIL"] = "backup@example.com"
    return env


def _git(repo: Path, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=check,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README").write_text("base\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")
    return repo


def _capture(sent):
    def post(url, content=None, headers=None, timeout=None, **kwargs):
        sent.append({"url": url, "content": content, "headers": headers})

        class _Resp:
            status_code = 200

        return _Resp()

    return post


@pytest.mark.parametrize(
    ("path", "denied"),
    [
        (".env.bak-x", True),
        ("docker/.env.bak-y", True),
        (".env.example", False),
        ("certs/server.pem", True),
        ("keys/id_ed25519", True),
        ("config/client_secret.json", True),
        ("config/secrets.example", False),
        ("data/app.sqlite", True),
        ("backups/app.sqlite", False),
        ("keep.txt", False),
    ],
)
def test_denylist(path, denied):
    assert is_denied(path) is denied


def test_env_backups_are_not_committed_and_ntfy_names_them(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / ".env.bak-x").write_text(f"STRIPE_SECRET={STRIPE}\n")
    docker = repo / "docker"
    docker.mkdir()
    (docker / ".env.bak-y").write_text(f"DISCORD_TOKEN={DISCORD}\n")
    (repo / "keep.txt").write_text("ordinary backup note\n")
    (repo / ".env.example").write_text("PLACEHOLDER=1\n")

    sent = []
    monkeypatch.setenv("COVE_NTFY_URL", "http://ntfy.example/backup")
    monkeypatch.setattr(httpx, "post", _capture(sent))

    _git(repo, "add", "-A")
    skipped = guard_staged(repo)
    assert set(skipped) == {".env.bak-x", "docker/.env.bak-y"}
    _git(repo, "commit", "-m", "Auto-backup")

    names = set(_git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines())
    assert "keep.txt" in names
    assert ".env.example" in names
    assert ".env.bak-x" not in names
    assert "docker/.env.bak-y" not in names
    history = _git(repo, "log", "-p", "--all").stdout
    assert "ordinary backup note" in history
    assert STRIPE not in history
    assert DISCORD not in history

    assert len(sent) == 1
    body = sent[0]["content"].decode()
    assert "\n" not in body
    assert ".env.bak-x" in body
    assert "docker/.env.bak-y" in body
    assert STRIPE not in body
    assert DISCORD not in body
    assert "STRIPE" not in body
    assert "DISCORD" not in body


def test_every_agent_add_all_calls_the_guard():
    system = (ROOT / "src/dashboard/routes/system.py").read_text()
    backup = (ROOT / "src/utils/cove_backup.py").read_text()
    assert system.count('["git", "add", "-A"]') == 2
    assert system.count("guard_staged(") == 2
    assert backup.count('["git", "add", "-A"]') == 1
    assert backup.count("guard_staged(") == 1
