# =============================================================================
# host_reachability.py — MESH3 L2: read host punchability probe for Attention
# =============================================================================
# The app container has no tailscale CLI and must not hold the docker socket.
# The operator (or install) runs scripts/probe-host-reachability.sh on the HOST;
# that writes config/host_reachability.json on the bind-mounted config dir.
# This module only reads + classifies that file and builds the host command.
#
# Orthogonal to routes/reachability.py (Cloudflare *public tunnel* for remote
# invites). L2 never opens the Cove to the public internet — mesh only.
# =============================================================================
from __future__ import annotations

import json
import logging
import os
import posixpath
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATUS_NAME = "host_reachability.json"
# Re-nudge if last probe is older than this (seconds) and was hard / missing.
STALE_AFTER_SEC = 7 * 24 * 3600


def _config_dir() -> Path:
    return Path(os.environ.get("COVE_CONFIG_DIR") or "/app/config")


def status_path() -> Path:
    return _config_dir() / STATUS_NAME


def _mountinfo_host_paths() -> tuple[str, str]:
    """Best-effort (host_config_dir, host_clone_dir) from /proc/self/mountinfo.

    Stamped layouts bind-mount:
      <clone>/out/<id>-cove/config → /app/config
      <clone>                     → /cove-core  (optional, ro)
    When COVE_HOST_DIR was never stamped (Clearfield founder path), env is empty
    but mountinfo still has the real host paths — use them so Attention never
    prints a relative command that only works if cwd happens to be right.
    """
    host_config = ""
    host_clone = ""
    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", ""
    for line in text.splitlines():
        # mountinfo: ... <root> <mountpoint> ... - <fstype> <source> ...
        # We care about the host path in the optional field after the separator
        # when root is a real host dir (bind mounts show root as the host path).
        try:
            left, _right = line.split(" - ", 1)
        except ValueError:
            continue
        parts = left.split()
        if len(parts) < 5:
            continue
        root = parts[3]
        mountpoint = parts[4]
        if not root.startswith("/"):
            continue
        if mountpoint == "/app/config" and root != "/":
            host_config = root.rstrip("/")
        elif mountpoint == "/cove-core" and root != "/":
            host_clone = root.rstrip("/")
    return host_config, host_clone


def _resolve_probe_paths() -> tuple[str, str]:
    """Return (script_path_or_rel, out_path) for the host command.

    Prefer stamped env / cove.yaml deploy paths (same as runbooks), then
    mountinfo bind sources, then a relative dev fallback.
    """
    cove_dir = ""
    clone_dir = ""
    try:
        from src.dashboard.routes.runbooks import _host_paths
        cove_dir, clone_dir = _host_paths()
    except Exception:
        pass

    host_instance = (os.environ.get("COVE_HOST_DIR") or "").strip()
    if host_instance and not cove_dir:
        cove_dir = host_instance.rstrip("/")
    if host_instance and not clone_dir:
        clone_dir = posixpath.dirname(posixpath.dirname(host_instance.rstrip("/")))

    if not (cove_dir and clone_dir):
        m_cfg, m_clone = _mountinfo_host_paths()
        if m_cfg and not cove_dir:
            # m_cfg is .../out/<id>-cove/config → instance dir is parent
            if posixpath.basename(m_cfg) == "config":
                cove_dir = posixpath.dirname(m_cfg)
            else:
                cove_dir = m_cfg
        if m_clone and not clone_dir:
            clone_dir = m_clone
        # If we only got config mount: derive clone as parent of out/
        if cove_dir and not clone_dir:
            # <clone>/out/<id>-cove
            parent = posixpath.dirname(cove_dir.rstrip("/"))
            if posixpath.basename(parent) == "out":
                clone_dir = posixpath.dirname(parent)

    if clone_dir and cove_dir:
        script = posixpath.join(clone_dir, "scripts", "probe-host-reachability.sh")
        out = posixpath.join(cove_dir.rstrip("/"), "config", STATUS_NAME)
        return script, out

    return (
        f"scripts/probe-host-reachability.sh",
        f"./config/{STATUS_NAME}",
    )


def host_probe_command() -> str:
    """Exact command the founder runs on the box (same shape as set-address)."""
    script, out = _resolve_probe_paths()
    # Absolute script → full bash path form; relative keeps prior dev UX.
    if script.startswith("/") or script.startswith("~"):
        return f"bash {script} --out {out}"
    return f"bash {script} --out {out}"


def read_status() -> dict[str, Any] | None:
    """Return parsed probe JSON, or None if missing/unreadable."""
    path = status_path()
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("host_reachability read failed: %s", e)
        return None


def _parse_ts(ts: str | None) -> float | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # 2026-07-18T16:57:34Z
        from datetime import datetime, timezone
        s = ts.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def classify(status: dict[str, Any] | None) -> dict[str, Any]:
    """
    Build the Attention-facing summary.

    done / hide card when:
      - operator skipped (handled by caller via agent_config), or
      - last probe exists, is fresh enough, and hard_to_reach is false

    show fix card when hard_to_reach is true

    show "run check" card when never probed (or unreadable) after foundation
    """
    cmd = host_probe_command()
    if not status:
        return {
            "probed": False,
            "hard_to_reach": None,
            "done": False,
            "available_reason": "never_probed",
            "host_command": cmd,
            "status": None,
        }

    hard = bool(status.get("hard_to_reach"))
    ts = _parse_ts(status.get("ts") if isinstance(status.get("ts"), str) else None)
    age = (time.time() - ts) if ts is not None else None
    stale = age is not None and age > STALE_AFTER_SEC

    if hard:
        return {
            "probed": True,
            "hard_to_reach": True,
            "done": False,
            "available_reason": "hard_to_reach",
            "host_command": cmd,
            "status": status,
            "stale": stale,
        }

    # Easy path — card clears (self-clear, like backup green).
    if stale:
        # Soft re-check after a week; don't claim "hard".
        return {
            "probed": True,
            "hard_to_reach": False,
            "done": False,
            "available_reason": "stale_ok_recheck",
            "host_command": cmd,
            "status": status,
            "stale": True,
        }

    return {
        "probed": True,
        "hard_to_reach": False,
        "done": True,
        "available_reason": "ok",
        "host_command": cmd,
        "status": status,
        "stale": False,
    }
