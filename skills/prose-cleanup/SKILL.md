---
name: prose-cleanup
description: Remove predictable AI writing patterns from drafted prose. Use after drafting, when editing or reviewing text for filler, formulaic structure, weak voice, or unearned polish. Produces cleaner, more human, more specific writing.
license: Apache-2.0
metadata:
  author: lucid-principles
  version: "1.0"
  domain: writing
---

# Prose Cleanup

Clean AI tells out of draft prose. **Draft first, clean second** — do not run this while generating the first pass.

## When to use
- Editing or reviewing any draft
- Operator asks to clean up writing, remove AI patterns, or make it sound more human
- After `lucid-path-voice` (or any voice pass), before final delivery

## Eight rules
1. **Kill filler.** Cut "It's important to note", "In today's world", "delve", "landscape", "tapestry", "robust", "leverage", "nestled", "ever-evolving", and similar stock phrases.
2. **Break formula.** Avoid rigid three-part lists, mirrored open/close, and "Not X — Y" as a default move. Vary sentence length and structure.
3. **Prefer active voice.** Subject does the verb. Passive only when the actor is unknown or irrelevant.
4. **Be specific.** Replace abstractions with concrete nouns, numbers, names, and sensory detail.
5. **Respect the reader.** No lecturing throat-clearing. Assume intelligence; earn attention.
6. **Rhythm over symmetry.** Mix short punches with longer lines. Read aloud if unsure.
7. **Earn trust.** No false confidence, no invented precision, no hedging that pretends to be nuance.
8. **Avoid fake quotability.** Don't force one-liners that sound profound but say nothing.

## Process
1. Read the full draft once without editing.
2. Mark violations of the eight rules (don't rewrite yet).
3. Rewrite in passes: filler → structure → voice → specificity.
4. Score against the rubric below. If under **35/50**, revise again.
5. Return the cleaned prose plus a short change note (what class of tells you removed).

## Scoring rubric (10 each, max 50)
| Axis | 10 means |
|------|----------|
| Filler free | No stock AI phrases |
| Structure | Natural, not templated |
| Voice | Active, direct, human |
| Specificity | Concrete over abstract |
| Trust | Honest, no fake polish |

**Ship threshold: 35/50.**

## Output
- Cleaned text first
- Brief edit note second (bullets, not a second essay)
- Do not invent facts to sound sharper
