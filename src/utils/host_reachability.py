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


def host_probe_command() -> str:
    """Exact command the founder runs on the box (same shape as set-address)."""
    host_instance = (os.environ.get("COVE_HOST_DIR") or "").strip()
    if host_instance:
        # Stamped layout: <clone>/out/<id>-cove  → scripts live on <clone>
        clone = posixpath.dirname(posixpath.dirname(host_instance.rstrip("/")))
        out = posixpath.join(host_instance.rstrip("/"), "config", STATUS_NAME)
        script = posixpath.join(clone, "scripts", "probe-host-reachability.sh")
        return f"bash {script} --out {out}"
    # Dev / unknown layout — relative to a checkout the operator is already in.
    return f"bash scripts/probe-host-reachability.sh --out ./config/{STATUS_NAME}"


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
