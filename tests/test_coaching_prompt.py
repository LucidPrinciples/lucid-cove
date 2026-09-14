"""Tune Now coaching: triad is context; never quote the Key."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = (
    ROOT / "src" / "dashboard" / "routes" / "tuning_request" / "core.py",
    ROOT / "src" / "dashboard" / "routes" / "tuning_request.py",
)


class CoachingPromptTests(unittest.TestCase):
    def test_prompt_does_not_quote_the_key(self) -> None:
        for path in PROMPTS:
            with self.subTest(path=str(path)):
                src = path.read_text(encoding="utf-8")
                self.assertIn("Do not quote it", src)
                self.assertIn("Do not paraphrase it", src)
                self.assertIn("Under 50 words", src)
                self.assertNotIn("Reference it, don't rewrite it", src)


if __name__ == "__main__":
    unittest.main()
