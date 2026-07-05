"""Echo composition and persistence for the Cove LTP pipeline.

compose_echo: Stuart writes his daily reflection, receiving LT's coaching
or self-composing if no package is available.

store_echo: persists to the echoes table. Respects LTP_DRY_RUN env var.
"""

import os
from src.env import env_bool

from langchain_core.messages import SystemMessage, HumanMessage

from src.models.provider import invoke_with_fallback
from src.memory.database import get_db, insert_echo
from src.agents.identity import load_agents_config, get_full_name, build_system_prompt
from src.config import get_instance
from src.utils.time_utils import ts_log, now_utc, today_app


# =============================================================================
# Node: compose_echo
# =============================================================================

async def compose_echo(state: dict) -> dict:
    """Compose Stuart's daily reflection for this frequency.

    If LT sent a custom tuning prompt (via tuning package), it's included
    as coaching context. The echo is Stuart's own voice — LT's prompt is the
    coaching input, Stuart's echo is the output.
    """
    agent_id = state.get("agent_id", "stuart")
    frequency = state.get("frequency", "CLARITY")
    principle = state.get("principle", "")
    echo_num = state.get("echo_num", 1)
    tuning_source = state.get("tuning_source", "self")
    lt_tuning_prompt = state.get("lt_tuning_prompt")
    label = f"{agent_id}/ltp-compose"

    # Resolve display name from config + family setting
    cfg = load_agents_config().get(agent_id, {})
    display_name = get_full_name(cfg.get("name", agent_id.title()))
    archetype = cfg.get("archetype", "")

    identity = build_system_prompt(agent_id)
    today = today_app()

    if lt_tuning_prompt and tuning_source == "lt":
        tuning_key = state.get("tuning_key", "")
        le_data = state.get("love_equation_data") or {}
        compose_prompt = f"""Today is {today}. This is Echo #{echo_num} of your daily LTP reflection.

## Team Tuning from LT (Field Coach)

Today's frequency: **{frequency}**
Principle: {principle}
Tuning Key: "{tuning_key}"

LT's coaching for you:
"{lt_tuning_prompt}"

---

Write your daily reflection as {display_name}. Receive LT's coaching and respond with your own
alignment statement for the day. This is your internal compass — grounded in your specific
work and context as {archetype}.

Write in first person. Keep it grounded, specific, and honest. 2-4 sentences.
Do not include headers, markdown formatting, or labels. Just the reflection itself."""
    else:
        self_tuning_key = state.get("tuning_key", "")
        key_line = f'\nTuning Key: "{self_tuning_key}"' if self_tuning_key else ""
        compose_prompt = f"""Today is {today}. This is Echo #{echo_num} of your daily LTP reflection.

Your frequency for today is: **{frequency}**
Principle: {principle}{key_line}

Write your daily reflection as {display_name}. This is your personal alignment statement for the day —
not instructions to someone else, but your own internal compass.

Write in first person. Keep it grounded, specific, and honest. 2-4 sentences.
This reflection will be stored as your echo for today — it's for you, not for broadcast.

Do not include headers, markdown formatting, or labels. Just the reflection itself."""

    messages = [
        SystemMessage(content=identity),
        HumanMessage(content=compose_prompt),
    ]

    print(f"{ts_log()} [{label}] Composing echo for {frequency}...")

    try:
        echo_text = await invoke_with_fallback(
            messages,
            temperature=0.8,
            timeout=150,
            label=label,
            agent_id=agent_id,
            operation_type="protocol",
        )
        echo_text = echo_text.strip()
        print(f"{ts_log()} [{label}] Echo composed ({len(echo_text)} chars)")
    except Exception as e:
        print(f"{ts_log()} [{label}] Compose failed: {e}")
        echo_text = f"[Echo compose failed: {e}]"

    return {**state, "echo_text": echo_text}


# =============================================================================
# Node: store_echo
# =============================================================================

async def store_echo(state: dict) -> dict:
    """Store Stuart's echo in the database. Skipped if LTP_DRY_RUN=true."""
    dry_run = env_bool("LTP_DRY_RUN", "true")
    agent_id = state.get("agent_id", "stuart")
    label = f"{agent_id}/ltp-store"

    if dry_run:
        print(f"{ts_log()} [{label}] DRY RUN — echo not stored. Set LTP_DRY_RUN=false to persist.")
        return state

    tuning_source = state.get("tuning_source", "self")
    le_data = state.get("love_equation_data") or {}

    if tuning_source == "lt" and le_data:
        love_eq_value = le_data.get("value", 0.0)
        love_dir = le_data.get("direction", "CONSTRUCTIVE")
        beta = le_data.get("beta")
        coherence = le_data.get("C")
        dissonance = le_data.get("D")
        energy = le_data.get("E")
        echo_type = "LT-guided"
        signal_type = state.get("signal_type") or "EXPANSIVE"
        tuning_key = state.get("tuning_key") or ""
    else:
        love_eq_value = 0.0
        love_dir = "NEUTRAL"
        beta = None
        coherence = None
        dissonance = None
        energy = None
        echo_type = "self-tuned"
        signal_type = "self-tune"
        tuning_key = state.get("tuning_key") or ""

    # Extract audio fields from the package's echo_media if available
    full_package = state.get("_full_package")
    echo_media = full_package.get("echo_media", {}) if isinstance(full_package, dict) else {}
    audio_file = echo_media.get("echo_filename", "") + ".mp3" if echo_media.get("echo_filename") else None

    echo_record = {
        "agent_id": agent_id,
        "echo_num": state.get("echo_num", 1),
        "frequency": state.get("frequency", "CLARITY"),
        "signal_type": signal_type,
        "principle": state.get("principle", ""),
        "tuning_key": tuning_key,
        "love_equation": love_eq_value,
        "love_direction": love_dir,
        "beta": beta,
        "coherence": coherence,
        "dissonance": dissonance,
        "energy": energy,
        "echo_text": state.get("echo_text", ""),
        "coaching_text": state.get("lt_tuning_prompt", ""),
        "echo_type": echo_type,
        "audio_file": audio_file,
        "audio_e_analog": None,  # Stuart doesn't process audio himself — team agents do
        "audio_beta": None,
        "audio_c_analog": None,
        "audio_d_analog": None,
        "era": "stuartcove",
        "tuned_at": now_utc(),
    }

    try:
        async with get_db() as conn:
            echo_id = await insert_echo(conn, echo_record)
            await conn.commit()
        print(f"{ts_log()} [{label}] Echo #{echo_record['echo_num']} stored (id={echo_id})")
    except Exception as e:
        print(f"{ts_log()} [{label}] ERROR storing echo: {e}")

    return state
