"""
Video posting metadata profile — per-presence + per-Cove, empty by default.

Used when the pipeline LLM writes YouTube/social draft titles and descriptions
after clips or captioned-full render. Hardware-store Cove and Lucid founders
share the same code path: no Lucid Tuner / lucidprinciples hardcodes.

Resolution (field-by-field):
  presence posting.video_meta  →  Cove video_meta (feature override)  →  ""

Empty profile = neutral prompts, no forced CTA URL, no brand hashtags.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# All string fields. Empty string is the product default for every Cove.
VIDEO_META_FIELDS = (
    "brand_name",          # e.g. "Ridge Hardware" — voice line for the LLM
    "brand_topics",        # e.g. "home improvement, tools, local store tips"
    "short_cta_url",       # final-line URL for Shorts / Facebook (optional)
    "short_cta_line",      # full final line for shorts; if empty + url → url only
    "full_cta_url",        # long-form YouTube CTA URL
    "full_cta_line",        # full final line for long-form; if empty + url → url only
    "hashtag_seeds",       # "#hardware #diy" — seeds, model may add topical tags
    "description_extra",   # free text to weave into descriptions when relevant
    "voice_notes",         # how this creator talks (optional)
)

_COVE_FLAG = "video_meta"


def empty_video_meta() -> dict[str, str]:
    return {k: "" for k in VIDEO_META_FIELDS}


def _clean_section(raw: Any) -> dict[str, str]:
    out = empty_video_meta()
    if not isinstance(raw, dict):
        return out
    for k in VIDEO_META_FIELDS:
        v = raw.get(k)
        if isinstance(v, str):
            out[k] = v.strip()
        elif v is not None:
            out[k] = str(v).strip()
    return out


def get_cove_video_meta() -> dict[str, str]:
    """Cove-wide defaults (admin). Empty when unset."""
    try:
        from src.config import get_feature_flags
        return _clean_section(get_feature_flags().get(_COVE_FLAG))
    except Exception as e:
        logger.debug("cove video_meta read failed: %s", e)
        return empty_video_meta()


def save_cove_video_meta(data: dict) -> bool:
    """Persist Cove-wide video_meta into feature overrides."""
    cleaned = _clean_section(data)
    try:
        from src.config import save_feature_overrides
        return bool(save_feature_overrides({_COVE_FLAG: cleaned}))
    except Exception as e:
        logger.warning("cove video_meta save failed: %s", e)
        return False


async def get_presence_video_meta(owner_id: str | None) -> dict[str, str]:
    if not owner_id:
        return empty_video_meta()
    try:
        from src.dashboard.routes.posting_identity import _account_prefs
        prefs = await _account_prefs(owner_id)
        section = ((prefs or {}).get("posting") or {}).get("video_meta")
        return _clean_section(section)
    except Exception as e:
        logger.debug("presence video_meta read failed: %s", e)
        return empty_video_meta()


async def save_presence_video_meta(owner_id: str, data: dict) -> bool:
    from src.dashboard.routes.posting_identity import save_posting_section
    return await save_posting_section(owner_id, "video_meta", _clean_section(data))


def merge_video_meta(presence: dict[str, str], cove: dict[str, str]) -> dict[str, str]:
    """Field-level: non-empty presence wins, else cove, else empty."""
    out = empty_video_meta()
    for k in VIDEO_META_FIELDS:
        pv = (presence or {}).get(k) or ""
        cv = (cove or {}).get(k) or ""
        out[k] = (pv.strip() if isinstance(pv, str) else "") or (
            cv.strip() if isinstance(cv, str) else ""
        )
    return out


async def resolve_video_meta(
    owner_id: str | None = None,
    request=None,
) -> dict[str, str]:
    """Effective profile for metadata generation."""
    if owner_id is None and request is not None:
        try:
            from src.dashboard.routes.posting_identity import owner_id_from_request
            owner_id = await owner_id_from_request(request)
        except Exception:
            owner_id = None
    presence = await get_presence_video_meta(owner_id)
    cove = get_cove_video_meta()
    return merge_video_meta(presence, cove)


def _final_line(line: str, url: str) -> str:
    line = (line or "").strip()
    url = (url or "").strip()
    if line:
        return line
    return url


def build_platform_system_prompt(platform: str, meta: dict[str, str], clip_type: str, duration: str) -> str:
    """Neutral platform prompt; brand/CTA only when profile fields are set."""
    from src.dashboard.routes.social_templates import UNIVERSAL_RULES

    meta = meta or empty_video_meta()
    brand = meta.get("brand_name") or ""
    topics = meta.get("brand_topics") or ""
    voice = meta.get("voice_notes") or ""
    seeds = meta.get("hashtag_seeds") or ""
    extra = meta.get("description_extra") or ""
    short_final = _final_line(meta.get("short_cta_line") or "", meta.get("short_cta_url") or "")

    who = f'The creator\'s brand is "{brand}".' if brand else "The creator has not set a brand name — do not invent one."
    about = f"Typical topics: {topics}." if topics else "Infer topics only from the clip transcript."
    voice_line = f"Voice: {voice}" if voice else "Voice: plain, authentic, like a real person — not a marketer."
    extra_line = (
        f"When natural, weave this operator note into the description (do not dump it raw if irrelevant): {extra}"
        if extra else ""
    )
    seed_line = f"Prefer including these hashtag seeds when they fit: {seeds}" if seeds else ""

    if short_final:
        yt_link_rule = (
            f'Final line of the description must be exactly: {short_final} '
            f"(the only link allowed)."
        )
        fb_link_rule = f"Final line may be exactly: {short_final} (the only link allowed)."
    else:
        yt_link_rule = (
            "Do not add any URL or link unless it appears in the transcript. "
            "No invented websites. No placeholder links."
        )
        fb_link_rule = yt_link_rule

    hash_brand = seed_line or "Hashtags from the clip topics only — no forced brand tags."

    common = f"""{who} {about}
{voice_line}
{extra_line}
The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}"""

    # Fill after we inject clip_type — use format carefully
    base = {
        "youtube": f"""You are a YouTube Shorts metadata writer.

Write metadata for a YouTube Short clip.
{who} {about}
{voice_line}
{extra_line}

Rules:
- Title: 50-70 chars. Hook-first. No clickbait but must grab attention. Include a key concept from the clip.
- Description: 3-5 short paragraphs, blank line between each. First line is the hook (shows in search). Include key concepts as bullet points (→ arrows, not dashes). {yt_link_rule}
- Hashtags: 8-12 relevant hashtags, mix of broad and niche. {hash_brand}
- Tags: 10-15 comma-separated search terms for YouTube's tag system.

The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": ["tag1", "tag2", ...]}}""",
        "x": f"""You are an X/Twitter post writer.

Write a post to accompany a video clip.
{who} {about}
{voice_line}
{extra_line}

Rules:
- Title: Not used on X. Set to the clip label.
- Description: This IS the post text. Max 240 chars including hashtags (the video doesn't count). Punchy, conversational. NEVER include a URL or link.
- Hashtags: Default to NONE. At most 1 if it genuinely aids discovery. Usually return "".
- Tags: Empty array.

The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 #tag3", "tags": []}}""",
        "tiktok": f"""You are a TikTok caption writer.

Write a caption for a TikTok video.
{who} {about}
{voice_line}
{extra_line}

Rules:
- Title: Short hook (shown in search).
- Description: Caption 150-300 chars. Hook in first line. No URLs.
- Hashtags: 4-6 searchable topic tags. {hash_brand} Skip spam tags like #fyp.
- Tags: Empty array.

The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
        "instagram": f"""You are an Instagram Reels caption writer.

{who} {about}
{voice_line}
{extra_line}

Rules:
- Title: Short hook for the cover (40 chars max).
- Description: 2-4 paragraphs, blank line between each. End with a simple CTA. No URLs (not clickable). If a pointer is needed, say "link in bio".
- Hashtags: 8-12 focused hashtags at the end. {hash_brand}
- Tags: Empty array.

The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
        "facebook": f"""You are a Facebook post writer.

{who} {about}
{voice_line}
{extra_line}

Rules:
- Title: Not used. Set to the clip label.
- Description: 2-3 paragraphs, blank line between each, conversational. {fb_link_rule}
- Hashtags: 0-3 max.
- Tags: Empty array.

The clip is {{clip_type}} length ({{duration}}s).
{{universal_rules}}

Return ONLY valid JSON:
{{"title": "...", "description": "...", "hashtags": "#tag1 #tag2 ...", "tags": []}}""",
    }

    tmpl = base.get(platform)
    if not tmpl:
        return ""
    # Only substitute known placeholders — never str.format on operator text
    # (brand/topics may contain braces).
    return (
        tmpl
        .replace("{clip_type}", str(clip_type))
        .replace("{duration}", str(duration))
        .replace("{universal_rules}", UNIVERSAL_RULES)
    )


def build_full_video_system_prompt(meta: dict[str, str]) -> str:
    meta = meta or empty_video_meta()
    brand = meta.get("brand_name") or ""
    topics = meta.get("brand_topics") or ""
    voice = meta.get("voice_notes") or ""
    extra = meta.get("description_extra") or ""
    seeds = meta.get("hashtag_seeds") or ""
    full_final = _final_line(meta.get("full_cta_line") or "", meta.get("full_cta_url") or "")

    who = f'Creator brand: "{brand}".' if brand else "No brand name is configured — do not invent a company or product name."
    about = f"Topics they often cover: {topics}." if topics else "Infer topics only from the transcript."
    voice_line = f"Voice guidance: {voice}" if voice else "Voice: authentic and specific, not clickbait, not generic marketing."
    extra_line = (
        f"Operator note to honor when relevant: {extra}"
        if extra else ""
    )
    if full_final:
        link_rules = (
            f'The only link allowed is in the final line. End the description with this exact final line: "{full_final}". '
            f"Do not invent other links."
        )
        desc_final = f'Final line exactly: {full_final}'
    else:
        link_rules = (
            "Do not add any URL unless it appears in the transcript. No invented websites. No placeholder links."
        )
        desc_final = "No required final link line."

    tag_hint = f"Hashtag/tag seeds when they fit: {seeds}" if seeds else "Tags from content only."

    return f"""You are a YouTube content strategist writing metadata from a video transcript.

{who}
{about}
{voice_line}
{extra_line}

Generate metadata for a full-length YouTube video. Title should be compelling and searchable. Description should summarize the content and key topics.

Hard rules:
- Write finished, postable copy only. NEVER use placeholder text (no "[links here]", no "[...]", no TODO).
- Separate every paragraph with a blank line (\\n\\n in the JSON string).
- No em dashes. Use periods or commas instead.
- Provide 8 to 12 tags. Each tag is 1 to 3 words, no tag longer than 25 characters, and the whole tag set stays under 400 characters total.
- {link_rules}
- {tag_hint}

Return ONLY valid JSON:
{{
  "title": "Compelling YouTube title (50-70 chars ideal, max 100)",
  "description": "YouTube description (2-3 paragraphs, ~150-300 words, blank line between paragraphs). First line is the hook. Include timestamps if obvious sections exist. {desc_final}",
  "hashtags": "#hashtag1 #hashtag2 #hashtag3 (3-5 relevant hashtags)",
  "tags": ["tag1", "tag2", "tag3", "..."]
}}"""
