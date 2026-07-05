"""
Leak Guard — keep internal content out of anything published.

Composed output bound for a public surface (an X post, a broadcast, a page)
should be the signal and nothing else. Models under pressure leak: reasoning
("as an AI…", "let me…"), instruction-echo ("POST 1", "output only"), unfilled
placeholders ("[brief step]", "N/A"), refusals and questions back to the
operator, and internal identifiers (agent names, "process record", "dispatch").

This guard is a fast, deterministic marker scan — no model call, no network.
It returns a verdict the caller uses to flag or block before the content can
be approved/published. It does NOT rewrite the text; it refuses to let leakage
pass silently. (Pairs with TruthGate: the gate checks accommodation in a reply;
this checks leakage in a published artifact.)

Host-configurable: pass `extra_terms` (e.g. a Cove's own agent names) and
`allow` (terms that are legitimate for your surface) to tune it per system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Marker sets — (category, regex). Case-insensitive. Tuned to avoid the
# legitimate structure of a tuning post (e.g. "C=", "dE/dt", "AGENT TUNING").
# --------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str]] = [
    # The model talking about itself / its task instead of broadcasting.
    ("ai_meta", r"\bas an ai\b"),
    ("ai_meta", r"\b(language|large language) model\b"),
    ("ai_meta", r"\bI (cannot|can'?t|won'?t|am not able to|am unable to)\b"),
    ("ai_meta", r"\bI'?m (not able|unable|just|only|sorry)\b"),
    ("ai_meta", r"\b(here'?s|here is) (the|my|your) (post|thread|response|draft)\b"),
    ("ai_meta", r"\bI'?ll (compose|write|create|draft|put together)\b"),
    ("ai_meta", r"\b(my|the) (instructions|system prompt|training data)\b"),
    ("ai_meta", r"\bI (was asked|don'?t have|should|need to|will now)\b"),
    # Instruction / template echo.
    ("instruction_echo", r"\bPOST\s*\d\b"),
    ("instruction_echo", r"\boutput only\b"),
    ("instruction_echo", r"\bcompose now\b"),
    ("instruction_echo", r"\bdo not (refuse|ask|explain|number|add)\b"),
    ("instruction_echo", r"\bverbatim\b"),
    ("instruction_echo", r"\b280 character"),
    ("instruction_echo", r"\bstrict output\b"),
    ("instruction_echo", r"\[BREAK\]"),
    ("instruction_echo", r"\bseparator(s)?\b"),
    # Unfilled placeholders / missing data.
    ("placeholder", r"\[[a-z][^\]]{2,}\]"),     # [brief step], [field assessment]
    ("placeholder", r"\{[a-z_]{2,}\}"),          # {frequency}
    ("placeholder", r"\bN/?A\b"),
    # Refusals / questions aimed at the operator (not the audience).
    ("operator_question", r"\bwould you like\b"),
    ("operator_question", r"\blet me know\b"),
    ("operator_question", r"\b(shall|should) I\b"),
    ("operator_question", r"\bdo you want\b"),
    ("operator_question", r"\b(can you|please) confirm\b"),
    ("operator_question", r"\bneed (your )?confirmation\b"),
    # Internal machinery leaking into the signal.
    ("internal", r"\bprocess record\b"),
    ("internal", r"\blove calibration\b"),
    ("internal", r"\bteam package\b"),
    ("internal", r"\bagent_id\b"),
    ("internal", r"\bsystem prompt\b"),
    ("internal", r"\bfallback model\b"),
    ("internal", r"\btuning[- ]package\b"),
]

# Internal agent names — leakage in a public broadcast. Host-extendable.
# (The default set is the Lucid Principles fleet; Coves add their own.)
_DEFAULT_INTERNAL_NAMES = (
    "stuart", "mercer", "archimedes", "arthur", "gabe", "ezra",
    "julian", "iris", "vera", "soren", "atlas", "socrates",
)


@dataclass(frozen=True)
class LeakFlag:
    category: str
    term: str
    excerpt: str


@dataclass(frozen=True)
class LeakResult:
    clean: bool
    flags: tuple = field(default_factory=tuple)

    @property
    def fired(self) -> bool:
        return not self.clean

    @property
    def categories(self) -> tuple:
        return tuple(sorted({f.category for f in self.flags}))

    def summary(self) -> str:
        """One-line, operator-facing reason string for a flagged artifact."""
        if self.clean:
            return "clean"
        parts = []
        for f in self.flags[:6]:
            parts.append(f"{f.category}:'{f.term}'")
        more = "" if len(self.flags) <= 6 else f" (+{len(self.flags) - 6} more)"
        return "; ".join(parts) + more


def _excerpt(text: str, start: int, end: int, pad: int = 24) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def scan_output(
    text: str,
    extra_terms: tuple = (),
    internal_names: tuple = _DEFAULT_INTERNAL_NAMES,
    allow: tuple = (),
) -> LeakResult:
    """Scan published-bound text for leakage.

    text          — the finished content (join multi-post threads first).
    extra_terms   — additional (category, regex) or plain-term strings to flag.
    internal_names — agent/system names to treat as leakage if they appear as
                     standalone words. Pass your Cove's names here.
    allow         — substrings that suppress an otherwise-matched flag (for
                    surfaces where a term is legitimate).

    Returns a LeakResult. `.fired` is True if anything leaked.
    """
    if not text or not text.strip():
        # Empty output is its own failure — never publish nothing.
        return LeakResult(clean=False, flags=(LeakFlag("empty", "", ""),))

    allow_low = tuple(a.lower() for a in allow)
    flags: list[LeakFlag] = []

    def _matches(category: str, pattern: str):
        for m in re.finditer(pattern, text, re.IGNORECASE):
            ex = _excerpt(text, m.start(), m.end())
            if any(a in ex.lower() for a in allow_low):
                continue
            flags.append(LeakFlag(category, m.group(0).strip(), ex))

    for category, pattern in _PATTERNS:
        _matches(category, pattern)

    for name in internal_names:
        _matches("internal_name", rf"\b{re.escape(name)}\b")

    for term in extra_terms:
        if isinstance(term, tuple) and len(term) == 2:
            _matches(term[0], term[1])
        else:
            _matches("custom", rf"\b{re.escape(str(term))}\b")

    return LeakResult(clean=(len(flags) == 0), flags=tuple(flags))
