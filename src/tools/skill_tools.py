"""
Skill tools — let an agent discover and activate agentskills.io skills.

`list_skills()` returns the catalog; `use_skill(name)` returns the full SKILL.md
instructions so the agent can follow them. Both are read-only (they return text).
Any tools the skill then tells the agent to run go through the normal approval
tiers, and skill scripts stay inside the container boundary.

Tier: AUTO — both just return text. (Add `use_skill`/`list_skills` to the auto
tier in cove.yaml so activation is frictionless.)
"""
from langchain_core.tools import tool

from src.skills.loader import discover_skills, load_skill


@tool
def list_skills() -> str:
    """List the skills available to activate, each with a short description.
    Use this to see what specialized instruction sets you can pull in."""
    skills = discover_skills()
    if not skills:
        return "No skills are installed in this Cove."
    return "\n".join(f"- {s['name']}: {s['description']}" for s in skills)


@tool
def use_skill(name: str) -> str:
    """Activate a skill by name and return its full instructions. Call this when a
    task clearly matches one of the Available Skills, then follow the returned
    instructions. Example: use_skill("research-summary")."""
    s = load_skill(name)
    if not s:
        avail = ", ".join(x["name"] for x in discover_skills()) or "(none installed)"
        return f"No skill named '{name}'. Available skills: {avail}."
    files_note = (
        f"\n\n---\nSkill files live in `{s['dir']}`. Read anything under `references/` "
        f"or run `scripts/` from there as the instructions direct (your normal tool "
        f"approvals still apply)."
    )
    return f"# Skill: {s['name']}\n_{s['description']}_\n\n{s['body']}{files_note}"


# Also loadable as a normal tool module (TOOLS list) if listed in tools.modules.
TOOLS = [list_skills, use_skill]
