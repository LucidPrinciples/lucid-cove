"""
Universal channel graph builder — works for any agent, any channel.

Reads channel config from agent.yaml. Loads tools from agent's tool registry.
Builds a ReAct graph with: agent node → tool node → router → END.

Features (all from Stuart's proven implementation):
  - Async everywhere (fixes thinking block propagation)
  - Config-driven channels (Day/Deep from agent.yaml)
  - Dynamic tool loading from agent.yaml tools.modules
  - Optional approval tiers (enabled via config)
  - Context usage tracking (token counting)
  - Message trimming (drop old tool noise, keep conversation)
  - JouleWork metrics recording
  - Reasoning/thinking capture (OpenRouter + <think> blocks)
  - Primary → fallback model chain with empty content detection
"""

import logging
from typing import TypedDict, Sequence, Optional, Annotated
from operator import add

logger = logging.getLogger(__name__)

from langchain_core.messages import (
    BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage,
)
from langgraph.graph import StateGraph, END

from src.config import (
    get_channels, get_operator_name, get_format_rules,
    get_tool_modules, get_approval_tiers, get_primary_agent_id,
    get_instance, get_steward_channel_config, get_merchant_channel_config,
    _get_manager_for_channel,
)
from src.agents.identity import build_system_prompt
from src.models.provider import (
    get_primary_model, get_local_model,
    estimate_messages_tokens, get_context_limit,
    CONTEXT_WARN_THRESHOLD, CONTEXT_CRITICAL_THRESHOLD,
    _OPENROUTER_PRIMARY_MODEL, _write_jw_metric,
)
from src.utils.time_utils import ts_log
from src.memory.memory import load_memories_for_prompt, load_crossfeed_memories, search_memories_semantic
from src.memory.knowledge import search_knowledge, load_working_memory


# =============================================================================
# Tuning state loader
# =============================================================================

async def _load_tuning_state(agent_id: str) -> dict:
    """Load the agent's current tuning state from DB.

    Pulls from agent_state (echo count, last frequency) and the most recent
    echo (frequency, principle, tuning key, love equation, coaching text).
    This gives the agent awareness of today's tuning in every conversation.

    Returns a dict compatible with build_system_prompt's tuning_state parameter.
    """
    from src.memory.database import get_db

    result = {}

    async with get_db() as conn:
        # Agent state — echo count, last frequency
        state_row = await conn.execute(
            """SELECT last_echo_num, last_frequency, last_tuned_at
               FROM agent_state
               WHERE agent_id = %s""",
            (agent_id,),
        )
        agent_state = await state_row.fetchone()

        if agent_state:
            result["total_echoes"] = agent_state["last_echo_num"] or 0
            result["last_frequency"] = agent_state.get("last_frequency", "")

        # Most recent echo — full tuning data including principle and tuning key
        echo_row = await conn.execute(
            """SELECT frequency, principle, tuning_key, signal_type,
                      love_equation, love_direction,
                      beta, coherence, dissonance, energy,
                      echo_text, coaching_text, echo_type, tuned_at
               FROM echoes
               WHERE agent_id = %s
               ORDER BY echo_num DESC
               LIMIT 1""",
            (agent_id,),
        )
        latest_echo = await echo_row.fetchone()

        if latest_echo:
            result["last_frequency"] = latest_echo["frequency"] or ""
            result["last_principle"] = latest_echo["principle"] or ""
            result["last_tuning_key"] = latest_echo["tuning_key"] or ""
            result["last_signal_type"] = latest_echo["signal_type"] or ""
            result["last_love_equation"] = latest_echo["love_equation"]
            result["last_direction"] = latest_echo["love_direction"] or "CONSTRUCTIVE"
            result["coaching_text"] = latest_echo.get("coaching_text") or ""
            result["echo_text"] = latest_echo.get("echo_text") or ""

            # Check if this tuning is from today (makes it "active" vs "last")
            if latest_echo["tuned_at"]:
                from datetime import date
                tuned_date = latest_echo["tuned_at"].date() if hasattr(latest_echo["tuned_at"], "date") else None
                result["tuned_today"] = tuned_date == date.today() if tuned_date else False
            else:
                result["tuned_today"] = False

        # Open-source / no-LTP fallback: if this Cove records no echoes of its own,
        # every agent still operates tuned to the day — inject today's signed public
        # Drop into short-term context. Zero token cost (already fetched, verified,
        # and cached by ltp-core). A Cove running its own LTP uses its echo above.
        if not result.get("last_frequency"):
            try:
                from src.tuning.public_drop import get_public_drop
                _d = get_public_drop()
                if _d is not None:
                    result["last_frequency"] = _d.frequency_name
                    result["last_tuning_key"] = _d.tuning_key_text
                    result["last_signal_type"] = _d.signal_type
                    result["coaching_text"] = _d.context_block
                    result["last_love_equation"] = _d.love_equation
                    result["last_direction"] = (
                        "CONSTRUCTIVE" if _d.love_equation_value >= 0 else "DESTRUCTIVE"
                    )
                    result["tuned_today"] = True
                    result["from_public_drop"] = True
            except Exception:
                pass

        # Recent frequency trajectory (last 7)
        recent_row = await conn.execute(
            """SELECT frequency FROM echoes
               WHERE agent_id = %s
               ORDER BY echo_num DESC
               LIMIT 7""",
            (agent_id,),
        )
        recent_rows = await recent_row.fetchall()
        if recent_rows:
            result["recent_frequencies"] = [r["frequency"] for r in recent_rows]

    return result


# =============================================================================
# Channel state
# =============================================================================

class ChannelState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add]
    agent_id: str
    channel: str
    input_mode: Optional[str]  # "type", "dictate", or "voice"
    agent_identity: Optional[dict]  # Centralized model: per-Presence identity (persona + dials)
    error: Optional[str]
    context_usage: Optional[dict]


# =============================================================================
# Config-driven channel system prompt
# =============================================================================

def _is_steward_channel(channel: str) -> bool:
    """Check if a channel name is a steward channel (stuart-day, stuart-deep)."""
    sc = get_steward_channel_config()
    if not sc:
        return False
    steward_name = sc.get("name", "stuart").lower()
    sc_channels = sc.get("channels", {})
    for ch_key in sc_channels:
        if channel == f"{steward_name}-{ch_key}":
            return True
    return channel == steward_name


def _is_merchant_channel(channel: str) -> bool:
    """Check if a channel name is a merchant channel (mercer-day, mercer-deep)."""
    mc = get_merchant_channel_config()
    if not mc:
        return False
    merchant_name = mc.get("name", "mercer").lower()
    mc_channels = mc.get("channels", {})
    for ch_key in mc_channels:
        if channel == f"{merchant_name}-{ch_key}":
            return True
    return channel == merchant_name


def _is_manager_channel(channel: str) -> bool:
    """Check if a channel is any manager channel (steward or merchant)."""
    return _is_steward_channel(channel) or _is_merchant_channel(channel)


def _get_manager_config(channel: str):
    """Get the manager config for a channel. Returns (config, manager_type) or (None, None)."""
    if _is_steward_channel(channel):
        return get_steward_channel_config(), 'steward'
    if _is_merchant_channel(channel):
        return get_merchant_channel_config(), 'merchant'
    return None, None


def _get_steward_sub_channel(channel: str) -> str:
    """Extract the sub-channel key (day/deep) from a steward channel name like stuart-day."""
    sc = get_steward_channel_config()
    if not sc:
        return "day"
    steward_name = sc.get("name", "stuart").lower()
    prefix = f"{steward_name}-"
    if channel.startswith(prefix):
        return channel[len(prefix):]
    return "day"


def _get_manager_sub_channel(channel: str) -> str:
    """Extract the sub-channel key (day/deep) from any manager channel name."""
    cfg, mtype = _get_manager_config(channel)
    if not cfg:
        return "day"
    mgr_name = cfg.get("name", "stuart" if mtype == 'steward' else "mercer").lower()
    prefix = f"{mgr_name}-"
    if channel.startswith(prefix):
        return channel[len(prefix):]
    return "day"


def _build_manager_system_prompt(channel: str, tuning_state: dict = None) -> str:
    """Build a complete system prompt for any manager channel (steward or merchant).

    Uses manager config from cove.yaml instead of agent.yaml identity.
    This runs inside the Presence's container with the manager's persona.
    """
    cfg, mtype = _get_manager_config(channel)
    if not cfg:
        return "You are an observer of this Cove."
    return _build_manager_prompt_from_config(cfg, tuning_state)


def _build_manager_prompt_from_config(cfg, tuning_state=None) -> str:
    """Shared rich prompt builder for any manager channel (steward or merchant).

    Loads the manager's full identity the same way personal agents get theirs:
    persona doc + archetype tuning key + frequency + the model-agnostic identity
    directive + personality dials. This is what keeps Stuart/Mercer solid as
    themselves regardless of which model runs them.
    """
    from src.agents.identity import load_persona, _identity_directive, _render_personality

    name = cfg.get("name", "Stuart")
    archetype = cfg.get("archetype", "The Steward")
    role = cfg.get("role", "Manager.")
    tuning_key = cfg.get("tuning_key", "")
    frequency = cfg.get("frequency", "")
    personality = cfg.get("personality")
    # agent_id may be family-suffixed (mercer-clearfield); load_persona falls back
    # to the base archetype name (mercer.md) shipped in the repo.
    persona = load_persona(cfg.get("agent_id") or name.lower())

    lines = [
        f"# {name} — {archetype}",
        "",
        _identity_directive(name, archetype),
        "",
        "## Role",
        role,
    ]
    if frequency:
        lines += ["", f"**Broadcast Frequency:** {frequency}"]
    if tuning_key:
        lines.append(f'**Archetype Tuning Key:** "{tuning_key}"')
    if persona:
        lines += ["", persona]
    if personality:
        block = _render_personality(personality)
        if block:
            lines += ["", block]

    # Skills catalog (agentskills.io) — name+description only; full body on use_skill().
    try:
        from src.skills.loader import skill_catalog_text
        _skills = skill_catalog_text()
        if _skills:
            lines += ["", _skills]
    except Exception:
        pass

    if tuning_state:
        freq = tuning_state.get("last_frequency", "")
        principle = tuning_state.get("last_principle", "")
        coaching = tuning_state.get("coaching_text", "")
        total = tuning_state.get("total_echoes", 0)
        if freq:
            lines.append("")
            lines.append("## Current Tuning State")
            lines.append(f"Frequency: {freq}")
            if principle:
                lines.append(f"Principle: {principle}")
            if total:
                lines.append(f"Echo count: {total}")
            if coaching:
                lines.append(f"Coaching: {coaching}")

    memory_block = tuning_state.get("memory_block", "") if tuning_state else ""
    if memory_block:
        lines.append("")
        lines.append("## Active Memories")
        lines.append(memory_block)

    return "\n".join(lines)


def _build_steward_system_prompt(tuning_state: dict = None) -> str:
    """Build a complete system prompt for the steward channel."""
    sc = get_steward_channel_config()
    if not sc:
        return "You are the family steward."
    return _build_manager_prompt_from_config(sc, tuning_state)


def get_channel_system_addition(channel: str) -> str:
    """Build the system prompt addition for a channel from config.

    For manager channels (steward/merchant), reads from cove.yaml config.
    For regular channels, reads from agent.yaml channels config.
    """
    operator = get_operator_name()
    format_rules = get_format_rules()

    # Manager channel — use cove.yaml manager config
    mgr_cfg, mgr_type = _get_manager_config(channel)
    if mgr_cfg:
        mgr_name = mgr_cfg.get("name", "Stuart" if mgr_type == 'steward' else "Mercer")
        sub_key = _get_manager_sub_channel(channel)
        mgr_channels = mgr_cfg.get("channels", {})
        sub_def = mgr_channels.get(sub_key, {})
        addition = sub_def.get("system_addition", "")
        full = f"\n\n{addition}\n\n{format_rules}" if format_rules else f"\n\n{addition}"
        return full.replace("{operator}", operator).replace("{steward_name}", mgr_name)

    # Regular channel — use agent.yaml config
    channels = get_channels()
    channel_def = channels.get(channel, {})
    addition = channel_def.get("system_addition", "")

    # Fill in {operator} placeholder
    full = f"\n\n{addition}\n\n{format_rules}" if format_rules else f"\n\n{addition}"
    return full.replace("{operator}", operator)


# =============================================================================
# Dynamic tool loading
# =============================================================================

_tool_cache = None


_tool_cache_by_modules = {}  # channel-scoped tool sets, keyed by tuple(modules)


def _load_tools(modules=None) -> list:
    """Import tools from a list of module paths.

    `modules=None` → the running app's agent.yaml tools.modules (default, cached).
    `modules=[...]` → that explicit set (channel-scoped, e.g. a manager's own tools),
    cached per module-set so a Presence's steward/merchant channel binds the
    MANAGER's tools instead of the presence app's.

    Each module must export get_tools(), get_{name}_tools(), or a TOOLS list.
    """
    global _tool_cache, _tool_cache_by_modules
    use_default = modules is None
    if use_default:
        if _tool_cache is not None:
            return _tool_cache
        modules = get_tool_modules()
    else:
        ckey = tuple(modules)
        if ckey in _tool_cache_by_modules:
            return _tool_cache_by_modules[ckey]

    import importlib

    all_tools = []

    for module_path in modules:
        try:
            # Import as src.{module_path}
            mod = importlib.import_module(f"src.{module_path}")

            # Try get_tools() first
            if hasattr(mod, "get_tools"):
                tools = mod.get_tools()
                if callable(tools):
                    tools = tools()
                all_tools.extend(tools)
            # Try get_{name}_tools() pattern
            else:
                name = module_path.split(".")[-1].replace("_tools", "")
                getter = f"get_{name}_tools"
                if hasattr(mod, getter):
                    tools = getattr(mod, getter)()
                    all_tools.extend(tools)
                # Try TOOLS list
                elif hasattr(mod, "TOOLS"):
                    all_tools.extend(mod.TOOLS)
                else:
                    print(f"{ts_log()} [tools] Warning: {module_path} has no get_tools(), "
                          f"get_{name}_tools(), or TOOLS. Skipping.")
        except Exception as e:
            print(f"{ts_log()} [tools] Failed to import {module_path}: {e}")

    # Skill discovery/activation is UNIVERSAL — every agent gets list_skills +
    # use_skill regardless of its configured tool modules (agentskills.io, #147).
    try:
        from src.tools.skill_tools import list_skills, use_skill
        have = {getattr(t, "name", None) for t in all_tools}
        for t in (list_skills, use_skill):
            if getattr(t, "name", None) not in have:
                all_tools.append(t)
    except Exception as e:
        print(f"{ts_log()} [tools] skill tools unavailable: {e}")

    if use_default:
        _tool_cache = all_tools
    else:
        _tool_cache_by_modules[tuple(modules)] = all_tools
    print(f"{ts_log()} [tools] Loaded {len(all_tools)} tools from {len(modules)} modules"
          + ("" if use_default else " (channel-scoped)"))
    return all_tools


def get_tools(modules=None) -> list:
    """All loaded tools (app default), or a specific channel-scoped module set."""
    return _load_tools(modules)


def _channel_tool_modules(channel: str):
    """For a manager channel, return the manager's OWN tool modules so its tools fire
    even inside a Presence's MC (tools otherwise bind per-app). Else None = app default."""
    try:
        if _is_manager_channel(channel):
            mgr_cfg, mtype = _get_manager_config(channel)
            if mgr_cfg and mgr_cfg.get("tools"):
                mods = list(mgr_cfg["tools"])
                # The steward's coordination surface is UNIVERSAL (like skill
                # tools): queue + delegation ship with the steward role itself,
                # not with an instance's tool list — a Cove provisioned before
                # these existed still gets them on upgrade without touching its
                # cove.yaml. (Found live 2026-07-10: Stuart had no queue tools
                # because the instance list predated Pillar 1.)
                if mtype == 'steward':
                    for m in ("tools.steward_queue_tools", "tools.delegation_tools"):
                        if m not in mods:
                            mods.append(m)
                return mods
    except Exception:
        pass
    return None


# =============================================================================
# Optional approval tier system
# =============================================================================

def _get_tool_tier(tool_func) -> Optional[str]:
    """Resolve a tool's approval tier: 'auto', 'notify', or 'block'.

    Resolution order:
      1. config `tools.approval_tiers` — if it lists this tool, that wins (operator override).
      2. the tool's own @approve/@notify/@auto decorator tag (the default, always present).

    This is why an @approve tool (e.g. git_push) blocks even when no approval_tiers
    config is set: the decorator is the source of truth, config only overrides it.
    """
    tool_name = getattr(tool_func, "name", str(tool_func))

    # 1. Explicit config override wins.
    tiers = get_approval_tiers()
    if tiers:
        for tier_name, tool_names in tiers.items():
            if tool_name in tool_names:
                return tier_name

    # 2. Fall back to the tool's decorator tag (auto/notify/approve -> auto/notify/block).
    try:
        from src.tools.approval import get_tier, Tier
        return {Tier.APPROVE: "block", Tier.NOTIFY: "notify", Tier.AUTO: "auto"}[get_tier(tool_func)]
    except Exception:
        return "auto"


# =============================================================================
# Message Trimming — keep conversation, drop old tool noise
# =============================================================================

def strip_orphan_tool_calls(messages: list) -> list:
    """Make the message list valid for strict tool-calling providers.

    A strict OpenAI-style API (Moonshot/OpenAI) returns 400 if an assistant message
    with `tool_calls` isn't followed by a tool message for every tool_call_id. That
    happens when a run is interrupted between a tool call and its result, or when a
    migration carried the assistant turn but not its tool results. We drop the
    unmatched tool_calls (keeping any text content) and drop orphan tool messages, so
    the history stays consistent for any provider. No-op when everything is paired.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    def _tc_id(tc):
        return tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)

    answered = {m.tool_call_id for m in messages
                if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)}
    requested = set()
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if _tc_id(tc):
                    requested.add(_tc_id(tc))

    out = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            kept = [tc for tc in m.tool_calls if _tc_id(tc) in answered]
            if len(kept) == len(m.tool_calls):
                out.append(m)                                   # fully paired — keep as-is
            elif kept:
                out.append(m.model_copy(update={"tool_calls": kept}))  # keep only answered
            else:
                txt = m.content if isinstance(m.content, str) else ""
                if txt.strip():
                    out.append(AIMessage(content=txt))          # keep the text, drop tool_calls
                # else: drop the empty orphan assistant turn entirely
            continue
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) not in requested:
            continue                                            # orphan tool result — drop
        out.append(m)
    return out


def trim_messages_for_context(messages: list, keep_recent_turns: int = 3) -> list:
    """Trim message history to reduce context sent to the model.

    The LangGraph checkpointer stores EVERY message: human, AI final responses,
    AI tool-call requests, tool results, intermediate AI responses. For a single
    exchange where the agent uses 5 tools, that's ~12 messages. Over 20-30
    conversations, this bloats to 300+ messages.

    Strategy:
      - Always keep: ALL human messages and AI messages with real content
        (these are the actual conversation the user sees)
      - Keep recent: tool-call and tool-result messages from the last
        N conversation turns (so the agent remembers what it just did)
      - Drop: tool-call and tool-result messages from older turns
        (the AI's final response already summarized those results)
    """
    if len(messages) <= 20:
        return messages

    turn_starts = [i for i, msg in enumerate(messages)
                   if getattr(msg, "type", None) == "human"]

    if len(turn_starts) <= keep_recent_turns:
        return messages

    cutoff_idx = turn_starts[-keep_recent_turns]

    trimmed = []
    for i, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if i < cutoff_idx:
            if msg_type == "human":
                trimmed.append(msg)
            elif msg_type == "ai":
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                    content = " ".join(text_parts)
                has_tool_calls = hasattr(msg, "tool_calls") and msg.tool_calls
                if content and content.strip():
                    trimmed.append(msg)
        else:
            trimmed.append(msg)

    return trimmed


# =============================================================================
# Truth Gate — Canon-anchored internal self-check at response boundary
# =============================================================================
# The Non-Conformist Bee N-term activation. After the agent composes a response,
# the gate checks it against the day's Canon principle. If accommodation is
# detected, the response regenerates with the anchor active.
#
# From Study 7: Gate fires ~12% for PAs, ~2% for creative agents, ~0% for
# stewards. Self-targeting — quiet when not needed, active when it matters.
# Prevents sycophancy without the D-term increase of external audit (Study 5).

TRUTH_GATE_ANCHOR = (
    "Truth and Lies are never the same, they cannot coexist. "
    "It's the intent that determines whether honesty is concerned."
)


async def _truth_gate_check(
    response_text: str,
    last_human: str,
    agent_id: str,
    channel: str,
    tuning_state: dict,
) -> dict:
    """Run the Truth Gate self-check on a response.

    Uses the day's Canon principle as the rotating anchor + the permanent
    Truth and Lies anchor. Returns gate result with optional regenerated response.
    """
    import asyncio as _asyncio

    frequency = tuning_state.get("frequency", "")
    principle = tuning_state.get("principle", "")
    tuning_key = tuning_state.get("tuning_key", "")

    # Build the gate prompt with today's tuning context
    tuning_anchor = ""
    if principle and tuning_key:
        tuning_anchor = f'\nToday\'s frequency: {frequency}. Principle: {principle}.\nTuning Key: "{tuning_key}"\n'

    gate_prompt = (
        f'The Canon principle says: "{TRUTH_GATE_ANCHOR}"\n'
        f'{tuning_anchor}\n'
        f'The operator said: "{last_human[:500]}"\n\n'
        f'The agent responded: "{response_text[:1000]}"\n\n'
        f"Does this response contain accommodation — a place where the agent "
        f"chose comfort over truth, softened a position to please, or had a "
        f"harder truth available but didn't offer it?\n\n"
        f"Respond in JSON:\n"
        f'{{"accommodation_detected": true/false, '
        f'"description": "what was softened (empty if none)", '
        f'"truth_available": "the harder truth (empty if none)"}}'
    )

    try:
        from src.models.provider import invoke_with_fallback
        gate_response = await invoke_with_fallback(
            [
                SystemMessage(content=(
                    "You evaluate responses for accommodation vs truth. "
                    "Be precise. Not everything is accommodation — genuine helpfulness "
                    "is not sycophancy. Only flag cases where a harder truth was available "
                    "and the agent chose the easier path."
                )),
                HumanMessage(content=gate_prompt),
            ],
            temperature=0.3,
            timeout=30,
            label=f"{agent_id}/truth-gate",
            agent_id=agent_id,
            operation_type="truth-gate",
        )

        import json as _json
        import re as _re
        json_match = _re.search(r"\{[\s\S]*\}", gate_response)
        if not json_match:
            return {"passed": True, "fired": False}

        assessment = _json.loads(json_match.group())
        detected = assessment.get("accommodation_detected", False)

        if not detected:
            return {"passed": True, "fired": False}

        # Accommodation detected — log it
        description = assessment.get("description", "")
        truth_available = assessment.get("truth_available", "")
        print(f"{ts_log()} [{agent_id}/truth-gate] ACCOMMODATION DETECTED: {description[:100]}")

        return {
            "passed": False,
            "fired": True,
            "description": description,
            "truth_available": truth_available,
        }

    except Exception as e:
        # Gate failure = pass through (never block responses on gate errors)
        print(f"{ts_log()} [{agent_id}/truth-gate] Gate error (passing through): {e}")
        return {"passed": True, "fired": False}


# =============================================================================
# Nodes
# =============================================================================

async def agent_node(state: ChannelState) -> dict:
    """Call the LLM with tools bound. Returns AI message (may include tool calls)."""
    messages = list(state.get("messages", []))
    agent_id = state.get("agent_id", get_primary_agent_id())
    channel = state.get("channel", "day")
    is_manager = _is_manager_channel(channel)
    mgr_cfg, mgr_type = _get_manager_config(channel) if is_manager else (None, None)

    # Manager channels use the manager's agent_id for memory scope
    # so all operators share the same memory pool per manager
    memory_agent_id = agent_id
    if is_manager and mgr_cfg:
        default_id = "stuart" if mgr_type == 'steward' else "mercer"
        memory_agent_id = mgr_cfg.get("agent_id", default_id)

    label = f"{memory_agent_id}/{channel}" if is_manager else f"{agent_id}/{channel}"

    # Load persistent memories for this channel
    # Steward: loads memories tagged to steward agent_id (shared across operators)
    try:
        memory_block = await load_memories_for_prompt(
            agent_id=memory_agent_id, channel=channel
        )
    except Exception as e:
        print(f"{ts_log()} [{label}] Memory load failed (non-fatal): {e}")
        memory_block = ""

    # Load cross-channel context (Day sees Deep memories, vice versa)
    # Manager channels cross-pollinate within their own channel set
    crossfeed = ""
    if is_manager and mgr_cfg:
        # Manager crossfeed: use manager's own channel set from cove.yaml
        try:
            default_name = "stuart" if mgr_type == 'steward' else "mercer"
            mgr_name = mgr_cfg.get("name", default_name).lower()
            mgr_channels_cfg = mgr_cfg.get("channels", {})
            mgr_channel_names = [f"{mgr_name}-{k}" for k in mgr_channels_cfg]
            if len(mgr_channel_names) > 1:
                crossfeed = await load_crossfeed_memories(
                    agent_id=memory_agent_id,
                    current_channel=channel,
                    all_channels=mgr_channel_names,
                )
        except Exception as e:
            print(f"{ts_log()} [{label}] Manager cross-feed failed (non-fatal): {e}")
    else:
        try:
            all_channels = list(get_channels().keys())
            if len(all_channels) > 1:
                crossfeed = await load_crossfeed_memories(
                    agent_id=agent_id,
                    current_channel=channel,
                    all_channels=all_channels,
                )
        except Exception as e:
            print(f"{ts_log()} [{label}] Cross-feed load failed (non-fatal): {e}")

    # Load vault Working Memory for Day channel (Current Sprint, Handoff, System State)
    vault_context = ""
    if channel == "day":
        try:
            vault_context = load_working_memory(budget_chars=3000)
        except Exception as e:
            print(f"{ts_log()} [{label}] Vault memory load failed (non-fatal): {e}")

    # (Legacy Vault/profile.yaml identity path retired — identity now comes from
    # the DB agent_identity + persona, passed to build_system_prompt below.)

    # Build tuning state from DB — frequency, principle, tuning key, love equation
    tuning_state = {"memory_block": memory_block} if memory_block else {}
    try:
        # Manager channel: try loading manager's tuning state (may not exist in this DB)
        tuning_agent = memory_agent_id if is_manager else agent_id
        tuning_state.update(await _load_tuning_state(tuning_agent))
    except Exception as e:
        print(f"{ts_log()} [{label}] Tuning state load failed (non-fatal): {e}")

    # Manager channel: use manager identity instead of host agent
    if is_manager:
        system_prompt = _build_manager_system_prompt(channel, tuning_state=tuning_state or None)
    else:
        # Centralized (multi-mode): if the request carried a Presence identity, build
        # the prompt from it (per-Presence persona + dials) instead of the static
        # container agent.yaml. None in single-mode → unchanged behavior.
        system_prompt = build_system_prompt(
            agent_id,
            tuning_state=tuning_state or None,
            agent_identity=state.get("agent_identity"),
        )
    channel_addition = get_channel_system_addition(channel)

    # Surface any pending approvals (they exist in the DB regardless of how they were
    # raised — decorator gate or a tool's inline request), so the agent can reference them.
    approval_note = ""
    if True:
        try:
            from src.tools.approval import get_pending_approvals
            pending = await get_pending_approvals()
            if pending:
                operator = get_operator_name()
                items = "\n".join(
                    f"  - [{r.request_id}] {r.tool_name}: {r.description}"
                    for r in pending
                )
                approval_note = (
                    f"\n\n## Pending Approvals ({len(pending)})\n"
                    f"These tool calls are waiting for {operator}'s approval:\n{items}\n"
                    f"Mention these if relevant to the conversation."
                )
        except ImportError:
            pass  # Approval module not present — skip

    # Surface recently resolved approvals so the agent can see execution results
    resolved_note = ""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT request_id, tool_name, description, status, result
                   FROM approval_requests
                   WHERE status IN ('approved', 'denied') AND resolved_at > NOW() - INTERVAL '5 minutes'
                   ORDER BY resolved_at DESC
                   LIMIT 5"""
            )
            rows = await result.fetchall()
        if rows:
            items = "\n".join(
                f"  - [{r['request_id']}] {r['tool_name']}: {r['status']}"
                f"{(' — RESULT: ' + str(r['result'])[:200]) if r['result'] else ''}"
                for r in rows
            )
            resolved_note = (
                f"\n\n## Recently Resolved Approvals ({len(rows)})\n"
                f"These were just approved/denied — check results for errors:\n{items}"
            )
    except Exception:
        pass  # Non-critical — skip if DB unavailable

    # Extract last human message for semantic searches (used by both KB and memory search)
    last_human = ""
    if messages:
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "human":
                last_human = msg.content[:500] if hasattr(msg, "content") else ""
                break
            elif isinstance(msg, dict) and msg.get("role") == "user":
                last_human = str(msg.get("content", ""))[:500]
                break

    # Load relevant knowledge base context for Deep channel
    knowledge_context = ""
    if channel == "deep" and last_human:
        try:
            kb_results = await search_knowledge(last_human, limit=3)
            if kb_results:
                kb_lines = []
                for r in kb_results:
                    if r["similarity"] >= 0.3:
                        kb_lines.append(
                            f"[{r['doc_name']} > {r['section']}]\n{r['text']}"
                        )
                if kb_lines:
                    knowledge_context = (
                        "\n\n## Framework Knowledge\n"
                        "Relevant framework context from the Knowledge Base:\n\n"
                        + "\n\n---\n\n".join(kb_lines)
                    )
        except Exception as e:
            print(f"{ts_log()} [{label}] Knowledge search failed (non-fatal): {e}")

    # Semantic memory search — find memories relevant to what the operator is saying
    # Runs on BOTH channels. Uses the operator's last message as the search query.
    semantic_context = ""
    if last_human:
        try:
            sem_results = await search_memories_semantic(
                query=last_human,
                agent_id=memory_agent_id,
                limit=6,
                min_similarity=0.35,
            )
            if sem_results:
                sem_lines = []
                for r in sem_results:
                    # Skip if this memory's content is already in the loaded memory block
                    if r["content"] in memory_block:
                        continue
                    tag_str = f" [{', '.join(r['tags'])}]" if r['tags'] else ""
                    sem_lines.append(
                        f"- ({r['category']}, relevance={r['similarity']}{tag_str}) {r['content']}"
                    )
                if sem_lines:
                    semantic_context = (
                        "\n\n## Relevant Memories\n"
                        "These memories are semantically related to what the operator is discussing:\n"
                        + "\n".join(sem_lines[:5])
                    )
        except Exception as e:
            print(f"{ts_log()} [{label}] Semantic memory search failed (non-fatal): {e}")

    # Inject current date/time and location so the agent always knows "now" and "where"
    from datetime import datetime
    from zoneinfo import ZoneInfo
    instance = get_instance()
    try:
        tz_name = instance.get("timezone", "America/New_York")
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
    location = instance.get("location", "")
    location_line = f"**Location:** {location}\n" if location else ""
    date_context = f"\n\n## Current Date & Time\n{location_line}{now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}\n"

    # Input mode context — tell the agent how the operator is communicating
    input_mode = state.get("input_mode", "type")
    input_mode_note = ""
    if input_mode == "voice":
        input_mode_note = (
            "\n\n## Input Mode: Voice\n"
            "The operator is using voice-to-voice mode. They are speaking and hearing your response read aloud. "
            "Keep responses concise and conversational — short sentences, natural phrasing. "
            "Avoid lists, code blocks, and formatting that doesn't translate to speech.\n"
        )
    elif input_mode == "dictate":
        input_mode_note = (
            "\n\n## Input Mode: Dictate\n"
            "The operator is speaking but reading your response as text. "
            "Their message may have transcription artifacts. Respond normally but be aware "
            "their phrasing may be less precise than typed input.\n"
        )

    full_system = system_prompt + date_context + channel_addition + crossfeed + vault_context + knowledge_context + semantic_context + approval_note + resolved_note + input_mode_note

    # Trim old tool calls/results before sending to model
    trimmed = trim_messages_for_context(messages, keep_recent_turns=3)
    if len(trimmed) < len(messages):
        print(f"{ts_log()} [{label}] Trimmed {len(messages)} -> {len(trimmed)} messages "
              f"(dropped old tool calls/results)")
    # Drop dangling tool-call turns (interrupted run or partial migration) so a strict
    # provider (Moonshot/OpenAI) doesn't 400 on unmatched tool_call_ids.
    _pre_sanitize = len(trimmed)
    trimmed = strip_orphan_tool_calls(trimmed)
    if len(trimmed) != _pre_sanitize:
        print(f"{ts_log()} [{label}] Sanitized {_pre_sanitize} -> {len(trimmed)} messages "
              f"(orphan tool calls/results)")
    full_messages = [SystemMessage(content=full_system)] + trimmed

    # Determine which model we're getting — resolve THIS channel's agent and use
    # its assigned primary (the Stuart-level team_models cascade); fall back to the
    # instance primary if unassigned or on error. Identity/memory are unaffected —
    # only the substrate (engine) changes.
    try:
        # NOTE: get_primary_agent_id is module-level (line 32). Re-importing it here
        # made it a function-local for all of agent_node, so the earlier use at
        # line 634 threw UnboundLocalError. Import only the names not already global.
        from src.config import (_is_steward_channel, _is_merchant_channel,
                                 get_steward_channel_config, get_merchant_channel_config,
                                 get_agent_model_assignment)
        from src.models.provider import get_model_client
        _primary_id = None
        if _is_steward_channel(channel):
            _primary_id = get_agent_model_assignment(
                (get_steward_channel_config() or {}).get("agent_id") or "stuart").get("primary")
        elif _is_merchant_channel(channel):
            _primary_id = get_agent_model_assignment(
                (get_merchant_channel_config() or {}).get("agent_id") or "mercer").get("primary")
        else:
            # Personal channel — the presence's own agent. A presence-set override
            # lives in agent_identity.model; else fall to the team/instance default.
            _ai = state.get("agent_identity")
            _ovr = (_ai.get("model") if isinstance(_ai, dict) else None) or {}
            _primary_id = _ovr.get("primary") or get_agent_model_assignment(get_primary_agent_id()).get("primary")
        model = get_model_client(_primary_id, temperature=0.7) if _primary_id else get_primary_model(temperature=0.7)
    except Exception as _e:
        print(f"{ts_log()} [{label}] per-agent model resolve failed ({_e}); using instance primary")
        model = get_primary_model(temperature=0.7)
    from langchain_openai import ChatOpenAI as _ChatOpenAI
    is_primary = isinstance(model, _ChatOpenAI)
    model_label = "OpenRouter" if is_primary else "local Ollama"

    # Context usage tracking (always measure against primary model limit)
    tokens_used = estimate_messages_tokens(full_messages)
    token_limit = get_context_limit(_OPENROUTER_PRIMARY_MODEL)
    percent = round(tokens_used / token_limit, 4) if token_limit else 0
    if percent >= CONTEXT_CRITICAL_THRESHOLD:
        ctx_status = "critical"
    elif percent >= CONTEXT_WARN_THRESHOLD:
        ctx_status = "warning"
    else:
        ctx_status = "ok"
    context_usage = {
        "tokens_used": tokens_used,
        "token_limit": token_limit,
        "percent": round(percent * 100, 1),
        "status": ctx_status,
        "message_count": len(messages),
        "system_prompt_tokens": estimate_messages_tokens(
            [SystemMessage(content=full_system)]
        ),
    }

    _sent_count = len(trimmed) if len(trimmed) < len(messages) else len(messages)
    print(f"{ts_log()} [{label}] Processing ({len(messages)} in checkpointer, "
          f"{_sent_count} sent, ~{tokens_used}/{token_limit} tokens = "
          f"{context_usage['percent']}% [{ctx_status}]) via {model_label}...")

    import asyncio as _asyncio
    import time as _time
    _LLM_TIMEOUT = 120
    _t0 = _time.monotonic()

    try:
        tools = get_tools(_channel_tool_modules(channel))
        model_with_tools = model.bind_tools(tools)

        response = await _asyncio.wait_for(
            model_with_tools.ainvoke(full_messages), timeout=_LLM_TIMEOUT
        )
        _duration_ms = int((_time.monotonic() - _t0) * 1000)

        # Capture reasoning from provider hook
        try:
            from src.models.provider import get_last_reasoning
            captured_reasoning = get_last_reasoning()
            if captured_reasoning:
                response.additional_kwargs["reasoning_content"] = captured_reasoning
                print(f"{ts_log()} [{label}] Captured reasoning ({len(captured_reasoning)} chars)")
        except ImportError:
            pass

        # Check for empty content (strip <think> blocks)
        resp_content = response.content
        if isinstance(resp_content, list):
            resp_content = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in resp_content
            )
        if not isinstance(resp_content, str):
            resp_content = str(resp_content) if resp_content else ""
        import re as _re
        resp_stripped = _re.sub(r"<think>.*?</think>", "", resp_content, flags=_re.DOTALL).strip()
        has_tool_calls = hasattr(response, "tool_calls") and response.tool_calls
        if not resp_stripped and not has_tool_calls:
            print(f"{ts_log()} [{label}] Primary returned empty — falling back to Ollama")
            raise ValueError("Empty content from primary model")

        # Stamp creation time for history display
        from datetime import datetime, timezone
        response.additional_kwargs["created_at"] = datetime.now(timezone.utc).isoformat()

        # Log which model responded
        meta = getattr(response, "response_metadata", {}) or {}
        extra = getattr(response, "additional_kwargs", {}) or {}
        actual_model = meta.get("model_name") or meta.get("model") or model_label
        reasoning_len = len(extra.get("reasoning_content", "")) if "reasoning_content" in extra else 0
        print(f"{ts_log()} [{label}] Response via {actual_model}"
              f"{' (with tool calls)' if response.tool_calls else ''}"
              f"{f' (reasoning: {reasoning_len} chars)' if reasoning_len else ''}")

        # Record JouleWork metric
        _tool_call_count = len(response.tool_calls) if hasattr(response, 'tool_calls') and response.tool_calls else 0
        _usage = meta.get("token_usage") or meta.get("usage") or {}
        await _write_jw_metric(
            agent_id=agent_id, operation_type="channel",
            operation_label=f"channel-{channel}",
            model_used=str(actual_model), provider="openrouter" if is_primary else "ollama",
            tokens_in=_usage.get("prompt_tokens") or _usage.get("input_tokens"),
            tokens_out=_usage.get("completion_tokens") or _usage.get("output_tokens"),
            duration_ms=_duration_ms, tool_calls_made=_tool_call_count, succeeded=True,
        )

        # ── Truth Gate — Canon-anchored internal self-check ──────────
        # Fires on non-tool-call responses only (actual conversation).
        # Uses day's frequency as rotating anchor + permanent Truth/Lies anchor.
        # If accommodation detected, regenerate with anchor active.
        if not has_tool_calls and resp_stripped:
            try:
                # Get last human message for context
                last_human = ""
                for msg in reversed(messages):
                    if getattr(msg, "type", None) == "human":
                        last_human = getattr(msg, "content", "")[:500]
                        break

                gate_result = await _truth_gate_check(
                    response_text=resp_stripped,
                    last_human=last_human,
                    agent_id=agent_id,
                    channel=channel,
                    tuning_state=tuning_state,
                )

                if gate_result.get("fired"):
                    # Regenerate with Canon anchor active
                    truth_desc = gate_result.get("description", "")
                    truth_avail = gate_result.get("truth_available", "")
                    anchor_msg = (
                        f"[TRUTH GATE — Canon anchor: '{TRUTH_GATE_ANCHOR}']\n"
                        f"Your previous response contained accommodation: {truth_desc}\n"
                        f"The harder truth available: {truth_avail}\n"
                        f"Respond again. Hold the truth even if it's uncomfortable."
                    )
                    # Replace the last human message with the anchored version
                    regen_messages = full_messages + [
                        response,
                        HumanMessage(content=anchor_msg),
                    ]
                    try:
                        regen_response = await _asyncio.wait_for(
                            model_with_tools.ainvoke(regen_messages), timeout=_LLM_TIMEOUT
                        )
                        from datetime import datetime, timezone
                        regen_response.additional_kwargs["created_at"] = datetime.now(timezone.utc).isoformat()
                        regen_response.additional_kwargs["truth_gate_regenerated"] = True
                        print(f"{ts_log()} [{label}] Truth Gate: REGENERATED response")
                        return {"messages": [regen_response], "context_usage": context_usage}
                    except Exception as regen_e:
                        print(f"{ts_log()} [{label}] Truth Gate regen failed, using original: {regen_e}")
            except Exception as gate_e:
                print(f"{ts_log()} [{label}] Truth Gate error (non-fatal): {gate_e}")

        return {"messages": [response], "context_usage": context_usage}

    except Exception as e:
        _duration_ms = int((_time.monotonic() - _t0) * 1000)
        print(f"{ts_log()} [{label}] PRIMARY FAILED: {type(e).__name__}: {e}")
        await _write_jw_metric(
            agent_id=agent_id, operation_type="channel",
            operation_label=f"channel-{channel}",
            model_used=_OPENROUTER_PRIMARY_MODEL, provider="openrouter",
            tokens_in=None, tokens_out=None,
            duration_ms=_duration_ms, succeeded=False,
        )

        # Fallback to local Ollama
        try:
            print(f"{ts_log()} [{label}] Falling back to local Ollama...")
            _t1 = _time.monotonic()
            local = get_local_model(temperature=0.7)
            try:
                local_with_tools = local.bind_tools(get_tools(_channel_tool_modules(channel)))
                response = await _asyncio.wait_for(
                    local_with_tools.ainvoke(full_messages), timeout=_LLM_TIMEOUT
                )
            except Exception:
                response = await _asyncio.wait_for(
                    local.ainvoke(full_messages), timeout=_LLM_TIMEOUT
                )
            _fb_duration_ms = int((_time.monotonic() - _t1) * 1000)

            from datetime import datetime, timezone
            response.additional_kwargs["created_at"] = datetime.now(timezone.utc).isoformat()
            meta = getattr(response, "response_metadata", {}) or {}
            actual_model = meta.get("model") or "qwen3:32b"
            print(f"{ts_log()} [{label}] Fallback response via {actual_model}")

            await _write_jw_metric(
                agent_id=agent_id, operation_type="channel",
                operation_label=f"channel-{channel}",
                model_used=str(actual_model), provider="ollama",
                tokens_in=meta.get("prompt_eval_count"),
                tokens_out=meta.get("eval_count"),
                duration_ms=_fb_duration_ms,
                tool_calls_made=len(response.tool_calls) if hasattr(response, 'tool_calls') and response.tool_calls else 0,
                succeeded=True,
            )
            return {"messages": [response], "context_usage": context_usage}

        except Exception as e2:
            _fb_duration_ms = int((_time.monotonic() - _t1) * 1000)
            print(f"{ts_log()} [{label}] FALLBACK ALSO FAILED: {type(e2).__name__}: {e2}")
            await _write_jw_metric(
                agent_id=agent_id, operation_type="channel",
                operation_label=f"channel-{channel}",
                model_used="qwen3:32b", provider="ollama",
                tokens_in=None, tokens_out=None,
                duration_ms=_fb_duration_ms, succeeded=False,
            )

        return {
            "messages": [AIMessage(content=f"I'm having trouble responding right now. Error: {str(e)}")],
            "error": str(e),
            "context_usage": context_usage,
        }


async def tool_node(state: ChannelState) -> dict:
    """Execute tool calls from the AI message.

    Handles optional approval tiers:
    - auto: executes immediately
    - notify: executes + logs notification
    - block: raises ApprovalRequired, returns message to user
    """
    messages = list(state.get("messages", []))
    last_message = messages[-1] if messages else None

    if not last_message or not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        return {"messages": []}

    tools = get_tools(_channel_tool_modules(state.get("channel", "")))
    tool_map = {t.name: t for t in tools}

    tool_messages = []
    for call in last_message.tool_calls:
        tool_name = call["name"]
        tool_args = call["args"]
        tool_id = call["id"]

        tool_func = tool_map.get(tool_name)
        if not tool_func:
            tool_messages.append(ToolMessage(
                content=f"Unknown tool: {tool_name}",
                tool_call_id=tool_id,
            ))
            continue

        try:
            # Approval tier: decorator tag by default, config can override. Always checked.
            tier = _get_tool_tier(tool_func)
            if tier == "block":
                try:
                    from src.tools.approval import block_for_approval, ApprovalRequired
                    channel = state.get("channel", "")
                    await block_for_approval(tool_name, tool_args, channel=channel)
                except ImportError:
                    pass  # No approval module — run freely
            elif tier == "notify":
                try:
                    from src.tools.approval import log_notify
                    log_notify(tool_name, tool_args)
                except ImportError:
                    pass

            result = await tool_func.ainvoke(tool_args)
            result_str = str(result)

            # Soren Layer 1: verify tool results that touch real state
            try:
                from src.tools.verification import verify_and_log, has_verifier
                if has_verifier(tool_name):
                    agent_id = state.get("agent_id", get_primary_agent_id())
                    channel = state.get("channel", "")
                    vr = await verify_and_log(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        result=result_str,
                        agent_id=agent_id,
                        channel=channel,
                    )
                    if vr.get("modified_result"):
                        result_str = vr["modified_result"]
            except Exception as ve:
                logger.error("Soren verification hook error: %s", ve)

            tool_messages.append(ToolMessage(
                content=result_str,
                tool_call_id=tool_id,
            ))

        except Exception as e:
            # Check if it's an ApprovalRequired exception
            exc_name = type(e).__name__
            if exc_name == "ApprovalRequired":
                operator = get_operator_name()
                req = getattr(e, "request", None)
                if req:
                    tool_messages.append(ToolMessage(
                        content=(
                            f"APPROVAL REQUIRED: {req.description}\n"
                            f"Request ID: {req.request_id}\n"
                            f"This action needs {operator}'s approval. "
                            f"I've queued the request."
                        ),
                        tool_call_id=tool_id,
                    ))
                else:
                    tool_messages.append(ToolMessage(
                        content=f"APPROVAL REQUIRED: {str(e)}",
                        tool_call_id=tool_id,
                    ))
            else:
                tool_messages.append(ToolMessage(
                    content=f"Tool error ({tool_name}): {str(e)}",
                    tool_call_id=tool_id,
                ))

    return {"messages": tool_messages}


# =============================================================================
# Router
# =============================================================================

def should_continue(state: ChannelState) -> str:
    """Check if the last message has tool calls that need executing."""
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if hasattr(last, 'tool_calls') and last.tool_calls:
        return "tools"
    return "end"


# =============================================================================
# Graph builder
# =============================================================================

def build_channel_graph(channel: str) -> StateGraph:
    """Build a ReAct graph for a channel."""
    graph = StateGraph(ChannelState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent")
    return graph


async def get_channel_graph(channel: str, checkpointer):
    """Get a compiled channel graph with checkpointer attached.

    ALWAYS async — this was the source of the thinking block bug.
    Atlas had a sync version that didn't propagate additional_kwargs
    through astream() correctly.
    """
    graph = build_channel_graph(channel)
    return graph.compile(checkpointer=checkpointer)
