"""
SKILL.md format validation against the agentskills.io specification.

Validates directory shape + YAML frontmatter constraints so repo-shipped and
imported skills stay portable with Claude Code, OpenClaw, Hermes, and other
agentskills.io clients.

Spec reference: https://agentskills.io/specification
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# name: 1–64 chars, lowercase a-z / 0-9 / hyphen, no leading/trailing/consecutive hyphens
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """Return (frontmatter, body, errors)."""
    errors: list[str] = []
    t = text.lstrip("\ufeff")  # strip BOM if present
    if not t.startswith("---"):
        return {}, text, ["SKILL.md must start with YAML frontmatter (---)"]
    parts = t.split("---", 2)
    if len(parts) < 3:
        return {}, text, ["SKILL.md frontmatter is not closed with ---"]
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception as e:
        return {}, parts[2] if len(parts) > 2 else "", [f"frontmatter YAML parse error: {e}"]
    if not isinstance(fm, dict):
        return {}, parts[2].lstrip("\n"), ["frontmatter must be a YAML mapping"]
    return fm, parts[2].lstrip("\n"), errors


def validate_skill_md_text(text: str, *, dir_name: str | None = None) -> dict:
    """Validate SKILL.md content. Returns {ok, errors, warnings, frontmatter}."""
    errors: list[str] = []
    warnings: list[str] = []
    fm, body, split_errs = _split_frontmatter(text)
    errors.extend(split_errs)
    if split_errs and not fm:
        return {"ok": False, "errors": errors, "warnings": warnings, "frontmatter": {}}

    name = fm.get("name")
    desc = fm.get("description")

    if name is None or (isinstance(name, str) and not name.strip()):
        errors.append("frontmatter.name is required")
    else:
        name_s = str(name).strip()
        if len(name_s) > 64:
            errors.append("frontmatter.name must be ≤ 64 characters")
        if not _NAME_RE.match(name_s):
            errors.append(
                "frontmatter.name must be lowercase letters, numbers, single hyphens "
                "(no leading/trailing/consecutive hyphens)"
            )
        if dir_name and name_s != dir_name:
            errors.append(
                f"frontmatter.name '{name_s}' must match parent directory name '{dir_name}'"
            )

    if desc is None or (isinstance(desc, str) and not str(desc).strip()):
        errors.append("frontmatter.description is required")
    else:
        desc_s = str(desc).strip()
        if len(desc_s) > 1024:
            errors.append("frontmatter.description must be ≤ 1024 characters")
        if len(desc_s) < 20:
            warnings.append("description is very short; include what + when to use")

    if "license" in fm and fm["license"] is not None:
        lic = str(fm["license"]).strip()
        if not lic:
            warnings.append("license is empty; omit the field or set a real license")

    if "compatibility" in fm and fm["compatibility"] is not None:
        compat = str(fm["compatibility"])
        if len(compat) > 500:
            errors.append("frontmatter.compatibility must be ≤ 500 characters")

    if "metadata" in fm and fm["metadata"] is not None:
        if not isinstance(fm["metadata"], dict):
            errors.append("frontmatter.metadata must be a mapping")
        else:
            for k, v in fm["metadata"].items():
                if not isinstance(k, str) or not isinstance(v, (str, int, float, bool)):
                    # Spec: string keys to string values; we allow simple scalars coerced to str
                    if not isinstance(k, str):
                        errors.append("frontmatter.metadata keys must be strings")
                    elif not isinstance(v, (str, int, float, bool)):
                        warnings.append(
                            f"metadata.{k} should be a string (agentskills.io string→string map)"
                        )

    if "allowed-tools" in fm and fm["allowed-tools"] is not None:
        if not isinstance(fm["allowed-tools"], str):
            errors.append("frontmatter.allowed-tools must be a space-separated string")

    if not (body or "").strip():
        warnings.append("SKILL.md body is empty; instructions help agents execute the skill")

    body_lines = (body or "").count("\n") + (1 if body else 0)
    if body_lines > 500:
        warnings.append(
            f"body is {body_lines} lines; agentskills.io recommends keeping SKILL.md under 500 lines"
        )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "frontmatter": fm,
    }


def validate_skill_dir(skill_dir) -> dict:
    """Validate a skill directory (must contain SKILL.md)."""
    d = Path(skill_dir)
    if not d.is_dir():
        return {
            "ok": False,
            "errors": [f"not a directory: {d}"],
            "warnings": [],
            "frontmatter": {},
            "skill_dir": str(d),
        }
    md = d / "SKILL.md"
    if not md.is_file():
        return {
            "ok": False,
            "errors": ["missing SKILL.md"],
            "warnings": [],
            "frontmatter": {},
            "skill_dir": str(d),
        }
    try:
        text = md.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "ok": False,
            "errors": [f"cannot read SKILL.md: {e}"],
            "warnings": [],
            "frontmatter": {},
            "skill_dir": str(d),
        }
    result = validate_skill_md_text(text, dir_name=d.name)
    result["skill_dir"] = str(d)
    return result


def validate_skills_tree(root) -> dict:
    """Validate every immediate child directory under a skills root."""
    root = Path(root)
    if not root.is_dir():
        return {"ok": False, "errors": [f"not a directory: {root}"], "skills": []}
    reports = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            reports.append(validate_skill_dir(child))
    ok = all(r.get("ok") for r in reports) if reports else True
    return {"ok": ok, "root": str(root), "skills": reports}
