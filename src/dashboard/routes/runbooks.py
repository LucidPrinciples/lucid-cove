"""
Runbooks routes — operational command sequences for deploy/maintenance workflows.
JSON files in /app/data/runbooks/ (persistent volume). Both Claude and agents can
read and update them via API. The MC System tab renders them as copy-paste steps.

Seed files live in /app/runbooks/ (baked into image). On first boot, if the
persistent dir is empty, seeds are copied in automatically.
"""

import json
import os
from src.env import env
import shutil
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

RUNBOOKS_DIR = Path(env("RUNBOOKS_DIR", "/app/data/runbooks"))
SEED_DIR = Path(env("RUNBOOKS_SEED_DIR", "/cove-core/runbooks"))


def _ensure_dir():
    RUNBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    # Seed from baked-in defaults — always overwrite so seed files are source of truth.
    # API edits to persistent runbooks will be replaced on next deploy. If you need
    # a permanent change, update the seed JSON in cove-core/runbooks/.
    if SEED_DIR.exists():
        for f in SEED_DIR.glob("*.json"):
            shutil.copy2(f, RUNBOOKS_DIR / f.name)


# ── Role scoping (CF-35) ─────────────────────────────────────────────────────
# Runbooks are scoped by audience so a Cove admin/steward sees the ops runbooks
# (deploy/restart/backup/logs) while a non-admin Presence sees only their own
# working commands. The audience is config-driven: each runbook JSON carries an
# optional "audience" field. Untagged runbooks default to "steward" — every
# runbook shipped today is operational, so this keeps ops out of a presence's
# view without needing every seed re-tagged.

_VALID_AUDIENCES = ("steward", "presence", "all")


def _runbook_audience(data: dict) -> str:
    """Who a runbook is for. Default 'steward' (ops) for untagged runbooks."""
    a = (data.get("audience") or "steward").strip().lower()
    return a if a in _VALID_AUDIENCES else "steward"


async def _is_restricted_presence(request: Request) -> bool:
    """True ONLY when the caller positively resolves to a non-admin Presence.

    Single-user mode, admins, and any unresolved/ambiguous context return False
    (sees everything). This can only HIDE steward ops from a known non-admin
    presence — it never regresses an admin's or a single-user operator's view.
    """
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        if not p:
            return False
        return (p.get("cove_role") or "") != "admin"
    except Exception:
        return False


def _visible_to(audience: str, restricted: bool) -> bool:
    """A restricted (non-admin) presence sees only presence/all runbooks."""
    if not restricted:
        return True
    return audience in ("presence", "all")


@router.get("/api/runbooks")
async def list_runbooks(request: Request):
    """List runbooks with metadata (no steps), scoped to the caller's role (CF-35)."""
    _ensure_dir()
    restricted = await _is_restricted_presence(request)
    runbooks = []
    for f in sorted(RUNBOOKS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            audience = _runbook_audience(data)
            if not _visible_to(audience, restricted):
                continue
            # Support both old format (order/name) and new format (num/title)
            num = data.get("num", data.get("order", 99))
            order = num if isinstance(num, int) else 99
            runbooks.append({
                "slug": f.stem,
                "order": order,
                "num": num,
                "name": data.get("title", data.get("name", f.stem)),
                "category": data.get("category", "general"),
                "audience": audience,
                "description": data.get("description", ""),
                "step_count": len(data.get("steps", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            continue
    runbooks.sort(key=lambda r: r["order"])
    return {"runbooks": runbooks}


@router.get("/api/runbooks/{slug}")
async def get_runbook(slug: str, request: Request):
    """Get a full runbook with all steps (role-scoped, CF-35)."""
    _ensure_dir()
    path = RUNBOOKS_DIR / f"{slug}.json"
    if not path.exists():
        return JSONResponse({"error": f"Runbook '{slug}' not found"}, status_code=404)
    try:
        data = json.loads(path.read_text())
        if not _visible_to(_runbook_audience(data), await _is_restricted_presence(request)):
            return JSONResponse(
                {"error": "Runbook not available for this role"}, status_code=403
            )
        data["slug"] = slug
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/runbooks/{slug}")
async def update_runbook(slug: str, request: Request):
    """Create or update a runbook. Full replace of the JSON file."""
    _ensure_dir()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Validate minimum structure
    if "name" not in body or "steps" not in body:
        return JSONResponse(
            {"error": "Runbook must have 'name' and 'steps' fields"},
            status_code=400,
        )

    # Add timestamp
    from datetime import datetime, timezone
    body["updated_at"] = datetime.now(timezone.utc).isoformat()

    path = RUNBOOKS_DIR / f"{slug}.json"
    path.write_text(json.dumps(body, indent=2))
    return {"success": True, "slug": slug, "updated_at": body["updated_at"]}


@router.delete("/api/runbooks/{slug}")
async def delete_runbook(slug: str):
    """Delete a runbook."""
    _ensure_dir()
    path = RUNBOOKS_DIR / f"{slug}.json"
    if not path.exists():
        return JSONResponse({"error": f"Runbook '{slug}' not found"}, status_code=404)
    path.unlink()
    return {"success": True, "slug": slug}
