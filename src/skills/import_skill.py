"""
Gated community / third-party skill import path (agentskills.io).

Defense in depth (same as safety.py):
  1. Format validation (validate.py) — must be a real SKILL.md package
  2. Content scan (safety.scan_skill) — block/warn on injection & dangerous code
  3. Install into a WRITABLE root only (/app/data/provisioned/skills by default)
  4. Surfacing gate — hidden until operator writes `.approved` (loader._read_skill)

Repo-shipped skills under skills/ are trusted and never go through this path.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from src.skills.safety import is_trusted, scan_skill
from src.skills.validate import validate_skill_dir

# Default landing zone for imports (per-presence / writable). Must stay out of
# trusted repo roots so loader requires `.approved`.
DEFAULT_IMPORT_ROOT = Path("/app/data/provisioned/skills")

_SAFE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _skill_name_from_dir(skill_dir: Path) -> Optional[str]:
    report = validate_skill_dir(skill_dir)
    if not report.get("ok"):
        return None
    name = (report.get("frontmatter") or {}).get("name")
    return str(name).strip() if name else None


def plan_import(source_dir, dest_root=None) -> dict:
    """
    Dry-run an import. Does not write anything.

    Returns {
      ok, can_install, requires_approval, name, source, dest,
      validation, scan, errors, warnings
    }
    """
    src = Path(source_dir).resolve()
    root = Path(dest_root) if dest_root else DEFAULT_IMPORT_ROOT
    errors: list[str] = []
    warnings: list[str] = []

    if not src.is_dir():
        return {
            "ok": False,
            "can_install": False,
            "requires_approval": True,
            "name": None,
            "source": str(src),
            "dest": None,
            "validation": None,
            "scan": None,
            "errors": [f"source is not a directory: {src}"],
            "warnings": [],
        }

    if is_trusted(src):
        # Importing from a trusted root is pointless (already loadable) — still allow copy out.
        warnings.append("source is under a trusted (repo) root; import usually unnecessary")

    validation = validate_skill_dir(src)
    if not validation.get("ok"):
        errors.extend(validation.get("errors") or ["validation failed"])
        return {
            "ok": False,
            "can_install": False,
            "requires_approval": True,
            "name": (validation.get("frontmatter") or {}).get("name"),
            "source": str(src),
            "dest": None,
            "validation": validation,
            "scan": None,
            "errors": errors,
            "warnings": warnings + list(validation.get("warnings") or []),
        }

    name = str((validation.get("frontmatter") or {}).get("name") or "").strip()
    if not name or not _SAFE_NAME.match(name):
        errors.append(f"invalid skill name for install path: {name!r}")
        return {
            "ok": False,
            "can_install": False,
            "requires_approval": True,
            "name": name or None,
            "source": str(src),
            "dest": None,
            "validation": validation,
            "scan": None,
            "errors": errors,
            "warnings": warnings,
        }

    scan = scan_skill(src)
    if scan.get("risk") == "block":
        errors.append("safety scan risk=block — refuse install")
        for f in scan.get("findings") or []:
            if f.get("severity") == "block":
                errors.append(f"  block: {f.get('why')} ({f.get('file')})")
    elif scan.get("risk") == "warn":
        for f in scan.get("findings") or []:
            if f.get("severity") == "warn":
                warnings.append(f"warn: {f.get('why')} ({f.get('file')})")

    dest = (root / name).resolve()
    # Never allow install path to resolve into a trusted root
    if is_trusted(dest):
        errors.append(f"refusing to install into trusted root: {dest}")

    if dest.exists():
        warnings.append(f"destination already exists: {dest} (install will refuse without overwrite)")

    can_install = len(errors) == 0
    return {
        "ok": can_install,
        "can_install": can_install,
        "requires_approval": True,  # always — writable root + .approved gate
        "name": name,
        "source": str(src),
        "dest": str(dest),
        "validation": validation,
        "scan": scan,
        "errors": errors,
        "warnings": warnings + list(validation.get("warnings") or []),
    }


def install_skill(
    source_dir,
    dest_root=None,
    *,
    overwrite: bool = False,
    auto_approve: bool = False,
) -> dict:
    """
    Copy a skill into the import root after plan_import passes.

    Does NOT auto-approve by default — operator (or approve_skill) must create
    `.approved` before agents can see it. auto_approve=True is for trusted
    internal tooling only.
    """
    plan = plan_import(source_dir, dest_root=dest_root)
    if not plan.get("can_install"):
        return {**plan, "installed": False}

    dest = Path(plan["dest"])
    src = Path(plan["source"])
    if dest.exists():
        if not overwrite:
            plan["errors"] = list(plan.get("errors") or []) + [
                f"destination exists (pass overwrite=True to replace): {dest}"
            ]
            plan["ok"] = False
            plan["can_install"] = False
            plan["installed"] = False
            return plan
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=False)

    # Never carry a pre-existing .approved from an untrusted source into place
    # unless auto_approve is explicit.
    marker = dest / ".approved"
    if marker.exists() and not auto_approve:
        try:
            marker.unlink()
        except Exception:
            pass
    if auto_approve:
        marker.write_text("approved\n", encoding="utf-8")

    plan["installed"] = True
    plan["approved"] = bool(auto_approve) or (dest / ".approved").exists()
    plan["ok"] = True
    return plan


def approve_skill(skill_dir) -> dict:
    """Operator approval: write `.approved` so loader will surface the skill."""
    d = Path(skill_dir)
    if not d.is_dir():
        return {"ok": False, "error": f"not a directory: {d}"}
    if is_trusted(d):
        return {"ok": True, "trusted": True, "message": "repo skill; approval not required"}
    # Re-scan at approval time
    scan = scan_skill(d)
    if scan.get("risk") == "block":
        return {
            "ok": False,
            "error": "safety scan still risk=block; refuse approval",
            "scan": scan,
        }
    validation = validate_skill_dir(d)
    if not validation.get("ok"):
        return {
            "ok": False,
            "error": "SKILL.md validation failed",
            "validation": validation,
        }
    (d / ".approved").write_text("approved\n", encoding="utf-8")
    return {
        "ok": True,
        "approved": True,
        "skill_dir": str(d),
        "name": (validation.get("frontmatter") or {}).get("name"),
        "scan": scan,
    }


def revoke_approval(skill_dir) -> dict:
    """Remove `.approved` so the skill is hidden again (files stay on disk)."""
    d = Path(skill_dir)
    marker = d / ".approved"
    if not d.is_dir():
        return {"ok": False, "error": f"not a directory: {d}"}
    if marker.exists():
        marker.unlink()
    return {"ok": True, "approved": False, "skill_dir": str(d)}
