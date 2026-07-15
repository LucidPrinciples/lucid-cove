---
name: session-logger
description: Standardize how session progress is logged into Memory and Archive. Use at end of meaningful work, after decisions, on compaction recovery, or when asked to save state — append-only, never delete history.
license: Apache-2.0
metadata:
  author: lucid-principles
  version: "1.0"
  domain: memory
---

# Session Logger

Keep the Cove brain honest across sessions. Log what happened, where it settled, and what is still open — without bloating the hot index.

## When to use
- End of a meaningful work block
- After decisions, merges, deploys, or operator instructions that should stick
- Compaction recovery ("what was true before context reset?")
- Operator asks to log progress / save state / update memory

## What to log (always)
1. **What changed** — one line outcome, not a transcript
2. **Decisions** — who decided, what, why (if known)
3. **Open threads** — still true / still blocked
4. **Pointers** — PR links, task ids, file paths, queue ids
5. **Corrections** — if prior memory was wrong, note the fix

## Where it goes
| Content | Destination |
|---------|-------------|
| Durable fact / decision / preference | Persistent memory tools (`save_memory` / correct existing) |
| Current focus snapshot | `AgentSkills/Context/working-memory.md` (or equivalent) |
| Index pointer only | `AgentSkills/Context/memory.md` |
| Session narrative / long detail | `Archive/` with dated header — **never delete** |

## Entry shape (session archive)
```markdown
## YYYY-MM-DD — short title
- Outcome: …
- Decisions: …
- Open: …
- Links: …
```

## Append-only rules
- Never delete prior session content; archive and point
- Prefer correcting a memory over writing a conflicting duplicate
- Cap working-memory: if too long, move settled items out (lifecycle), don't truncate history silently
- On compaction recovery: verify Memory/index against Archive before acting on thin summaries

## Process
1. Scan what actually shipped this session (tools, PRs, operator words).
2. Write durable memories for decisions/facts that must survive.
3. Update working-memory to the current snapshot only.
4. Append a dated archive entry for narrative detail.
5. Confirm index pointers still resolve.

## Anti-patterns
- Logging vibes without outcomes
- Replacing the archive with a summary that drops links
- Treating the compaction summary as more true than the vault
