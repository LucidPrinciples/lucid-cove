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

# Host-specific ops that must NEVER ship as a universal seed. RB16 talks to
# Clearfield + Founders host paths via ssh to lp-homebase — useless (and confusing)
# on a fresh Woods/Quietgrove/iMac install. Keep those on the Clearfield operator's
# Nextcloud at AgentSkills/Ops/runbooks/ (NC merge still shows them on Clearfield).
_REMOVED_SEED_FILES = frozenset({
    "16-deploy-main-clearfield-founders.json",
})


def _ensure_dir():
    RUNBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    # Seed from baked-in defaults — always overwrite so seed files are source of truth.
    # API edits to persistent runbooks will be replaced on next deploy. If you need
    # a permanent change, update the seed JSON in cove-core/runbooks/.
    if SEED_DIR.exists():
        for f in SEED_DIR.glob("*.json"):
            if f.name in _REMOVED_SEED_FILES:
                continue
            shutil.copy2(f, RUNBOOKS_DIR / f.name)
    # Drop orphans left from older seeds (e.g. RB16 shipped to every Cove once).
    for name in _REMOVED_SEED_FILES:
        p = RUNBOOKS_DIR / name
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


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


# ── Path templating (long-term: runbooks never make the user dig for a folder) ──
# Runbook steps may use {{COVE_DIR}} (the stack folder that holds docker-compose.yml)
# and {{CLONE_DIR}} (the lucid-cove repo you `git pull`). At serve time we fill them
# with this box's real host paths, derived from COVE_HOST_DIR (baked into every Cove's
# env by the provisioner). If a path is unknown, a readable placeholder is shown instead
# of an empty string, so a copy-pasted command is never silently broken.

def _host_paths() -> tuple[str, str]:
    """(cove_dir, clone_dir) real host paths for THIS box, or ("","") when unknown.

    Sources, in priority order (first non-empty wins per path), so a box whose layout
    isn't the provisioner default (e.g. the migrated founder at ~/CoveCoveNew/...) can
    still fill these instead of always showing a placeholder (#D27):
      1. explicit env overrides COVE_CLONE_DIR / COVE_COVE_DIR
      2. cove.yaml deploy.clone_dir / deploy.cove_dir
      3. COVE_HOST_DIR — the stack folder, laid out as <clone>/out/<id>-cove by
         install.sh + the manual provisioner, so the clone root is its grandparent.
    """
    cove_dir = (env("COVE_COVE_DIR", "") or "").strip()
    clone_dir = (env("COVE_CLONE_DIR", "") or "").strip()

    if not (cove_dir and clone_dir):
        try:
            from src.config import load_cove_config
            deploy = (load_cove_config().get("deploy") or {})
            if isinstance(deploy, dict):
                cove_dir = cove_dir or str(deploy.get("cove_dir") or "").strip()
                clone_dir = clone_dir or str(deploy.get("clone_dir") or "").strip()
        except Exception:
            pass

    host_dir = (env("COVE_HOST_DIR", "") or "").strip()
    if host_dir:
        cove_dir = cove_dir or host_dir
        if not clone_dir:
            p = Path(host_dir)
            if p.parent.name == "out":       # <clone>/out/<id>-cove -> clone root
                clone_dir = str(p.parent.parent)
    return cove_dir, clone_dir


# Explicit, actionable hints for when a path can't be resolved for this box — never a
# silent generic placeholder that reads as if it were filled (#D27).
_COVE_DIR_HINT = "<set your Cove dir: COVE_COVE_DIR env or cove.yaml deploy.cove_dir>"
_CLONE_DIR_HINT = "<set your clone dir: COVE_CLONE_DIR env or cove.yaml deploy.clone_dir>"


def _fill_paths(data: dict) -> dict:
    """Substitute {{COVE_DIR}} / {{CLONE_DIR}} in every step's command/note/label with
    this box's real host paths. When a path is unknown, substitute an explicit
    'set this in Settings' hint (not a silent generic placeholder) and flag the step +
    runbook so the UI can warn that a command needs a path before it will run (#D27)."""
    cove_dir, clone_dir = _host_paths()
    cove_sub = cove_dir or _COVE_DIR_HINT
    clone_sub = clone_dir or _CLONE_DIR_HINT

    def sub(s):
        if not isinstance(s, str):
            return s
        return s.replace("{{COVE_DIR}}", cove_sub).replace("{{CLONE_DIR}}", clone_sub)

    incomplete = False
    for step in data.get("steps", []):
        if not isinstance(step, dict):
            continue
        needs = set()
        for k in ("command", "note", "label"):
            if k not in step:
                continue
            if isinstance(step[k], str):
                if not cove_dir and "{{COVE_DIR}}" in step[k]:
                    needs.add("cove")
                if not clone_dir and "{{CLONE_DIR}}" in step[k]:
                    needs.add("clone")
            step[k] = sub(step[k])
        if needs:
            incomplete = True
            hints = []
            if "clone" in needs:
                hints.append("your lucid-cove clone dir (COVE_CLONE_DIR or "
                             "cove.yaml deploy.clone_dir)")
            if "cove" in needs:
                hints.append("your Cove stack dir (COVE_COVE_DIR or "
                             "cove.yaml deploy.cove_dir)")
            step["path_hint"] = ("This box's paths aren't set yet — configure "
                                 + " and ".join(hints)
                                 + " so this command fills in automatically.")
    if incomplete:
        data["paths_incomplete"] = True
    return data


# ── #D28: custom runbooks from the operator's NC space ───────────────────────
# Beside the baked-in seeds (/app/data/runbooks, editable only via the API), read
# the operator's own AgentSkills/Ops/runbooks/*.json over WebDAV. That folder syncs
# to the operator's machine (NC desktop) and mounts into the Cowork bridge, so a
# runbook becomes a FILE the operator and the agent edit locally and the MC renders
# everywhere — the ops hub lives INSIDE the Cove, not beside it. Best-effort: NC
# down / not configured → we just show the seeds. NC WINS on an id/slug collision
# (the operator's copy overrides a seed of the same id).

NC_RUNBOOKS_DIR = "AgentSkills/Ops/runbooks"


async def _nc_runbooks(request: Request) -> dict:
    """{slug: data} for every *.json under the operator's NC AgentSkills/Ops/runbooks.
    Best-effort — any failure (no creds, NC down, bad JSON) yields {} / skips the file."""
    if request is None:
        return {}
    try:
        from src.dashboard.routes.nextcloud import get_nc_creds
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
    except Exception:
        return {}
    if not (nc_url and nc_user and nc_pass):
        return {}

    import httpx
    import xml.etree.ElementTree as ET
    from urllib.parse import quote, unquote

    base = f"{nc_url}/remote.php/dav/files/{nc_user}"
    folder_url = f"{base}/{quote(NC_RUNBOOKS_DIR, safe='/')}/"
    propfind = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>')
    out: dict = {}
    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=10) as client:
            resp = await client.request(
                "PROPFIND", folder_url,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=propfind)
            if resp.status_code != 207:
                return {}  # 404 = folder doesn't exist yet (fine), anything else = skip
            names = []
            for r in ET.fromstring(resp.text).findall(".//{DAV:}response"):
                href = (r.findtext("{DAV:}href") or "").rstrip("/")
                name = unquote(href.split("/")[-1])
                if name.lower().endswith(".json"):
                    names.append(name)
            for name in names:
                try:
                    fr = await client.get(f"{base}/{quote(NC_RUNBOOKS_DIR, safe='/')}/{quote(name)}")
                    if fr.status_code != 200:
                        continue
                    data = json.loads(fr.text)
                    if not isinstance(data, dict) or "steps" not in data:
                        continue
                    # Key by the FILE stem — the same identifier space as the seeds,
                    # so an NC file named like a seed (e.g. 01-update-cove.json)
                    # overrides it, and get_runbook(slug)/update_runbook stay
                    # consistent. A new name (e.g. 90-my-ops.json) just adds one.
                    slug = name[:-5]
                    data["source"] = "nc"
                    out[slug] = data
                except Exception:
                    continue
    except Exception:
        return {}
    return out


def _seed_runbooks() -> dict:
    """{slug: data} for the baked-in seed runbooks in RUNBOOKS_DIR."""
    out: dict = {}
    for f in sorted(RUNBOOKS_DIR.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except Exception:
            continue
    return out


def _merge_runbooks(seed: dict, nc: dict) -> dict:
    """Seed runbooks overlaid with the operator's NC runbooks — NC wins on an id
    collision (#D28). Pure."""
    merged = dict(seed)
    merged.update(nc)  # NC overrides same-slug seeds
    return merged


@router.get("/api/runbooks")
async def list_runbooks(request: Request):
    """List runbooks with metadata (no steps), scoped to the caller's role (CF-35).
    Merges the baked-in seeds with the operator's NC runbooks (NC wins, #D28)."""
    _ensure_dir()
    restricted = await _is_restricted_presence(request)
    merged = _merge_runbooks(_seed_runbooks(), await _nc_runbooks(request))
    runbooks = []
    for slug, data in merged.items():
        try:
            audience = _runbook_audience(data)
            if not _visible_to(audience, restricted):
                continue
            # Support both old format (order/name) and new format (num/title)
            num = data.get("num", data.get("order", 99))
            order = num if isinstance(num, int) else 99
            runbooks.append({
                "slug": slug,
                "order": order,
                "num": num,
                "name": data.get("title", data.get("name", slug)),
                "category": data.get("category", "general"),
                "audience": audience,
                "source": data.get("source", "seed"),
                "description": data.get("description", ""),
                "step_count": len(data.get("steps", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            continue
    runbooks.sort(key=lambda r: (r["order"], r["slug"]))
    return {"runbooks": runbooks}


@router.get("/api/runbooks/{slug}")
async def get_runbook(slug: str, request: Request):
    """Get a full runbook with all steps (role-scoped, CF-35). An operator's NC
    runbook of the same slug wins over the seed (#D28)."""
    _ensure_dir()
    # #D28: the operator's NC copy overrides a seed of the same slug.
    data = (await _nc_runbooks(request)).get(slug)
    if data is None:
        path = RUNBOOKS_DIR / f"{slug}.json"
        if not path.exists():
            return JSONResponse({"error": f"Runbook '{slug}' not found"}, status_code=404)
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    try:
        if not _visible_to(_runbook_audience(data), await _is_restricted_presence(request)):
            return JSONResponse(
                {"error": "Runbook not available for this role"}, status_code=403
            )
        data["slug"] = slug
        # Mirror the list endpoint: the UI reads rb.name, but the new runbook format stores
        # the display name under `title`. Without this the detail header renders "undefined".
        data["name"] = data.get("title", data.get("name", slug))
        return _fill_paths(data)
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
