"""
Skill subsystem — agentskills.io-compatible skills for cove-core agents.

A skill is a DIRECTORY containing a `SKILL.md` (YAML frontmatter: name +
description, optional license / compatibility / allowed-tools / metadata) plus
optional `scripts/`, `references/`, `assets/`. This is the same open format used
by Claude Code, OpenClaw (~13.7k skills), and Hermes — so a Cove can consume any
of them.

PROGRESSIVE DISCLOSURE (the whole point):
  1. Catalog — only `name` + `description` are surfaced to the agent at
     prompt-build time (cheap; see `skill_catalog_text`).
  2. Instructions — the full SKILL.md body loads ONLY when the agent calls
     `use_skill(name)` (see src/tools/skill_tools.py).
  3. Resources — scripts/references/assets are read or run by the agent on
     demand, using its existing tools.

SEARCH ROOTS (first root that defines a given skill name wins, so the repo's
standard skills can be overridden per-Cove or per-presence):
  1. /cove-core/skills            — shipped in the repo (the standard catalog)
  2. <repo>/skills (local dev)     — same, when running off the Mac checkout
  3. /app/skills                   — per-Cove overlay additions
  4. /app/data/provisioned/skills  — per-presence, writable (where imports land)

NOTE: third-party / imported skills must pass the import safety gate
(src/skills/safety.py, backlog #148) before they land in a search root.
"""
from pathlib import Path
from typing import Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]  # .../cove-core

_SKILL_ROOTS = [
    Path("/cove-core/skills"),               # repo (container mount)
    _REPO_ROOT / "skills",                   # repo (local dev checkout)
    Path("/app/skills"),                     # per-Cove overlay (container)
    Path("/app/data/provisioned/skills"),    # per-presence (writable)
]


def _roots():
    """Existing skill roots, de-duplicated by resolved path, in priority order."""
    seen, out = set(), []
    for r in _SKILL_ROOTS:
        try:
            if r.is_dir():
                key = str(r.resolve())
                if key not in seen:
                    seen.add(key)
                    out.append(r)
        except Exception:
            continue
    return out


def _split_frontmatter(text: str):
    """Return (frontmatter_dict, body) from a SKILL.md string."""
    t = text.lstrip()
    if not t.startswith("---"):
        return {}, text
    parts = t.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].lstrip("\n")


def _read_skill(skill_dir: Path) -> Optional[dict]:
    """Parse one skill directory. Returns None if invalid (no SKILL.md/description)."""
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except Exception:
        return None
    fm, body = _split_frontmatter(text)
    name = (fm.get("name") or skill_dir.name or "").strip()
    desc = (fm.get("description") or "").strip()
    if not name or not desc:
        return None
    # Safety gate (#148): a skill in a WRITABLE (non-repo) root is hidden from
    # agents until an operator has reviewed + approved it (a `.approved` marker).
    # Repo-shipped skills are trusted and always surface.
    try:
        from src.skills.safety import is_trusted
        if not is_trusted(skill_dir) and not (skill_dir / ".approved").exists():
            return None
    except Exception:
        pass
    return {
        "name": name,
        "description": desc,
        "dir": str(skill_dir),
        "frontmatter": fm,
        "body": body,
    }


def discover_skills() -> list[dict]:
    """Catalog: [{name, description, dir}] across all roots. Frontmatter only
    (no body) — this is what gets surfaced to the agent cheaply at prompt time."""
    out: dict[str, dict] = {}
    for root in _roots():
        try:
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                s = _read_skill(d)
                if s and s["name"] not in out:  # first root wins
                    out[s["name"]] = {"name": s["name"], "description": s["description"], "dir": s["dir"]}
        except Exception:
            continue
    return list(out.values())


def load_skill(name: str) -> Optional[dict]:
    """Full skill (incl. body) for a given name, or None. Used by use_skill()."""
    name = (name or "").strip().lower()
    for root in _roots():
        try:
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                s = _read_skill(d)
                if s and s["name"].lower() == name:
                    return s
        except Exception:
            continue
    return None


def skill_catalog_text() -> str:
    """Markdown catalog injected into the agent's system prompt (name+description
    only — progressive disclosure). Empty string when no skills are installed."""
    skills = discover_skills()
    if not skills:
        return ""
    lines = [
        "## Available Skills",
        "Specialized instruction sets you can activate on demand. When a task clearly "
        "matches one, call `use_skill(\"<name>\")` to load its full instructions, then follow them.",
        "",
    ]
    for s in skills:
        lines.append(f"- **{s['name']}** — {s['description']}")
    return "\n".join(lines)
