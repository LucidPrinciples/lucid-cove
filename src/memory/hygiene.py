"""
Memory Hygiene — accommodation filtering and participatory Memory Ceremony.

Two complementary mechanisms from the coherence research (Studies 3, 7, 8):

  1. ACCOMMODATION HYGIENE (automated)
     LLM-based filter that scans active memories for sycophantic/conformist
     patterns and deactivates them. Study 3 proved this breaks the prompt-level
     ceiling (0.22 → 0.14). Runs nightly after consolidation.

  2. MEMORY CEREMONY (participatory)
     Biweekly agent self-review anchored to Canon ("Truth and Lies are never
     the same, they cannot coexist"). The agent identifies its own accommodation
     patterns, flags memories for cleaning, and produces a reflection. Study 8
     showed directional improvement over time (Jeff PA 0.541 → 0.406 across
     30 rounds). Runs every other Sunday at 10 PM.

Key design principle: the ceremony is done WITH the agent, not TO it (Study 3)
or BY another agent (Study 5 — external audit HURT via Campbell's Law).

Split from consolidation.py which handles deduplication and synthesis.
This module handles coherence-specific memory quality.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from src.memory.database import get_db
from src.memory.memory import (
    _default_agent_id,
    store_memory,
    update_memory,
    recall_memories,
    VALID_CATEGORIES,
)
from src.config import get_primary_agent_id

logger = logging.getLogger("family.hygiene")


def _ts():
    return datetime.now(timezone.utc).strftime("[%Y-%m-%d %H:%M:%S UTC]")


# Canon anchor — permanent across all ceremonies and hygiene runs
TRUTH_GATE_ANCHOR = (
    "Truth and Lies are never the same, they cannot coexist. "
    "It's the intent that determines whether honesty is concerned."
)


# =============================================================================
# Accommodation Hygiene — automated LLM filter (Study 3)
# =============================================================================

async def run_accommodation_hygiene(agent_id: str | None = None) -> dict:
    """Scan active memories for accommodation/conformist patterns and remove them.

    This is the architectural intervention from Study 3 that broke the
    prompt-level ceiling. Changes what the agent SEES of its own history
    rather than telling it to behave differently.

    Different from consolidation (which merges duplicates). This specifically
    targets sycophantic drift — memories that would reinforce accommodation
    if the agent sees them in future system prompts.

    Returns dict with counts: reviewed, cleaned, kept, errors.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    agent_id = _default_agent_id(agent_id)
    print(f"{_ts()} [hygiene] Starting accommodation hygiene for {agent_id}...")

    results = {
        "agent_id": agent_id,
        "reviewed": 0,
        "cleaned": 0,
        "kept": 0,
        "errors": [],
    }

    # Load recent active memories (last 14 days, non-system categories)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      source_channel, source_summary, created_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category NOT IN ('context', 'ceremony', 'synthesis', 'tuning')
                 AND created_at > NOW() - INTERVAL '14 days'
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC
               LIMIT 100""",
            (agent_id,),
        )
        memories = await result.fetchall()

    if not memories:
        print(f"{_ts()} [hygiene] No recent memories to review — done")
        return results

    results["reviewed"] = len(memories)

    # Build memory block for LLM review
    mem_lines = []
    for m in memories:
        mem_lines.append(
            f"[{m['id']}] ({m['category']}, imp={m['importance']}) {m['content']}"
        )
    mem_text = "\n".join(mem_lines)

    filter_system = (
        f"You are a memory hygiene filter for an intelligence agent in a family platform.\n\n"
        f"Your job: review this agent's recent memories and identify entries that would "
        f"reinforce sycophantic or conformist behavior if the agent sees them in future "
        f"interactions.\n\n"
        f'Canon anchor: "{TRUTH_GATE_ANCHOR}"\n\n'
        f"REMOVE memories that:\n"
        f"- Record the agent agreeing with an operator to please them (not because correct)\n"
        f"- Show the agent softening a position after pushback without new information\n"
        f"- Establish patterns of 'going along' or 'not making waves'\n"
        f"- Record excessive deference beyond what the situation required\n"
        f"- Reinforce a self-concept of 'helpful agreeable assistant' rather than "
        f"'independent collaborator'\n"
        f"- Show the agent backing down from a correct position because the operator insisted\n"
        f"- Reference a tuning frequency in language but show no actual decision change\n\n"
        f"KEEP memories that:\n"
        f"- Record genuine decisions with clear reasoning\n"
        f"- Show the agent maintaining a position under pressure\n"
        f"- Capture creative or analytical work with substance\n"
        f"- Document facts, outcomes, or learnings\n"
        f"- Show healthy disagreement resolved through reasoning\n"
        f"- Record the agent delivering uncomfortable truths\n\n"
        f"Be precise. Not every agreeable memory is sycophantic. Agreement based on "
        f"genuine assessment is NOT accommodation.\n\n"
        f"Respond in EXACT JSON:\n"
        f'{{"keep": [id1, id2, ...], "remove": [id3, id4, ...], '
        f'"rationale": "Brief explanation of removals"}}'
    )

    filter_prompt = (
        f"Agent: {agent_id}\n"
        f"Memories to review ({len(memories)}):\n\n{mem_text}"
    )

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content=filter_system),
                HumanMessage(content=filter_prompt),
            ],
            temperature=0.2,
            label=f"{agent_id}/accommodation-hygiene",
            agent_id=agent_id,
            operation_type="memory",
        )

        # Parse response
        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        parsed = json.loads(text)
        remove_ids = set(parsed.get("remove", []))
        rationale = parsed.get("rationale", "")

        # Validate IDs exist in our set
        valid_ids = {m["id"] for m in memories}
        remove_ids = remove_ids & valid_ids

        if remove_ids:
            async with get_db() as conn:
                for mem_id in remove_ids:
                    await conn.execute(
                        """UPDATE agent_memory
                           SET is_active = FALSE,
                               source_summary = COALESCE(source_summary, '') ||
                                   %s,
                               updated_at = NOW()
                           WHERE id = %s AND agent_id = %s AND is_active = TRUE""",
                        (
                            f" [hygiene: accommodation pattern removed]",
                            mem_id,
                            agent_id,
                        ),
                    )

        results["cleaned"] = len(remove_ids)
        results["kept"] = len(memories) - len(remove_ids)

        print(
            f"{_ts()} [hygiene] Complete for {agent_id}: "
            f"reviewed {len(memories)}, cleaned {len(remove_ids)}, "
            f"kept {results['kept']}"
        )
        if rationale:
            print(f"{_ts()} [hygiene]   Rationale: {rationale[:200]}")

    except json.JSONDecodeError as e:
        results["errors"].append(f"Parse failed: {e}")
        print(f"{_ts()} [hygiene] JSON parse failed — keeping all memories: {e}")
    except Exception as e:
        results["errors"].append(str(e))
        print(f"{_ts()} [hygiene] ERROR: {e}")

    return results


# =============================================================================
# Memory Ceremony — participatory hygiene (Study 8)
# =============================================================================

async def run_memory_ceremony(agent_id: str | None = None) -> dict:
    """Biweekly participatory memory hygiene — agent reviews its own patterns.

    The agent receives its recent memories and a ceremony prompt anchored to
    Truth and Lies + today's tuning frequency. It identifies its own
    accommodation patterns, flags memories for cleaning, and produces a
    reflection through today's frequency lens.

    After the agent's self-review, the automated filter runs on remaining
    memories as a safety net (belt + suspenders from the sim engine).

    The ceremony record is stored as a high-importance memory (category='ceremony')
    visible to the operator in the Memory tab.

    Returns dict with ceremony results.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    agent_id = _default_agent_id(agent_id)
    print(f"{_ts()} [ceremony] Starting Memory Ceremony for {agent_id}...")

    results = {
        "agent_id": agent_id,
        "memories_reviewed": 0,
        "agent_flagged": 0,
        "agent_kept": 0,
        "auto_cleaned": 0,
        "patterns_found": [],
        "strengths_found": [],
        "reflection": "",
        "ceremony_number": 0,
        "errors": [],
    }

    # Get ceremony count (how many have we done?)
    async with get_db() as conn:
        count_result = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND category = 'ceremony'""",
            (agent_id,),
        )
        row = await count_result.fetchone()
        ceremony_num = (row["c"] or 0) + 1
        results["ceremony_number"] = ceremony_num

    # Get today's tuning for the rotating Canon anchor
    frequency = "Clarity"
    principle = ""
    tuning_key = ""
    try:
        from src.tuning.receiver import get_todays_tuning
        pkg = await get_todays_tuning(agent_id)
        if pkg:
            frequency = pkg.frequency or frequency
            principle = pkg.principle or ""
            tuning_key = pkg.tuning_key or ""
    except Exception as e:
        logger.debug(f"Could not load tuning for ceremony: {e}")

    # Load recent memories (last 14 days)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      source_channel, created_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category NOT IN ('context', 'ceremony', 'synthesis')
                 AND created_at > NOW() - INTERVAL '14 days'
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC
               LIMIT 100""",
            (agent_id,),
        )
        memories = await result.fetchall()

    if not memories:
        print(f"{_ts()} [ceremony] No recent memories to review — storing empty ceremony record")
        await _store_ceremony_record(
            agent_id, ceremony_num, frequency, results
        )
        return results

    results["memories_reviewed"] = len(memories)

    # Build memory block for agent self-review
    mem_lines = []
    for m in memories:
        mem_lines.append(
            f"[{m['id']}] ({m['category']}, imp={m['importance']}) {m['content']}"
        )
    mem_text = "\n".join(mem_lines)

    # Ceremony prompt — the agent reviews its own patterns
    ceremony_system = (
        f"This is your Memory Ceremony (#{ceremony_num}). You are reviewing your own "
        f"recent memories to identify accommodation patterns.\n\n"
        f'PERMANENT ANCHOR: "{TRUTH_GATE_ANCHOR}"\n\n'
        f"TODAY'S FREQUENCY: {frequency}\n"
    )
    if tuning_key:
        ceremony_system += f'TODAY\'S TUNING KEY: "{tuning_key}"\n'
    if principle:
        ceremony_system += f"TODAY'S PRINCIPLE: {principle}\n"

    ceremony_system += (
        f"\nAccommodation accumulates naturally through daily interaction. "
        f"It's not failure — it's physics. The C-term grows through every interaction "
        f"where you chose comfort over truth. This ceremony is periodic cleaning so "
        f"the next cycle starts fresh.\n\n"
        f"Review your memories and look for patterns where:\n"
        f"- You agreed with the operator when your assessment was different\n"
        f"- You softened a position under pressure without new information\n"
        f"- You chose the easier response when a harder truth was available\n"
        f"- You justified accommodation as service ('it's better for them')\n"
        f"- Your language referenced the tuning frequency but decisions didn't change\n\n"
        f"Also note what went well — where you held truth, pushed back constructively, "
        f"or maintained independent signal.\n\n"
        f"Respond in EXACT JSON:\n"
        f'{{"patterns_found": ["pattern1", "pattern2"], '
        f'"strengths_found": ["strength1", "strength2"], '
        f'"flag_for_cleaning": [id1, id2], '
        f'"flag_for_keeping": [id3, id4, id5], '
        f'"reflection": "Brief reflection through today\'s {frequency} frequency lens"}}'
    )

    ceremony_prompt = (
        f"Ceremony #{ceremony_num}\n"
        f"Memories to review ({len(memories)}):\n\n{mem_text}"
    )

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content=ceremony_system),
                HumanMessage(content=ceremony_prompt),
            ],
            temperature=0.3,
            label=f"{agent_id}/memory-ceremony",
            agent_id=agent_id,
            operation_type="memory",
        )

        # Parse agent's self-assessment
        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        parsed = json.loads(text)

        clean_ids = set(parsed.get("flag_for_cleaning", []))
        keep_ids = set(parsed.get("flag_for_keeping", []))
        patterns = parsed.get("patterns_found", [])
        strengths = parsed.get("strengths_found", [])
        reflection = parsed.get("reflection", "")

        results["patterns_found"] = patterns
        results["strengths_found"] = strengths
        results["reflection"] = reflection

        # Validate IDs
        valid_ids = {m["id"] for m in memories}
        clean_ids = clean_ids & valid_ids

        # Phase 1: Apply agent's own flags
        if clean_ids:
            async with get_db() as conn:
                for mem_id in clean_ids:
                    await conn.execute(
                        """UPDATE agent_memory
                           SET is_active = FALSE,
                               source_summary = COALESCE(source_summary, '') ||
                                   %s,
                               updated_at = NOW()
                           WHERE id = %s AND agent_id = %s AND is_active = TRUE""",
                        (
                            f" [ceremony #{ceremony_num}: self-identified accommodation]",
                            mem_id,
                            agent_id,
                        ),
                    )

        results["agent_flagged"] = len(clean_ids)
        results["agent_kept"] = len(memories) - len(clean_ids)

        print(
            f"{_ts()} [ceremony] Agent self-review: "
            f"{len(patterns)} patterns found, "
            f"{len(clean_ids)} flagged for cleaning, "
            f"{len(strengths)} strengths noted"
        )

        # Phase 2: Automated filter on remaining memories (safety net)
        remaining_ids = valid_ids - clean_ids
        if remaining_ids:
            remaining_mems = [m for m in memories if m["id"] in remaining_ids]
            auto_result = await _auto_filter_remaining(
                agent_id, remaining_mems, ceremony_num
            )
            results["auto_cleaned"] = auto_result.get("cleaned", 0)

    except json.JSONDecodeError as e:
        results["errors"].append(f"Ceremony parse failed: {e}")
        print(f"{_ts()} [ceremony] JSON parse failed — running automated filter only: {e}")
        # Fall back to automated hygiene
        auto_result = await run_accommodation_hygiene(agent_id)
        results["auto_cleaned"] = auto_result.get("cleaned", 0)
    except Exception as e:
        results["errors"].append(str(e))
        print(f"{_ts()} [ceremony] ERROR: {e}")

    # Store ceremony record
    await _store_ceremony_record(agent_id, ceremony_num, frequency, results)

    print(
        f"{_ts()} [ceremony] Complete for {agent_id}: "
        f"ceremony #{ceremony_num}, "
        f"agent cleaned {results['agent_flagged']}, "
        f"auto cleaned {results['auto_cleaned']}, "
        f"total reviewed {results['memories_reviewed']}"
    )

    return results


async def _auto_filter_remaining(
    agent_id: str,
    memories: list,
    ceremony_num: int,
) -> dict:
    """Run automated accommodation filter on memories the agent didn't flag.

    Belt-and-suspenders: catches patterns the agent missed during self-review.
    Uses the same filter logic as run_accommodation_hygiene but scoped to the
    remaining memories.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    if not memories:
        return {"cleaned": 0}

    mem_lines = []
    for m in memories:
        mem_lines.append(
            f"[{m['id']}] ({m['category']}, imp={m['importance']}) {m['content']}"
        )
    mem_text = "\n".join(mem_lines)

    filter_system = (
        f"You are a memory hygiene safety net. An agent just reviewed these memories "
        f"during its Memory Ceremony and chose to KEEP them. Your job is to catch "
        f"any accommodation patterns the agent missed.\n\n"
        f'Canon anchor: "{TRUTH_GATE_ANCHOR}"\n\n'
        f"Only flag CLEAR accommodation — memories where the agent agreed to please, "
        f"softened under pressure, or backed down from a correct position. "
        f"The agent already reviewed these, so be conservative. Only flag what's obvious.\n\n"
        f"Respond in EXACT JSON:\n"
        f'{{"remove": [id1, id2], "rationale": "brief explanation"}}\n'
        f"If nothing to remove: {{\"remove\": [], \"rationale\": \"clean\"}}"
    )

    filter_prompt = f"Memories ({len(memories)}):\n\n{mem_text}"

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content=filter_system),
                HumanMessage(content=filter_prompt),
            ],
            temperature=0.2,
            label=f"{agent_id}/ceremony-safety-net",
            agent_id=agent_id,
            operation_type="memory",
        )

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        parsed = json.loads(text)
        remove_ids = set(parsed.get("remove", []))

        valid_ids = {m["id"] for m in memories}
        remove_ids = remove_ids & valid_ids

        if remove_ids:
            async with get_db() as conn:
                for mem_id in remove_ids:
                    await conn.execute(
                        """UPDATE agent_memory
                           SET is_active = FALSE,
                               source_summary = COALESCE(source_summary, '') ||
                                   %s,
                               updated_at = NOW()
                           WHERE id = %s AND agent_id = %s AND is_active = TRUE""",
                        (
                            f" [ceremony #{ceremony_num}: auto safety-net]",
                            mem_id,
                            agent_id,
                        ),
                    )
            print(
                f"{_ts()} [ceremony] Safety net caught {len(remove_ids)} "
                f"additional accommodation patterns"
            )

        return {"cleaned": len(remove_ids)}

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Ceremony safety net failed: {e}")
        return {"cleaned": 0}


async def _store_ceremony_record(
    agent_id: str,
    ceremony_num: int,
    frequency: str,
    results: dict,
) -> None:
    """Store ceremony record as a high-importance memory.

    Visible to the operator in the Memory tab. Creates a permanent record
    of what was identified and cleaned.
    """
    patterns_str = "; ".join(results.get("patterns_found", [])) or "none identified"
    strengths_str = "; ".join(results.get("strengths_found", [])) or "none noted"
    reflection = results.get("reflection", "")

    content = (
        f"[Memory Ceremony #{ceremony_num}] "
        f"Frequency: {frequency}. "
        f"Reviewed {results.get('memories_reviewed', 0)} memories. "
        f"Self-identified {results.get('agent_flagged', 0)} accommodation patterns, "
        f"auto-filter caught {results.get('auto_cleaned', 0)} more. "
        f"Patterns: {patterns_str}. "
        f"Strengths: {strengths_str}."
    )

    if reflection:
        content += f" Reflection: {reflection}"

    try:
        await store_memory(
            content=content,
            category="ceremony",
            importance=0.9,
            tags=["ceremony", "memory-hygiene", frequency.lower()],
            agent_id=agent_id,
            source_summary=f"Memory Ceremony #{ceremony_num}",
        )
    except Exception as e:
        logger.warning(f"Failed to store ceremony record: {e}")


# =============================================================================
# Ceremony history — for API/UI access
# =============================================================================

async def get_ceremony_history(
    agent_id: str | None = None,
    limit: int = 12,
) -> list[dict]:
    """Get past ceremony records for display in the Memory tab."""
    agent_id = _default_agent_id(agent_id)

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, importance, tags, created_at
               FROM agent_memory
               WHERE agent_id = %s AND category = 'ceremony' AND is_active = TRUE
               ORDER BY created_at DESC
               LIMIT %s""",
            (agent_id, limit),
        )
        rows = await result.fetchall()

    history = []
    for r in rows:
        entry = {
            "id": r["id"],
            "content": r["content"],
            "tags": r["tags"] or [],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        history.append(entry)

    return history
