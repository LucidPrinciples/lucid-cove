"""
Agent presets — the Quick door of Agent Setup.

Serves the framework-native template archetypes (data/agent-presets.json) the
Quick path offers: pick a preset, name it, provision. Each preset pre-fills the
archetype + frequency + tuning key + a seed persona + default personality dials,
so a keyless self-hoster can stand up a personal agent in seconds.

The three input doors (Quick / Guided / Dictate) all converge on the same engine
(POST /api/presence/provision, COVE_MODE=multi). Quick just pre-fills the identity
instead of deriving it. See Reference/cove-bootstrap-onboarding-spec.md §10.2.

SINGLE SOURCE OF TRUTH: the archetype library is data/agent-presets.json. Nothing
here hardcodes a copy — edit the JSON and it filters through. This module only
resolves WHERE that file lives at runtime (container mount vs local repo).
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger(__name__)


# In the container, docker/entrypoint.sh merges ONLY /cove-core/src -> /app/src;
# data/ is NOT copied, it stays on the read-only cove-core mount at /cove-core/data
# (same place FRAMEWORK_DIR = /cove-core/data/knowledge-base lives). Locally (running
# from the repo) resolve relative to the repo root instead. No data is duplicated here
# — these are just the places the one JSON file can physically be.
def _candidate_paths():
    here = Path(__file__).resolve()
    return [
        Path("/cove-core/data/agent-presets.json"),         # container (canonical)
        here.parents[3] / "data" / "agent-presets.json",    # local repo root
        Path("/app/data/agent-presets.json"),               # merged tree (defensive)
    ]


def _load_presets() -> dict:
    """Load the preset library from the JSON file (first readable candidate path).
    Returns {version, presets}. If the file genuinely can't be read, returns an
    empty list and logs LOUDLY — that's a packaging bug to surface, not to mask
    with a stale embedded copy."""
    for p in _candidate_paths():
        try:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            log.error("agent-presets read failed at %s: %s", p, e)
    log.error("agent-presets.json not found in any candidate path: %s",
              ", ".join(str(p) for p in _candidate_paths()))
    return {"version": 1, "presets": []}


@router.get("/api/flow/agent-presets")
async def get_agent_presets():
    """List the Quick-door template archetypes for the Agent Setup gallery."""
    data = _load_presets()
    presets = data.get("presets", [])
    # Strip the internal _comment; expose only what the gallery needs.
    return {
        "ok": True,
        "version": data.get("version", 1),
        "count": len(presets),
        "presets": presets,
    }


@router.get("/api/flow/agent-presets/{preset_id}")
async def get_agent_preset(preset_id: str):
    """Fetch a single preset by id (used when the operator picks one to name)."""
    for p in _load_presets().get("presets", []):
        if p.get("id") == preset_id:
            return {"ok": True, "preset": p}
    return JSONResponse({"ok": False, "error": f"Unknown preset '{preset_id}'"}, status_code=404)
