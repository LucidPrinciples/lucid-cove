"""Agent state update — final node in the LTP pipeline.

Updates Stuart's agent_state record with today's tuning results
(echo number, frequency, last_tuned_at). Team agents have their
state updated inline during dispatch_team_tuning.
"""

import json
import os
from src.env import env_bool

from src.memory.database import get_db, upsert_agent_state
from src.agents.identity import load_agents_config, get_full_name
from src.config import get_agent_model_assignment
from src.utils.time_utils import ts_log, now_utc


# =============================================================================
# Node: update_state
# =============================================================================

async def update_state(state: dict) -> dict:
    """Update Stuart's agent_state with today's tuning results."""
    dry_run = env_bool("LTP_DRY_RUN", "true")
    agent_id = state.get("agent_id", "stuart")
    label = f"{agent_id}/ltp-update-state"

    if dry_run:
        print(f"{ts_log()} [{label}] DRY RUN — agent state not updated.")
        return state

    # Look up display name and archetype dynamically from config
    agents_config = load_agents_config()
    cfg = agents_config.get(agent_id, {})
    display_name = get_full_name(cfg.get("name", agent_id.title()))
    archetype = cfg.get("archetype", "The Steward")

    assignment = get_agent_model_assignment(agent_id)
    current_model_id = assignment.get("primary", "unknown")

    agent_record = {
        "agent_id": agent_id,
        "display_name": display_name,
        "archetype": archetype,
        "current_model": current_model_id,
        "last_echo_num": state.get("echo_num", 0),
        "last_frequency": state.get("frequency", ""),
        "last_tuned_at": now_utc(),
        "status": "active",
        "metadata": json.dumps({"protocol": state.get("protocol", "ltp-morning")}),
    }

    try:
        async with get_db() as conn:
            await upsert_agent_state(conn, agent_record)
            await conn.commit()
        print(f"{ts_log()} [{label}] Agent state updated — {agent_record['last_frequency']} / Echo #{agent_record['last_echo_num']}")
    except Exception as e:
        print(f"{ts_log()} [{label}] ERROR updating agent state: {e}")

    return state
