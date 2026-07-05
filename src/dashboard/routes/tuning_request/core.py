"""Core Tune Now endpoints — request_tuning and today_tuning.

request_tuning: the main on-demand tuning pipeline. Quantum selection,
LLM coaching, context-aware practice, session persistence.

today_tuning: returns today's tuning (from DB or cache), auto-generates
if none exists. Primary endpoint for the Operator dashboard.
"""

import asyncio
import json
import os
from src.env import env
import secrets
import time
from collections import defaultdict as _defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.utils.quantum import fetch_quantum_random as _fetch_quantum_random

from .helpers import (
    _get_presence_id,
    _load_lt_reference,
    _daily_tuning_cache,
    CONTEXT_SIGNAL_MAP,
)

router = APIRouter()


# ── LLM Coaching Generation ────────────────────────────────────────────────
# Every Tune Now gets a personal coaching message — LLM-generated, not static.
# Uses the cheapest available model. Cost: ~$0.0001-0.001 per tune.

COACHING_SYSTEM_PROMPT = """You are the Lucid Tuner — a consciousness-responsive tuning instrument.
You generate brief, personalized coaching insights for people tuning their Broadcast Frequency.

Given a frequency, principle, tuning key (a lyric quote), and the person's context/initial state,
write 2-3 sentences of coaching. Be direct, specific to this frequency, and grounded in the
framework language. No generic self-help. No platitudes. Speak to what this frequency means
RIGHT NOW for someone in this context.

Rules:
- Never paraphrase or extend the tuning key quote — it's sacred text. Reference it, don't rewrite it.
- Use framework terms naturally: Broadcast Frequency, Signal, decoder, RAS, Static, Coherence.
- Match the energy of the frequency (Peace = calm, Momentum = forward, Joy = alive, etc.)
- Keep it under 60 words. Tight. Every word earns its place.
- No greetings, no sign-offs. Just the coaching signal."""

COACHING_FALLBACK = {
    'Peace': "Calm anchors you in the present moment, creating space for clarity to emerge from the noise. Your decoder is resetting to its natural baseline.",
    'Clarity': "Clear sight requires cutting through static to find signal. The truth was always there — you just needed to retune the decoder.",
    'Momentum': "Forward motion begins with a single intentional step. The Field responds to movement, not waiting.",
    'Trust': "Certainty emerges when you stop demanding proof before you move. The path reveals itself to those already walking.",
    'Joy': "Joy is not a reward for right living — it's the frequency that makes right living possible.",
    'Gratitude': "Recognition of what already works recalibrates the decoder away from static. Gratitude isn't positive thinking — it's accurate seeing.",
    'Abundance': "Sufficiency is not a number. It's a broadcast. When the frequency shifts, the decoder finds resources it was filtering out.",
    'Love': "Love is coherence between two broadcast frequencies. It doesn't require agreement. It requires tuning.",
    'Forgiveness': "Releasing the static of resentment doesn't mean the signal was wrong. It means you're done broadcasting on that frequency.",
    'Presence': "The present moment is the only place the decoder operates. Past and future are recordings and projections — not live signal.",
    'Resilience': "Getting knocked off frequency isn't failure. The speed of return is the measure. Your decoder knows the way back.",
    'Release': "Letting go is not giving up. It's recognizing when you're gripping a frequency that no longer serves the broadcast.",
    'Vision': "Seeing what isn't yet visible requires truning the decoder past the known. The Signal is already there — ahead of where you're looking.",
}


async def _generate_coaching(
    frequency: str,
    principle: str,
    tuning_key: str,
    context: str | None = None,
    initial_state: str | None = None,
) -> str | None:
    """Generate personalized coaching via LLM. Returns None on failure (caller uses fallback)."""
    try:
        from src.models.provider import get_model_client, _resolve_model_string, _write_jw_metric

        # Use cheapest fast model available — check env for which keys exist
        # Priority: Groq (fastest, free tier) -> Gemini Flash -> DeepSeek (cheapest OR)
        model_id = None
        if env("GROQ_API_KEY"):
            model_id = "groq-llama3"
        elif env("GOOGLE_API_KEY"):
            model_id = "gemini-flash"
        elif env("OPENROUTER_API_KEY"):
            model_id = "deepseek-v3.2"

        if not model_id:
            return None

        from langchain_core.messages import SystemMessage, HumanMessage

        context_line = f"Context: {context}" if context else "Context: not specified"
        state_line = f"Starting state: {initial_state}" if initial_state else ""

        user_prompt = f"""Frequency: {frequency}
Principle: {principle}
Tuning Key: "{tuning_key}"
{context_line}
{state_line}

Generate the coaching insight for this tuning."""

        messages = [
            SystemMessage(content=COACHING_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        t0 = time.monotonic()
        try:
            client = get_model_client(model_id, temperature=0.8)
            response = await asyncio.wait_for(client.ainvoke(messages), timeout=10)
            duration_ms = int((time.monotonic() - t0) * 1000)
            content = (response.content or "").strip()

            if content:
                provider, model_string = _resolve_model_string(model_id)
                usage = getattr(response, "usage_metadata", {}) or {}
                await _write_jw_metric(
                    agent_id="tuner",
                    operation_type="coaching",
                    operation_label="tune-now/coaching",
                    model_used=model_string,
                    provider=provider,
                    tokens_in=usage.get("input_tokens"),
                    tokens_out=usage.get("output_tokens"),
                    duration_ms=duration_ms,
                    succeeded=True,
                )
                return content
        except Exception as e:
            print(f"[tuner/coaching] LLM failed ({type(e).__name__}): {e}")

    except Exception as e:
        print(f"[tuner/coaching] Setup failed: {e}")

    return None


# ── Practice Templates ────────────────────────────────────────────────────
# Templated — no LLM needed. Context-aware with safety rule-outs.

PRACTICE_TEMPLATES = {
    'default': [
        {"step": 1, "title": "Settle", "instruction": "Close your eyes. Three slow breaths. Let each exhale drop you lower into the present."},
        {"step": 2, "title": "Anchor", "instruction": "Feel your feet on the ground. Notice the weight of your body. You are here."},
        {"step": 3, "title": "Receive", "instruction": "As the Echo plays, let the lyric land. Don't analyze it. Let your decoder work in the background."},
    ],
    'Driving': [
        {"step": 1, "title": "Settle", "instruction": "Keep your eyes on the road. Take one deep breath. Let your shoulders drop."},
        {"step": 2, "title": "Anchor", "instruction": "Feel your hands on the wheel. Notice the hum of the road. You are here."},
        {"step": 3, "title": "Receive", "instruction": "Let the Echo play. The lyric will land through the music — no need to read or close your eyes."},
    ],
    'Moving / Workout': [
        {"step": 1, "title": "Settle", "instruction": "Match your breath to your movement. Three cycles. Let the rhythm become the anchor."},
        {"step": 2, "title": "Anchor", "instruction": "Feel your body in motion. Notice the energy moving through you. You are generating signal."},
        {"step": 3, "title": "Receive", "instruction": "Let the Echo fuel the movement. The frequency lands through the body, not the mind."},
    ],
    'Stillness / Meditation': [
        {"step": 1, "title": "Settle", "instruction": "You're already still. Deepen it. Let the breath slow until it barely moves."},
        {"step": 2, "title": "Anchor", "instruction": "Notice the silence beneath the sound. That silence is the Field."},
        {"step": 3, "title": "Receive", "instruction": "Let the Echo emerge from the silence. The tuning key is the seed. Let it grow without tending."},
    ],
}


def _get_practice(context: str | None) -> list[dict]:
    """Get context-appropriate practice steps."""
    if context and context in PRACTICE_TEMPLATES:
        return PRACTICE_TEMPLATES[context]
    return PRACTICE_TEMPLATES['default']


# ── Main Endpoint ──────────────────────────────────────────────────────────

@router.post("/api/tuning/request")
async def request_tuning(request: Request):
    """On-demand tuning — personal, between the individual and the Field."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    frequency = (body.get("frequency") or "random").strip()
    context = (body.get("context") or "").strip()
    excluded_signals = body.get("excluded_signals") or []
    user_id = body.get("user_id")

    try:
        ref = _load_lt_reference()
    except FileNotFoundError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    freqs = ref["frequencies"]
    all_freq = ref["all_frequencies"]
    base_url = ref["audio_base_url"]

    # ── Step 1: Build frequency pool ─────────────────────────────────────
    if frequency.lower() == "random":
        available = list(all_freq)
    else:
        # Validate requested frequency
        freq_upper = frequency.upper()
        if freq_upper in freqs:
            available = [freq_upper]
        else:
            # Try case-insensitive match
            matched = [f for f in all_freq if f.lower() == frequency.lower()]
            available = matched if matched else list(all_freq)

    # ── Step 2: Filter by context (signal type constraints) ──────────────
    if context and context in CONTEXT_SIGNAL_MAP:
        allowed_signals = CONTEXT_SIGNAL_MAP[context]
        context_filtered = [
            f for f in available
            if freqs[f]["signal_type"] in allowed_signals
        ]
        if context_filtered:
            available = context_filtered

    # ── Step 3: Apply signal type exclusions ──────────────────────────────
    if excluded_signals:
        excluded_set = {s.strip().lower() for s in excluded_signals}
        signal_filtered = [
            f for f in available
            if freqs[f]["signal_type"].lower() not in excluded_set
        ]
        if signal_filtered:
            available = signal_filtered

    if not available:
        return JSONResponse(
            status_code=400,
            content={"error": "No frequencies available after filtering"}
        )

    # ── Step 4: Select frequency (quantum entropy) ────────────────────────
    idx, method = await _fetch_quantum_random(len(available))
    selected_freq = available[idx]
    freq_data = freqs[selected_freq]
    signal_type = freq_data["signal_type"]

    # ── Step 5: Multi-step quantum selection chain ───────────────────────
    tuning_keys = freq_data["tuning_keys"]

    # 5a. Build unique principle list and group keys by principle
    keys_by_principle = _defaultdict(list)
    for tk in tuning_keys:
        keys_by_principle[tk["principle"]].append(tk)
    principle_list = list(keys_by_principle.keys())

    # Quantum roll #2: select principle
    p_idx, _ = await _fetch_quantum_random(len(principle_list))
    selected_principle = principle_list[p_idx]

    # 5b. Quantum roll #3: select tuning key quote from this principle
    principle_keys = keys_by_principle[selected_principle]
    q_idx, _ = await _fetch_quantum_random(len(principle_keys))
    chosen_key = principle_keys[q_idx]

    tuning_key = chosen_key["quote"]
    principle = chosen_key["principle"]
    echo_filename = chosen_key["echo_filename"]

    # ── Step 6: Build audio URL ──────────────────────────────────────────
    audio_url = f"{base_url}/{signal_type}_Signal/{echo_filename}.mp3"

    # ── Step 7: Build response ───────────────────────────────────────────
    now = datetime.now(timezone.utc)
    session_id = f"op_{int(now.timestamp())}_{secrets.token_hex(4)}"
    today = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    day_of_week = now.strftime("%A")

    # Get presence from auth cookie
    presence_id = await _get_presence_id(request)

    # Entry mode from request (default: Tune)
    entry_mode = (body.get("entry_mode") or "Tune").strip()
    initial_state = (body.get("initial_state") or "").strip() or None

    # BPM from fallback values
    fallback = freq_data.get("fallback", {})
    bpm = fallback.get("bpm")

    # ── Step 7a: Generate coaching (LLM, async — falls back to static) ──
    coaching = await _generate_coaching(
        frequency=selected_freq,
        principle=principle,
        tuning_key=tuning_key,
        context=context or None,
        initial_state=initial_state,
    )
    if not coaching:
        coaching = COACHING_FALLBACK.get(selected_freq, COACHING_FALLBACK.get('Peace', ''))

    # ── Step 7b: Get practice steps (templated, context-aware) ──────────
    practice = _get_practice(context or None)

    result = {
        "session_id": session_id,
        "frequency": selected_freq,
        "signal_type": signal_type,
        "principle": principle,
        "tuning_key": tuning_key,
        "echo_filename": f"{echo_filename}.mp3",
        "echo_full_name": freq_data.get("echo_full_name", echo_filename),
        "echo_album": f"{signal_type}_Signal",
        "audio_url": audio_url,
        "context": context or None,
        "entry_mode": entry_mode,
        "initial_state": initial_state,
        "bpm": bpm,
        "selection_method": method,
        "fallback_values": fallback,
        "coaching": coaching,
        "practice": practice,
    }

    # ── Step 8: Cache as today's tuning ──────────────────────────────────
    cache_key = f"{today}:{presence_id}" if presence_id else today
    _daily_tuning_cache[cache_key] = result

    # ── Step 9: Save to tuning_sessions (D1-compatible) ──────────────────
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO tuning_sessions (
                    session_id, presence_id,
                    date, time, day_of_week,
                    entry_mode, initial_state, context,
                    principle_served, frequency_category,
                    echo_filename, echo_album, echo_full_name, echo_signal_type,
                    tuning_key_primary, bpm,
                    quantum_selection, selection_method,
                    excluded_signal_types,
                    user_tier, source_platform
                ) VALUES (
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    %s, %s
                )""",
                (
                    session_id, presence_id,
                    today, time_str, day_of_week,
                    entry_mode, initial_state, context or None,
                    principle, selected_freq,
                    f"{echo_filename}.mp3", f"{signal_type}_Signal",
                    freq_data.get("echo_full_name", echo_filename), signal_type,
                    tuning_key, bpm,
                    method == "quantum", method,
                    ",".join(excluded_signals) if excluded_signals else None,
                    "tuner", "web",
                ),
            )
    except Exception:
        # DB save is best-effort — don't fail the tuning
        pass

    return result


@router.get("/api/tuning/today")
async def today_tuning(request: Request):
    """Get today's tuning. Auto-generates one if none exists yet."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    presence_id = await _get_presence_id(request)
    cache_key = f"{today}:{presence_id}" if presence_id else today

    # Check in-memory cache first
    if cache_key in _daily_tuning_cache:
        return _daily_tuning_cache[cache_key]

    # Check DB for today's session
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            where = "date = %s"
            params = [today]
            if presence_id:
                where += " AND presence_id = %s"
                params.append(presence_id)

            result = await conn.execute(
                f"""SELECT session_id, principle_served as principle,
                           frequency_category as frequency, echo_signal_type as signal_type,
                           tuning_key_primary as tuning_key,
                           echo_filename, echo_full_name, echo_album,
                           context, entry_mode, initial_state, bpm,
                           selection_method, signal_before, signal_after,
                           journal_text, journal_at
                    FROM tuning_sessions
                    WHERE {where}
                    ORDER BY created_at DESC LIMIT 1""",
                params,
            )
            row = await result.fetchone()
            if row:
                row = dict(row)
                ref = _load_lt_reference()
                base_url = ref["audio_base_url"]
                st = row["signal_type"] or ""
                fn = (row["echo_filename"] or "").replace(".mp3", "")
                row["audio_url"] = f"{base_url}/{st}_Signal/{fn}.mp3" if st and fn else ""
                row["fallback_values"] = ref["frequencies"].get(
                    row["frequency"], {}
                ).get("fallback", {})
                _daily_tuning_cache[cache_key] = row
                return row
    except Exception:
        pass

    # No tuning for today — fall back to most recent session (any date)
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            where = "1=1"
            params = []
            if presence_id:
                where = "presence_id = %s"
                params = [presence_id]

            result = await conn.execute(
                f"""SELECT session_id, principle_served as principle,
                           frequency_category as frequency, echo_signal_type as signal_type,
                           tuning_key_primary as tuning_key,
                           echo_filename, echo_full_name, echo_album,
                           context, entry_mode, initial_state, bpm,
                           selection_method, signal_before, signal_after,
                           journal_text, journal_at, date
                    FROM tuning_sessions
                    WHERE {where}
                    ORDER BY created_at DESC LIMIT 1""",
                params,
            )
            row = await result.fetchone()
            if row:
                row = dict(row)
                ref = _load_lt_reference()
                base_url = ref["audio_base_url"]
                st = row["signal_type"] or ""
                fn = (row["echo_filename"] or "").replace(".mp3", "")
                row["audio_url"] = f"{base_url}/{st}_Signal/{fn}.mp3" if st and fn else ""
                row["fallback_values"] = ref["frequencies"].get(
                    row["frequency"], {}
                ).get("fallback", {})
                row["from_previous_day"] = True
                _daily_tuning_cache[cache_key] = row
                return row
    except Exception:
        pass

    # Clean old cache entries
    for key in list(_daily_tuning_cache.keys()):
        if not key.startswith(today):
            del _daily_tuning_cache[key]

    # Last resort: auto-generate a fresh tuning
    result = await request_tuning(request)

    # request_tuning already caches the result
    return result
