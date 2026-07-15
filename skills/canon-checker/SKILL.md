---
name: canon-checker
description: Verify Canon lyric quotes against lucid-canon.md, the single source of truth. Use whenever writing or editing content that quotes the 22 Principles — catch misquotes, paraphrase, fabricated lines, wrong attributions, and smoothed grammar before publish.
license: Apache-2.0
compatibility: Requires access to Knowledge Base lucid-canon.md (or equivalent Canon source of truth) in the Cove vault.
metadata:
  author: lucid-principles
  version: "1.0"
  domain: framework
---

# Canon Checker

**Canon lyrics are exact.** Never paraphrase. Never "fix" grammar. Never invent a line that "sounds like" a Principle.

## When to use
- Any draft that quotes or closely echoes a Principle lyric
- Before finalizing chapters, posts, decks, or UI copy with lyric fragments
- When recovering from compaction and unsure a quote is real

## Source of truth
1. Primary: `AgentSkills/Knowledge Base/lucid-canon.md` (or the Cove's KB path for Canon)
2. If that file is missing, say so and **do not invent** lyrics — flag the quote as unverified

## Formatting conventions (from Canon)
- Line breaks within quotes use `/`
- Omitted portions within quotes use `...`
- Quote text must match Canon character-for-character aside from intentional `/` and `...` elision

## Process
1. Extract every lyric quote or near-quote from the draft.
2. For each, identify the Principle (title) if claimed.
3. Look up the Principle in `lucid-canon.md`.
4. Compare to KEY / SECONDARY / TERTIARY / FULL_LYRICS as needed.
5. Classify each quote:
   - **Exact** — matches Canon
   - **Elided OK** — exact with valid `...` / `/` usage
   - **Misquote** — wrong words
   - **Paraphrase** — meaning-ish, not Canon
   - **Fabricated** — not in Canon
   - **Wrong attribution** — real line, wrong Principle
   - **Smoothed** — grammar or wording "cleaned"
6. Report findings; fix only with operator intent (replace with exact Canon or remove).

## Output format
```
## Canon check
- [Exact] "…" — Principle: NAME
- [Misquote] draft: "…" → Canon: "…" (Principle: NAME)
- [Fabricated] "…" — not in Canon
Summary: N quotes, M failures
```

## Rules
- Prefer KEY_LYRIC / documented lyrics over memory
- If unsure, mark **unverified** — never guess
- Do not silently "improve" a lyric while editing surrounding prose
