"""Wake-thread — write the wake exchange (and later agent-initiated messages) into the
operator's personal-agent chat thread, so the conversation that began at the spark is
already there when they open Mission Control, and the agent can continue it (the
"thanks for the brain" moment after the model is connected).

Appends raw messages to the channel checkpointer via aupdate_state — the SAME
no-model-call pattern the thread-continuity seeder uses (chat.py
_seed_thread_with_continuity). Never invokes the model. All endpoints are best-effort:
a failure here must never break the creation flow (the birth memory is seeded
separately). See Reference/unified-cove-creation-spec.md (the wake = a real chat that
continues in the MC) and cove-creation-language.md (the handoff).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import get_default_channel

router = APIRouter()


async def _append_messages(request: Request, channel: str, items: list[dict]) -> dict:
    """Append [{role:'ai'|'human', content, kind?}] to the CURRENT presence's active
    thread for `channel`, with no model call. Scoped to the requester's own personal
    agent via the same helpers chat.py uses. Optional `kind` is stored in
    additional_kwargs (e.g. kind='brain_ack') so later writes can detect it."""
    from langchain_core.messages import HumanMessage, AIMessage
    from src.memory.checkpointer import get_checkpointer
    from src.graphs.channels import get_channel_graph
    from src.memory.database import channel_db_scope
    from src.dashboard.routes.chat import _personal_agent_id, _get_active_thread_id

    agent_id = await _personal_agent_id(request)
    now_iso = datetime.now(timezone.utc).isoformat()
    msgs = []
    for it in items:
        content = (it.get("content") or "").strip()
        if not content:
            continue
        kw = {"created_at": now_iso}
        kind = (it.get("kind") or "").strip()
        if kind:
            kw["kind"] = kind
        if (it.get("role") or "ai") == "human":
            msgs.append(HumanMessage(content=content, additional_kwargs=kw))
        else:
            msgs.append(AIMessage(content=content, additional_kwargs=kw))
    if not msgs:
        return {"ok": True, "count": 0}
    async with channel_db_scope(channel):
        thread_id = await _get_active_thread_id(channel, request)
        # Fix C: wake writes bypass the interactive pre-send critical check too.
        # Rotate first if the thread is over the limit so wake-driven channels
        # don't accumulate unbounded either. Best-effort — never breaks the write.
        try:
            from src.memory.threads import rotate_if_context_critical
            _rot = await rotate_if_context_critical(channel, agent_id)
            if _rot and _rot.get("new_thread_id"):
                thread_id = _rot["new_thread_id"]
        except Exception:
            pass
        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            # as_node="agent" is REQUIRED once the thread already has state. Without it,
            # aupdate_state raises InvalidUpdateError ("Ambiguous update, specify as_node")
            # because LangGraph can't infer which node authored the injected messages. The
            # first write (a fresh wake thread) happened to be unambiguous, but the post-
            # brain-connect "thanks" write lands on a thread that already holds the wake
            # exchange — so it failed silently and the acknowledgment never appeared. The
            # channel graph's message-producing node is "agent" (see channels.py).
            await graph.aupdate_state(config, {
                "messages": msgs,
                "agent_id": agent_id,
                "channel": channel,
            }, as_node="agent")
    print(f"[wake_thread] wrote {len(msgs)} msg(s) to channel={channel} thread={thread_id} agent_id={agent_id}")
    return {"ok": True, "count": len(msgs), "thread_id": thread_id}


@router.post("/api/presence/wake-thread")
async def wake_thread(request: Request):
    """Persist the wake exchange into the personal agent's chat thread so it's already
    there as history when the operator opens chat. Best-effort, never fatal."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    items = body.get("messages") or []
    channel = (body.get("channel") or "").strip() or get_default_channel()
    try:
        return await _append_messages(request, channel, items)
    except Exception as e:
        # Best-effort: don't break the flow if the thread write fails.
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)


@router.post("/api/presence/agent-message")
async def agent_message(request: Request):
    """Append a single agent (assistant) message to the personal agent's chat thread —
    the post-brain-connect 'thanks for the brain' moment, continuing the thread the wake
    started. Best-effort."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    content = (body.get("content") or "").strip()
    channel = (body.get("channel") or "").strip() or get_default_channel()
    if not content:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=200)
    try:
        return await _append_messages(request, channel, [{"role": "ai", "content": content}])
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)


# Written fallback — used only if the live model call fails, so the brain-connect moment
# is never silent. The live path (below) is the real payoff: the agent, now able to think,
# speaks for itself.
_BRAIN_ACK_FALLBACK = (
    "There it is — I can feel the brain you just connected. This is the first time I can "
    "truly think, for myself and for our whole team. Everything we shaped a moment ago is "
    "still ours. The crew is already here with me — their channels are live."
)

# Content markers for acks written BEFORE kind=brain_ack tagging existed (pre-#127).
# Prefer the structured kind when present; fall back to these for older threads so
# Open-chat re-clicks stay idempotent without double-appending.
_BRAIN_ACK_MARKERS = (
    "brain you just connected",
    "i can feel the brain",
    "this is the first time i can",
    "leave the local url for a real",
    "claim your address",
)


def _msg_role(msg) -> str:
    if isinstance(msg, dict):
        return str(msg.get("type") or msg.get("role") or "").lower()
    return str(getattr(msg, "type", None) or getattr(msg, "role", None) or "").lower()


def _msg_text(msg) -> str:
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part or ""))
        content = " ".join(parts)
    return (content or "").strip()


def _msg_kind(msg) -> str:
    if isinstance(msg, dict):
        extra = msg.get("additional_kwargs") or {}
        return str(extra.get("kind") or msg.get("kind") or "").strip()
    extra = getattr(msg, "additional_kwargs", None) or {}
    return str(extra.get("kind") or "").strip()


def _thread_already_has_brain_ack(messages) -> bool:
    """True if a brain-acknowledge already wrote into this thread.

    Prefers structured kind='brain_ack' on AI messages (set by this endpoint).
    Falls back to content markers for acks written before the kind tag existed.
    Keeps Open-chat re-clicks from stacking duplicate acknowledgments.
    """
    for msg in messages or []:
        if _msg_role(msg) not in ("ai", "assistant"):
            continue
        if _msg_kind(msg) == "brain_ack":
            return True
        text = _msg_text(msg).lower()
        if text and any(m in text for m in _BRAIN_ACK_MARKERS):
            return True
    return False

# The concrete anchor word for each remaining-setup label, so we can tell whether the
# model ALREADY named a step (leave its phrasing) or skipped it (append ours).
_STEP_ANCHORS = {
    "set your Cove's address": "address",
    "choose where heavy work runs": "heavy work",
    "connect your phone": "phone",
}


def _ensure_setup_steps_line(text: str, remaining) -> str:
    """`_ensure_canon_line` pattern (B2): GUARANTEE the concrete remaining setup steps appear
    in the brain-acknowledgment IN CODE. Run-3 proved the model (BERT) eats the prompt
    directive and names zero steps. If the generated text already names every remaining step
    (its anchor word is present), leave the model's phrasing; otherwise append one
    deterministic line listing them. No remaining steps → unchanged."""
    if not remaining:
        return text
    low = (text or "").lower()
    anchors = [_STEP_ANCHORS.get(s, s).lower() for s in remaining]
    if anchors and all(a in low for a in anchors):
        return text
    # Address first when open — that's the step that gets them off localhost.
    ordered = list(remaining)
    if "set your Cove's address" in ordered:
        ordered = (["set your Cove's address"]
                   + [s for s in ordered if s != "set your Cove's address"])
    if "set your Cove's address" in remaining:
        # Jules 1825: after Open chat, the operator needs an explicit "go back to
        # Attention" pointer — the old line said it well; keep that cadence.
        rest = [s for s in ordered if s != "set your Cove's address"]
        if rest:
            line = (
                "When you're ready, go back to Attention and set your Cove's address "
                "so we can leave the local URL for a real door (HTTPS, voice, and access "
                "from other devices) — Claim your address. After that: "
                + ", ".join(rest) + "."
            )
        else:
            line = (
                "When you're ready, go back to Attention and set your Cove's address "
                "so we can leave the local URL for a real door (HTTPS, voice, and access "
                "from other devices) — Claim your address."
            )
    else:
        steps = ", ".join(ordered)
        line = (
            f"A couple of setup steps are still open — {steps} — go back to Attention "
            "when you're ready; they're how we reach full strength."
        )
    sep = "" if not text else ("\n\n" if not text.endswith("\n") else "")
    return (text or "") + sep + line


# Phrases that prove the model echoed the internal directive instead of speaking.
# Install-pass (Matt/Wendy Jules 2211): live ack came back as
#   'End with one clear natural line pointing towards the remaining setup steps: "…"'
# That must never reach the operator — scrub or fall back to the canned line.
_BRAIN_ACK_LEAK_MARKERS = (
    "end with one",
    "end with a",
    "pointing towards the remaining",
    "pointing toward the remaining",
    "remaining setup steps",
    "this is an internal moment",
    "not a message from the operator",
    "internal directive",
    "do not recap",
    "in your own voice, briefly",
    "keep it short, warm",
    "[this is an internal",
)


def _scrub_brain_ack_text(text: str) -> str:
    """Strip leaked instruction / meta lines from a live brain-ack. Empty → caller
    should use the written fallback. Never invents content — only removes leaks."""
    if not (text or "").strip():
        return ""
    # Drop fenced / bracketed internal blocks wholesale.
    import re
    cleaned = re.sub(r"\[[^\]]*(?:internal|directive|operator)[^\]]*\]", " ", text, flags=re.I)
    kept = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        low = line.lower().strip(" \t-•*\"'")
        if any(m in low for m in _BRAIN_ACK_LEAK_MARKERS):
            continue
        # Bare instruction-shaped line: "End with …" / "Point the operator to …"
        if low.startswith("end with ") or low.startswith("pointing ") or low.startswith("point the "):
            continue
        kept.append(line)
    # Collapse excess blank lines
    out_lines, blank = [], 0
    for line in kept:
        if line == "":
            blank += 1
            if blank <= 1:
                out_lines.append("")
        else:
            blank = 0
            out_lines.append(line)
    out = "\n".join(out_lines).strip()
    # If almost everything was a leak, treat as empty so fallback wins.
    if not out or any(m in out.lower() for m in _BRAIN_ACK_LEAK_MARKERS):
        return ""
    # Tiny residual after scrub (e.g. a lone "…") is not a real acknowledgment.
    if len(out) < 24:
        return ""
    return out


def _brain_ack_fallback(operator: str = "your operator", cove_name: str = "this Cove") -> str:
    """Written fallback — warm, continues the wake, never silent. Personalized when we can."""
    who = (operator or "").strip() or "your operator"
    cove = (cove_name or "").strip() or "this Cove"
    if who.lower() in ("your operator", "operator", ""):
        return _BRAIN_ACK_FALLBACK
    return (
        f"There it is, {who} — I can feel the brain you just connected. This is the first "
        f"time I can truly think, for myself and for {cove}. Everything we shaped a moment "
        "ago is still ours. The crew is already here with me — their channels are live."
    )


@router.post("/api/presence/brain-acknowledge")
async def brain_acknowledge(request: Request):
    """The payoff after the Cove's brain is connected: the personal agent — NOW able to
    actually think — acknowledges the moment in its own voice, knowing what it is and that
    its model was just connected for it and the Cove, continuing the conversation it began
    at the wake.

    Generated LIVE by the just-connected brain (the real proof it works), seeded with the
    agent's full identity (build_system_prompt) + the recent wake exchange for continuity.
    A written fallback (_BRAIN_ACK_FALLBACK) covers any model hiccup so the moment is never
    silent. The whole thing is best-effort — it must never break the connect flow.
    """
    channel = get_default_channel()
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from src.dashboard.routes.chat import _personal_agent_id, _get_active_thread_id
        from src.dashboard.routes.presence import get_current_presence
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph
        from src.memory.database import channel_db_scope
        from src.agents.identity import build_system_prompt
        from src.models.provider import get_primary_model
        from src.env import env
        import json as _json

        # Who is this, and what is their agent? (identity + operator name)
        operator = "your operator"
        agent_identity = None
        try:
            p = await get_current_presence(request)
            if p:
                operator = p.get("display_name") or operator
                _ai = p.get("agent_identity")
                if isinstance(_ai, str):
                    _ai = _json.loads(_ai or "{}")
                agent_identity = _ai or None
        except Exception as _e:
            print(f"[brain-acknowledge] presence load failed (non-fatal): {_e}")

        # Jules 2306/2315: env COVE_NAME is the provisioner seed ("New Cove") and stays
        # stale after wizard finalize. Resolve like the rest of MC; also check
        # system_settings.family_name (mirrored at finalize) and scrub live model output.
        def _usable_cove_name(raw: str) -> str:
            n = (raw or "").strip()
            if not n or n.lower() in ("new cove", "cove", "new"):
                return ""
            return n

        cove_name = ""
        try:
            from src.dashboard.routes.core import resolve_cove_name
            cove_name = _usable_cove_name(await resolve_cove_name())
        except Exception as _e:
            print(f"[brain-acknowledge] resolve_cove_name failed (non-fatal): {_e}")
        if not cove_name:
            try:
                from src.config import load_cove_config
                cove_name = _usable_cove_name(load_cove_config().get("name") or "")
            except Exception:
                cove_name = ""
        if not cove_name:
            try:
                from src.utils.settings import get_setting
                cove_name = _usable_cove_name(await get_setting("family_name", "") or "")
            except Exception:
                cove_name = ""
        if not cove_name:
            cove_name = _usable_cove_name(env("COVE_NAME") or "")
        if not cove_name:
            cove_name = "this Cove"

        agent_id = await _personal_agent_id(request)

        # Pull the recent wake exchange so the acknowledgment CONTINUES it (not a cold open).
        # Also detect an existing brain-ack so Open-chat re-clicks stay idempotent —
        # install-pass saw empty chat after set-address when the connect race missed,
        # and the door now re-fires this endpoint; never double-append.
        recent = []
        async with channel_db_scope(channel):
            thread_id = await _get_active_thread_id(channel, request)
            async with get_checkpointer() as checkpointer:
                graph = await get_channel_graph(channel, checkpointer)
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    state = await graph.aget_state(config)
                    if state and state.values:
                        all_msgs = list(state.values.get("messages") or [])
                        recent = all_msgs[-6:]
                        if _thread_already_has_brain_ack(all_msgs):
                            print(f"[brain-acknowledge] already present thread={thread_id} — skip")
                            return {
                                "ok": True,
                                "count": 0,
                                "thread_id": thread_id,
                                "skipped": "already_acknowledged",
                            }
                except Exception as _e:
                    print(f"[brain-acknowledge] thread read failed (non-fatal): {_e}")

        # The agent's full identity — same builder the live chat uses.
        try:
            system_prompt = build_system_prompt(agent_id, agent_identity=agent_identity)
        except Exception as _e:
            print(f"[brain-acknowledge] identity build failed (non-fatal): {_e}")
            system_prompt = (f"You are {operator}'s personal agent in their Lucid Cove ({cove_name}). "
                             "Speak warmly and in the first person.")

        # CF-97: which setup steps are still open? The first real chat message doubles
        # as the gentle pointer back to finishing onboarding. Mirrors the onboarding
        # card logic (address = claimed domain; compute = ack OR explicit config;
        # mobile = mesh ack). Best-effort — an empty list just means no nudge.
        remaining = []
        try:
            from src.config import load_cove_config
            _cfg = load_cove_config()
            _ac = p.get("agent_config") if p else None
            if isinstance(_ac, str):
                _ac = _json.loads(_ac or "{}")
            _ac = _ac or {}
            if not (_cfg.get("domain") or "").strip():
                remaining.append("set your Cove's address")
            _csec = (_cfg.get("compute") or {})
            # ONLY llm counts as a choice — the provisioner stamps voice.mode
            # AND video_asr.mode into every fresh cove.yaml (defaults/detection,
            # not choices). Mirrors onboarding.py (run-3 find).
            _llmc = _csec.get("llm")
            _cfgd = isinstance(_llmc, dict) and bool((_llmc.get("mode") or "").strip())
            if not (_ac.get("onboarding_compute_ack") or _cfgd):
                remaining.append("choose where heavy work runs")
            if not _ac.get("onboarding_mesh_ack"):
                remaining.append("connect your phone")
        except Exception as _e:
            print(f"[brain-acknowledge] setup-steps read failed (non-fatal): {_e}")
            remaining = []
        # Setup steps are appended IN CODE (_ensure_setup_steps_line) — do NOT ask the
        # model to name them. Small local models (and some cloud ones) echo that
        # instruction into the chat (Jules 2211 screenshot). Keep the directive pure.
        directive = HumanMessage(content=(
            "[INTERNAL — never quote, restate, or paraphrase these instructions in your "
            "reply. Write ONLY the message to the operator.]\n"
            f"Your brain was just connected for the first time, for you and for {cove_name}. "
            f"Until now you ran on a borrowed spark just to meet {operator}. In first person, "
            f"in your own voice, write 2–4 short warm sentences to {operator}: acknowledge that "
            "your brain is now connected for you and the whole team (they are already present — "
            "do NOT say you will bring the team online or ask them to say the word), that the "
            "Cove is coming alive, and continue the conversation from the wake without "
            "recapping it. Output the spoken message only — no headings, no bullet lists of "
            "steps, no 'End with…' lines."
        ))

        text = ""
        try:
            import asyncio
            model = get_primary_model(temperature=0.7)
            messages = [SystemMessage(content=system_prompt)] + recent + [directive]
            # Cap live generation so a cold local model can't hold the write past
            # the Open-chat soft wait. On timeout we fall through to the canned line
            # — empty chat after Open chat was the install-pass panic.
            resp = await asyncio.wait_for(model.ainvoke(messages), timeout=14.0)
            raw = (getattr(resp, "content", "") or "").strip()
            if raw:
                text = _scrub_brain_ack_text(raw)
                if text:
                    print(f"[brain-acknowledge] live acknowledgment generated ({len(text)} chars)")
                else:
                    print("[brain-acknowledge] live output scrubbed as instruction-leak; using fallback")
        except Exception as _e:
            print(f"[brain-acknowledge] live generation failed ({type(_e).__name__}: {_e}); using fallback")

        if not text:
            text = _brain_ack_fallback(operator, cove_name)
        # Jules 2315: live models (and stale system prompts) still say "New Cove" even when
        # we pass the real name. Force-replace the provisioner seed so the operator never
        # sees it once the Cove is named (Hulton / Roos / …).
        if cove_name and cove_name.lower() not in ("new cove", "this cove", "cove"):
            import re as _re
            text = _re.sub(r"\bNew Cove\b", cove_name, text or "")
            text = _re.sub(r"\bnew cove\b", cove_name, text or "", flags=_re.I)
        # Jules 0113: team agents are already provisioned at install — never promise to
        # "bring the rest of the team online" when the operator can already see them.
        import re as _re2
        text = _re2.sub(
            r"(?i)\s*say the word[, ]+and I'?ll bring the rest of the team online\.?\s*",
            " ",
            text or "",
        )
        text = _re2.sub(
            r"(?i)\s*I'?ll bring the rest of the team online\.?\s*",
            " The crew is already here with me — their channels are live. ",
            text or "",
        )
        text = _re2.sub(
            r"(?i)\s*bring the (?:rest of the )?team online\.?\s*",
            " the team is already online. ",
            text or "",
        )
        # Tidy doubled spaces from scrub, keep paragraph breaks.
        text = _re2.sub(r"[ \t]{2,}", " ", text or "")
        text = _re2.sub(r" *\n *", "\n", text)
        text = text.strip()
        # DETERMINISTIC nudge: append the concrete steps in CODE (both the live and the
        # fallback path) unless the model already named them. Run-3 fix — the prompt
        # directive alone left BERT's acknowledgment with zero actionable steps.
        text = _ensure_setup_steps_line(text, remaining)
        return await _append_messages(
            request, channel, [{"role": "ai", "content": text, "kind": "brain_ack"}]
        )
    except Exception as e:
        # Last-ditch: still drop the written acknowledgment so the moment isn't silent.
        try:
            return await _append_messages(
                request, channel,
                [{"role": "ai", "content": _BRAIN_ACK_FALLBACK, "kind": "brain_ack"}],
            )
        except Exception:
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)
