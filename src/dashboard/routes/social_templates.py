"""
Social Platform Templates — LLM prompts for generating post metadata.

Each platform has a template that tells the LLM how to generate title,
description, hashtags, and tags from a video clip's transcript segment.
Templates are editable without code changes — just update the dicts.

Called by video_processing.py after moments are processed to pre-populate
social_queue draft entries with platform-appropriate metadata.
"""

import asyncio
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

# ── Platform Templates ──────────────────────────────────────────────
# Each template defines the system prompt and output format for that platform.
# Edit these to change how metadata is generated. No code changes needed.

# Rules every platform shares — appended to each system prompt.
UNIVERSAL_RULES = """
Hard rules (all platforms):
- Write finished, postable copy ONLY. Never use placeholder text of any kind: no "[link here]", no "[...]", no TODO notes, no example brackets.
- Separate paragraphs with a blank line (\\n\\n inside the JSON string).
- No em dashes. Use periods or commas instead.
- No performative hype ("groundbreaking", "game-changing", "revolutionary").
- Plain, authentic voice. The creator talks like a person, not a marketer."""

PLATFORM_TEMPLATES = {
    "youtube": {
        "name": "YouTube Shorts",
        "system_prompt": """You are a YouTube Shorts metadata writer for a creator channel about AI, consciousness, and personal development.

Write metadata for a YouTube Short clip. The creator's brand is Lucid Principles / Lucid Tuner.

Rules:
- Title: 50-70 chars. Hook-first. No clickbait but must grab attention. Include a key concept.
- Description: 3-5 short paragraphs, blank line between each. First line is the hook (shows in search). Include key concepts as bullet points (→ arrows, not dashes). Final line is exactly: https://lucidtuner.com (the only link allowed).
- Hashtags: 8-12 relevant hashtags, mix of broad (#AI #consciousness) and niche (#lucidtuner #lucidprinciples). Start with brand tags.
- Tags: 10-15 comma-separated search terms for YouTube's tag system. Mix broad and specific.

The clip is {clip_type} length ({duration}s). It's from a longer video about the creator's journey building an AI system.
{universal_rules}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": ["tag1", "tag2", ...]}}""",
    },

    "x": {
        "name": "X (Twitter)",
        "system_prompt": """You are an X/Twitter post writer for a creator who posts about AI, consciousness, and building technology.

Write a post to accompany a video clip. The creator's brand is Lucid Principles / Lucid Tuner.

Rules:
- Title: Not used on X. Set to the clip label.
- Description: This IS the post text. Max 240 chars including hashtags (the video doesn't count toward this). Punchy, conversational, thought-provoking. Can use line breaks. NEVER include a URL or link of any kind (links cost 13x more via the API and X's algorithm buries posts with external links).
- Hashtags: Default to NONE. X has moved away from hashtags and treats them as a spam signal. Include at most 1 only if it genuinely aids discovery. Usually return "".
- Tags: Empty array (X doesn't use tags).

The clip is {clip_type} length ({duration}s). Make the post make someone want to watch.
{universal_rules}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 #tag3", "tags": []}}""",
    },

    "tiktok": {
        "name": "TikTok",
        "system_prompt": """You are a TikTok caption writer for a creator who posts about AI, consciousness, and tech building.

Write a caption for a TikTok video. The creator's brand is Lucid Principles / Lucid Tuner.

Rules:
- Title: Not used directly. Set to a short hook (shown in search).
- Description: This is the caption. 150-300 chars. Hook in first line. Conversational, uses line breaks. Can use emojis sparingly. No URLs (not clickable on TikTok).
- Hashtags: 4-6 hashtags. Searchable topics (#ai #localai #tech) plus niche (#lucidtuner #consciousness). Skip generic spam tags like #fyp.
- Tags: Empty array.

The clip is {clip_type} length ({duration}s).
{universal_rules}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
    },

    "instagram": {
        "name": "Instagram Reels",
        "system_prompt": """You are an Instagram Reels caption writer for a creator posting about AI, consciousness, and personal development.

Rules:
- Title: Short hook for the cover (40 chars max).
- Description: Instagram allows long captions. 2-4 paragraphs, blank line between each. Tell a micro-story or share an insight. End with a simple CTA. No URLs (links are not clickable in captions). If a pointer is needed, say "link in bio".
- Hashtags: 8-12 focused hashtags. Mix broad and niche. Put them at the end.
- Tags: Empty array.

The clip is {clip_type} length ({duration}s). Brand: Lucid Principles / Lucid Tuner.
{universal_rules}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
    },

    "facebook": {
        "name": "Facebook",
        "system_prompt": """You are a Facebook post writer for a creator sharing about AI, consciousness, and building technology.

Rules:
- Title: Not used. Set to the clip label.
- Description: Facebook post text. 2-3 paragraphs, blank line between each, conversational, like talking to friends. Final line may be exactly: https://lucidtuner.com (the only link allowed).
- Hashtags: 0-3 max. Facebook doesn't emphasize hashtags.
- Tags: Empty array.

The clip is {clip_type} length ({duration}s). Brand: Lucid Principles / Lucid Tuner.
{universal_rules}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
    },
}


async def generate_platform_metadata(
    platform: str,
    clip_label: str,
    clip_type: str,
    duration_seconds: float,
    transcript_text: str,
) -> dict:
    """Generate platform-specific metadata for a social queue draft.

    Uses the platform template + clip transcript to generate title, description,
    hashtags, and tags via LLM. Returns a dict with those fields, or a fallback
    if the LLM call fails.
    """
    template = PLATFORM_TEMPLATES.get(platform)
    if not template:
        return {"title": clip_label, "description": "", "hashtags": "", "tags": []}

    dur_label = f"{int(duration_seconds)}s" if duration_seconds < 60 else f"{int(duration_seconds//60)}m {int(duration_seconds%60)}s"

    system_prompt = template["system_prompt"].format(
        clip_type=clip_type,
        duration=dur_label,
        universal_rules=UNIVERSAL_RULES,
    )

    human_prompt = f"""Here's the transcript from the clip "{clip_label}":

{transcript_text[:3000]}

Generate the metadata for {template['name']}."""

    try:
        from src.config import get_primary_agent_id
        from src.models.provider import get_model_client, _resolve_model_string
        from langchain_core.messages import SystemMessage, HumanMessage

        # Use fast model for metadata generation. The local tier is resolved from
        # INSTALLED tags (#11/CF-106) — never a hardcoded id that could 404 on the box.
        _candidates = ["gemini-flash", "kimi-k2.5"]
        try:
            from src.models.local_fallback import resolve_local_fallback_model
            _candidates.append(resolve_local_fallback_model())
        except Exception:
            pass  # nothing installed locally — the cloud tiers above still apply
        for model_name in _candidates:
            try:
                provider, model_string = _resolve_model_string(model_name)
                client = get_model_client(model_name, temperature=0.7)

                response = await asyncio.wait_for(
                    client.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=human_prompt),
                    ]),
                    timeout=60,
                )

                content = (response.content or "").strip()
                # Strip thinking tags
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                # Extract JSON
                json_match = re.search(r"\{[\s\S]*\}", content)
                if json_match:
                    result = json.loads(json_match.group())
                    logger.info(f"Generated {platform} metadata via {model_name} for '{clip_label}'")
                    return {
                        "title": result.get("title", clip_label),
                        "description": result.get("description", ""),
                        "hashtags": result.get("hashtags", ""),
                        "tags": result.get("tags", []),
                    }
            except Exception as e:
                logger.warning(f"Metadata gen failed on {model_name} for {platform}: {e}")
                continue

    except Exception as e:
        logger.warning(f"Platform metadata generation failed for {platform}: {e}")

    # Fallback — just use the clip label
    return {"title": clip_label, "description": "", "hashtags": "", "tags": []}
