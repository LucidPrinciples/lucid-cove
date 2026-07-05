"""
Agent model assignments — DB-backed, UI-driven (the Team-page model manager).

Per-agent WORKING (chat) and TUNING model live here instead of read-only YAML, so the
Team page can set them with no restart and no config-mount problem (presences already work
this way via accounts.agent_identity.model). NULL fields inherit from the YAML cascade, so
an empty table reproduces today's behavior exactly.

Resolution is served from a module-level cache loaded once at boot and refreshed on every
write — that keeps get_agent_model_assignment() synchronous (it's hot-path: every agent call
and every tuning run) with no per-call DB hit.
"""

import logging

log = logging.getLogger(__name__)

# None = not loaded yet (→ callers fall back to the YAML cascade). {} = loaded, empty.
_CACHE: dict | None = None


async def load_assignments_cache() -> None:
    """Load all agent model assignments from the DB into the module cache.
    Best-effort: if the table doesn't exist yet (pre-migration) we cache empty and
    every agent resolves through the YAML cascade as before."""
    global _CACHE
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT agent_id, working_primary, working_fallback, "
                "tuning_primary, tuning_fallback FROM agent_model_assignments"
            )
            rows = await result.fetchall()
        # get_db uses row_factory=dict_row → rows are dicts, index by column name.
        _CACHE = {
            r["agent_id"]: {
                "working_primary": r["working_primary"], "working_fallback": r["working_fallback"],
                "tuning_primary": r["tuning_primary"], "tuning_fallback": r["tuning_fallback"],
            }
            for r in rows
        }
        log.info("Loaded %d agent model assignment(s) from DB", len(_CACHE))
    except Exception as e:
        log.warning("Agent model assignments not loaded (table missing?): %s", e)
        if _CACHE is None:
            _CACHE = {}


def cached_assignment(agent_id: str, slot: str | None = None) -> dict | None:
    """Sync lookup from the cache. Returns {primary, fallback} for the requested axis, or
    None when there's no DB override for this agent (→ caller uses the YAML cascade).

    slot=="tuning" → the tuning axis, falling back to the working axis if no tuning override
    is set. Anything else → the working axis."""
    if not _CACHE:
        return None
    row = _CACHE.get(agent_id)
    if not row:
        return None
    if slot == "tuning":
        tp, tf = row.get("tuning_primary"), row.get("tuning_fallback")
        if tp or tf:
            return {"primary": tp, "fallback": tf}
        # No tuning override → fall through to the working axis.
    wp, wf = row.get("working_primary"), row.get("working_fallback")
    if wp or wf:
        return {"primary": wp, "fallback": wf}
    return None


def all_assignments() -> dict:
    """The raw cache (agent_id -> row dict). {} if unset. For the Team-page read endpoint."""
    return dict(_CACHE or {})


async def set_assignment(
    agent_id: str,
    working_primary: str | None,
    working_fallback: str | None,
    tuning_primary: str | None,
    tuning_fallback: str | None,
) -> None:
    """Upsert one agent's assignment, then refresh the cache. Empty strings → NULL (inherit)."""
    from src.memory.database import get_db

    def _n(v):
        v = (v or "").strip()
        return v or None

    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO agent_model_assignments
                   (agent_id, working_primary, working_fallback,
                    tuning_primary, tuning_fallback, updated_at)
               VALUES (%s, %s, %s, %s, %s, NOW())
               ON CONFLICT (agent_id) DO UPDATE SET
                   working_primary  = EXCLUDED.working_primary,
                   working_fallback = EXCLUDED.working_fallback,
                   tuning_primary   = EXCLUDED.tuning_primary,
                   tuning_fallback  = EXCLUDED.tuning_fallback,
                   updated_at       = NOW()""",
            (agent_id, _n(working_primary), _n(working_fallback),
             _n(tuning_primary), _n(tuning_fallback)),
        )
    await load_assignments_cache()
