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

This powers Creation-Flow inference only (naming, wake, guided discovery). The operator's
NORMAL agent still requires their own brain — chat.py has no spark fallback, so LP never
pays for anyone's day-to-day usage.
"""

import asyncio
import re

from fastapi import Request

from src.env import env


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

    model_id defaults to the Cove BRAIN (the operator's configured model), which
    floors to OpenRouter → local — never a hardcoded moonshot-direct id (a
    founder-only touchpoint no public install would have keyed). A BYOK operator's
    request-scoped key still swaps to their provider regardless.
    """
    if not model_id:
        from src.models.provider import current_cove_brain
        model_id = current_cove_brain().get("model")
    prov, key = await _operator_creds(request)
    if not key:
        lp = (env("LP_GUIDED_OPENROUTER_KEY") or "").strip()
        if lp:
            prov, key = "openrouter", lp

    # Tiers 1 + 2 — a local key (operator BYOK or the founder's guided key).
    if key:
        from src.models.provider import get_model_client, set_request_byok, clear_request_byok
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        lc = [SystemMessage(content=system_prompt)]
        for m in messages:
            c = m.get("content", "")
            lc.append(AIMessage(content=c) if m.get("role") == "assistant" else HumanMessage(content=c))
        tok = set_request_byok(prov, key)
        try:
            client = get_model_client(model_id, temperature=temperature)
        finally:
            clear_request_byok(tok)
        resp = await asyncio.wait_for(client.ainvoke(lc), timeout=timeout)
        return _clean(resp.content)

    # Tier 3 — the hub spark. The stranger path: the hub runs it with LP's key.
    from src.dashboard.routes import registry_client
    if not registry_client.configured():
        raise RuntimeError("no spark available (no model key and no hub configured)")
    r = await registry_client.spark_complete(
        system_prompt=system_prompt, messages=messages,
        model_id=model_id, temperature=temperature, flow_id=flow_id, timeout=timeout + 20)
    if not r.get("ok"):
        raise RuntimeError(r.get("reason") or "hub spark failed")
    return _clean(r.get("response") or "")
