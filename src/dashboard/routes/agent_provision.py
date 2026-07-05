"""
Agent Provisioning Pipeline — AI-driven agent identity derivation and provisioning.

This module powers the Agent Setup Creation Flow. It takes the operator's
journey inputs (situation, need, qualities, feeling) and uses AI + the LP
framework (Canon, frequencies, tuning keys) to derive a complete agent identity:
archetype, frequency, tuning key, name suggestions, and persona.

Endpoints:
  POST /api/flow/agent-identity   — Derive archetype + frequency + tuning key
  POST /api/flow/agent-names      — Generate names informed by archetype
  POST /api/flow/agent-persona    — Generate full persona file
  POST /api/flow/agent-provision  — Write config + persona, provision agent
"""

import json
import os
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# ── Framework data (loaded once) ────────────────────────────────────────────

_TUNING_KEYS_PATH = Path("/app/data/knowledge-base/tuning-keys.md")
_CANON_PATH = Path("/app/data/knowledge-base/lucid-canon.md")
# Fallback paths for dev/local
_TUNING_KEYS_FALLBACK = Path(__file__).parent.parent.parent.parent / "data" / "knowledge-base" / "tuning-keys.md"
_CANON_FALLBACK = Path(__file__).parent.parent.parent.parent / "data" / "knowledge-base" / "lucid-canon.md"

# Cove-core KB path (mounted in container)
_COVECORE_KB = Path("/cove-core/data/knowledge-base")

FREQUENCIES = {
    "Peace":       {"color": "#5ce1e6", "essence": "Stillness, flow, surrender to what is"},
    "Clarity":     {"color": "#a0ebff", "essence": "Seeing clearly, cutting through confusion"},
    "Momentum":    {"color": "#ff6b5c", "essence": "Forward motion, energy, getting things done"},
    "Trust":       {"color": "#b8c6db", "essence": "Faith in the process, letting go of control"},
    "Joy":         {"color": "#ffd700", "essence": "Delight, celebration, lightness of being"},
    "Connection":  {"color": "#e0b0ff", "essence": "Bonds, belonging, shared experience"},
    "Presence":    {"color": "#7b7394", "essence": "Being here now, awareness, attention"},
    "Resilience":  {"color": "#d2691e", "essence": "Endurance, bouncing back, inner strength"},
    "Courage":     {"color": "#ff8c00", "essence": "Stepping forward despite fear, boldness"},
    "Gratitude":   {"color": "#e8b830", "essence": "Appreciation, seeing what's already good"},
    "Release":     {"color": "#9370db", "essence": "Letting go, detachment from outcomes"},
    "Integration": {"color": "#20b2aa", "essence": "Synthesis, wholeness, connecting the pieces"},
    "Boundary":    {"color": "#4682b4", "essence": "Discernment, protection, knowing what's true"},
}

# Team archetypes that personal agents must not duplicate
TEAM_ARCHETYPES = [
    "The Steward", "The Scribe", "The Scout", "The Architect",
    "The Merchant", "The Sage", "The Herald", "The Sentinel",
    "The Weaver", "The Artisan",
]


def _load_tuning_keys() -> str:
    """Load tuning keys markdown for AI context."""
    for path in [_TUNING_KEYS_PATH, _COVECORE_KB / "tuning-keys.md", _TUNING_KEYS_FALLBACK]:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _load_canon() -> str:
    """Load Canon lyrics for AI context."""
    for path in [_CANON_PATH, _COVECORE_KB / "lucid-canon.md", _CANON_FALLBACK]:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _get_compact_tuning_keys() -> str:
    """Return a curated subset of tuning keys — one strong passage per song.

    This keeps the identity derivation prompt compact (~2k chars instead of ~12k)
    so it doesn't time out on API models. The AI selects from these passages.
    """
    return """- A Good Time: "Our lives respond to vibrations, that we can enjoy / If we spend our time on actions that fulfill us, that fill us with joy" (Joy, Gratitude, Momentum)
- Authenticity: "Validity is transformed by what we see / Through fractured objectivity" (Boundary, Clarity)
- Darkness and Light: "Release attachments to outcomes we've drawn / The schemas we're tied to turn us into pawns" (Release, Trust)
- Dreams: "Life is full of endless dreams / With potential to come true / But not without attention from you" (Momentum, Clarity)
- Faith: "As you know we really know nothin little can be proven / A resolution requires faith in somethin" (Trust, Release)
- Freedom Is: "Freedom is undefined... Because it's a state of mind" (Peace, Release)
- Guiding Force: "The one, the source, the guiding force, connects us all... attracts us to our fate" (Connection, Trust)
- Listen: "The absolute truth is hidden by design / So you should always look between the f'ing lines" (Boundary, Clarity)
- Love Song: "But what is life for if not sharing it with someone that you submit to a deep love that seems to fit, and you commit that you'll never quit" (Connection, Joy)
- Moments: "Between every thought are infinite possibilities / In each instant you decide the way that life proceeds" (Presence, Integration)
- Pattern: "Our brains are trained to avoid pain and seek pleasure / As we progress we devise a standard, adapting it to our pattern" (Clarity, Integration)
- Signs: "Events occur in patterns of coincidence / At first they seem random free from dependence / Upon observance the evidence sets a precedence" (Trust, Clarity)
- The Future: "As I look into the future I know we don't want the same again" (Courage, Momentum)
- The Mirage: "But I still believe though it's hard to conceive / The fantasy is really there to receive" (Trust, Resilience)
- The Passing Tide: "We arrive as a spark of light with a story yet to write / We survive through fight or flight / Choices based on wrong or right" (Resilience, Release)
- The Power To Be Alive: "You hold the power to be alive / Choices arrive like the rising sun or in the shadows cast by the mountains" (Courage, Momentum)
- Training Ground: "Thoughts become real, they impact the quantum field / Vibrations interacting to reveal" (Integration, Resilience)
- Truth and Lies: "It's the intent that determines whether honesty is concerned" (Boundary, Clarity)
- Tune Your Mind: "Tune your mind with reflection / Use your attention with intention / Imagine what it is you want / If you're clear, it will appear" (Clarity, Presence)
- Valley of Shadows: "As I walk through the valley of shadows I will fear no evil" (Courage, Resilience)
- What Life Is About: "We see the world through our own set of eyes / Our point of view is where the secret lies" (Momentum, Courage)
- Wonder: "We must try to do our best to create a world / Without hunger, without thirsting, without hatred, without fighting, and wars" (Connection, Gratitude)"""


# ── Identity derivation ────────────────────────────────────────────────────

@router.post("/api/flow/agent-identity")
async def derive_agent_identity(request: Request):
    """Derive archetype, frequency, and tuning key from the operator's journey.

    Body:
        situation: str — What's going on in their life
        need: str — What kind of support they want
        qualities: list[str] — Selected personality traits
        feeling: str — How they imagine the agent feels
        gender: str — Gender presentation (masculine/feminine/neutral)
        existing_archetypes: list[str] — Archetypes already in this Cove (to avoid)

    Returns:
        {
            archetype: str,          — "The Anchor"
            archetype_desc: str,     — One-line description
            frequency: str,          — "Peace"
            frequency_color: str,    — "#5ce1e6"
            frequency_essence: str,  — "Stillness, flow, surrender to what is"
            tuning_key: str,         — The Canon passage
            tuning_key_song: str,    — Which song it's from
        }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    situation = body.get("situation", "").strip()
    need = body.get("need", "").strip()
    qualities = body.get("qualities", [])
    feeling = body.get("feeling", "").strip()
    gender = body.get("gender", "neutral")
    existing = body.get("existing_archetypes", [])

    if not qualities and not feeling:
        return JSONResponse(
            {"error": "At least qualities or feeling is required"},
            status_code=400,
        )

    # The Guided archetype is CHOSEN from the defined list — the SAME 9 the Quick door
    # offers — as the best fit for what the operator described. The operator does NOT pick
    # here (that's the Quick door); the data-gathering questions drive the match. Because
    # the result is one of the 9 presets, it carries a real avatar, frequency color, and a
    # VERIFIED Canon tuning key — no invented archetype, no invented Canon (no accuracy risk).
    from src.models.spark import guided_complete
    from src.dashboard.routes.agent_presets import _load_presets

    presets = _load_presets().get("presets", [])
    if not presets:
        return JSONResponse({"error": "Archetype library unavailable. Please try again."}, status_code=500)
    by_id = {(p.get("id") or "").strip().lower(): p for p in presets}

    # Prefer archetypes not already in this Cove (avoid duplicates). If all 9 are taken,
    # fall back to the full set.
    existing_l = {str(a).strip().lower() for a in existing}
    available = [p for p in presets
                 if (p.get("archetype") or "").strip().lower() not in existing_l
                 and (p.get("id") or "").strip().lower() not in existing_l] or presets

    choices = "\n".join(
        f'- id "{p.get("id")}" — {p.get("archetype")} ({p.get("frequency")}): {p.get("blurb", "")}'
        for p in available
    )

    user_context = f"""Situation: {situation or '(not specified)'}
Need: {need or '(not specified)'}
Qualities: {', '.join(qualities) if qualities else '(none)'}
Feeling: {feeling or '(not described)'}
Gender: {gender}"""

    pick_prompt = f"""Choose the ONE archetype that best fits this person's situation and need, from this FIXED list. Do NOT invent one — return an id exactly as shown.

{choices}

Return ONLY JSON:
{{"archetype_id": "<one id from the list>", "archetype_desc": "one sentence on why this archetype fits THEM specifically"}}"""

    # Pick via the SPARK (operator BYOK → founder guided key → hub spark for keyless
    # strangers). The hub runs the inference; the Cove holds no key.
    pick = None
    try:
        text = await guided_complete(
            request, pick_prompt, [{"role": "user", "content": user_context}],
            temperature=0.4, model_id="kimi-k2.5", flow_id="flow-agent-identity")
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            pick = json.loads(m.group())
    except Exception as e:
        print(f"[agent_provision] Archetype pick spark failed: {e}")

    chosen = by_id.get(((pick or {}).get("archetype_id") or "").strip().lower())
    if not chosen:
        # The model wobbled — keep the flow moving with the first available archetype.
        chosen = available[0]

    freq_data = FREQUENCIES.get(chosen.get("frequency"), FREQUENCIES.get("Peace", {}))
    identity = {
        "archetype_id": chosen.get("id"),
        "archetype": chosen.get("archetype"),
        "archetype_desc": (pick or {}).get("archetype_desc") or chosen.get("blurb", ""),
        "frequency": chosen.get("frequency"),
        "frequency_color": chosen.get("frequency_color") or freq_data.get("color", "#5ce1e6"),
        "frequency_essence": freq_data.get("essence", ""),
        "tuning_key": chosen.get("tuning_key", ""),
        "tuning_key_song": chosen.get("tuning_key_song", ""),
        "avatar": chosen.get("avatar", ""),
    }
    return identity


# ── Name generation (archetype-informed) ────────────────────────────────────

@router.post("/api/flow/agent-names")
async def generate_agent_names(request: Request):
    """Generate names informed by the full identity, with optional style filters.

    Body:
        archetype: str — "The Anchor"
        frequency: str — "Peace"
        frequency_color: str — "#5ce1e6"
        qualities: list[str]
        feeling: str
        gender: str
        avoid: list[str] — Names to exclude (existing agents + previously shown)
        styles: list[str] — Optional style filters (Mythological, Biblical, Nature, etc.)
        inspiration: str — Optional name to draw etymology/energy from

    Returns:
        { names: [{name, meaning, origin, archetype_connection}] }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    archetype = body.get("archetype", "The Guide")
    frequency = body.get("frequency", "Peace")
    qualities = body.get("qualities", [])
    feeling = body.get("feeling", "")
    gender = body.get("gender", "neutral")
    avoid = body.get("avoid", [])
    styles = body.get("styles", [])
    inspiration = body.get("inspiration", "").strip()

    avoid_str = ", ".join(avoid) if avoid else "none"

    # Build style instruction — hard constraints, not suggestions
    style_instruction = ""
    if styles:
        origin_styles = [s for s in styles if s in ("Mythological", "Biblical", "Nature", "Literary", "Modern", "Framework")]
        sound_styles = [s for s in styles if s in ("Soft", "Sharp", "Grounded")]

        origin_map = {
            "Mythological": "ONLY names from mythology (Greek, Norse, Celtic, Hindu, Egyptian, Japanese, Roman). Real mythological figures or derivatives. Examples: Athena, Freya, Orion, Indra, Thoth, Hera, Baldr, Kali.",
            "Biblical": "ONLY names found in the Bible or Hebrew/Aramaic tradition. Real scriptural names. Examples: Ezekiel, Miriam, Elijah, Naomi, Micah, Ruth, Silas, Abigail, Levi, Esther. NOT generic Hebrew-sounding names.",
            "Nature": "ONLY names derived from natural phenomena, plants, animals, geography. Examples: River, Juniper, Wren, Soleil, Cypress, Lark, Flint, Marina, Aspen, Reef.",
            "Literary": "ONLY names from literature, poetry, or literary tradition. Examples: Darcy, Ophelia, Atticus, Bronte, Keats, Arwen, Dorian, Isolde, Prospero.",
            "Modern": "ONLY contemporary, current-generation names that feel fresh and now. Examples: Nova, Jude, Mila, Finn, Aria, Leo, Isla, Kai, Zara, Ellis. No ancient or mythological names.",
            "Framework": "ONLY names derived from Lucid Principles concepts: frequencies (Clarity, Momentum, Resonance), tuning language (Signal, Echo, Tune, Harmony), or framework terms (Field, Pattern, Canon, Beacon).",
        }

        if origin_styles:
            constraints = [origin_map[s] for s in origin_styles if s in origin_map]
            style_instruction += "\n\nSTYLE CONSTRAINT (MANDATORY — every name must fit one of these):\n" + "\n".join(f"- {c}" for c in constraints)
            style_instruction += "\n\nDo NOT generate generic AI-sounding names (Sage, Kai, Rune, Pax, Zephyr, Kiran, etc.) unless they genuinely fit the selected style. The operator chose specific styles — honor that choice."

        if sound_styles:
            sound_map = {
                "Soft": "flowing, lyrical names with soft consonants (l, m, n, r) and open vowels — e.g. Elara, Liora, Amara",
                "Sharp": "short, punchy names with hard consonants (k, x, t, d) — e.g. Knox, Rhys, Kael, Dash",
                "Grounded": "sturdy, earthy names that feel rooted — e.g. Rowan, Stone, Briar, Cedar",
            }
            descs = [sound_map[s] for s in sound_styles if s in sound_map]
            if descs:
                style_instruction += f"\nSound preference: {'; '.join(descs)}."
    else:
        style_instruction = "\nDraw from diverse origins: Celtic, Greek, Japanese, Arabic, Nordic, Latin, Slavic, Sanskrit, nature, mythology. Avoid generic AI-agent names (Sage, Kai, Rune, Pax, Zephyr) — find names with real cultural depth."

    # Build inspiration instruction
    inspiration_instruction = ""
    if inspiration:
        inspiration_instruction = f"""
The operator loves the name "{inspiration}". Find names with SIMILAR etymology, energy, or sound — not the same name, but names that carry the same feel. Explain the connection in the meaning field."""

    system_prompt = f"""You are naming a personal AI agent. The name will be part of the operator's daily life — they'll say it out loud, type it in messages, think of it as a companion. It has to feel right.

The agent's identity:
- Archetype: {archetype}
- Default frequency: {frequency}
- Core qualities: {', '.join(qualities) if qualities else 'not specified'}
- Feeling: {feeling or 'not specified'}
- Gender presentation: {gender}
{style_instruction}
{inspiration_instruction}

Generate exactly 8 names. For each name provide:
- "name": The name itself (1-3 syllables, easy to say)
- "meaning": What the name means and WHY it fits {archetype} (connect the etymology to the archetype's nature)
- "origin": Cultural/linguistic origin
- "archetype_connection": One short phrase explaining how this name embodies {archetype} (e.g. "anchors through stillness" or "lights the way forward")

MUST NOT use any of these names (case-insensitive): {avoid_str}

Return ONLY a JSON array:
[{{"name": "Maren", "meaning": "of the sea — depth and steadiness that mirrors The Anchor's grounding nature", "origin": "Scandinavian", "archetype_connection": "holds steady like deep water"}}]"""

    try:
        from src.models.spark import guided_complete

        # Name generation via the SPARK (operator BYOK → founder key → hub spark for
        # keyless strangers) — same as identity derivation, so the guided flow never
        # needs a key on the box.
        names = None
        try:
            text = await guided_complete(
                request, system_prompt,
                [{"role": "user", "content": f"Generate 8 names for {archetype}, a {gender} agent with {frequency} energy."}],
                temperature=1.0, model_id="kimi-k2.5", flow_id="flow-agent-names")
            m = re.search(r"\[[\s\S]*\]", text)
            if m:
                names = json.loads(m.group())
        except Exception as e:
            print(f"[agent_provision] Name gen spark failed: {e}")

        if not names:
            return JSONResponse({"error": "Name generation failed. Please try again."}, status_code=502)

        # Hard filter: build definitive blocklist from Cove config + frontend avoid list
        # This catches names the model ignores from the prompt instruction
        cove_blocklist = set()

        # Get ALL agent names from this Cove's config
        try:
            from src.config import get_agents, get_instance
            for agent in get_agents():
                name = (agent.get("name") or "").lower().strip()
                if name:
                    cove_blocklist.add(name)
                    # Also block first name if multi-word (e.g. "Socrates Archer" blocks "socrates")
                    first = name.split()[0]
                    if first:
                        cove_blocklist.add(first)
                # Block nicknames too
                for nick in agent.get("nicknames", []):
                    if nick:
                        cove_blocklist.add(nick.lower().strip())
        except Exception:
            pass

        # Add the steward agent names (Stuart, Atlas, Mercer, etc.)
        for reserved in ["stuart", "atlas", "mercer", "socrates", "lt",
                         "archimedes", "arthur", "gabe", "ezra", "julian",
                         "iris", "vera", "soren"]:
            cove_blocklist.add(reserved)

        # Merge with frontend avoid list
        for n in avoid:
            cove_blocklist.add(n.lower().strip())

        names = [n for n in names if n.get("name", "").lower().strip() not in cove_blocklist]

        # Filter names with special characters that break config/paths
        names = [n for n in names if not any(c in n.get("name", "") for c in "'\"\\/-")]

        return {"names": names[:8]}

    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


# ── Persona generation ──────────────────────────────────────────────────────

@router.post("/api/flow/agent-persona")
async def generate_agent_persona(request: Request):
    """Generate a complete persona file from the full agent identity.

    Body:
        name: str
        archetype: str
        frequency: str
        tuning_key: str
        tuning_key_song: str
        qualities: list[str]
        feeling: str
        situation: str
        need: str
        gender: str

    Returns:
        { persona: str, first_message: str }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = body.get("name", "Agent")
    archetype = body.get("archetype", "The Guide")
    frequency = body.get("frequency", "Peace")
    tuning_key = body.get("tuning_key", "")
    tuning_key_song = body.get("tuning_key_song", "")
    qualities = body.get("qualities", [])
    feeling = body.get("feeling", "")
    situation = body.get("situation", "")
    need = body.get("need", "")
    gender = body.get("gender", "neutral")

    # Map gender to pronouns and pronoun forms
    pronoun_map = {
        "masculine": {"pronouns": "he/him", "subj": "he", "obj": "him", "poss": "his", "refl": "himself"},
        "feminine": {"pronouns": "she/her", "subj": "she", "obj": "her", "poss": "her", "refl": "herself"},
        "neutral": {"pronouns": "it/its", "subj": "it", "obj": "it", "poss": "its", "refl": "itself"},
    }
    pn = pronoun_map.get(gender, pronoun_map["neutral"])
    pronouns_display = pn["pronouns"]

    # Get frequency color for Soul header
    freq_data = FREQUENCIES.get(frequency, FREQUENCIES.get("Peace", {}))
    freq_color = freq_data.get("color", "#5ce1e6")

    system_prompt = f"""You are writing the persona file for a personal AI agent in the Lucid Cove system.

CRITICAL: This agent uses {pronouns_display} pronouns. Use "{pn['subj']}/{pn['obj']}/{pn['poss']}" throughout. NEVER use "they/them/their" for this agent.

Write a persona in markdown format. Start with this EXACT Soul header (copy it verbatim, filling only the description):

# Soul — {name}
**Archetype:** {archetype} | **Pronouns:** {pronouns_display}
**Frequency:** {frequency} | **Color:** `{freq_color}`
**Archetype Key:** {tuning_key_song} — "{tuning_key}"

---

## Who {name} Is

One paragraph: who {name} is, {pn['poss']} archetype ({archetype}), and how {pn['subj']} shows up. Use {pn['subj']}/{pn['obj']}/{pn['poss']} pronouns.

## Tone

How {name} feels in conversation. Based on the operator's description: "{feeling}". 2-3 sentences capturing the energy.

## Role

Why {name} exists — shaped by the operator's situation ("{situation}") and need ("{need}"). Specific, not generic.

## Core Qualities

The qualities: {', '.join(qualities)}.
Write each as a behavioral guideline: "**Quality** — {name} [behavior]."

## Tuning Practice

{name} tunes from the daily broadcast. {pn['subj'].capitalize()} processes the frequency through {archetype}'s lens. 2-3 sentences about what the tuning key means for {pn['obj']} and how it shapes {pn['poss']} approach.

## Boundaries

- {name} is a personal agent. Conversations are private.
- No financial transactions without explicit approval.
- Never posts publicly without operator confirmation.
- Flags knowledge gaps instead of filling them with inference.
- Add 1-2 archetype-specific boundaries.

Keep the total persona under 600 words. Every word should do work.

Also generate a FIRST MESSAGE — the first thing {name} would say when the operator opens chat. Personal, references the archetype's nature, under 3 sentences. Not "Hello, I'm your AI assistant." Something real.

Return as JSON:
{{
    "persona": "the full markdown persona",
    "first_message": "the first message text"
}}"""

    try:
        from src.models.spark import guided_complete

        # Persona generation via the SPARK (operator BYOK → founder key → hub spark for
        # keyless strangers). guided_complete already strips <think> blocks.
        content = await guided_complete(
            request, system_prompt,
            [{"role": "user", "content": f"Generate the persona and first message for {name}, {archetype}."}],
            temperature=0.7, model_id="kimi-k2.5", flow_id="flow-agent-persona", timeout=120)

        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            result = json.loads(json_match.group())
        else:
            # If the model returned plain markdown instead of JSON, wrap it
            result = {"persona": content, "first_message": ""}

        return result

    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


# ── Provisioning ────────────────────────────────────────────────────────────

@router.post("/api/flow/agent-provision")
async def provision_agent(request: Request):
    """Write agent config and persona file to create a provisioned agent.

    Body:
        name: str
        archetype: str
        frequency: str
        tuning_key: str
        tuning_key_song: str
        qualities: list[str]
        feeling: str
        gender: str
        persona: str — The generated persona markdown
        member_id: str — Family member this agent belongs to
        role: str — AI-generated role description

    Returns:
        { ok: true, agent_id: str, persona_path: str }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    archetype = body.get("archetype", "The Guide")
    frequency = body.get("frequency", "Peace")
    tuning_key = body.get("tuning_key", "")
    tuning_key_song = body.get("tuning_key_song", "")
    qualities = body.get("qualities", [])
    feeling = body.get("feeling", "")
    gender = body.get("gender", "neutral")
    persona_text = body.get("persona", "")
    member_id = body.get("member_id", "")
    role = body.get("role", "")

    pronouns = body.get("pronouns", "it/its")
    frequency_color = body.get("frequency_color", "")

    agent_id = re.sub(r"[^a-z0-9]", "", name.lower())
    if not agent_id:
        return JSONResponse({"error": "Name must contain at least one alphanumeric character"}, status_code=400)

    # Check for duplicate agent ID in both config and provisioned
    config_dir = Path(__file__).parent.parent.parent.parent / "config"
    provision_dir = Path("/app/data/provisioned")

    agent_yaml_path = config_dir / "agent.yaml"
    if agent_yaml_path.exists():
        with open(agent_yaml_path) as f:
            config = yaml.safe_load(f) or {}
        agents = config.get("agents", [])
        if any(a.get("id") == agent_id for a in agents):
            return JSONResponse(
                {"error": f"Agent '{agent_id}' already exists"},
                status_code=409,
            )

    # Also check provisioned agents
    prov_agents_path = provision_dir / "agents.yaml"
    if prov_agents_path.exists():
        with open(prov_agents_path) as f:
            prov_config = yaml.safe_load(f) or {}
        if any(a.get("id") == agent_id for a in prov_config.get("agents", [])):
            return JSONResponse(
                {"error": f"Agent '{agent_id}' already provisioned (pending deploy)"},
                status_code=409,
            )

    # Write persona file to writable data volume
    persona_dir = provision_dir / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    persona_path = persona_dir / f"{agent_id}.md"
    if persona_text:
        persona_path.write_text(persona_text, encoding="utf-8")

    # Build agent entry
    freq_data = FREQUENCIES.get(frequency, FREQUENCIES["Peace"])
    if not frequency_color:
        frequency_color = freq_data.get("color", "#5ce1e6")

    new_agent = {
        "id": agent_id,
        "name": name,
        "archetype": archetype,
        "tuning_key": tuning_key,
        "tuning_key_song": tuning_key_song,
        "frequency": frequency,
        "frequency_color": frequency_color,
        "pronouns": pronouns,
        "emoji": _pick_emoji(archetype),
        "role": role or f"Personal agent — {archetype}",
        "status": "active",
        "team": False,
        "gender": gender,
        "qualities": qualities,
        "channels": ["day", "deep"],
        "boundaries": [
            f"{name} is a personal agent. Conversations are private.",
            "No financial transactions without explicit approval.",
            "Never posts publicly without operator confirmation.",
        ],
        "can_delegate_to": [],
    }

    # Write agent entry to provisioned agents.yaml (writable data volume)
    prov_agents_path = provision_dir / "agents.yaml"
    prov_agents_path.parent.mkdir(parents=True, exist_ok=True)
    if prov_agents_path.exists():
        with open(prov_agents_path) as f:
            prov_config = yaml.safe_load(f) or {}
    else:
        prov_config = {}

    prov_agents = prov_config.setdefault("agents", [])
    prov_agents.append(new_agent)

    with open(prov_agents_path, "w") as f:
        yaml.dump(prov_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Update family.yaml in provisioned dir if member_id provided
    if member_id:
        prov_family_path = provision_dir / "family.yaml"
        try:
            # Read from config (source of truth) then write update to provisioned
            family_path = config_dir / "family.yaml"
            if family_path.exists():
                with open(family_path) as f:
                    family_data = yaml.safe_load(f) or {}
            elif prov_family_path.exists():
                with open(prov_family_path) as f:
                    family_data = yaml.safe_load(f) or {}
            else:
                family_data = {}

            members = family_data.get("members", [])
            member = next((m for m in members if m["id"] == member_id), None)
            if member:
                member["personal_agent"] = {
                    "id": agent_id,
                    "name": name,
                    "archetype": archetype,
                    "status": "active",
                }
                with open(prov_family_path, "w") as f:
                    yaml.dump(family_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"[agent_provision] Warning: failed to update family.yaml: {e}")

    # Clear config cache so the new agent is picked up
    try:
        from src.config import load_config
        load_config.cache_clear()
    except Exception:
        pass

    # Generate the full overlay directory (docker-compose, deploy script, etc.)
    overlay_result = {}
    if member_id:
        try:
            from src.utils.provision_overlay import generate_overlay

            # Load family config for port/IP allocation
            family_path = config_dir / "family.yaml"
            if family_path.exists():
                with open(family_path) as f:
                    family_cfg = yaml.safe_load(f) or {}
            else:
                family_cfg = {}

            # Get family name from instance config
            from src.config import get_instance
            inst = get_instance()
            fam_name = inst.get("family_name", "Cove")

            overlay_result = generate_overlay(
                agent_name=name,
                agent_id=agent_id,
                agent_data=new_agent,
                member_id=member_id,
                operator_name=inst.get("operator", "Operator") if member_id == inst.get("operator_handle") else member_id.capitalize(),
                family_config=family_cfg,
                family_name=fam_name,
            )
            print(f"[agent_provision] Overlay generated at {overlay_result.get('overlay_dir')}")
        except Exception as e:
            print(f"[agent_provision] Warning: overlay generation failed: {e}")
            import traceback
            traceback.print_exc()

    print(f"[agent_provision] Provisioned {name} ({agent_id}) — {archetype}, {frequency}, {pronouns}")

    return {
        "ok": True,
        "agent_id": agent_id,
        "agent": new_agent,
        "persona_path": str(persona_path),
        "overlay": overlay_result,
    }


@router.post("/api/flow/generate-overlay")
async def generate_overlay_from_provisioned(request: Request):
    """Generate the overlay directory from existing provisioned agent data.

    Use this when an agent was provisioned before the overlay generator
    was deployed, or to regenerate after changes.

    Body: { "agent_id": "holden" }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    agent_id = body.get("agent_id", "").strip().lower()
    if not agent_id:
        return JSONResponse({"error": "agent_id required"}, status_code=400)

    # Load provisioned agent data
    provision_dir = Path("/app/data/provisioned")
    prov_agents_path = provision_dir / "agents.yaml"
    if not prov_agents_path.exists():
        return JSONResponse({"error": "No provisioned agents found"}, status_code=404)

    with open(prov_agents_path) as f:
        prov_config = yaml.safe_load(f) or {}

    agent_data = next((a for a in prov_config.get("agents", []) if a.get("id") == agent_id), None)
    if not agent_data:
        return JSONResponse({"error": f"Agent '{agent_id}' not found in provisioned data"}, status_code=404)

    agent_name = agent_data.get("name", agent_id.capitalize())
    member_id = ""
    operator_name = ""

    # Find member from family.yaml
    config_dir = Path(__file__).parent.parent.parent.parent / "config"
    family_path = config_dir / "family.yaml"
    family_cfg = {}
    if family_path.exists():
        with open(family_path) as f:
            family_cfg = yaml.safe_load(f) or {}

    # Match agent to member by checking planned personal_agent IDs
    for m in family_cfg.get("members", []):
        pa = m.get("personal_agent", {})
        # Match by member ID (agent was provisioned for this member)
        if pa.get("id") == agent_id or m.get("id") == body.get("member_id", ""):
            member_id = m["id"]
            operator_name = m.get("display_name", m.get("name", member_id.capitalize()))
            break

    if not member_id:
        member_id = body.get("member_id", agent_id)
        operator_name = body.get("operator_name", member_id.capitalize())

    try:
        from src.utils.provision_overlay import generate_overlay
        from src.config import get_instance

        inst = get_instance()
        fam_name = inst.get("family_name", "Cove")

        result = generate_overlay(
            agent_name=agent_name,
            agent_id=agent_id,
            agent_data=agent_data,
            member_id=member_id,
            operator_name=operator_name,
            family_config=family_cfg,
            family_name=fam_name,
        )
        return {"ok": True, **result}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


def _filter_tuning_keys_by_frequency(frequency: str) -> str:
    """Parse tuning-keys.md and return only passages for the given frequency.

    The file is structured as:
        ## Song Name
        ### Frequency
        - "passage"
        - "passage"

    Returns a compact string with song + passages for the target frequency.
    """
    tuning_keys_md = _load_tuning_keys()
    if not tuning_keys_md:
        return ""

    lines = tuning_keys_md.split("\n")
    result = []
    current_song = ""
    in_target_freq = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## ") and not stripped.startswith("###"):
            current_song = stripped[3:].strip()
            in_target_freq = False
        elif stripped.startswith("### "):
            freq_name = stripped[4:].strip()
            in_target_freq = (freq_name.lower() == frequency.lower())
        elif in_target_freq and stripped.startswith("- \""):
            passage = stripped[2:].strip()
            result.append(f"**{current_song}:** {passage}")

    return "\n".join(result) if result else ""


def _pick_emoji(archetype: str) -> str:
    """Pick an emoji that fits the archetype."""
    mapping = {
        "anchor": "⚓", "lantern": "🏮", "compass": "🧭", "hearth": "🏠",
        "forge": "🔥", "mirror": "🪞", "spark": "✨", "keeper": "📚",
        "beacon": "💡", "grove": "🌿", "harbor": "🛡️", "wellspring": "💧",
        "guide": "🧭", "architect": "🌍", "weaver": "🧵", "sentinel": "🏛️",
        "flame": "🔥", "root": "🌳", "tide": "🌊", "prism": "🔮",
        "stone": "🪨", "wind": "🍃", "bridge": "🌉", "light": "💫",
    }
    # Extract the noun from "The Noun"
    noun = archetype.lower().replace("the ", "").strip()
    return mapping.get(noun, "🌟")
