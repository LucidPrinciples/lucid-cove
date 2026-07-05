"""Hyphen-safe video stem derivation (batch8 #6).

Live case that motivated this: a stem can itself contain hyphens
(`IMG_7168-Test1`), and that stem flows into every derived name
(`{stem}-transcript.json`, `{stem}-moments.json`, `{stem}-captioned.mp4`, ...).
Any code that recovers the stem by splitting on the FIRST hyphen would truncate
`IMG_7168-Test1` to `IMG_7168` and read/write the wrong file.

The rule: recover a stem by stripping a KNOWN SUFFIX (anchored to the end), never
by splitting on a hyphen. These helpers centralize that so no call site has to
re-derive it. Existing call sites already strip suffixes correctly; new code
should call these instead of hand-rolling a regex.
"""
from __future__ import annotations

import re

# Known derived-name suffixes, longest first so the most specific wins.
_SUFFIXES = (
    "-transcript-edited.json",
    "-transcript.json",
    "-moments-processed.json",
    "-moments.json",
    "-captioned.mp4",
    "-preview.mp4",
)

_TRANSCRIPT_RE = re.compile(r"-transcript(-edited)?\.json$")


def stem_from_derived_name(name: str) -> str:
    """Recover the stem from any known derived filename by stripping the known
    suffix (end-anchored). Unknown name → returned unchanged. Hyphen-safe: a
    hyphenated stem survives intact."""
    if not name:
        return name
    for suf in _SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def stem_from_transcript_name(name: str) -> str:
    """`{stem}-transcript.json` / `{stem}-transcript-edited.json` → `{stem}`.
    End-anchored, so a hyphenated stem is preserved."""
    return _TRANSCRIPT_RE.sub("", name or "")


def caption_glob_matches_sibling(stem: str, filename: str) -> bool:
    """True when a `{stem}-*-captioned.mp4` glob hit is actually a DIFFERENT,
    hyphen-extended stem's plain caption rather than a title-renamed file for
    `stem`. Lets a caption-existence check reject the false positive where, e.g.,
    stem `IMG_7168` globs `IMG_7168-Test1-captioned.mp4` (which belongs to the
    sibling stem `IMG_7168-Test1`). Heuristic, best-effort: a hit whose middle
    segment is empty is not ambiguous; otherwise the caller should confirm the
    plain name for `stem` doesn't collide with a known sibling stem."""
    base = filename
    if base.endswith("-captioned.mp4"):
        base = base[: -len("-captioned.mp4")]
    if not base.startswith(stem + "-"):
        return False
    middle = base[len(stem) + 1:]
    # A title-renamed file has a non-empty middle; a plain sibling caption whose
    # stem is `{stem}-{x}` also lands here — genuinely ambiguous from the name
    # alone. Report ambiguity so the caller can fall back to an exact check.
    return bool(middle)
