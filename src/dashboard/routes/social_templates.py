"""
Social Platform Templates — LLM prompts for generating post metadata.

Brand / CTA / hashtag seeds come from video_meta (per-presence + Cove defaults),
empty by default — see video_meta.py. No Lucid Tuner hardcodes.

Called by video_processing.py after moments are processed to pre-populate
social_queue draft entries with platform-appropriate metadata.
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

# Rules every platform shares — appended to each system prompt.
UNIVERSAL_RULES = """
Hard rules (all platforms):
- Write finished, postable copy ONLY. Never use placeholder text of any kind: no "[link here]", no "[...]", no TODO notes, no example brackets.
- Separate paragraphs with a blank line (\\n\\n inside the JSON string).
- No em dashes. Use periods or commas instead.
- No performative hype ("groundbreaking", "game-changing", "revolutionary").
- Plain, authentic voice. The creator talks like a person, not a marketer."""

# Display names — prompts are built by video_meta.build_platform_system_prompt.
PLATFORM_NAMES = {
    "youtube": "YouTube Shorts",
    "x": "X (Twitter)",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
    "facebook": "Facebook",
}

# Backward-compat: older code/tests may still import PLATFORM_TEMPLATES.
# system_prompt is empty; generate_platform_metadata never reads it.
PLATFORM_TEMPLATES = {
    k: {"name": v, "system_prompt": ""} for k, v in PLATFORM_NAMES.items()
}


async def generate_platform_metadata(
    platform: str,
    clip_label: str,
    clip_type: str,
    duration_seconds: float,
    transcript_text: str,
    video_meta: dict | None = None,
    request=None,
    owner_id: str | None = None,
) -> dict:
    """Generate platform-specific metadata for a social queue draft.

    Uses the effective video_meta profile (presence → Cove → empty) + clip
    transcript. Returns title/description/hashtags/tags, or a label fallback.
    """
    if platform not in PLATFORM_NAMES:
        return {"title": clip_label, "description": "", "hashtags": "", "tags": []}

    from src.dashboard.routes.video_meta import (
        build_platform_system_prompt,
        empty_video_meta,
        resolve_video_meta,
    )

    if video_meta is None:
        video_meta = await resolve_video_meta(owner_id=owner_id, request=request)
    video_meta = video_meta or empty_video_meta()

    dur_label = (
        f"{int(duration_seconds)}s"
        if duration_seconds < 60
        else f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
    )

    system_prompt = build_platform_system_prompt(
        platform, video_meta, clip_type, dur_label,
    )
    if not system_prompt:
        return {"title": clip_label, "description": "", "hashtags": "", "tags": []}

    human_prompt = f"""Here's the transcript from the clip "{clip_label}":

{transcript_text[:3000]}

Generate the metadata for {PLATFORM_NAMES[platform]}."""

    try:
        from src.models.provider import get_model_client, _resolve_model_string
        from langchain_core.messages import SystemMessage, HumanMessage

        _candidates = ["gemini-flash", "kimi-k2.5"]
        try:
            from src.models.local_fallback import resolve_local_fallback_model
            _candidates.append(resolve_local_fallback_model())
        except Exception:
            pass
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
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                json_match = re.search(r"\{[\s\S]*\}", content)
                if json_match:
                    result = json.loads(json_match.group())
                    logger.info(
                        f"Generated {platform} metadata via {model_name} for '{clip_label}'"
                    )
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

    return {"title": clip_label, "description": "", "hashtags": "", "tags": []}
