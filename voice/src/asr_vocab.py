"""ASR proper-noun replace map (ASRVOCAB1).

Applied after ASR and before the operator edits the transcript so repetitive
mishearings (Stewart→Stuart, Open Claw→OpenClaw, …) are already fixed in the
editor. Presence map extends/overrides the repo default.

Map files:
  - Repo default: packaged next to this module (`asr_vocab_default.json`)
  - Presence: AgentSkills/Content/video/asr-vocab.json
    { "replacements": [ {"from": "Stewart", "to": "Stuart"}, ... ] }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MAP_PATH = Path(__file__).resolve().with_name("asr_vocab_default.json")
PRESENCE_MAP_REL = "asr-vocab.json"  # under AgentSkills/Content/video/


def _as_pairs(raw: Any) -> list[tuple[str, str]]:
    """Normalize JSON shapes into (from, to) pairs. Skips empties."""
    pairs: list[tuple[str, str]] = []
    if raw is None:
        return pairs
    if isinstance(raw, dict) and "replacements" in raw:
        raw = raw.get("replacements")
    if isinstance(raw, dict):
        # {"Stewart": "Stuart", ...}
        for k, v in raw.items():
            if k in ("replacements", "version", "notes"):
                continue
            a, b = str(k or "").strip(), str(v or "").strip()
            if a and b and a != b:
                pairs.append((a, b))
        return pairs
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                a = str(item.get("from") or item.get("src") or item.get("wrong") or "").strip()
                b = str(item.get("to") or item.get("dst") or item.get("right") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                a, b = str(item[0] or "").strip(), str(item[1] or "").strip()
            else:
                continue
            if a and b and a != b:
                pairs.append((a, b))
    return pairs


def load_default_pairs() -> list[tuple[str, str]]:
    try:
        data = json.loads(DEFAULT_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {
            "replacements": [
                {"from": "Stewart", "to": "Stuart"},
                {"from": "Open Claw", "to": "OpenClaw"},
                {"from": "OpenClaw", "to": "OpenClaw"},
            ]
        }
    return _as_pairs(data)


def merge_pairs(
    default: Iterable[tuple[str, str]] | None = None,
    presence: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Presence wins on same `from` (case-insensitive). Longer `from` first."""
    by_key: dict[str, tuple[str, str]] = {}
    for src, dst in list(default or []) + list(presence or []):
        if not src or not dst:
            continue
        by_key[src.casefold()] = (src, dst)
    pairs = list(by_key.values())
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _match_case(replacement: str, matched: str) -> str:
    """Preserve ALLCAPS / Title / lower style of the matched span when possible."""
    if matched.isupper():
        return replacement.upper()
    if matched.islower():
        return replacement.lower()
    if matched[0].isupper() and matched[1:].islower():
        return replacement[:1].upper() + replacement[1:]
    # Mixed or single-char — use configured replacement as-is
    return replacement


def apply_text(text: str, pairs: list[tuple[str, str]] | None) -> tuple[str, int]:
    """Apply whole-phrase replacements. Returns (new_text, hit_count)."""
    if not text or not pairs:
        return text or "", 0
    hits = 0
    out = text
    for src, dst in pairs:
        if not src:
            continue
        # Word-ish boundaries so "Stuart" is not eaten inside another token.
        # Allow flexible whitespace inside multi-word sources.
        parts = [re.escape(p) for p in src.split() if p]
        if not parts:
            continue
        body = r"\s+".join(parts)
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", re.IGNORECASE)

        def _sub(m: re.Match, _dst: str = dst) -> str:
            nonlocal hits
            hits += 1
            return _match_case(_dst, m.group(0))

        out = pattern.sub(_sub, out)
    return out, hits


def apply_to_transcript(
    transcript: dict,
    pairs: list[tuple[str, str]] | None,
    *,
    force: bool = False,
) -> tuple[dict, dict]:
    """Return a copy of transcript with segment/text replacements applied.

    Skips when vocab_applied is already true unless force=True.
    Stats: {applied: bool, hits: int, skipped: bool}
    """
    if not isinstance(transcript, dict):
        return transcript, {"applied": False, "hits": 0, "skipped": True}
    if transcript.get("vocab_applied") and not force:
        return transcript, {"applied": False, "hits": 0, "skipped": True}

    pairs = list(pairs or [])
    if not pairs:
        out = dict(transcript)
        out["vocab_applied"] = True
        out["vocab_hits"] = 0
        return out, {"applied": True, "hits": 0, "skipped": False}

    hits_total = 0
    out = dict(transcript)
    segs = out.get("segments")
    if isinstance(segs, list):
        new_segs = []
        for seg in segs:
            if not isinstance(seg, dict):
                new_segs.append(seg)
                continue
            s = dict(seg)
            text = s.get("text")
            if isinstance(text, str) and text:
                new_t, n = apply_text(text, pairs)
                s["text"] = new_t
                hits_total += n
            new_segs.append(s)
        out["segments"] = new_segs
        # Rebuild full text from segments when we have them
        out["text"] = " ".join(
            str(s.get("text") or "") for s in new_segs if isinstance(s, dict)
        ).strip()
    else:
        text = out.get("text")
        if isinstance(text, str) and text:
            new_t, n = apply_text(text, pairs)
            out["text"] = new_t
            hits_total += n

    out["vocab_applied"] = True
    out["vocab_hits"] = hits_total
    return out, {"applied": True, "hits": hits_total, "skipped": False}


def pairs_from_map_data(data: Any) -> list[tuple[str, str]]:
    return _as_pairs(data)


def map_to_json(pairs: list[tuple[str, str]], *, notes: str = "") -> dict:
    body: dict[str, Any] = {
        "version": 1,
        "replacements": [{"from": a, "to": b} for a, b in pairs],
    }
    if notes:
        body["notes"] = notes
    return body
