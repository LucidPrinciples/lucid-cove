"""Unstage secret paths after git add -A.

Cove backups stage everything. This guard lists staged names, unstages the
denylist, and sends one ntfy line of those names. It never reads file
contents and never puts contents in the notice.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# (^|/)\.env($|[.-]) — .env, .env.bak-x, docker/.env, docker/.env.local
_ENV_FILE = re.compile(r"(^|/)\.env($|[.-])")


def is_denied(path: str) -> bool:
    """True when a staged path must not be committed. Case-insensitive.

    *.example is allowed. sqlite and db dumps are allowed only under backups/.
    """
    rel = path.replace("\\", "/").strip()
    if rel.startswith("./"):
        rel = rel[2:]
    lower = rel.lower()
    if lower.endswith(".example"):
        return False
    name = lower.rsplit("/", 1)[-1]
    if _ENV_FILE.search(lower):
        return True
    if ".env" in name and ".bak" in name:
        return True
    if name.endswith(".pem") or name.endswith(".key"):
        return True
    if name.startswith("id_rsa") or name.startswith("id_ed25519"):
        return True
    if "credentials" in name or "secret" in name:
        return True
    parents = lower.split("/")[:-1]
    if name.endswith(".sqlite") or name.endswith(".db"):
        return "backups" not in parents
    return False


def staged_paths(cwd: str | Path) -> list[str]:
    """Staged names only. Does not diff contents."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=str(cwd),
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("backup guard could not list staged paths")
    return [
        part.decode("utf-8", "surrogateescape")
        for part in result.stdout.split(b"\0")
        if part
    ]


def format_notice(paths: list[str]) -> str:
    """One line. Path names only."""
    names = ", ".join(path.replace("\n", "").replace("\r", "") for path in paths)
    return f"Backup skipped secret paths: {names}"


def notify_skipped(paths: list[str]) -> None:
    """One ntfy post naming the skipped paths. Never raises. No file contents."""
    if not paths:
        return
    line = format_notice(paths)
    print(f"[backup-guard] {line}")
    url = (os.getenv("COVE_NTFY_URL", "").strip() or os.getenv("NOON_NTFY_URL", "").strip())
    if not url:
        print("[backup-guard] ntfy skipped (no URL)")
        return
    try:
        import httpx
        httpx.post(
            url,
            content=line.encode("utf-8"),
            headers={"Title": "Backup secret guard"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[backup-guard] ntfy failed (non-fatal): {exc}")


def guard_staged(cwd: str | Path) -> list[str]:
    """Unstage every denied path. Call this after git add -A, before commit.

    Returns the paths that were unstaged. Sends one ntfy line when that list
    is not empty.
    """
    skipped = [path for path in staged_paths(cwd) if is_denied(path)]
    for path in skipped:
        reset = subprocess.run(
            ["git", "reset", "-q", "--", path],
            cwd=str(cwd),
            capture_output=True,
            timeout=15,
        )
        if reset.returncode != 0:
            raise RuntimeError("backup guard could not unstage a denied path")
    if skipped:
        notify_skipped(skipped)
    return skipped
