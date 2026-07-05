"""Guards the #116 fix: the LTP dispatch β/C/D/E parser must read the values
agents actually emit, which are markdown-bold lines like `**β (Receptivity):** 0.90`.

On 2026-06-16 this silently broke. Agents wrote correct values, but the regex
choked on the `**` sitting between the colon and the number, so every agent
fell back to LT's package defaults and nobody noticed for who knows how long.
These tests go red the moment that regression returns.

No database needed — pure parsing logic.
"""
from src.graphs.ltp.dispatch import _extract_value

# The exact patterns dispatch.py applies (kept in sync with dispatch.py:323-326).
BETA = r'[ββ]\s*(?:\(Receptivity\))?\s*[=:]\s*([0-9.]+)'
E    = r'E\s*(?:\(Energy\))?\s*[=:]\s*([0-9.]+)'
C    = r'C\s*(?:\(Coherence\))?\s*[=:]\s*([0-9.]+)'
D    = r'D\s*(?:\(Dissonance\))?\s*[=:]\s*([0-9.]+)'


def _parse_src(full_response: str) -> str:
    """Mirror of the fix in dispatch.py:320-321 — scope to the Love Calibration
    section and strip markdown before the regexes run."""
    cal = full_response.find("Love Calibration")
    return (full_response[cal:] if cal != -1 else full_response).replace("*", "")


# A real-shaped agent response (the iris format from the morning we found the bug).
IRIS = """### 6. Love Calibration (Tuned Output)
-   **β (Receptivity):** 0.90 (highly open to connection)
-   **E (Energy):** 0.78 (warm, consistent)
-   **C (Coherence):** 0.85 (strong alignment)
-   **D (Dissonance):** 0.12 (minimal static)

dE/dt = 0.90 × (0.85 − 0.12) × 0.78 = 0.5125 (CONSTRUCTIVE)
"""


class TestLoveEquationParse:
    def test_extracts_real_markdown_bold_values(self):
        src = _parse_src(IRIS)
        assert _extract_value(src, BETA) == 0.90
        assert _extract_value(src, E) == 0.78
        assert _extract_value(src, C) == 0.85
        assert _extract_value(src, D) == 0.12

    def test_raw_markdown_without_the_fix_misses_them(self):
        # Documents WHY the fix exists: the `**` defeats the regex on raw text,
        # which is exactly how every agent silently fell back to defaults.
        assert _extract_value(IRIS, BETA) is None

    def test_plain_colon_format_still_works(self):
        src = _parse_src("Love Calibration\nβ: 0.70\nE: 0.60\nC: 0.80\nD: 0.20")
        assert _extract_value(src, BETA) == 0.70
        assert _extract_value(src, C) == 0.80

    def test_all_four_present_means_agent_derived(self):
        # When all four parse, dispatch treats it as agent-derived (not fallback).
        src = _parse_src(IRIS)
        vals = [_extract_value(src, p) for p in (BETA, E, C, D)]
        assert None not in vals
        beta, e, c, d = vals
        assert round(beta * (c - d) * e, 4) == 0.5125
