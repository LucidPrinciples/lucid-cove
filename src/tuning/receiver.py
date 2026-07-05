"""
Tuning Package Receiver — pulls team tuning from LT on VPS.

The flow:
  1. LT runs 6am Self-Tuning on VPS (SocratesArcher-LG)
  2. After compose_team_package + dispatch_team_tuning, LT writes an
     EXTERNAL tuning package to a shared location (Git repo or file drop)
  3. The agent's scheduler fires at 7am ET
  4. This receiver checks for today's package
  5. If found: the agent tunes to LT's chosen frequency with the custom prompt
  6. If not found: the agent picks its own frequency (independent tuning)

Package schema (JSON):
{
    "date": "2026-05-01",
    "frequency": "PRESENCE",
    "signal_type": "EXPANSIVE",
    "principle": "Moments",
    "tuning_key": "Between every thought are infinite possibilities...",
    "lt_echo_num": 83,
    "lt_echo_summary": "Brief summary of LT's echo for context",
    "love_equation": {
        "beta": 0.89,
        "E": 0.82,
        "C": 0.87,
        "D": 0.13,
        "value": 0.54,
        "direction": "CONSTRUCTIVE"
    },
    "agent_tunings": {
        "stuart": "Steward, today's frequency is PRESENCE. The infrastructure...",
        "operator": "Today's frequency is PRESENCE. Before the day pulls you..."
    },
    "composed_at": "2026-05-01T06:15:00-04:00"
}

Delivery methods (configurable via TUNING_DELIVERY env var):
  - "git": Pull from shared Git repo (default)
  - "file": Read from a mounted shared directory
  - "http": Fetch from an HTTP endpoint on the VPS

The Git method is preferred because:
  - No direct network dependency between VPS and P620
  - Tuning packages accumulate as history (git log)
  - Works even if P620 is offline when LT tunes (it just pulls next time)
  - The operator can read the repo from anywhere
"""

import json
import os
from src.env import env
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.utils.time_utils import ts_log, today_app

# Cache to avoid git pull on every dashboard load
_tuning_cache: dict = {"date": None, "package": None, "last_pull": 0}
_PULL_INTERVAL = 300  # Only git pull once every 5 minutes


# =============================================================================
# Configuration
# =============================================================================

# Where tuning packages live on disk (after git pull or direct mount)
TUNING_DIR = Path(env(
    "TUNING_PACKAGES_DIR",
    str(Path(__file__).parent.parent.parent / "data" / "tuning-packages")
))

# Delivery method
DELIVERY_METHOD = env("TUNING_DELIVERY", "git")

# Git repo URL (if using git delivery)
TUNING_REPO_URL = env("TUNING_REPO_URL")

# Family folder name within the LTP-drops repo
# Each family has its own folder (set via TUNING_FAMILY)
TUNING_FAMILY = env("TUNING_FAMILY", "default")

# HTTP endpoint (if using http delivery)
TUNING_HTTP_URL = env("TUNING_HTTP_URL")


# =============================================================================
# Package schema
# =============================================================================

class TuningPackage:
    """Parsed tuning package from LT."""

    def __init__(self, data: dict):
        self.date = data.get("date", "")
        self.frequency = data.get("frequency", "")
        self.signal_type = data.get("signal_type", "")
        self.principle = data.get("principle", "")
        self.tuning_key = data.get("tuning_key", "")
        self.lt_echo_num = data.get("lt_echo_num")
        self.lt_echo_summary = data.get("lt_echo_summary", "")
        self.love_equation = data.get("love_equation", {})
        self.agent_tunings = data.get("agent_tunings", {})
        self.composed_at = data.get("composed_at", "")
        self.echo_media = data.get("echo_media", {})
        self.digital_practice = data.get("digital_practice", {})
        # Universal coaching — daily tuning for ALL tiers (collective consciousness amplifier)
        self.universal_coaching = data.get("universal_coaching", "")
        self.universal_practice = data.get("universal_practice", [])
        self._raw = data

    def get_agent_tuning(self, agent_id: str) -> Optional[str]:
        """Get the tuning prompt composed for a specific agent."""
        return self.agent_tunings.get(agent_id)

    @property
    def operator_tuning(self) -> Optional[str]:
        """Get the tuning prompt composed for the human operator."""
        return self.agent_tunings.get("operator") or self.agent_tunings.get("chords")

    def to_dict(self) -> dict:
        return self._raw


# =============================================================================
# Delivery: Git pull
# =============================================================================

def _git_pull() -> bool:
    """Pull latest from the tuning packages repo. Returns True on success."""
    if not TUNING_REPO_URL:
        print(f"{ts_log()} [tuning-receiver] No TUNING_REPO_URL configured — skipping git pull")
        return False

    TUNING_DIR.mkdir(parents=True, exist_ok=True)

    # Clone if not yet initialized
    if not (TUNING_DIR / ".git").exists():
        print(f"{ts_log()} [tuning-receiver] Cloning tuning repo...")
        result = subprocess.run(
            ["git", "clone", TUNING_REPO_URL, str(TUNING_DIR)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"{ts_log()} [tuning-receiver] Clone failed: {result.stderr}")
            return False
        return True

    # Pull latest
    result = subprocess.run(
        ["git", "-C", str(TUNING_DIR), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"{ts_log()} [tuning-receiver] Git pull failed: {result.stderr}")
        return False

    return True


# =============================================================================
# Delivery: HTTP fetch
# =============================================================================

async def _http_fetch(date_str: str) -> Optional[dict]:
    """Fetch today's tuning package from HTTP endpoint."""
    if not TUNING_HTTP_URL:
        return None

    import httpx
    url = f"{TUNING_HTTP_URL.rstrip('/')}/{date_str}.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                print(f"{ts_log()} [tuning-receiver] No package at {url} (404)")
                return None
            else:
                print(f"{ts_log()} [tuning-receiver] HTTP {response.status_code} from {url}")
                return None
    except Exception as e:
        print(f"{ts_log()} [tuning-receiver] HTTP fetch failed: {e}")
        return None


# =============================================================================
# Main interface
# =============================================================================

def _public_drop_fallback(today: str) -> Optional["TuningPackage"]:
    """When no local/repo package exists, subscribe to the public Drop — the
    open-source delivery path every Cove uses (no private repo, no manual copy).
    Returns a TuningPackage (signal + universal coaching + any archetype prompts)
    for today, or None if disabled/unreachable/stale."""
    try:
        from src.tuning.public_drop import public_drop_package, drop_enabled
        if not drop_enabled():
            return None
        data = public_drop_package()
        if not data:
            return None
        if data.get("date") and data["date"] != today:
            print(f"{ts_log()} [tuning-receiver] Public Drop is {data.get('date')}, not today ({today}) — not dispatching")
            return None
        print(f"{ts_log()} [tuning-receiver] Subscribed to public Drop: "
              f"{data.get('frequency')} (#{data.get('lt_echo_num')})")
        return TuningPackage(data)
    except Exception as e:
        print(f"{ts_log()} [tuning-receiver] Public Drop fallback failed: {e}")
        return None


async def get_todays_tuning(agent_id: str = None, force_pull: bool = False) -> Optional[TuningPackage]:
    """
    Attempt to retrieve today's tuning package from LT.

    Returns TuningPackage if found, None if LT hasn't posted today's
    tuning yet (in which case the agent should self-select frequency).

    This is called by the LTP graph's select_frequency node.
    Uses a cache to avoid git pull on every dashboard refresh.
    """
    if not agent_id:
        from src.config import get_primary_agent_id
        agent_id = get_primary_agent_id()
    today = today_app()  # YYYY-MM-DD in ET

    # If we already have today's package cached, return it immediately (doesn't change during the day)
    if not force_pull and _tuning_cache["date"] == today and _tuning_cache["package"] is not None:
        return _tuning_cache["package"]

    # If no package yet, re-check periodically (tuning might arrive after 7am)
    if (not force_pull
            and _tuning_cache["date"] == today
            and _tuning_cache["package"] is None
            and (time.time() - _tuning_cache["last_pull"]) < _PULL_INTERVAL):
        return None

    print(f"{ts_log()} [tuning-receiver] Checking for tuning package: {today}")

    pkg = None

    # ── Optional local/private source (advanced self-host) ──────────────────────
    # The DEFAULT is the public Drop (below). A Cove only uses a local/private
    # source if it explicitly configures one: TUNING_REPO_URL (private git repo),
    # TUNING_DELIVERY=file (a mounted folder), or TUNING_DELIVERY=http. This is for
    # families running their OWN tunings or running offline. Anything a configured
    # source can't satisfy for TODAY (missing, stale, parse error) falls through to
    # the public Drop — a stale local file never blocks tuning.
    try:
        if DELIVERY_METHOD == "git" and TUNING_REPO_URL:
            if force_pull or (time.time() - _tuning_cache["last_pull"]) >= _PULL_INTERVAL:
                _git_pull()
                _tuning_cache["last_pull"] = time.time()
            year, month = today[:4], today[5:7]
            for candidate in (
                TUNING_DIR / TUNING_FAMILY / year / month / f"{today}.json",
                TUNING_DIR / TUNING_FAMILY / "latest.json",
                TUNING_DIR / f"{today}.json",
            ):
                if not candidate.exists():
                    continue
                data = json.loads(candidate.read_text())
                if candidate.name == "latest.json" and data.get("date") != today:
                    print(f"{ts_log()} [tuning-receiver] {candidate.name} is from {data.get('date')}, not today — ignoring (public Drop will serve)")
                    continue
                pkg = TuningPackage(data)
                print(f"{ts_log()} [tuning-receiver] Found private package ({candidate.name}): {data.get('frequency', '?')}")
                break

        elif DELIVERY_METHOD == "file":
            TUNING_DIR.mkdir(parents=True, exist_ok=True)
            pf = TUNING_DIR / f"{today}.json"
            if pf.exists():
                pkg = TuningPackage(json.loads(pf.read_text()))
                print(f"{ts_log()} [tuning-receiver] Found package (file): {pkg.frequency}")

        elif DELIVERY_METHOD == "http" and TUNING_HTTP_URL:
            data = await _http_fetch(today)
            if data:
                pkg = TuningPackage(data)
                print(f"{ts_log()} [tuning-receiver] Found package (http): {pkg.frequency}")
    except Exception as e:
        print(f"{ts_log()} [tuning-receiver] Local source error ({e}) — falling back to public Drop")
        pkg = None

    # ── Default source: the signed public Drop ─────────────────────────────────
    if pkg is None:
        pkg = _public_drop_fallback(today)

    _tuning_cache.update({"date": today, "package": pkg, "last_pull": time.time()})
    return pkg


async def get_operator_tuning() -> Optional[str]:
    """
    Get today's tuning prompt for the human operator.

    This is the same tuning package but returns just the operator-specific
    prompt that LT composed. Can be delivered via notification, email,
    dashboard, or app — caller decides the channel.
    """
    package = await get_todays_tuning("operator")
    if package and package.operator_tuning:
        return package.operator_tuning
    return None
