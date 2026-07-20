"""Spark — the shared onboarding model Lucid Cove gives so a stranger's agent can
wake and the guided tour can run, WITHOUT the stranger holding any key.

Resolution order (Creation-Flow / onboarding inference ONLY):
  1. The operator's own model creds (BYOK), if they've added one.
  2. A local LP_GUIDED_OPENROUTER_KEY in the Cove's env (the founder path).
  3. The HUB spark proxy — the Cove asks the hub (app.lucidcove.org) to run the
     inference with LP's key, authenticated by the operator token. This is what makes
     the spark work for ANY stranger: the key lives only on the hub, never in the repo
     or on the stranger's box. (cove-creation-language.md: "Spark = the shared model
     Lucid Cove gives so the agent can wake and meet the operator.")

THE SPARK BOUNDARY (2026-07-19): LP's key (tiers 2 + 3) carries exactly ONE thing —
the install wizard from the public repo, i.e. creating the Cove. The wizard's finalize
writes the admin's `agent_identity`; from that moment the spark is DEAD to this Cove.
Full stop. Everything after — the admin onboarding, Action Board, mirror builder — runs
on the operator's own brain or doesn't run. `spark_allowed()` is that boundary; every
LP-key path must check it. Tier 1 (the operator's OWN key, their money) is never gated.

THE SPARK PIN: on LP's key the model is ALWAYS Kimi K2.5 via OpenRouter — never the
Cove brain, never a caller-supplied model_id, never BYOK_DEFAULT_MODEL's lottery.
(2026-07-17..19 incident: the spark inherited a non-OpenRouter Cove brain, hit the
provider-mismatch branch in get_model_client, and ran openrouter/auto — Opus 4.6 and
GPT-5.6 billed to the LP Cove Onboarding key.)

This powers Creation-Flow inference only (naming, wake, guided discovery). The operator's
NORMAL agent still requires their own brain — chat.py has no spark fallback, so LP never
pays for anyone's day-to-day usage.
"""

import asyncio
import re

from fastapi import Request

from src.env import env

# The one model LP's key will ever run. Registry id + raw string (the string is also
# pinned into set_request_byok so no resolution path can reroute it).
SPARK_MODEL_ID = "kimi-k2.5-openrouter"
SPARK_MODEL_STRING = "moonshotai/kimi-k2.5"

# Abuse caps for LP-key calls. Creation-Flow calls are single-turn JSON generations —
# there is no conversation with this key.
SPARK_MAX_MESSAGES = 4
SPARK_MAX_SYSTEM_CHARS = 8000
SPARK_MAX_TOTAL_CHARS = 12000


async def spark_allowed(request: Request) -> bool:
    """True only while the signed-in operator is a Cove admin still inside the
    new-cove-setup wizard (empty `agent_identity` — the same check presence.py's
    login redirect uses for firstrun). The wizard's finalize writes the identity,
    which permanently closes this. Fails CLOSED: no session, not admin, or any
    error → no spark."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        if not p or (p.get("cove_role") or "") != "admin":
            return False
        ai = p.get("agent_identity")
        if isinstance(ai, str):
            import json
            try:
                ai = json.loads(ai) if ai.strip() else {}
            except Exception:
                ai = {}
        return not (isinstance(ai, dict) and ai)
    except Exception:
        return False


def spark_caps_ok(system_prompt: str, messages: list) -> bool:
    """Size caps for LP-key calls. Pure."""
    if len(system_prompt or "") > SPARK_MAX_SYSTEM_CHARS:
        return False
    if not isinstance(messages, list) or len(messages) > SPARK_MAX_MESSAGES:
        return False
    total = sum(len(str((m or {}).get("content", ""))) for m in messages)
    return total <= SPARK_MAX_TOTAL_CHARS


async def _operator_creds(request: Request) -> tuple[str, str]:
    """This operator's own model provider + key (BYOK), or ('', '')."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        ac = (p or {}).get("agent_config") or {}
        if isinstance(ac, str):
            import json
            try:
                ac = json.loads(ac) or {}
            except Exception:
                ac = {}
        if isinstance(ac, dict):
            return (ac.get("model_provider") or "").strip(), (ac.get("model_api_key") or "").strip()
    except Exception:
        pass
    return "", ""


def _clean(content: str) -> str:
    content = (content or "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content


async def guided_complete(request: Request, system_prompt: str, messages: list,
                          *, temperature: float = 0.7, model_id: str = None,
                          flow_id: str = None, timeout: float = 90.0) -> str:
    """Run a guided/onboarding completion via the best available spark tier.

    `messages` is a list of {role: 'user'|'assistant', content: str}. Returns the
    model's text. Raises RuntimeError if no tier is available (a fully off-network
    keyless Cove — the caller should fall back to a non-model path).

    Tier 1 (operator BYOK): model_id defaults to the Cove BRAIN, and the operator's
    request-scoped key swaps to their provider — their key, their choice.
    Tiers 2 + 3 (LP's key): gated by spark_allowed() (creation only), capped by
    spark_caps_ok(), and PINNED to SPARK_MODEL — the caller's model_id and the
    Cove brain are both ignored on LP's dime.
    """
    prov, key = await _operator_creds(request)
    on_lp_key = False
    if not key:
        lp = (env("LP_GUIDED_OPENROUTER_KEY") or "").strip()
        if lp and await spark_allowed(request):
            prov, key, on_lp_key = "openrouter", lp, True

    if on_lp_key:
        if not spark_caps_ok(system_prompt, messages):
            raise RuntimeError("spark request too large (creation calls are single short turns)")
        model_id = SPARK_MODEL_ID
    elif not model_id:
        from src.models.provider import current_cove_brain
        model_id = current_cove_brain().get("model")

    # Tiers 1 + 2 — a local key (operator BYOK or the founder's guided key).
    if key:
        from src.models.provider import get_model_client, set_request_byok, clear_request_byok
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        lc = [SystemMessage(content=system_prompt)]
        for m in messages:
            c = m.get("content", "")
            lc.append(AIMessage(content=c) if m.get("role") == "assistant" else HumanMessage(content=c))
        # On LP's key the explicit model pin rides the BYOK context too, so even a
        # provider mismatch inside get_model_client cannot reroute to another model.
        tok = set_request_byok(prov, key, model=SPARK_MODEL_STRING if on_lp_key else "")
        try:
            client = get_model_client(model_id, temperature=temperature)
        finally:
            clear_request_byok(tok)
        resp = await asyncio.wait_for(client.ainvoke(lc), timeout=timeout)
        return _clean(resp.content)

    # Tier 3 — the hub spark. The stranger path: the hub runs it with LP's key.
    # Same boundary: creation only, capped, pinned (the hub re-pins server-side too).
    from src.dashboard.routes import registry_client
    if not registry_client.configured():
        raise RuntimeError("no spark available (no model key and no hub configured)")
    if not await spark_allowed(request):
        raise RuntimeError("spark unavailable: Cove creation is complete — connect your own intelligence")
    if not spark_caps_ok(system_prompt, messages):
        raise RuntimeError("spark request too large (creation calls are single short turns)")
    r = await registry_client.spark_complete(
        system_prompt=system_prompt, messages=messages,
        model_id=SPARK_MODEL_ID, temperature=temperature, flow_id=flow_id, timeout=timeout + 20)
    if not r.get("ok"):
        raise RuntimeError(r.get("reason") or "hub spark failed")
    return _clean(r.get("response") or "")
