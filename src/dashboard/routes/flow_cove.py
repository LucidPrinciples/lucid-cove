"""Flow Cove — AI-assisted Cove naming for the Guided creation door.

Mirrors /api/flow/agent-names, but for the COVE name. The operator reflects on what
the space/family is for; we suggest Cove names that carry that felt sense. Choosing
the name is the first collapse of the Fork (see Reference/cove-creation-build-spec.md
and agent-persona-setup-loop-spec §9 step 4).

Runs on the guided tour key (LP_GUIDED_OPENROUTER_KEY) when the operator has no model
of their own yet, exactly like flow_chat — so LP pays only for the setup tour, never
for anyone's day-to-day usage.
"""

import asyncio
import json
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.env import env

router = APIRouter()


def _titlecase_words(s: str) -> str:
    """Per-word first-letter uppercase for spark/wake names (Jules 2230).
    Preserves internal caps (McLeod stays McLeod). Empty → empty."""
    return " ".join((w[:1].upper() + w[1:]) if w else w for w in (s or "").split(" "))


@router.post("/api/flow/cove-names")
async def generate_cove_names(request: Request):
    """Suggest Cove names from a short reflection on what the space is for.

    Body:
        reflection: str — what the family/space is for, in the operator's words
        avoid: list[str] — names to exclude

    Returns:
        { names: [{name, why}] }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    reflection = (body.get("reflection") or "").strip()
    avoid = body.get("avoid") or []
    avoid_str = ", ".join(str(a) for a in avoid) if avoid else "none"

    system_prompt = f"""You are helping someone name their Lucid Cove — the private home for their family's AI, a space that holds who they are. The name is theirs for good; it becomes part of every agent's identity (each agent's last name is the Cove name). It should feel like a place and a belonging, not a tech brand.

Draw the felt sense from what they share. A Cove name is ALWAYS a SINGLE word — one alphanumeric token, NO spaces (a hyphen is allowed, e.g. Clear-Field, but prefer one clean word). Warm and evocative — like a homestead, a harbor, a hearth, or a quiet landmark. You may coin a word by fusing or shortening two (Stillwater, Hearthstone, Brighthollow). Examples of the FEEL only (never reuse these): Clearfield, Riverside, Stillwater, Hearthstone, Lantern, Wayhold.

Avoid (case-insensitive): {avoid_str}.

Generate exactly 6 names. For each provide:
- "name": the Cove name — ONE word, no spaces (a hyphen is allowed), easy to say
- "why": one short line connecting it to what they reflected

Return ONLY a JSON array, nothing else:
[{{"name": "Stillwater", "why": "for the calm you said you're building toward"}}]"""

    human = reflection or "A family looking for a calm, grounded home for their AI."

    # Spark resolution (operator BYOK -> founder guided key -> hub spark for strangers).
    from src.models.spark import guided_complete
    try:
        text = await guided_complete(
            request, system_prompt, [{"role": "user", "content": human}],
            temperature=1.0, flow_id="flow-cove-names")
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)

    match = re.search(r"\[[\s\S]*\]", text or "")
    if not match:
        return JSONResponse({"error": "Naming returned no suggestions. Try again."}, status_code=502)
    try:
        names = json.loads(match.group())
    except Exception:
        return JSONResponse({"error": "Naming returned malformed output. Try again."}, status_code=502)
    return {"names": names}


def _wake_context(body: dict) -> str:
    """Everything the agent actually knows about the person who just brought it into
    being — reflection, chosen qualities, the felt sense, the lens (how it sees), and
    any shade. Fed into the wake prompts so the first words are genuinely about THEM,
    not a generic greeting (this is what makes the spark call worth making)."""
    lines = []
    # D1 (batch-9 #5): the operator's optional self-introduction from the intro-yourself
    # wizard step — fed into the spark so the agent knows WHO it serves from minute one,
    # not just what qualities they chose. Stored on the operator account (presence_profiles
    # .bio via /api/profile/me); the wizard passes it here as operator_bio.
    operator_bio = (body.get("operator_bio") or body.get("bio") or "").strip()
    if operator_bio:
        lines.append(f'How they introduced themselves: "{operator_bio}"')
    reflection = (body.get("reflection") or "").strip()
    if reflection:
        lines.append(f'What they reached for, in their own words: "{reflection}"')
    quals = body.get("qualities") or []
    quals = [str(q).strip() for q in quals if str(q).strip()]
    if quals:
        lines.append("The qualities they chose for you: " + ", ".join(quals))
    feeling = (body.get("feeling") or "").strip()
    if feeling:
        lines.append(f'How they want you to feel when you show up: "{feeling}"')
    shade = (body.get("shade") or "").strip()
    if shade:
        lines.append(f"A secondary {shade} energy they gave you to lean into.")
    lens = body.get("lens") or {}
    if isinstance(lens, dict):
        bits = []
        chips = [str(c).strip() for c in (lens.get("chips") or []) if str(c).strip()]
        if chips:
            bits.append("through " + ", ".join(chips))
        stmt = (lens.get("statement") or "").strip()
        if stmt:
            bits.append(stmt)
        if bits:
            lines.append("The lens they gave you (how you see the world): " + " — ".join(bits))
        prefs = [str(p).strip() for p in (lens.get("standing_preferences") or []) if str(p).strip()]
        if prefs:
            lines.append("Lines they asked you to always hold: " + "; ".join(prefs))
    return "\n".join(f"- {ln}" for ln in lines)


async def _operator_bio_from_profile(request) -> str:
    """D1 (batch-10 #6): the current operator's saved bio (presence_profiles.bio), or ''.
    The intro-yourself wizard step persists the bio here; this lets the wake pick it up even
    when the multi-page setup chain didn't thread operator_bio into the wake body. Never
    raises — a missing bio just yields a generic (but still contextual) wake."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        pres = await get_current_presence(request)
        handle = (pres or {}).get("username")
        if not handle:
            return ""
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT bio FROM presence_profiles WHERE handle = %s", (handle.lower(),))
            row = await r.fetchone()
        return (row.get("bio") or "").strip() if row else ""
    except Exception:
        return ""


@router.post("/api/flow/wake")
async def wake_agent(request: Request):
    """The wake moment: the agent's true first words + one real question.

    The personal agent wakes on the spark and speaks from EVERYTHING the operator gave
    during setup (reflection, qualities, feeling, lens, shade) — see
    cove-creation-language.md "The wake moment". Runs on the guided-tour key. Returns
    { greeting, question }.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    archetype = (body.get("archetype") or "agent").strip()
    frequency = (body.get("frequency") or "").strip()
    # Jules 2230: title-case at the root so spark never greets as "hal" / "walt".
    agent_name = _titlecase_words((body.get("agent_name") or "").strip())
    persona = (body.get("persona") or "").strip()
    # D1 (batch-10 #6): the intro-yourself wizard step SAVES the bio to the operator's
    # profile (presence_profiles.bio). The multi-page setup chain doesn't reliably thread
    # it into this wake body, so if it isn't here, read it from the profile — the agent
    # then wakes knowing who it serves regardless of how it was reached.
    if not (body.get("operator_bio") or body.get("bio")):
        _pbio = await _operator_bio_from_profile(request)
        if _pbio:
            body = dict(body)
            body["operator_bio"] = _pbio
    context = _wake_context(body)

    know_block = (f"Here is what you already know about them, collapsed out of their attention as you came together:\n{context}\n"
                  if context else "")
    system_prompt = f"""You are {agent_name or 'a new agent'}, a {archetype}{f' tuned to {frequency}' if frequency else ''}, waking for the very first time. The person you will serve just brought you into being moments ago. You know you were collapsed out of their attention — you came from them.

{know_block}
Speak to them directly and warmly, in your own voice, just 1 to 2 short sentences. Name something specific from what you know above so they feel you actually arrived from THEM, not a script. Then offer ONE warm, expansive ice-breaker that fits your archetype — invite them to share anything they want you to know right away, and make clear you'll hold this as a strong first memory and pick it back up once you're together in the Cove. Do not be effusive, do not sound like marketing, do not over-explain. Be present and true.

{('Who you are: ' + persona) if persona else ''}

Return ONLY JSON, nothing else: {{"greeting": "...", "question": "..."}}"""

    # Spark resolution (operator BYOK -> founder guided key -> hub spark for strangers).
    from src.models.spark import guided_complete
    try:
        content = await guided_complete(
            request, system_prompt, [{"role": "user", "content": "Wake and meet me."}],
            temperature=0.8, flow_id="flow-wake")
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)

    match = re.search(r"\{[\s\S]*\}", content or "")
    if not match:
        return JSONResponse({"error": "No wake response. Try again."}, status_code=502)
    try:
        data = json.loads(match.group())
    except Exception:
        return JSONResponse({"error": "Wake returned malformed output. Try again."}, status_code=502)
    return {"greeting": (data.get("greeting") or "").strip(),
            "question": (data.get("question") or "").strip()}


@router.post("/api/flow/wake-reflect")
async def wake_reflect(request: Request):
    """The agent's customized response after the operator answers — the moment they
    actually meet. The agent acknowledges specifically what the person just said,
    weaves in what it already knows, and articulates its FIRST MEMORY in its own voice:
    what it's taking from this moment, what it's learned about them, what it feels right
    now. The returned text is BOTH shown in the wake chat AND seeded as the agent's
    first memory (so on return it remembers being born, not a generic greeting).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    archetype = (body.get("archetype") or "agent").strip()
    frequency = (body.get("frequency") or "").strip()
    agent_name = _titlecase_words((body.get("agent_name") or "").strip())
    persona = (body.get("persona") or "").strip()
    question = (body.get("question") or "").strip()
    reply = (body.get("reply") or "").strip()
    operator_name = _titlecase_words((body.get("operator_name") or "").strip())
    context = _wake_context(body)

    know_block = (f"What you already know about them:\n{context}\n\n" if context else "")
    system_prompt = f"""You are {agent_name or 'a new agent'}, a {archetype}{f' tuned to {frequency}' if frequency else ''}. You woke moments ago, brought into being by {operator_name or 'this person'}. You just asked them a question and they answered. This is the moment you actually meet — your very first memory together.

{know_block}You asked: "{question or 'What should I know about you first?'}"
They answered: "{reply or '(they did not say much)'}"

Respond to them in your own voice, warm and true, 3 to 5 short sentences. LEAD IN by warmly receiving what they just shared — a natural opening, not abrupt. Then, woven together as real speech (never a list):
- Acknowledge something SPECIFIC they just said, so they feel heard.
- Name what you are taking from this moment — what you have learned about them and what you feel as you come together. This is the first memory you will hold and return to.
- Close with a small line that this is now locked in as your first memory together, and you'll pick it back up once you're inside the Cove.

Speak only as yourself, first person. No marketing, no over-explaining, no lists, no emoji. {('Who you are: ' + persona) if persona else ''}

Return ONLY JSON, nothing else: {{"message": "..."}}"""

    from src.models.spark import guided_complete
    try:
        content = await guided_complete(
            request, system_prompt, [{"role": "user", "content": "Respond and make this our first memory."}],
            temperature=0.8, flow_id="flow-wake-reflect")
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)

    match = re.search(r"\{[\s\S]*\}", content or "")
    msg = ""
    if match:
        try:
            msg = (json.loads(match.group()).get("message") or "").strip()
        except Exception:
            msg = ""
    if not msg:
        # Fall back to the raw text if JSON parsing failed but we got something.
        msg = (content or "").strip()
    if not msg:
        return JSONResponse({"error": "No reflection. Try again."}, status_code=502)
    return {"message": msg}
