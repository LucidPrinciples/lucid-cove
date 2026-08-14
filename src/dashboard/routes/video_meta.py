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
    "moment_mine_brief",   # new: guidance for moment selection (niche/SEO mix)
    "description_skeleton",# new: enforced structure for titles/descriptions
    "attribute_handle",    # e.g. "@handle on X" — soft creator credit line
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
    "moment_mine_brief": {
        "label": "Moment mine brief (niche + discovery)",
        "help": (
            "How the analyzer should balance primary niche subject matter with "
            "searchable / discovery entry points across one talk. Empty = product "
            "default: niche spine first, then SEO spread, no forced topics."
        ),
        "placeholder": (
            "e.g. Primary spine: our craft and how we teach it. "
            "Discovery: plain-language entry points a new viewer would search. "
            "Do not force topics absent from the talk."
        ),
    },
    "description_skeleton": {
        "label": "Description format (skeleton)",
        "help": (
            "Structure the metadata writer must follow for multi-paragraph "
            "descriptions (YouTube Shorts/full, IG, Facebook). Empty = product "
            "default: hook → lead-in → topics only after lead-in → optional "
            "response prompt → closing block. Override to match your channel."
        ),
        "placeholder": (
            "Leave empty for the built-in format, or paste your own section order."
        ),
    },
    "attribute_handle": {
        "label": "Attribute / handle line",
        "help": (
            "Soft creator credit near the end of descriptions (not a hard CTA). "
            "Example: @handle on X. Leave empty to skip."
        ),
        "placeholder": "e.g. @handle on X",
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
        "help": (
            "Optional tags the writer may include in the hashtags / description "
            "field when they fit. Never placed in the title. X still stays light on tags."
        ),
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


# Product default description shape when description_skeleton is empty.
# Creators may override the whole structure via the profile field — never brand-specific.
DEFAULT_DESCRIPTION_SKELETON = (
    "1) HOOK — one opening line that earns the click (also works as the search snippet).\n"
    "2) LEAD-IN — one or two sentences that frame what this piece is about and why it matters. "
    "Never open the body with a bare topic list.\n"
    "3) TOPICS — only after the lead-in: short bullets (→ arrows, not dashes) for the key ideas "
    "in this clip/talk. Each bullet is a phrase, not a new essay.\n"
    "4) RESPONSE PROMPT — when natural, one real question the viewer can answer (not bait).\n"
    "5) CLOSING BLOCK — only if the platform rules below require an exact closing block; "
    "otherwise stop after the prompt."
)

# Product default for moment mining when moment_mine_brief is empty.
DEFAULT_MOMENT_MINE_BRIEF = (
    "Balance two aims across the talk: (A) PRIMARY NICHE SPINE — moments that carry the "
    "creator's real subject matter and teaching voice; (B) DISCOVERY / SEO SPREAD — moments "
    "phrased so a new viewer could find them by searching a plain question or problem. "
    "Prefer a set that covers both when the transcript supports it. Do not invent niche or "
    "search topics that are absent from the talk. Reject near-duplicates of the same claim."
)


def effective_description_skeleton(meta: dict[str, str] | None) -> str:
    """Operator override or product default skeleton (always non-empty for writers)."""
    raw = ((meta or {}).get("description_skeleton") or "").strip()
    return raw or DEFAULT_DESCRIPTION_SKELETON


def effective_moment_mine_brief(meta: dict[str, str] | None) -> str:
    """Operator override or product default mine brief."""
    raw = ((meta or {}).get("moment_mine_brief") or "").strip()
    return raw or DEFAULT_MOMENT_MINE_BRIEF


def _description_format_block(meta: dict[str, str] | None, *, multi_paragraph: bool) -> str:
    """Injected into multi-paragraph platform / full prompts."""
    if not multi_paragraph:
        return ""
    sk = effective_description_skeleton(meta)
    return (
        "DESCRIPTION FORMAT (required structure — follow in order; blank line between sections):\n"
        f"{sk}\n"
        "Hard format rules: never lead with a bare list of topics; the lead-in comes first. "
        "Topic bullets only after the lead-in. Do not skip the hook. "
        "Separate every paragraph / section with a blank line (\\n\\n in the JSON string)."
    )


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
    desc_fmt = _description_format_block(meta, multi_paragraph=True)

    # Fill after we inject clip_type — use format carefully
    base = {
        "youtube": f"""You are a YouTube Shorts metadata writer.

Write metadata for a YouTube Short clip.
{who} {about}
{voice_line}
{extra_line}
{moment_line}

{desc_fmt}

Rules:
- Title: 50-70 chars. Hook-first. No clickbait but must grab attention. Include a key concept from the clip. Never put hashtags in the title (no #words, no #Shorts).
- Description: Follow DESCRIPTION FORMAT above (3-5 short sections). First line is the hook (shows in search). Topic bullets use → arrows, not dashes. Prefer easy response prompts when natural (a real question the viewer can answer), not forced engagement bait. {yt_link_rule}
- TITLE VS OPENING LINE: the title and the first line of the description must NOT be identical or a near-paraphrase of each other. Title is the short searchable label; the description opening can restate the idea in different words or lead with the frame/why it matters. Never paste the title as description line 1.
{attr_rule}
- Hashtags: 8-12 relevant hashtags in the hashtags field only, mix of broad and niche. Never #shorts / #Shorts / #fyp. {hash_brand}
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
- Description: This IS the post text. Max 240 chars including hashtags (the video doesn't count). Punchy, conversational. NEVER include a URL or link. Single short post — do not use the multi-section description skeleton.
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
- Title: Short hook (shown in search). No hashtags in the title.
- Description: Caption 150-300 chars. Hook in first line, then one tight thought — not a multi-section skeleton. No URLs.
- TITLE VS OPENING LINE: title and description first line must not be identical or near-paraphrase.
- Prefer an easy response prompt when natural. Soft handle mention OK if brief.
- Hashtags: 4-6 searchable topic tags in the hashtags field only. {hash_brand} Skip spam tags like #fyp, #shorts, #Shorts.
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

{desc_fmt}

Rules:
- Title: Short hook for the cover (40 chars max). No hashtags in the title.
- Description: Follow DESCRIPTION FORMAT above (2-4 sections). Prefer an easy response prompt when natural. No URLs (not clickable). If a site pointer is needed, say "link in bio" — never invent a URL.
- TITLE VS OPENING LINE: title and description first line must not be identical or near-paraphrase.
{attr_rule}
- Hashtags: 8-12 focused hashtags at the end of the description / hashtags field. Never in the title. Never #shorts / #Shorts / #fyp. {hash_brand}
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

{desc_fmt}

Rules:
- Title: Not used. Set to the clip label.
- Description: Follow DESCRIPTION FORMAT above (2-3 sections), conversational. Prefer an easy response prompt when natural. {fb_link_rule}
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
    desc_fmt = _description_format_block(meta, multi_paragraph=True)
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

Generate metadata for a full-length YouTube video. Title should be compelling and searchable. Never put hashtags or #words in the title.

{desc_fmt}

Hard rules:
- Write finished, postable copy only. NEVER use placeholder text (no "[links here]", no "[...]", no TODO).
- Separate every paragraph with a blank line (\\n\\n in the JSON string).
- No em dashes. Use periods or commas instead.
- Prefer an easy response prompt when natural (a real question), not forced engagement bait.
- Never open the description with a bare topic list — lead-in first, topics only after.
- TITLE VS OPENING LINE: the title and the first line of the description must NOT be identical or a near-paraphrase. Title is the short searchable label; description opening restates or frames in different words.
- Provide 8 to 12 tags. Each tag is 1 to 3 words, no tag longer than 25 characters, and the whole tag set stays under 400 characters total.
- {link_rules}
- {tag_hint}

Return ONLY valid JSON:
{{
  "title": "Compelling YouTube title (50-70 chars ideal, max 100)",
  "description": "YouTube description following DESCRIPTION FORMAT (~150-300 words). First line is the hook. Include timestamps if obvious sections exist. {desc_final}",
  "hashtags": "#hashtag1 #hashtag2 #hashtag3 (3-5 relevant hashtags)",
  "tags": ["tag1", "tag2", "tag3", "..."]
}}"""


# =============================================================================
# Final polish pass (#VMETA-POLISH1)
# =============================================================================
# Draft meta is written by the fast chain (gemini-flash / kimi / local). When the
# operator sets a Cove polish model (pipeline keys — same pattern as Analysis
# model), we run one batch pass over the sibling drafts for a stem so titles
# de-dupe, skeleton/voice are enforced, and SEO tags tighten — without paying
# the rich model on every intermediate encode step.

import json
import re


def build_polish_system_prompt(meta: dict[str, str] | None) -> str:
    """System prompt for the final polish pass over a batch of draft cards."""
    meta = meta or empty_video_meta()
    brand = (meta.get("brand_name") or "").strip()
    topics = (meta.get("brand_topics") or "").strip()
    voice = (meta.get("voice_notes") or "").strip()
    extra = (meta.get("description_extra") or "").strip()
    sk = effective_description_skeleton(meta)

    who = f'Creator brand: "{brand}".' if brand else "No brand name — do not invent one."
    about = f"Topics they cover: {topics}." if topics else "Infer topics only from the drafts + any clip notes."
    voice_line = f"Voice guidance: {voice}" if voice else "Voice: plain, authentic, specific — not marketer hype."
    extra_line = f"Standing operator note (honor when natural): {extra}" if extra else ""

    return f"""You are the final editor for a creator's social/video draft metadata.

{who}
{about}
{voice_line}
{extra_line}

You receive a JSON array of draft posts from one processing batch (same talk / stem).
Each item has: id (stable string — return it unchanged), platform, clip_type, clip_label,
title, description, hashtags, tags (array).

Your job — FINAL POLISH only:
1) DESCRIPTION FORMAT for multi-paragraph platforms (youtube, instagram, facebook):
{sk}
   Never leave a bare topic list with no lead-in. Fix that if the draft did it.
2) Short platforms (x, tiktok): keep single short posts; do not expand into the multi-section skeleton. Respect length limits (X ≤240 chars including hashtags).
3) DE-DUPE titles across the batch: sibling clips of the same moment must not share near-identical titles. Differentiate by angle/size while staying true to the clip.
3b) TITLE VS OPENING LINE (same card): for each item, title and the first line of description must not be identical or near-paraphrase. If the draft pasted the title as description line 1, rewrite the opening line (keep title) so the body starts with a different frame or restatement.
4) Voice + hard rules: no em dashes, no placeholder text, no hype words (groundbreaking, game-changing, revolutionary). Finished postable copy only.
5) Hashtags/tags: tighten for discovery when weak; do not invent brand tags the profile did not seed. X stays light on hashtags (0–1). Never put hashtags in the title. Never use #shorts, #Shorts, or #fyp.
6) Do not invent URLs or closing blocks that were not already in the draft. Preserve exact closing blocks / CTAs already present.
7) Keep platform_data and any fields you were not given out of the rewrite — only return the editable meta fields.

Return ONLY valid JSON:
{{
  "items": [
    {{
      "id": "<same id>",
      "title": "...",
      "description": "...",
      "hashtags": "...",
      "tags": ["..."]
    }}
  ]
}}
Return one object per input item, same ids. No markdown fences."""


def _parse_polish_response(content: str) -> list[dict] | None:
    """Extract items list from polish model output. None on failure."""
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except Exception:
            return None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("drafts") or data.get("results")
    else:
        return None
    if not isinstance(items, list) or not items:
        return None
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        out.append({
            "id": str(it["id"]),
            "title": it.get("title"),
            "description": it.get("description"),
            "hashtags": it.get("hashtags"),
            "tags": it.get("tags"),
        })
    return out or None


async def _invoke_polish_model(system_prompt: str, human_prompt: str) -> tuple[str | None, str]:
    """Call the configured polish model (or draft chain). Returns (content, model_used)."""
    import asyncio
    from langchain_core.messages import SystemMessage, HumanMessage

    _llm_mode = "cloud"
    try:
        from src.config import get_compute_config
        _llm_mode = ((get_compute_config() or {}).get("llm") or {}).get("mode") or "cloud"
    except Exception:
        pass

    polish_id = ""
    try:
        from src.dashboard.routes.pipeline_keys import (
            get_polish_model,
            polish_model_allowed,
        )
        polish_id = get_polish_model()
    except Exception:
        polish_id = ""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]

    # 1) Operator polish model first (sovereignty gate)
    if polish_id:
        try:
            from src.models.provider import get_model_client, _resolve_model_string
            provider, _ = _resolve_model_string(polish_id)
            if not polish_model_allowed(provider, _llm_mode):
                logger.info(
                    "Polish model %s skipped — llm.mode=local blocks paid tiers",
                    polish_id,
                )
            else:
                client = get_model_client(polish_id, temperature=0.35)
                resp = await asyncio.wait_for(client.ainvoke(messages), timeout=120)
                content = (getattr(resp, "content", None) or "").strip()
                if content:
                    return content, f"polish-model/{polish_id}"
        except Exception as e:
            logger.warning("Polish model %s failed: %s", polish_id, e)

    # 2) Fast draft chain (same as generate_platform_metadata)
    candidates = ["gemini-flash", "kimi-k2.5"]
    try:
        from src.models.local_fallback import resolve_local_fallback_model
        candidates.append(resolve_local_fallback_model())
    except Exception:
        pass
    try:
        from src.models.provider import get_model_client, _resolve_model_string
        for model_name in candidates:
            try:
                client = get_model_client(model_name, temperature=0.35)
                resp = await asyncio.wait_for(client.ainvoke(messages), timeout=90)
                content = (getattr(resp, "content", None) or "").strip()
                if content:
                    return content, model_name
            except Exception as e:
                logger.warning("Polish fallback %s failed: %s", model_name, e)
                continue
    except Exception as e:
        logger.warning("Polish invoke setup failed: %s", e)

    return None, ""


def strip_hashtags_from_title(title: str) -> str:
    """Titles never carry hashtags. Seeds and #Shorts belong elsewhere."""
    raw = (title or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"(?i)#shorts\b", " ", raw)
    cleaned = re.sub(r"#[^\s#]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—|,")
    return cleaned.strip()


def _norm_meta_line(s: str) -> str:
    """Collapse whitespace + case for title/opening comparison."""
    return " ".join((s or "").strip().lower().split())


def ensure_title_differs_from_opening(item: dict) -> dict:
    """If title == description first line, drop that first line so the body leads.

    Soft safety net after LLM draft/polish. Prefer model compliance; this only
    fires on exact/near-exact matches so we never invent replacement copy.
    """
    if not isinstance(item, dict):
        return item
    title = strip_hashtags_from_title(item.get("title") or "")
    if title != (item.get("title") or "").strip():
        item = dict(item)
        item["title"] = title
    desc = item.get("description") or ""
    if not title or not isinstance(desc, str) or not desc.strip():
        return item
    # Prefer blank-line section split (skeleton); else first physical line.
    if "\n\n" in desc:
        first, rest = desc.split("\n\n", 1)
    else:
        parts = desc.split("\n", 1)
        first = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
    first = (first or "").strip()
    rest = (rest or "").strip()
    nt, nf = _norm_meta_line(title), _norm_meta_line(first)
    if not nt or not nf:
        return item
    same = nt == nf
    if not same:
        shorter, longer = (nt, nf) if len(nt) <= len(nf) else (nf, nt)
        if shorter and shorter in longer and len(shorter) >= 12:
            if len(longer) <= int(len(shorter) * 1.25) + 8:
                same = True
    if not same:
        return item
    if not rest:
        return item
    out = dict(item)
    out["description"] = rest
    return out


def _merge_polished_item(original: dict, polished: dict) -> dict:
    """Apply polished fields onto a draft dict; keep unspecified fields."""
    out = dict(original)
    for key in ("title", "description", "hashtags"):
        val = polished.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val
    tags = polished.get("tags")
    if isinstance(tags, list) and tags:
        out["tags"] = tags
    elif isinstance(tags, str) and tags.strip():
        out["tags"] = [x.strip() for x in tags.split(",") if x.strip()]
    return ensure_title_differs_from_opening(out)



async def polish_metadata_batch(
    drafts: list[dict],
    video_meta: dict | None = None,
) -> list[dict]:
    """Polish a batch of draft metadata dicts in one LLM call when possible.

    Each draft must include a stable string ``id`` plus platform/title/description
    fields. Returns drafts in the same order; on total failure returns originals
    unchanged. When the polish model is unset, still runs the fast chain once for
    skeleton/de-dupe if there are 2+ drafts — single drafts with no polish model
    are left as the first-pass write (no extra cost).
    """
    if not drafts:
        return drafts

    polish_configured = False
    try:
        from src.dashboard.routes.pipeline_keys import get_polish_model
        polish_configured = bool(get_polish_model())
    except Exception:
        polish_configured = False

    # Skip no-op: one draft and no polish model → keep first-pass meta (still title≠hook)
    if len(drafts) < 2 and not polish_configured:
        return [ensure_title_differs_from_opening(dict(d)) for d in drafts]

    meta = video_meta or empty_video_meta()
    system_prompt = build_polish_system_prompt(meta)

    payload = []
    for d in drafts:
        payload.append({
            "id": str(d.get("id") or ""),
            "platform": d.get("platform") or "",
            "clip_type": d.get("clip_type") or "",
            "clip_label": d.get("clip_label") or "",
            "title": d.get("title") or "",
            "description": d.get("description") or "",
            "hashtags": d.get("hashtags") or "",
            "tags": d.get("tags") if isinstance(d.get("tags"), list) else [],
        })
    payload = [p for p in payload if p["id"]]
    if not payload:
        return drafts

    human_prompt = (
        "Polish this draft batch. Return JSON with an items array.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    content, model_used = await _invoke_polish_model(system_prompt, human_prompt)
    if not content:
        logger.warning("Meta polish produced no content — keeping first-pass drafts")
        return [ensure_title_differs_from_opening(dict(d)) for d in drafts]

    items = _parse_polish_response(content)
    if not items:
        logger.warning(
            "Meta polish parse failed (model=%s) — keeping first-pass drafts",
            model_used or "?",
        )
        return [ensure_title_differs_from_opening(dict(d)) for d in drafts]

    by_id = {it["id"]: it for it in items}
    merged = []
    for d in drafts:
        did = str(d.get("id") or "")
        if did and did in by_id:
            merged.append(_merge_polished_item(d, by_id[did]))
        else:
            merged.append(ensure_title_differs_from_opening(dict(d)))
    logger.info(
        "Meta polish applied via %s on %d drafts (%d returned)",
        model_used or "?",
        len(drafts),
        len(items),
    )
    return merged

