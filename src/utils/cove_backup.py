# =============================================================================
# cove_backup.py — CF-112: Cove-level backup to the operator's own git remote
# =============================================================================
# The replicable successor to the founder-shape weekly backup (system.py, SSH
# deploy keys, LucidTunerAI repos). This one is operator-configured in the UI:
# a private GitHub repo + a fine-grained PAT (Contents read/write on that repo
# only), stored in the feature-overrides store like the pipeline keys (AT-1
# pattern — never echoed back).
#
# SCOPE (operator decision, locked 2026-07-03): EVERYTHING that makes the Cove —
#   db/       pg_dump of the Cove database (gzip, dated, keep last 14)
#             LangGraph runtime checkpoint tables excluded (rebuildable session
#             state; can be multi-GB and is not family/product restore data)
#   config/   /app/config yaml files with secret-ish values REDACTED
#   files/{nc_username}/AgentSkills/…  every active presence's NC AgentSkills
#             via WebDAV with that presence's own creds — video binaries
#             EXCLUDED (transcripts and everything else included)
#
# Cadence: daily (scheduler) + on-demand ("Back up now"). The onboarding nag
# clears on configured + FIRST GREEN RUN (status lives in the same store).
#
# Token handling: the clean remote URL is what's stored/persisted in .git —
# the PAT is injected into the push URL ONLY for the duration of the push and
# the remote is reset to the clean URL immediately after (never lands on disk).
# =============================================================================
import gzip
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.env import env

log = logging.getLogger(__name__)

# Feature-override keys (same store as pipeline keys — save_feature_overrides).
FLAG_REMOTE = "backup_remote_url"
FLAG_TOKEN = "backup_pat"
FLAG_LAST = "backup_last"          # JSON: {ts, ok, summary, detail}

DUMPS_KEPT = 14

# Video binaries stay out of the backup (they're huge and reproducible from
# masters per the C4/CF-98 retention decisions). Transcripts/captions/etc stay.
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".3gp", ".wmv"}

# LangGraph checkpoint tables are runtime graph state (often multi-GB). They are
# not required to restore accounts, memory, tasks, or product config — and they
# blow past practical pg_dump + GitHub backup limits. Exclude from CF-112 dumps.
PG_DUMP_EXCLUDE_TABLES = (
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoints",
    "checkpoint_migrations",
)

# pg_dump of a healthy Cove can exceed 5 minutes once memory/history grows.
# Keep git ops shorter; dump/push get their own budgets.
PG_DUMP_TIMEOUT_SEC = 1800  # 30 minutes
GIT_PUSH_TIMEOUT_SEC = 600


def redact_secrets(text: str) -> str:
    """Strip credentials from process/error strings before status UI or logs."""
    s = text or ""
    s = re.sub(r"postgresql://[^\s\"']+", "postgresql://***", s)
    s = re.sub(r"postgres://[^\s\"']+", "postgres://***", s)
    s = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_***", s)
    s = re.sub(r"ghp_[A-Za-z0-9]+", "ghp_***", s)
    s = re.sub(r"oauth2:[^@\s]+@", "oauth2:***@", s)
    s = re.sub(r":([^:/@\s]{8,})@", ":***@", s)  # user:pass@host forms
    return s


# Keys whose VALUES get redacted from backed-up config yaml (matched on the key
# name, case-insensitive). The backup must be restorable WITHOUT being a
# credential dump — real secrets live in .env (never backed up) and the store.
_SECRET_KEY_RE = re.compile(
    r"^(\s*)([A-Za-z0-9_.\-]*(?:token|secret|password|api_key|apikey|pat)[A-Za-z0-9_.\-]*)(\s*:\s*)(\S.*)$",
    re.IGNORECASE,
)


def redact_config_text(text: str) -> str:
    """Redact secret-ish values in a yaml text, preserving structure. A line
    like `operator_token: abc` becomes `operator_token: __REDACTED__`. Lines
    whose value is already empty / a nested block are left alone."""
    out = []
    for line in text.splitlines():
        m = _SECRET_KEY_RE.match(line)
        if m and m.group(4).strip() not in ("", "|", ">", "{}", "[]"):
            out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}__REDACTED__")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def is_video_file(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTS


def normalize_remote_url(url: str) -> str:
    """Accept the forms an operator will paste (https page URL, .git URL,
    owner/repo shorthand) and normalize to https://github.com/{owner}/{repo}.git.
    Returns '' if it doesn't look like a repo."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    u = re.sub(r"^git@github\.com:", "https://github.com/", u)
    if re.match(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$", u):
        u = f"https://github.com/{u}"
    if not u.startswith("https://"):
        return ""
    if not u.endswith(".git"):
        u += ".git"
    # Only github.com-style https remotes for v1 (the nag instructions are GitHub).
    return u if re.match(r"^https://[^/]+/[^/]+/[^/]+\.git$", u) else ""


def build_push_url(remote_url: str, token: str) -> str:
    """Inject the PAT for the one push. oauth2:{token}@ works for fine-grained
    GitHub PATs over https."""
    clean = normalize_remote_url(remote_url)
    if not (clean and token):
        return ""
    return clean.replace("https://", f"https://oauth2:{token}@", 1)


# GitHub hard-rejects any single blob over 100MB. Stay under with headroom.
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024
DUMP_CHUNK_BYTES = 90 * 1024 * 1024  # 90MB chunks


def _dump_prefixes(db_dir: Path) -> list[str]:
    """Unique dump basenames, where a dump is either foo.sql.gz or foo.sql.gz.partNNN."""
    prefs: set[str] = set()
    if not db_dir.is_dir():
        return []
    for p in db_dir.iterdir():
        name = p.name
        if name.endswith(".sql.gz"):
            prefs.add(name[: -len(".sql.gz")])
        elif ".sql.gz.part" in name:
            prefs.add(name.split(".sql.gz.part", 1)[0])
    return sorted(prefs)


def rotate_dumps(db_dir: Path, keep: int = DUMPS_KEPT) -> int:
    """Delete oldest dated dumps beyond `keep`. A dump may be one file or many parts.
    Returns how many dump files were removed."""
    prefs = _dump_prefixes(db_dir)
    excess = prefs[:-keep] if len(prefs) > keep else []
    removed = 0
    for pref in excess:
        for f in list(db_dir.glob(f"{pref}.sql.gz")) + list(db_dir.glob(f"{pref}.sql.gz.part*")):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def write_dump_chunks(dump_file: Path, max_chunk: int = DUMP_CHUNK_BYTES) -> list[Path]:
    """If dump_file is under max_chunk, leave as single .sql.gz. Else split into
    dump_file.name + '.part001' etc and remove the original oversized file.
    Returns the list of files that should be committed."""
    if not dump_file.exists():
        return []
    size = dump_file.stat().st_size
    if size <= max_chunk:
        return [dump_file]
    parts: list[Path] = []
    with dump_file.open("rb") as src:
        idx = 1
        while True:
            chunk = src.read(max_chunk)
            if not chunk:
                break
            part = dump_file.parent / f"{dump_file.name}.part{idx:03d}"
            part.write_bytes(chunk)
            parts.append(part)
            idx += 1
    try:
        dump_file.unlink()
    except Exception:
        pass
    return parts


def reject_if_still_oversized(root: Path, limit: int = GITHUB_MAX_FILE_BYTES - 1024 * 1024) -> list[str]:
    """Return paths (relative) of any file still over GitHub's limit."""
    bad = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.stat().st_size > limit:
            bad.append(str(p.relative_to(root)))
    return bad


# ── config/status accessors (feature-overrides store) ───────────────────────

def get_backup_config() -> dict:
    """{remote_url, has_token} — token NEVER leaves this module."""
    try:
        from src.config import get_feature_flags
        flags = get_feature_flags()
        return {
            "remote_url": (flags.get(FLAG_REMOTE) or "").strip(),
            "has_token": bool((flags.get(FLAG_TOKEN) or "").strip()),
        }
    except Exception:
        return {"remote_url": "", "has_token": False}


def _get_token() -> str:
    try:
        from src.config import get_feature_flags
        return (get_feature_flags().get(FLAG_TOKEN) or "").strip()
    except Exception:
        return ""


def get_last_status() -> dict:
    try:
        from src.config import get_feature_flags
        raw = get_feature_flags().get(FLAG_LAST) or ""
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _set_last_status(st: dict) -> None:
    try:
        from src.config import save_feature_overrides
        save_feature_overrides({FLAG_LAST: json.dumps(st)})
    except Exception as e:
        log.warning("backup: could not persist status: %s", e)


def backup_configured() -> bool:
    c = get_backup_config()
    return bool(c["remote_url"] and c["has_token"])


def backup_green() -> bool:
    """Configured + the last run pushed clean — this is what clears the nag."""
    return backup_configured() and bool(get_last_status().get("ok"))


# ── the runner ───────────────────────────────────────────────────────────────

def _run(cmd, cwd=None, timeout=60, shell=False):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, shell=shell,
                          capture_output=True, text=True)


def _ensure_repo(root: Path, clean_remote: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _run(["git", "init", "-b", "main"], cwd=root)
    _run(["git", "config", "user.email", env("BACKUP_GIT_EMAIL", "backup@cove.internal")], cwd=root)
    _run(["git", "config", "user.name", env("BACKUP_GIT_NAME", "Cove Backup")], cwd=root)
    # Idempotent remote: always reset to the CLEAN url (tokenless).
    if "origin" in (_run(["git", "remote"], cwd=root).stdout or ""):
        _run(["git", "remote", "set-url", "origin", clean_remote], cwd=root)
    else:
        _run(["git", "remote", "add", "origin", clean_remote], cwd=root)


async def _backup_nc_agentskills(root: Path) -> dict:
    """Pull every active presence's NC AgentSkills via WebDAV (their own creds),
    skipping video binaries. Returns {users, files, skipped_videos, errors[]}."""
    import httpx
    nc_url = (env("NEXTCLOUD_URL") or "").rstrip("/")
    stats = {"users": 0, "files": 0, "skipped_videos": 0, "errors": []}
    if not nc_url:
        stats["errors"].append("NEXTCLOUD_URL not set")
        return stats
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT nc_username, nc_password FROM accounts "
            "WHERE active = TRUE AND nc_username IS NOT NULL AND nc_username <> '' "
            "AND nc_password IS NOT NULL AND nc_password <> ''")
        rows = await r.fetchall()

    for row in rows or []:
        user, pw = row["nc_username"], row["nc_password"]
        base = f"{nc_url}/remote.php/dav/files/{user}"
        dest_root = root / "files" / user / "AgentSkills"
        try:
            async with httpx.AsyncClient(auth=(user, pw), timeout=60) as client:
                await _dav_walk(client, base, "/AgentSkills", dest_root, stats)
            stats["users"] += 1
        except Exception as e:
            stats["errors"].append(f"{user}: {type(e).__name__}: {str(e)[:120]}")
    return stats


async def _dav_walk(client, base: str, rel: str, dest_root: Path, stats: dict) -> None:
    """Depth-1 PROPFIND walk, downloading files (except video binaries)."""
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote, quote
    resp = await client.request("PROPFIND", base + quote(rel), headers={"Depth": "1"})
    if resp.status_code == 404:
        return  # this presence has no AgentSkills — fine
    resp.raise_for_status()
    ns = {"d": "DAV:"}
    tree = ET.fromstring(resp.text)
    self_path = None
    for node in tree.findall("d:response", ns):
        href = unquote((node.findtext("d:href", "", ns) or ""))
        # href is the full dav path — make it relative to the user root
        marker = href.find("/remote.php/dav/files/")
        tail = href[marker:] if marker >= 0 else href
        tail = "/" + tail.split("/", 5)[-1] if tail.count("/") >= 5 else tail
        is_dir = node.find(".//d:collection", ns) is not None
        if self_path is None:
            self_path = tail.rstrip("/")
            continue
        if is_dir:
            await _dav_walk(client, base, tail.rstrip("/"), dest_root, stats)
        else:
            name = tail.rstrip("/").split("/")[-1]
            if is_video_file(name):
                stats["skipped_videos"] += 1
                continue
            sub = tail[len("/AgentSkills"):].lstrip("/") if tail.startswith("/AgentSkills") else name
            out = dest_root / sub
            out.parent.mkdir(parents=True, exist_ok=True)
            got = await client.get(base + quote(tail))
            if got.status_code == 200:
                out.write_bytes(got.content)
                stats["files"] += 1


async def run_cove_backup(trigger: str = "manual") -> dict:
    """The full CF-112 run. Never raises — always records + returns a status."""
    started = datetime.now(ZoneInfo("America/New_York"))
    ts = started.strftime("%Y-%m-%d_%H-%M")
    cfg = get_backup_config()
    token = _get_token()
    clean_remote = normalize_remote_url(cfg["remote_url"])
    detail = {"trigger": trigger}
    if not (clean_remote and token):
        st = {"ts": started.isoformat(), "ok": False,
              "summary": "Not configured (repo URL + token needed).", "detail": detail}
        _set_last_status(st)
        return st

    root = Path(env("BACKUP_COVE_DIR", "/app/data/cove-backup"))
    try:
        _ensure_repo(root, clean_remote)

        # 1 · DB dump (exclude rebuildable LangGraph checkpoint runtime state)
        db_dir = root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        dump_file = db_dir / f"{ts}.sql.gz"
        db_url = env("DATABASE_URL")
        exclude = " ".join(
            f"--exclude-table-data={shlex.quote(t)}" for t in PG_DUMP_EXCLUDE_TABLES
        )
        # Schema for excluded tables still ships; data does not.
        dump_cmd = (
            f"pg_dump {shlex.quote(db_url)} {exclude} "
            f"| gzip > {shlex.quote(str(dump_file))}"
        )
        p = _run(dump_cmd, shell=True, timeout=PG_DUMP_TIMEOUT_SEC)
        # Drop partial dumps on failure so the next run starts clean.
        if p.returncode != 0 and dump_file.exists():
            try:
                dump_file.unlink()
            except Exception:
                pass
        db_ok = p.returncode == 0 and dump_file.exists() and dump_file.stat().st_size > 0
        parts: list = []
        if db_ok:
            parts = write_dump_chunks(dump_file)
            db_ok = bool(parts) and all(x.exists() and x.stat().st_size > 0 for x in parts)
        detail["db"] = {
            "ok": db_ok,
            "err": redact_secrets((p.stderr or "").strip())[:200],
            "bytes": sum(x.stat().st_size for x in parts) if parts else (
                dump_file.stat().st_size if dump_file.exists() else 0
            ),
            "parts": len(parts),
            "files": [x.name for x in parts],
            "excluded_data": list(PG_DUMP_EXCLUDE_TABLES),
        }
        detail["db"]["rotated"] = rotate_dumps(db_dir)

        # 2 · config (redacted)
        cfg_dir = root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in Path("/app/config").glob("**/*.yaml"):
            rel = f.relative_to("/app/config")
            out = cfg_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(redact_config_text(f.read_text()))
            copied += 1
        detail["config"] = {"files": copied}

        # 3 · NC AgentSkills (per presence, video-excluded)
        detail["nc"] = await _backup_nc_agentskills(root)

        # 4 · commit + push (token only in the push URL, reset after)
        # Guard: never attempt a push that GitHub will hard-reject (>100MB blob).
        oversized = reject_if_still_oversized(root)
        if oversized:
            detail["push"] = {
                "ok": False,
                "note": "blocked",
                "err": f"files over GitHub 100MB limit: {', '.join(oversized[:8])}",
            }
            summary = (
                f"db {'✓' if detail['db']['ok'] else '✗'} · config {copied} files · "
                f"files {detail['nc']['files']} across {detail['nc']['users']} presences "
                f"({detail['nc']['skipped_videos']} videos excluded) · "
                f"push ✗ {detail['push']['err'][:80]}"
            )
            st = {"ts": started.isoformat(), "ok": False, "summary": summary, "detail": detail}
            _set_last_status(st)
            return st

        _run(["git", "add", "-A"], cwd=root, timeout=120)
        c = _run(["git", "commit", "-m", f"Cove backup {ts} ({trigger})"], cwd=root, timeout=60)
        nothing_new = "nothing to commit" in ((c.stdout or "") + (c.stderr or "")).lower()
        push_url = build_push_url(clean_remote, token)
        try:
            _run(["git", "remote", "set-url", "origin", push_url], cwd=root)
            push = _run(["git", "push", "-u", "origin", "main"], cwd=root, timeout=GIT_PUSH_TIMEOUT_SEC)
        finally:
            _run(["git", "remote", "set-url", "origin", clean_remote], cwd=root)
        push_txt = ((push.stdout or "") + (push.stderr or "")).strip()
        ok = push.returncode == 0 and detail["db"]["ok"]
        detail["push"] = {"ok": push.returncode == 0,
                          "note": "no changes" if nothing_new else "pushed",
                          "err": "" if push.returncode == 0 else redact_secrets(push_txt)[:300]}

        summary = (f"db {'✓' if detail['db']['ok'] else '✗'} · config {copied} files · "
                   f"files {detail['nc']['files']} across {detail['nc']['users']} presences "
                   f"({detail['nc']['skipped_videos']} videos excluded) · "
                   f"push {'✓' if detail['push']['ok'] else '✗ ' + detail['push']['err'][:80]}")
        st = {"ts": started.isoformat(), "ok": ok, "summary": summary, "detail": detail}
    except Exception as e:
        log.error("cove backup failed: %s", e)
        st = {"ts": started.isoformat(), "ok": False,
              "summary": f"Backup error: {type(e).__name__}: {redact_secrets(str(e))[:160]}",
              "detail": detail}
    _set_last_status(st)
    return st
