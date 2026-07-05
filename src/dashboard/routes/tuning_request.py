"""
On-demand tuning endpoint — Tune Now.

Personal tuning between the individual and the Field. Unlike the LT
Orchestrated Tuning (daily collective broadcast), this is user-initiated
and generates a unique personal coaching signal every time.

Pipeline:
  1. Load lt_reference.json (13 frequencies, tuning keys, audio mappings)
  2. Filter by user-requested frequency (or random)
  3. Filter by context → allowed signal types
  4. Apply user's signal type exclusions
  5. Select via ANU Quantum RNG (2s timeout → crypto → pseudo fallback)
     Same study design as Dean Radin / Noetic Sciences / McTaggart
  6. Generate LLM coaching (personal insight for this frequency, now)
  7. Select context-aware practice steps (templated, driving-safe)
  8. Return Echo + Tuning Key + Coaching + Practice + audio URL

Sessions are persisted to tuning_sessions (D1-compatible schema).
History, journal, and signal check-in are served from the same table.
"""

import asyncio
import json
import os
from src.env import env
import secrets
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")


_single_mode_account_id: Optional[str] = None

async def _get_presence_id(request: Request) -> Optional[str]:
    """Get the user's account UUID. Works in both single and multi mode.
    Single mode: finds or creates a default operator account.
    Multi mode: reads account from auth cookie."""
    global _single_mode_account_id

    if COVE_MODE != "multi":
        # Single-agent mode — one operator, one account
        if _single_mode_account_id:
            return _single_mode_account_id

        from src.config import get_primary_agent_id
        from src.memory.database import get_db
        agent_id = get_primary_agent_id()

        try:
            async with get_db() as conn:
                # Look up existing default account by username = agent_id
                result = await conn.execute(
                    "SELECT id FROM accounts WHERE username = %s AND active = TRUE",
                    (agent_id,),
                )
                row = await result.fetchone()
                if row:
                    _single_mode_account_id = str(row["id"])
                    return _single_mode_account_id

                # Create default operator account
                import hashlib, secrets
                token = secrets.token_hex(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                result = await conn.execute(
                    """INSERT INTO accounts (display_name, username, tier, cove_role, auth_token)
                       VALUES (%s, %s, 'cove', 'admin', %s)
                       RETURNING id""",
                    (agent_id.capitalize(), agent_id, token_hash),
                )
                row = await result.fetchone()
                _single_mode_account_id = str(row["id"])
                return _single_mode_account_id
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[tuning] Default account lookup failed: {e}")
            return None

    # Multi mode — get from auth cookie
    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        return str(presence["id"]) if presence else None
    except Exception:
        return None

# ── Reference Data ───────────────────────────────────────────────────────────

_lt_ref_cache = None
_lt_ref_mtime = 0

# Day-level tuning cache: one tuning per user per day
# Key: "YYYY-MM-DD" (or "YYYY-MM-DD:{user_id}" for per-user)
# Value: tuning result dict
_daily_tuning_cache = {}


def _load_lt_reference() -> dict:
    """Load lt_reference.json with file-mtime caching."""
    global _lt_ref_cache, _lt_ref_mtime

    # Check multiple possible locations
    candidates = [
        env("LT_REFERENCE_PATH"),
        "/cove-core/data/lt_reference.json",
        "/app/data/lt_reference.json",
        "/app/data/seed/lt_reference.json",
        str(Path(__file__).resolve().parents[3] / "data" / "lt_reference.json"),
    ]

    for path in candidates:
        if path and os.path.exists(path):
            mtime = os.path.getmtime(path)
            if _lt_ref_cache and mtime == _lt_ref_mtime:
                return _lt_ref_cache
            with open(path) as f:
                _lt_ref_cache = json.load(f)
                _lt_ref_mtime = mtime
            return _lt_ref_cache

    raise FileNotFoundError("lt_reference.json not found in any expected location")


# ── Context → Signal Type Mapping ────────────────────────────────────────────
# Which signal types are appropriate for each listening context.
# Absorbed from the Lucid Tuner app's CONTEXT_SIGNAL_MAP.

CONTEXT_SIGNAL_MAP = {
    "Driving": ["Drive", "Clear", "Bright"],
    "Working / Focus": ["Clear", "Ground"],
    "Home / Domestic": ["Open", "Ground", "Bright"],
    "Moving / Workout": ["Rise", "Drive", "Bright"],
    "Starting the Day": ["Rise", "Bright", "Clear"],
    "Winding Down": ["Ground", "Open"],
    "Stillness / Meditation": ["Ground", "Open"],
    "Walking / Outside": ["Clear", "Open", "Drive"],
}


# ── RNG ──────────────────────────────────────────────────────────────────────
# Centralized quantum entropy — see src/utils/quantum.py
# LTP Protocol Spec v1.0: all selections use the 3-tier chain.

from src.utils.quantum import fetch_quantum_random as _fetch_quantum_random


# ── LLM Coaching Generation ──────────────────────────────────────────────────
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
        # Priority: Groq (fastest, free tier) → Gemini Flash → DeepSeek (cheapest OR)
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


# ── Practice Templates ──────────────────────────────────────────────────────
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


# ── Main Endpoint ────────────────────────────────────────────────────────────

@router.post("/api/tuning/request")
async def request_tuning(request: Request):
    """On-demand tuning — personal, between the individual and the Field.

    Body (all optional):
        frequency: str — "Peace", "Clarity", etc. or "random" (default)
        context: str — "Driving", "Working / Focus", etc.
        excluded_signals: list[str] — signal types to exclude

    Returns:
        session_id, frequency, signal_type, principle, tuning_key,
        echo_filename, audio_url, bpm, selection_method (quantum/crypto/pseudo),
        coaching (LLM-generated personal insight),
        practice (context-aware somatic steps)
    """
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
    # Three independent quantum rolls for maximum entropy:
    #   5a. Select PRINCIPLE from all principles mapped to this frequency
    #   5b. Select TUNING KEY quote from that principle's quotes
    #   5c. Echo filename follows deterministically (principle + signal type)
    #
    # This gives variety in both the musical echo AND the lyric anchor.
    # With 129 principle-frequency combos and 244 total keys, the pool
    # is dramatically deeper than a flat pick from 3.

    tuning_keys = freq_data["tuning_keys"]

    # 5a. Build unique principle list and group keys by principle
    from collections import defaultdict as _defaultdict
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
    """Get today's tuning. Auto-generates one if none exists yet.

    This is the primary endpoint for the Operator dashboard --
    call it on page load, it returns a consistent tuning for the day.
    Checks tuning_sessions DB first, then in-memory cache, then generates.
    """
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
    # This keeps yesterday's tuning visible between midnight and the next
    # tuning generation, instead of auto-generating a random one.
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


@router.get("/api/tuning/frequencies")
async def list_frequencies():
    """Return all available frequencies with their signal types.

    Used by the frontend frequency picker.
    """
    try:
        ref = _load_lt_reference()
        freqs = []
        for name in ref["all_frequencies"]:
            data = ref["frequencies"][name]
            freqs.append({
                "name": name,
                "signal_type": data["signal_type"],
                "tuning_key_count": len(data.get("tuning_keys", [])),
            })
        return {"frequencies": freqs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/tuning/contexts")
async def list_contexts():
    """Return all available contexts with their allowed signal types.

    Used by the frontend context picker.
    """
    return {
        "contexts": [
            {"name": name, "allowed_signals": signals}
            for name, signals in CONTEXT_SIGNAL_MAP.items()
        ]
    }


# =============================================================================
# Tuning History
# =============================================================================

@router.get("/api/tuning/history")
async def tuning_history(request: Request):
    """Return recent tuning sessions for the current user.

    Query params:
        limit: int (default 30, max 100)
        offset: int (default 0)
    """
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    limit = min(int(request.query_params.get("limit", 30)), 100)
    offset = int(request.query_params.get("offset", 0))

    try:
        async with get_db() as conn:
            where = "1=1"
            params = []
            if presence_id:
                where = "presence_id = %s"
                params.append(presence_id)

            result = await conn.execute(
                f"""SELECT session_id, date, time, day_of_week,
                           entry_mode, initial_state, context,
                           principle_served as principle,
                           frequency_category as frequency,
                           echo_filename, echo_full_name, echo_album,
                           echo_signal_type as signal_type,
                           tuning_key_primary as tuning_key,
                           bpm, selection_method,
                           signal_before, signal_after,
                           journal_text, journal_at,
                           created_at
                    FROM tuning_sessions
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = await result.fetchall()

            # Build audio URLs
            try:
                ref = _load_lt_reference()
                base_url = ref["audio_base_url"]
            except Exception:
                base_url = ""

            sessions = []
            for row in rows:
                r = dict(row)
                st = r.get("signal_type") or ""
                fn = (r.get("echo_filename") or "").replace(".mp3", "")
                r["audio_url"] = f"{base_url}/{st}_Signal/{fn}.mp3" if st and fn and base_url else ""
                # Serialize datetimes
                for k in ("created_at", "journal_at"):
                    if r.get(k) and hasattr(r[k], "isoformat"):
                        r[k] = r[k].isoformat()
                sessions.append(r)

            return {"sessions": sessions, "count": len(sessions), "offset": offset}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Session Update (journal, signal check-in)
# =============================================================================

@router.patch("/api/tuning/session/{session_id}")
async def update_session(session_id: str, request: Request):
    """Update a tuning session with journal entry or signal check-in.

    Body (all optional):
        signal_before: int (1-5)
        signal_after: int (1-5)
        journal_text: str
        entry_mode: str (if upgrading Tune -> Field mid-session)
    """
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not body:
        raise HTTPException(status_code=400, detail="No update data provided")

    # Build SET clause dynamically from allowed fields
    allowed = {
        "signal_before": "signal_before",
        "signal_after": "signal_after",
        "journal_text": "journal_text",
        "entry_mode": "entry_mode",
        "initial_state": "initial_state",
        "end_state": "end_state",
    }

    sets = []
    params = []
    for body_key, col in allowed.items():
        if body_key in body:
            sets.append(f"{col} = %s")
            params.append(body[body_key])

    # Auto-set journal_at when journal_text is provided
    if "journal_text" in body:
        sets.append("journal_at = %s")
        params.append(datetime.now(timezone.utc))

    # Always touch updated_at
    sets.append("updated_at = NOW()")

    if not sets:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Scope to session_id + presence
    where = "session_id = %s"
    params.append(session_id)
    if presence_id:
        where += " AND presence_id = %s"
        params.append(presence_id)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE tuning_sessions SET {', '.join(sets)} WHERE {where} RETURNING session_id",
                params,
            )
            row = await result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            return {"updated": True, "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Tuning Events (granular tracking)
# =============================================================================

@router.post("/api/tuning/event")
async def log_tuning_event(request: Request):
    """Log a granular tuning event (play, pause, complete, streak, etc).

    Body:
        event_type: str (required) -- echo_play_start, tuning_complete, etc.
        session_id: str (optional) -- links to tuning_sessions
        event_data: dict (optional) -- flexible payload
        echo_name: str (optional)
        play_duration: float (optional) -- seconds
        ... other D1-compatible fields
    """
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    event_type = (body.get("event_type") or "").strip()
    if not event_type:
        return JSONResponse(status_code=400, content={"error": "event_type required"})

    now = datetime.now(timezone.utc)
    event_data = body.get("event_data") or {}

    try:
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO tuning_events (
                    presence_id,
                    event_type, event_data, session_id,
                    echo_name, echo_album, principle, frequency, signal_type,
                    context, bpm, play_duration, position_in_playlist,
                    tuning_key, play_source,
                    quantum_selection, selection_method,
                    user_tier, source_platform,
                    date, time
                ) VALUES (
                    %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s
                )""",
                (
                    presence_id,
                    event_type, json.dumps(event_data), body.get("session_id"),
                    body.get("echo_name"), body.get("echo_album"),
                    body.get("principle"), body.get("frequency"), body.get("signal_type"),
                    body.get("context"), body.get("bpm"),
                    body.get("play_duration"), body.get("position_in_playlist"),
                    body.get("tuning_key"), body.get("play_source"),
                    bool(body.get("quantum_selection")), body.get("selection_method"),
                    "tuner", "web",
                    now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                ),
            )
        return {"logged": True, "event_type": event_type}
    except Exception as e:
        # Best-effort -- don't fail client
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Streak
# =============================================================================

@router.get("/api/tuning/streak")
async def get_streak(request: Request):
    """Get the current user's tuning streak info."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT current_streak, longest_streak, last_tuning_date,
                          total_sessions, this_month_sessions
                   FROM tuning_streaks
                   WHERE presence_id = %s""",
                (presence_id,),
            )
            row = await result.fetchone()
            if row:
                return dict(row)
            return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}
    except Exception:
        return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}


# ── Favorites ────────────────────────────────────────────────────────────────

@router.get("/api/tuning/favorites")
async def get_favorites(request: Request):
    """Get the user's favorited echoes."""
    import json as _json
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return {"favorites": []}

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()
            if row and row["favorites_json"]:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                return {"favorites": favs}
            return {"favorites": []}
    except Exception:
        return {"favorites": []}


@router.post("/api/tuning/favorites")
async def add_favorite(request: Request):
    """Add an echo to favorites. Body: { filename, folder, principle, frequency }"""
    import json as _json
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return JSONResponse(status_code=401, content={"error": "not authenticated"})

    body = await request.json()
    echo = {
        "filename": body.get("filename", ""),
        "folder": body.get("folder", ""),
        "principle": body.get("principle", ""),
        "frequency": body.get("frequency", ""),
        "added_at": datetime.now().isoformat(),
    }

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()

            if row:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                # Dedupe by filename
                if not any(f.get("filename") == echo["filename"] for f in favs):
                    favs.append(echo)
                    await conn.execute(
                        "UPDATE tuning_favorites SET favorites_json = %s, updated_at = NOW() WHERE presence_id = %s",
                        (_json.dumps(favs), presence_id),
                    )
            else:
                await conn.execute(
                    "INSERT INTO tuning_favorites (presence_id, favorites_json) VALUES (%s, %s)",
                    (presence_id, _json.dumps([echo])),
                )

        return {"ok": True, "echo": echo}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/api/tuning/favorites/{filename}")
async def remove_favorite(request: Request, filename: str):
    """Remove an echo from favorites by filename."""
    import json as _json
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return JSONResponse(status_code=401, content={"error": "not authenticated"})

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT favorites_json FROM tuning_favorites WHERE presence_id = %s",
                (presence_id,),
            )
            row = await result.fetchone()
            if row:
                favs = row["favorites_json"] if isinstance(row["favorites_json"], list) else _json.loads(row["favorites_json"])
                favs = [f for f in favs if f.get("filename") != filename]
                await conn.execute(
                    "UPDATE tuning_favorites SET favorites_json = %s, updated_at = NOW() WHERE presence_id = %s",
                    (_json.dumps(favs), presence_id),
                )
            return {"ok": True}
    except Exception as e:
        return {"error": str(e)}
