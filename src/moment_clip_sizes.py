# =============================================================================
# moment_clip_sizes.py — hard platform lines for nested quote / thought / story.
# =============================================================================
# Product shape (operator):
#   Quote  — punchy hook, short
#   Thought — technical short: MUST stay under 60s (platform short traffic)
#   Story  — 2–3 minutes, hard max 180s (3 min short ceiling); target ~2–3 min
#
# When a moment window is long enough for a story (>= STORY_MIN), every moment
# MUST expose all three nested sizes so the operator can mix/match. Sliders in
# the review UI still allow fine trim; these bounds keep LLM output on-spec.
# Pure logic — unit-tested without model deps.
# =============================================================================
from __future__ import annotations

from typing import Any

# Hard platform lines (seconds)
QUOTE_MIN = 8.0
QUOTE_MAX = 30.0
QUOTE_DEFAULT = 18.0

THOUGHT_MIN = 35.0
THOUGHT_MAX = 59.0  # hard under 60s technical short
THOUGHT_DEFAULT = 55.0

STORY_MIN = 120.0  # 2 minutes — below this, no story nest required
STORY_MAX = 180.0  # 3 minutes — hard short ceiling
STORY_DEFAULT = 150.0  # 2.5 min target when expanding a short "story"

# Moment must span at least this to force full quote+thought+story nest
NEST_FULL_MIN = STORY_MIN


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clip_duration(clip: dict) -> float:
    d = _f(clip.get("duration_seconds"), 0.0)
    if d > 0:
        return d
    s = _f(clip.get("start_seconds"), 0.0)
    e = _f(clip.get("end_seconds"), 0.0)
    return max(0.0, e - s)


def _set_window(clip: dict, start: float, end: float) -> None:
    start = max(0.0, float(start))
    end = max(start + 0.5, float(end))
    clip["start_seconds"] = round(start, 2)
    clip["end_seconds"] = round(end, 2)
    clip["duration_seconds"] = round(end - start, 2)


def _clamp_window(
    start: float,
    end: float,
    *,
    min_dur: float,
    max_dur: float,
    anchor: str = "end",
    floor: float | None = None,
    ceiling: float | None = None,
) -> tuple[float, float]:
    """Clamp [start,end] into [min_dur, max_dur], preferring to keep hook end or start."""
    start = float(start)
    end = float(end)
    if end <= start:
        end = start + min_dur
    dur = end - start
    if dur > max_dur:
        if anchor == "start":
            end = start + max_dur
        else:
            start = end - max_dur
    elif dur < min_dur:
        if anchor == "start":
            end = start + min_dur
        else:
            start = end - min_dur
    if floor is not None and start < floor:
        shift = floor - start
        start += shift
        end += shift
    if ceiling is not None and end > ceiling:
        shift = end - ceiling
        start = max(floor if floor is not None else 0.0, start - shift)
        end = ceiling
    # Final duration clamp if bounds fought us
    dur = end - start
    if dur > max_dur:
        start = end - max_dur
    if dur < min_dur and (ceiling is None or start + min_dur <= ceiling + 1e-6):
        end = start + min_dur
        if ceiling is not None and end > ceiling:
            end = ceiling
            start = max(floor if floor is not None else 0.0, end - max_dur)
    return round(start, 2), round(end, 2)


def _pick_clip(clips: list, ctype: str) -> dict | None:
    for c in clips:
        if (c.get("type") or c.get("clip_type") or "").strip().lower() == ctype:
            return c
    return None


def _ensure_clip_shell(clips: list, ctype: str, template: dict | None = None) -> dict:
    existing = _pick_clip(clips, ctype)
    if existing is not None:
        existing["type"] = ctype
        return existing
    base = dict(template or {})
    base["type"] = ctype
    base.setdefault("label", ctype.capitalize())
    base.setdefault("hook_line", base.get("hook_line") or "")
    base.setdefault("platform_fit", {
        "quote": ["youtube_shorts", "tiktok", "reels", "instagram"],
        "thought": ["youtube_shorts", "tiktok", "reels", "instagram"],
        "story": ["youtube", "youtube_shorts", "tiktok", "facebook"],
    }.get(ctype, ["youtube_shorts", "tiktok"]))
    base.setdefault("virality_score", 50)
    base.setdefault("why", "nested size for operator mix/match")
    clips.append(base)
    return base


def normalize_moment_clips(moment: dict, video_duration: float | None = None) -> dict:
    """Normalize one moment's nested clips to hard platform lines.

    - thought: duration in [THOUGHT_MIN, THOUGHT_MAX] (hard < 60s)
    - story: duration in [STORY_MIN, STORY_MAX] when a story nest is required
    - quote: duration in [QUOTE_MIN, QUOTE_MAX]
    - If moment span >= NEST_FULL_MIN, ensure quote + thought + story all exist
      and quote ⊆ thought ⊆ story (nested windows).
    Mutates and returns the moment dict.
    """
    if not isinstance(moment, dict):
        return moment
    clips_in = moment.get("clips")
    if not isinstance(clips_in, list):
        clips_in = []
        moment["clips"] = clips_in

    m_start = _f(moment.get("start_seconds"), 0.0)
    m_end = _f(moment.get("end_seconds"), 0.0)
    # Derive moment window from clips if missing
    if m_end <= m_start and clips_in:
        starts = [_f(c.get("start_seconds")) for c in clips_in if isinstance(c, dict)]
        ends = [_f(c.get("end_seconds")) for c in clips_in if isinstance(c, dict)]
        if starts and ends:
            m_start = min(starts)
            m_end = max(ends)
    if m_end <= m_start:
        # Nothing usable
        return moment

    ceiling = m_end
    floor = m_start
    if video_duration is not None and video_duration > 0:
        ceiling = min(ceiling, float(video_duration))

    span = ceiling - floor
    full_nest = span >= NEST_FULL_MIN - 1e-6

    # Prefer existing typed clips as seeds
    story = _pick_clip(clips_in, "story")
    thought = _pick_clip(clips_in, "thought")
    quote = _pick_clip(clips_in, "quote")

    if full_nest:
        # Story window: prefer model story, else whole moment, clamped to 2–3 min
        if story is not None:
            s0, s1 = _f(story.get("start_seconds"), floor), _f(story.get("end_seconds"), ceiling)
        else:
            s0, s1 = floor, ceiling
        # If model story is too short, expand toward moment bounds (prefer keeping end/hook)
        s_dur = max(0.0, s1 - s0)
        if s_dur < STORY_MIN:
            # Expand to STORY_DEFAULT if moment allows, else as much as span allows
            target = min(STORY_DEFAULT, span)
            target = max(min(target, STORY_MAX), min(STORY_MIN, span))
            # Grow from center of existing, then clamp into moment
            mid = (s0 + s1) / 2.0 if s1 > s0 else (floor + ceiling) / 2.0
            s0 = mid - target / 2.0
            s1 = mid + target / 2.0
        s0, s1 = _clamp_window(
            s0, s1, min_dur=min(STORY_MIN, span), max_dur=min(STORY_MAX, span),
            anchor="end", floor=floor, ceiling=ceiling,
        )
        # If still under STORY_MIN because moment is exactly 2 min-ish, take full moment
        if (s1 - s0) < STORY_MIN - 1e-6 and span >= STORY_MIN - 1e-6:
            s0, s1 = floor, min(floor + STORY_MAX, ceiling)
            if (s1 - s0) > STORY_MAX:
                s0 = s1 - STORY_MAX
        story = _ensure_clip_shell(clips_in, "story", story)
        _set_window(story, s0, s1)

        # Thought: nested inside story, hard < 60s
        if thought is not None:
            t0, t1 = _f(thought.get("start_seconds"), s0), _f(thought.get("end_seconds"), s1)
        else:
            # Default: last THOUGHT_DEFAULT seconds of story (payoff-weighted)
            t1 = s1
            t0 = t1 - THOUGHT_DEFAULT
        t0 = max(s0, t0)
        t1 = min(s1, t1)
        t0, t1 = _clamp_window(
            t0, t1, min_dur=THOUGHT_MIN, max_dur=THOUGHT_MAX,
            anchor="end", floor=s0, ceiling=s1,
        )
        thought = _ensure_clip_shell(clips_in, "thought", thought)
        _set_window(thought, t0, t1)

        # Quote: nested inside thought
        if quote is not None:
            q0, q1 = _f(quote.get("start_seconds"), t0), _f(quote.get("end_seconds"), t1)
        else:
            q0, q1 = t0, t0 + QUOTE_DEFAULT
        q0 = max(t0, q0)
        q1 = min(t1, q1)
        q0, q1 = _clamp_window(
            q0, q1, min_dur=QUOTE_MIN, max_dur=QUOTE_MAX,
            anchor="start", floor=t0, ceiling=t1,
        )
        quote = _ensure_clip_shell(clips_in, "quote", quote)
        _set_window(quote, q0, q1)

        # Align moment envelope to story
        moment["start_seconds"] = round(min(_f(moment.get("start_seconds"), s0), s0), 2)
        moment["end_seconds"] = round(max(_f(moment.get("end_seconds"), s1), s1), 2)
    else:
        # Shorter moment: still clamp any present sizes to hard lines; no forced story
        if story is not None:
            s0, s1 = _f(story.get("start_seconds"), floor), _f(story.get("end_seconds"), ceiling)
            # Demote undersized "story" to thought territory if it cannot reach 2 min
            if (s1 - s0) < STORY_MIN and span < STORY_MIN:
                # Relabel to thought and clamp under 60
                story["type"] = "thought"
                thought = story if thought is None else thought
                # Remove duplicate if we now have two thoughts — keep clamped window on thought
                clips_in[:] = [c for c in clips_in if c is story or (c.get("type") != "thought")]
                if thought is story:
                    pass
                story = None
            else:
                s0, s1 = _clamp_window(
                    s0, s1, min_dur=min(STORY_MIN, span), max_dur=min(STORY_MAX, span),
                    anchor="end", floor=floor, ceiling=ceiling,
                )
                _set_window(story, s0, s1)

        if thought is not None:
            t0, t1 = _f(thought.get("start_seconds"), floor), _f(thought.get("end_seconds"), ceiling)
            t0, t1 = _clamp_window(
                t0, t1, min_dur=min(THOUGHT_MIN, span), max_dur=min(THOUGHT_MAX, span),
                anchor="end", floor=floor, ceiling=ceiling,
            )
            thought["type"] = "thought"
            _set_window(thought, t0, t1)

        if quote is not None:
            q0, q1 = _f(quote.get("start_seconds"), floor), _f(quote.get("end_seconds"), ceiling)
            q_floor = _f(thought.get("start_seconds"), floor) if thought else floor
            q_ceil = _f(thought.get("end_seconds"), ceiling) if thought else ceiling
            q0, q1 = _clamp_window(
                q0, q1, min_dur=min(QUOTE_MIN, span), max_dur=min(QUOTE_MAX, span),
                anchor="start", floor=q_floor, ceiling=q_ceil,
            )
            quote["type"] = "quote"
            _set_window(quote, q0, q1)

    # Stable order: quote, thought, story, then any other types
    order = {"quote": 0, "thought": 1, "story": 2}

    def _sort_key(c: dict) -> tuple:
        t = (c.get("type") or "").lower()
        return (order.get(t, 9), _f(c.get("start_seconds")))

    moment["clips"] = sorted(
        [c for c in clips_in if isinstance(c, dict)],
        key=_sort_key,
    )
    return moment


def normalize_moments_result(result: dict, video_duration: float | None = None) -> dict:
    """Normalize every moment in an analyzer result dict."""
    if not isinstance(result, dict):
        return result
    moments = result.get("moments")
    if not isinstance(moments, list):
        return result
    out = []
    for m in moments:
        if isinstance(m, dict):
            out.append(normalize_moment_clips(m, video_duration=video_duration))
        else:
            out.append(m)
    result["moments"] = out
    return result
