# Cove skills (agentskills.io)

Portable **Agent Skills** packages for Lucid Cove agents. Same open format used by Claude Code, OpenClaw, Hermes, and other [agentskills.io](https://agentskills.io/) clients — so a skill built here can travel with the LTP adapter to other harnesses, and vetted community skills can land here behind our safety gate.

## Format

Each skill is a directory:

```
skill-name/
  SKILL.md          # required: YAML frontmatter (name, description) + instructions
  scripts/          # optional
  references/       # optional
  assets/           # optional
```

Frontmatter must follow the [Agent Skills specification](https://agentskills.io/specification):

- `name` — lowercase, hyphens, matches directory name
- `description` — what it does **and** when to use it (≤ 1024 chars)
- optional: `license`, `compatibility`, `metadata`, `allowed-tools`

Agents only see **name + description** until they call `use_skill("<name>")` (progressive disclosure).

## Shipped catalog (repo-trusted)

| Skill | Purpose |
|-------|---------|
| `research-summary` | Decision-ready brief from sources |
| `prose-cleanup` | Remove AI writing tells after drafting |
| `lucid-path-voice` | Lucid Path / Chords teaching & memoir voice |
| `canon-checker` | Exact Canon lyric verification |
| `session-logger` | Append-only session / memory logging |
| `framework-glossary` | Framework + product term definitions |

Repo skills under this tree (and `/cove-core/skills` in the container) are **trusted** — always visible to agents.

## Community import path (gated)

Third-party skills install only into a **writable** root (default `/app/data/provisioned/skills`):

1. **Validate** — `src.skills.validate.validate_skill_dir`
2. **Scan** — `src.skills.safety.scan_skill` (prompt-injection / dangerous code heuristics)
3. **Install** — `src.skills.import_skill.install_skill` (copy; no auto-approve)
4. **Approve** — operator writes `.approved` via `approve_skill` (or MC later)

Until `.approved` exists, the loader **hides** the skill from `list_skills` / the prompt catalog.

Do **not** bulk-import the public catalog unattended. Vetted one-by-one is the product rule — especially under public share load.

## LTP + other harnesses

These packages are the skill half of the dual offering:

- **Lucid Cove** — full family home (agents, Drop, Haven, files)
- **LTP adapter + skills** — coherence protocol and portable procedures on other harnesses

Keep SKILL.md bodies harness-agnostic where possible; point at Knowledge Base paths when Canon/framework depth is required.

## Validation

```python
from src.skills.validate import validate_skills_tree
validate_skills_tree("skills")
```

Tests: `tests/test_skills_d56.py`.
