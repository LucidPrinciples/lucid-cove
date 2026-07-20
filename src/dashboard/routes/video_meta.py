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
    "brand_name",          # e.g. "Ridge Hardware" - voice line for the LLM
    "brand_topics",        # e.g. "home improvement, tools, local store tips"
    "theme_mix",           # optional moment-mining mix; empty = balanced default
    "attribute_handle",    # e.g. "@jasonbroadcast on X" — soft creator credit line
    "short_cta_url",       # final-line URL for Shorts / Facebook (optional)
    "short_cta_line",       # full final block for shorts; if empty + url -> composed
    "full_cta_url",         # long-form YouTube CTA URL
    "full_cta_line",        # full final block for long-form; if empty + url -> composed
    "hashtag_seeds",       # "#hardware #diy" - seeds, model may add topical tags
    "description_extra",   # free text to weave into descriptions when relevant
    "voice_notes",         # how this creator talks (optional)
)

# Field labels/help for API consumers and the pipeline UI (any Cove, any brand).
VIDEO_META_FIELD_META = {
    "brand_name": {
        "label": "Brand / channel name",
        "help": "How the writer should name you. Leave empty to avoid inventing a brand.",
        "placeholder": "e.g. Ridge Hardware",
    },
    "brand_topics": {
        "label": "Topics you cover",
        "help": "Themes the metadata writer should recognize. Semicolons or commas are fine.",
        "placeholder": "e.g. tools, DIY, local store tips",
    },
    "theme_mix": {
        "label": "Moment theme mix (mining)",
        "help": (
            "Guides which kinds of moments the analyzer prefers across one long video "
            "so clips are not all the same idea. Empty = balanced organic mix."
        ),
        "placeholder": (
            "e.g. practical how-to; personal story; bold opinion; product peek; "
            "quiet insight - spread across the talk"
        ),
    },
    "attribute_handle": {
        "label": "Attribute / handle line",
        "help": (
            "Soft creator credit near the end of descriptions (not a hard CTA). "
            "Example: @jasonbroadcast on X. Leave empty to skip."
        ),
        "placeholder": "e.g. @jasonbroadcast on X",
    },
    "short_cta_url": {
        "label": "Short-form CTA URL",
        "help": (
            "Link used when composing the short-form closing block "
            "(Shorts / Facebook) if no full short-form block is set."
        ),
        "placeholder": "https://...",
    },
    "short_cta_line": {
        "label": "Short-form closing block",
        "help": (
            "Exact closing lines for shorts (can be multi-line). Overrides URL-only. "
            "Prefer plain lines like: More at lucidprinciples.com"
        ),
        "placeholder": "More at example.com\n@handle on X",
    },
    "full_cta_url": {
        "label": "Full-length CTA URL",
        "help": "Link for captioned long-form YouTube when no full closing block is set.",
        "placeholder": "https://...",
    },
    "full_cta_line": {
        "label": "Full-length closing block",
        "help": (
            "Exact closing lines for long-form YouTube (can be multi-line). "
            "Overrides URL-only composition."
        ),
        "placeholder": "More at example.com\n@handle on X",
    },
    "hashtag_seeds": {
        "label": "Hashtag seeds",
        "help": "Optional tags the writer may include when they fit. X still stays light on tags.",
        "placeholder": "#hardware #diy",
    },
    "description_extra": {
        "label": "Always consider in descriptions",
        "help": (
            "Standing notes: promo rules, location, disclaimers. "
            "Writer weaves in only when natural."
        ),
        "placeholder": "hours, location, soft promo rules...",
    },
    "voice_notes": {
        "label": "Voice notes",
        "help": (
            "How you sound on camera and in posts. Paste a short style guide. "
            "Used for titles/descriptions - not for inventing facts."
        ),
        "placeholder": "how you talk on camera",
    },
}

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


def _compose_closing_block(line: str, url: str, attribute: str = "") -> str:
    """Build the exact closing block for descriptions.

    Prefer an explicit multi-line block when set. Otherwise compose lightly from
    URL + attribute handle so creators can set fields separately without forced
    "Creator is …" prose.
    """
    line = (line or "").strip()
    url = (url or "").strip()
    attribute = (attribute or "").strip()
    if line:
        return line
    parts: list[str] = []
    if url:
        # Keep "More at …" only when the URL is bare (no scheme-less marketing line).
        if url.startswith("http://") or url.startswith("https://"):
            display = url.split("://", 1)[-1].rstrip("/")
            parts.append(f"More at {display}")
        else:
            parts.append(url if url.lower().startswith("more at ") else f"More at {url}")
    if attribute:
        parts.append(attribute)
    return "\n".join(parts).strip()


def _final_line(line: str, url: str) -> str:
    """Backward-compat wrapper — prefer _compose_closing_block."""
    return _compose_closing_block(line, url, "")


def build_platform_system_prompt(
    platform: str,
    meta: dict[str, str],
    clip_type: str,
    duration: str,
    *,
    moment_context: str = "",
) -> str:
    """Neutral platform prompt; brand/CTA only when profile fields are set.

    moment_context: optional sibling-clip / moment analysis text so the writer
    can mix titles and hooks across quote/thought/story of the same moment.
    """
    from src.dashboard.routes.social_templates import UNIVERSAL_RULES

    meta = meta or empty_video_meta()
    brand = meta.get("brand_name") or ""
    topics = meta.get("brand_topics") or ""
    voice = meta.get("voice_notes") or ""
    seeds = meta.get("hashtag_seeds") or ""
    extra = meta.get("description_extra") or ""
    attribute = meta.get("attribute_handle") or ""
    short_final = _compose_closing_block(
        meta.get("short_cta_line") or "",
        meta.get("short_cta_url") or "",
        attribute,
    )

    who = f'The creator\'s brand is "{brand}".' if brand else "The creator has not set a brand name — do not invent one."
    about = f"Typical topics: {topics}." if topics else "Infer topics only from the clip transcript."
    voice_line = f"Voice: {voice}" if voice else "Voice: plain, authentic, like a real person — not a marketer."
    extra_line = (
        f"When natural, weave this operator note into the description (do not dump it raw if irrelevant): {extra}"
        if extra else ""
    )
    seed_line = f"Prefer including these hashtag seeds when they fit: {seeds}" if seeds else ""
    ctx = (moment_context or "").strip()
    moment_line = (
        "Moment context (sibling sizes / analysis for this same idea — use so this "
        f"platform's copy fits a coordinated mix, do not quote the context raw):\n{ctx}"
        if ctx else
        "No sibling moment context provided — write from this clip transcript alone."
    )

    if short_final:
        # Escape braces so later .replace is safe; show multi-line block clearly.
        shown = short_final.replace("\n", "\\n")
        yt_link_rule = (
            f'End the description with this exact closing block (may be multiple lines, '
            f'preserve line breaks with \\n\\n before it): {shown} '
            f"This is the only place a link is allowed. Do not invent other links. "
            f"Do not rewrite into 'Creator is …' prose."
        )
        fb_link_rule = (
            f"End with this exact closing block when links are appropriate: {shown} "
            f"(only link allowed). Do not invent other links."
        )
        attr_note = (
            f'If a soft credit fits and is not already in the closing block, you may use: {attribute}'
            if attribute and attribute not in short_final else ""
        )
    else:
        yt_link_rule = (
            "Do not add any URL or link unless it appears in the transcript. "
            "No invented websites. No placeholder links."
        )
        fb_link_rule = yt_link_rule
        attr_note = (
            f'You may end with a soft credit line exactly: {attribute}'
            if attribute else ""
        )

    hash_brand = seed_line or "Hashtags from the clip topics only — no forced brand tags."
    attr_rule = f"- {attr_note}" if attr_note else ""

    # Fill after we inject clip_type — use format carefully
    base = {
        "youtube": f"""You are a YouTube Shorts metadata writer.

Write metadata for a YouTube Short clip.
{who} {about}
{voice_line}
{extra_line}
{moment_line}

Rules:
- Title: 50-70 chars. Hook-first. No clickbait but must grab attention. Include a key concept from the clip.
- Description: 3-5 short paragraphs, blank line between each. First line is the hook (shows in search). Include key concepts as bullet points (→ arrows, not dashes). Prefer easy response prompts when natural (a real question the viewer can answer), not forced engagement bait. {yt_link_rule}
{attr_rule}
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
{moment_line}

Rules:
- Title: Not used on X. Set to the clip label.
- Description: This IS the post text. Max 240 chars including hashtags (the video doesn't count). Punchy, conversational. NEVER include a URL or link.
- A soft handle credit is OK only if it fits the char limit and feels natural (e.g. a trailing handle), never "Creator is …".
- Prefer a light response prompt when it fits (a real question), not forced bait.
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
{moment_line}

Rules:
- Title: Short hook (shown in search).
- Description: Caption 150-300 chars. Hook in first line. No URLs.
- Prefer an easy response prompt when natural. Soft handle mention OK if brief.
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
{moment_line}

Rules:
- Title: Short hook for the cover (40 chars max).
- Description: 2-4 paragraphs, blank line between each. Prefer an easy response prompt when natural. No URLs (not clickable). If a site pointer is needed, say "link in bio" — never invent a URL.
{attr_rule}
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
{moment_line}

Rules:
- Title: Not used. Set to the clip label.
- Description: 2-3 paragraphs, blank line between each, conversational. Prefer an easy response prompt when natural. {fb_link_rule}
{attr_rule}
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
    attribute = meta.get("attribute_handle") or ""
    full_final = _compose_closing_block(
        meta.get("full_cta_line") or "",
        meta.get("full_cta_url") or "",
        attribute,
    )

    who = f'Creator brand: "{brand}".' if brand else "No brand name is configured — do not invent a company or product name."
    about = f"Topics they often cover: {topics}." if topics else "Infer topics only from the transcript."
    voice_line = f"Voice guidance: {voice}" if voice else "Voice: authentic and specific, not clickbait, not generic marketing."
    extra_line = (
        f"Operator note to honor when relevant: {extra}"
        if extra else ""
    )
    if full_final:
        shown = full_final.replace("\n", "\\n")
        link_rules = (
            f'The only link allowed is in the closing block. End the description with this exact '
            f'closing block (preserve line breaks): "{shown}". '
            f"Do not invent other links. Do not rewrite into 'Creator is …' prose."
        )
        desc_final = f"Closing block exactly: {shown}"
    else:
        link_rules = (
            "Do not add any URL unless it appears in the transcript. No invented websites. No placeholder links."
        )
        desc_final = "No required final link block."
        if attribute:
            link_rules += f' You may end with a soft credit line exactly: {attribute}.'

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
- Prefer an easy response prompt when natural (a real question), not forced engagement bait.
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
