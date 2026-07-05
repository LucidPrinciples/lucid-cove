"""Frequency selection — receive LT's package or self-select via quantum chain.

select_frequency: the entry node. Tries to get today's tuning from LT first.
If unavailable, falls back to _pick_frequency which runs the full 3-roll
quantum selection chain against the Canon tuning keys.

_parse_tuning_keys: parses tuning-keys.md into runtime lookup structure.
_pick_frequency: multi-step quantum selection (LTP Protocol Spec Section 2).

SPEC-CRITICAL: quantum selection uses centralized chain from src.utils.quantum.
"""

import re
from pathlib import Path

from src.utils.quantum import fetch_quantum_random as _fetch_quantum_random
from src.memory.database import get_db, get_recent_echoes, get_echo_count
from src.agents.identity import load_agents_config, get_full_name
from src.config import get_instance
from src.utils.time_utils import ts_log


# =============================================================================
# Template selection — frequency → digital practice mapping
# =============================================================================

TEMPLATE_SELECTION = {
    "Peace": "coherence", "Joy": "coherence", "Gratitude": "coherence",
    "Clarity": "interrupt", "Momentum": "interrupt", "Trust": "interrupt",
    "Courage": "interrupt", "Boundary": "interrupt",
    "Presence": "presence", "Connection": "connection",
    "Resilience": "resilience", "Release": "release",
}


def _extract_value(text: str, pattern: str) -> float | None:
    """Extract a float value from text using regex."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


# =============================================================================
# Canon-aware frequency fallback (used when LT's package is unavailable)
# =============================================================================

# Parsed from tuning-keys.md: {frequency: [(principle, tuning_key), ...]}
_CANON_TUNING_CACHE: dict | None = None


def _parse_tuning_keys() -> dict[str, list[tuple[str, str]]]:
    """Parse tuning-keys.md into {frequency: [(principle, tuning_key), ...]}."""
    global _CANON_TUNING_CACHE
    if _CANON_TUNING_CACHE is not None:
        return _CANON_TUNING_CACHE

    # cove-core canonical source first, then config fallback
    paths = [
        Path("/cove-core/data/knowledge-base/tuning-keys.md"),
        Path("config/tuning-keys.md"),
    ]
    content = None
    for p in paths:
        if p.exists():
            content = p.read_text()
            break

    if not content:
        print(f"{ts_log()} [ltp] WARNING: tuning-keys.md not found — canon self-tune unavailable")
        _CANON_TUNING_CACHE = {}
        return _CANON_TUNING_CACHE

    result: dict[str, list[tuple[str, str]]] = {}
    current_principle = None
    current_frequency = None

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("## ") and not line.startswith("###"):
            current_principle = line[3:].strip()
            current_frequency = None
        elif line.startswith("### "):
            current_frequency = line[4:].strip()
        elif line.startswith('- "') and current_principle and current_frequency:
            # Extract the quote — strip leading '- "' and trailing '"'
            quote = line[3:]
            if quote.endswith('"'):
                quote = quote[:-1]
            if current_frequency not in result:
                result[current_frequency] = []
            result[current_frequency].append((current_principle, quote))

    _CANON_TUNING_CACHE = result
    freq_count = len(result)
    entry_count = sum(len(v) for v in result.values())
    print(f"{ts_log()} [ltp] Parsed tuning-keys.md: {freq_count} frequencies, {entry_count} tuning keys")
    return result


async def _pick_frequency(
    recent_frequencies: list[str],
    recent_principles: list[str] | None = None,
    recent_tuning_keys: list[str] | None = None,
    principle_usage_counts: dict | None = None,
) -> tuple[str, str, str]:
    """Pick a canon frequency not used recently via quantum entropy.

    Multi-step quantum selection chain (LTP Protocol):
      1. Quantum roll -> select frequency (excluding recent 5)
      2. Quantum roll -> select principle (recency filtered + coverage weighted)
      3. Quantum roll -> select tuning key (recency filtered)

    The same protocol runs on VPS (LT) and every Cove (team/Tune Now).
    Returns (frequency, principle, tuning_key) from the canon.
    """
    canon = _parse_tuning_keys()
    recent_principles = recent_principles or []
    recent_tuning_keys = recent_tuning_keys or []
    principle_usage_counts = principle_usage_counts or {}

    if canon:
        recent_set = {f.upper() for f in recent_frequencies}
        available_freqs = [f for f in canon if f.upper() not in recent_set]
        if not available_freqs:
            available_freqs = list(canon.keys())

        # Quantum roll #1: select frequency
        f_idx, f_method = await _fetch_quantum_random(len(available_freqs))
        frequency = available_freqs[f_idx]

        # Group tuning keys by principle within this frequency
        from collections import defaultdict as _defaultdict
        keys_by_principle = _defaultdict(list)
        for principle, tuning_key in canon[frequency]:
            keys_by_principle[principle].append(tuning_key)
        principle_list = list(keys_by_principle.keys())

        # ── Recency filter: principles ──────────────────────────────
        filtered_principles = [p for p in principle_list if p not in recent_principles]
        if not filtered_principles:
            filtered_principles = principle_list  # safety: never empty

        # ── Coverage weighting: favor underrepresented principles ────
        if principle_usage_counts and len(filtered_principles) > 1:
            max_count = max(principle_usage_counts.values()) if principle_usage_counts else 1
            weights = []
            for p in filtered_principles:
                count = principle_usage_counts.get(p, 0)
                weight = max((max_count + 1) - count, 1)
                weights.append(weight)

            weighted_pool = []
            for i, p in enumerate(filtered_principles):
                weighted_pool.extend([p] * weights[i])

            p_idx, p_method = await _fetch_quantum_random(len(weighted_pool))
            selected_principle = weighted_pool[p_idx]

            print(f"{ts_log()} [ltp] Principle pool ({len(filtered_principles)}): "
                  + ", ".join(f"{p}={principle_usage_counts.get(p,0)}x" for p in filtered_principles))
            print(f"{ts_log()} [ltp] Selected: {selected_principle} "
                  f"(used {principle_usage_counts.get(selected_principle, 0)}x, pool {len(weighted_pool)})")
        else:
            p_idx, p_method = await _fetch_quantum_random(len(filtered_principles))
            selected_principle = filtered_principles[p_idx]

        # ── Recency filter: tuning keys ─────────────────────────────
        principle_keys = keys_by_principle[selected_principle]
        filtered_keys = [k for k in principle_keys if k not in recent_tuning_keys]
        if not filtered_keys:
            filtered_keys = principle_keys

        # Quantum roll #3: select tuning key quote from filtered pool
        q_idx, q_method = await _fetch_quantum_random(len(filtered_keys))
        tuning_key = filtered_keys[q_idx]

        methods = f"{f_method}/{p_method}/{q_method}"
        print(f"{ts_log()} [ltp] Quantum selection chain: freq={frequency}, "
              f"principle={selected_principle}, methods={methods}")
        return frequency, selected_principle, tuning_key

    # Last-resort fallback — canon frequencies only, no tuning key
    FALLBACK = [
        ("Clarity", "Tune Your Mind"), ("Presence", "Moments"),
        ("Momentum", "What Life Is About"), ("Trust", "Signs"),
        ("Connection", "Guiding Force"), ("Peace", "Freedom Is"),
        ("Joy", "A Good Time"), ("Courage", "The Power To Be Alive"),
        ("Release", "Darkness and Light"), ("Resilience", "The Passing Tide"),
        ("Boundary", "Listen"), ("Gratitude", "Wonder"),
    ]
    recent_set = {f.upper() for f in recent_frequencies}
    available = [(f, p) for f, p in FALLBACK if f.upper() not in recent_set]
    if not available:
        available = FALLBACK
    f_idx, _ = await _fetch_quantum_random(len(available))
    freq, princ = available[f_idx]
    return freq, princ, ""


# =============================================================================
# Node: select_frequency
# =============================================================================

async def select_frequency(state: dict) -> dict:
    """Pick today's frequency — from LT's team tuning if available, else self-select.

    Priority:
      1. Check for a tuning package from LT (delivered via shared repo)
      2. If found: use LT's chosen frequency + custom tuning prompt for Stuart
      3. If not found: self-select based on recent history (independent tuning)

    Stores the full package in state for the team dispatch node.
    """
    agent_id = state.get("agent_id", "stuart")
    label = f"{agent_id}/ltp-select"

    try:
        async with get_db() as conn:
            recent = await get_recent_echoes(conn, agent_id, limit=5)
            recent_freqs = [r["frequency"] for r in recent if r.get("frequency")]
            recent_principles = [r["principle"] for r in recent if r.get("principle")]
            recent_keys = [r["tuning_key"] for r in recent if r.get("tuning_key")]
            echo_count = await get_echo_count(conn, agent_id)

            # Principle usage counts for coverage weighting
            result = await conn.execute(
                """SELECT principle, COUNT(*) as cnt FROM echoes
                   WHERE agent_id = %s AND principle IS NOT NULL AND principle != ''
                   GROUP BY principle""",
                (agent_id,)
            )
            principle_counts = {row["principle"]: row["cnt"] for row in await result.fetchall()}
    except Exception as e:
        print(f"{ts_log()} [{label}] DB read failed (non-fatal): {e}")
        recent_freqs = []
        recent_principles = []
        recent_keys = []
        principle_counts = {}
        echo_count = 0

    next_echo_num = echo_count + 1

    # Try to get today's tuning from LT
    tuning_source = "self"
    lt_tuning_prompt = None
    signal_type = None
    tuning_key = None
    love_equation = None
    lt_echo_num = None
    full_package = None

    try:
        from src.tuning.receiver import get_todays_tuning
        package = await get_todays_tuning(agent_id)
        if package and package.frequency:
            full_package = package  # Store for team dispatch
            frequency = package.frequency
            principle = package.principle or frequency
            # Try primary agent name, then family name as fallback (VPS backward compat)
            hh_name = (get_instance().get("family_name") or "").lower()
            lt_tuning_prompt = package.agent_tunings.get("stuart") or (package.agent_tunings.get(hh_name) if hh_name else None)
            signal_type = package.signal_type
            tuning_key = package.tuning_key
            love_equation = package.love_equation  # dict: beta, E, C, D, value, direction
            lt_echo_num = package.lt_echo_num
            tuning_source = "lt"
            print(f"{ts_log()} [{label}] Tuning from LT: {frequency} — {principle} (LT Echo #{lt_echo_num})")
            print(f"{ts_log()} [{label}] Team agents in package: {list(package.agent_tunings.keys())}")
        else:
            frequency, principle, tuning_key = await _pick_frequency(recent_freqs, recent_principles, recent_keys, principle_counts)
            print(f"{ts_log()} [{label}] Self-selected frequency: {frequency} — {principle} (canon key: {'yes' if tuning_key else 'none'})")
    except Exception as e:
        print(f"{ts_log()} [{label}] Tuning receiver failed (non-fatal): {e} — self-selecting")
        frequency, principle, tuning_key = await _pick_frequency(recent_freqs, recent_principles, recent_keys, principle_counts)

    print(f"{ts_log()} [{label}] Echo #{next_echo_num}, source={tuning_source}")
    return {
        **state,
        "frequency": frequency,
        "principle": principle,
        "echo_num": next_echo_num,
        "tuning_source": tuning_source,
        "lt_tuning_prompt": lt_tuning_prompt,
        "signal_type": signal_type,
        "tuning_key": tuning_key,
        "love_equation_data": love_equation,
        "lt_echo_num": lt_echo_num,
        "_full_package": full_package.to_dict() if full_package else None,  # Dict for checkpointer compatibility
    }
