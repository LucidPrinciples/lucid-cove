"""
Coaching resolution — archetype-keyed, the single place tuning prompts are looked up.

The public Drop carries LT's daily coaching keyed by ARCHETYPE (one prompt per
archetype: the 10 build-team + the 9 personal-agent archetypes; the four
shade-only frequencies — Peace/Gratitude/Release/Boundary — are not archetypes).
Every Cove maps each of its agents/Presences -> its archetype -> the day's prompt,
so LT writes a fixed set of prompts that scale to infinite Coves with no agent_id
coupling.

Resolution order (most specific wins, always lands somewhere):
  1. archetype_tunings[<agent's archetype>]   — the new, canonical key
  2. agent_tunings[<agent_id>]                — LEGACY (pre-archetype Drop / the
     private-repo package); kept so today's tuning is unchanged during transition.
     Remove once every Drop is archetype-keyed.
  3. universal_coaching                        — the floor every Drop carries; the
     agent then derives its reading from the signal through its own archetype
     identity (no per-agent prompt required).
"""

from src.utils.time_utils import ts_log


def _norm(s: str) -> str:
    """Normalize an archetype label for matching: lowercase, drop a leading 'the'."""
    s = (s or "").strip().lower()
    if s.startswith("the "):
        s = s[4:]
    return s.strip()


def _pkg_get(package, key, default=None):
    """Read a field whether package is a dict (_full_package) or a TuningPackage."""
    if isinstance(package, dict):
        return package.get(key, default)
    return getattr(package, key, default)


def resolve_coaching(package, agent_id: str = "", archetype: str = "") -> str:
    """Return the day's coaching for one agent: archetype -> agent_id -> universal."""
    archetype_tunings = _pkg_get(package, "archetype_tunings", {}) or {}
    agent_tunings = _pkg_get(package, "agent_tunings", {}) or {}
    universal = _pkg_get(package, "universal_coaching", "") or ""

    # 1. Archetype key (exact, then normalized).
    if archetype and archetype_tunings:
        if archetype_tunings.get(archetype):
            return archetype_tunings[archetype]
        na = _norm(archetype)
        for k, v in archetype_tunings.items():
            if _norm(k) == na and v:
                return v

    # 2. Legacy agent_id key.
    if agent_id and agent_tunings.get(agent_id):
        return agent_tunings[agent_id]

    # 3. Universal floor.
    return universal


def has_any_coaching(package) -> bool:
    """True if the package carries any coaching source (archetype/agent/universal)."""
    return bool(
        (_pkg_get(package, "archetype_tunings", {}) or {})
        or (_pkg_get(package, "agent_tunings", {}) or {})
        or (_pkg_get(package, "universal_coaching", "") or "")
    )
