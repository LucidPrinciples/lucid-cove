"""
Agent dictate — the Dictate door of Agent Setup.

The "talk it through" path: the operator reads a short questionnaire and speaks
or pastes a free-form answer. This endpoint analyzes that transcript and extracts
the discovery fields (situation, need, qualities, feeling, gender). The SAME engine
the Guided door uses then takes over (/api/flow/agent-identity + /api/flow/agent-persona)
to derive the archetype, frequency, tuning key, and persona — so all three input
doors converge on one engine. See Reference/cove-bootstrap-onboarding-spec.md §10.2.

Voice capture (pipecat) can feed this later; for now the transcript is typed/pasted.
"""

import re
import json
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger(__name__)

# The questionnaire the operator reads and answers aloud / in writing. These are
# meant to draw out not just WHAT the agent does, but the VALUES, WORLDVIEW, and
# PERSPECTIVE the operator wants it to hold — the lens that makes it theirs.
DICTATE_QUESTIONS = [
    "Who is this agent for, and what part of life will they share with you?",
    "What should they help with — and what should they care about even when you don't ask?",
    "What do you believe that you'd want them to share or honor? A faith, values, a worldview, a way of seeing things — be as specific as you want.",
    "What perspectives or topics should they prioritize or steer by? (A faith tradition, a philosophy, your family's way, a craft — name it.)",
    "What qualities matter most in someone you'd actually lean on?",
    "How should it feel to be around them — and is there a name or a vibe you already picture?",
]


@router.get("/api/flow/dictate-questions")
async def dictate_questions():
    """The prompts the Dictate door shows the operator to talk through."""
    return {"ok": True, "questions": DICTATE_QUESTIONS}


@router.post("/api/flow/agent-dictate")
async def analyze_dictation(request: Request):
    """Transcript -> discovery fields.

    Body: { transcript: str }
    Returns: { situation, need, qualities[], feeling, gender } — fed into the
    existing /api/flow/agent-identity + /api/flow/agent-persona engine by the UI.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    transcript = (body.get("transcript") or "").strip()
    if len(transcript) < 20:
        return JSONResponse(
            {"error": "Tell me a bit more so I have something to work with."},
            status_code=400,
        )

    from src.config import get_primary_agent_id
    from src.models.provider import get_model_client, _write_jw_metric, _resolve_model_string
    from langchain_core.messages import SystemMessage, HumanMessage

    prompt = """You are reading a transcript of someone describing, in their own words, the personal AI agent they want — its purpose, the values and worldview it should hold, what it should prioritize, and how it should feel. Extract the essence into structured fields. Capture their PERSPECTIVE faithfully (a faith, philosophy, values, a way of seeing) in their own spirit — do not water it down, secularize it, or invent details they did not imply.

Return ONLY JSON:
{
  "situation": "1-2 sentences on where they are / what this agent is for",
  "need": "1-2 sentences on the kind of support they want",
  "qualities": ["3 to 7 short qualities, drawn from their own words"],
  "perspective": "1-3 sentences capturing the values / worldview / lens they want the agent to hold and steer by (faith, philosophy, family's way, etc.), faithful to what they said; empty string if none given",
  "feeling": "a short phrase for how the agent should feel to be around",
  "name": "a name if they offered or implied one, else empty string",
  "gender": "masculine | feminine | neutral (only if they signaled one; else neutral)"
}"""

    result = None
    for model in ["gemini-flash", "kimi-k2.5", "qwen2.5:32b"]:
        try:
            provider, model_string = _resolve_model_string(model)
            client = get_model_client(model, temperature=0.5)
            response = await asyncio.wait_for(
                client.ainvoke([
                    SystemMessage(content=prompt),
                    HumanMessage(content=transcript),
                ]),
                timeout=90,
            )
            content = re.sub(r"<think>.*?</think>", "", (response.content or ""), flags=re.DOTALL).strip()
            m = re.search(r"\{[\s\S]*\}", content)
            if not m:
                log.warning("[agent_dictate] %s: no JSON in response, trying next", model)
                continue
            result = json.loads(m.group())
            try:
                usage = getattr(response, "usage_metadata", {}) or {}
                await _write_jw_metric(
                    agent_id=get_primary_agent_id(), operation_type="flow",
                    operation_label=f"flow-agent-dictate/{model}",
                    model_used=model_string, provider=provider,
                    tokens_in=usage.get("input_tokens"), tokens_out=usage.get("output_tokens"),
                    duration_ms=0, succeeded=True,
                )
            except Exception:
                pass
            break
        except Exception as e:
            log.warning("[agent_dictate] %s failed: %s", model, e)
            continue

    if not result:
        return JSONResponse({"error": "Could not analyze that. Please try again."}, status_code=502)

    quals = result.get("qualities") or []
    if isinstance(quals, str):
        quals = [q.strip() for q in quals.split(",") if q.strip()]

    return {
        "ok": True,
        "situation": (result.get("situation") or "").strip(),
        "need": (result.get("need") or "").strip(),
        "qualities": [str(q).strip() for q in quals if str(q).strip()][:7],
        "perspective": (result.get("perspective") or "").strip(),
        "feeling": (result.get("feeling") or "").strip(),
        "name": (result.get("name") or "").strip(),
        "gender": (result.get("gender") or "neutral").strip(),
    }
