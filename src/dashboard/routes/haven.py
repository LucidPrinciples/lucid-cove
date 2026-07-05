# =============================================================================
# haven.py — Haven management & display layer (repo-native, admin-facing).
# =============================================================================
# The Matrix plumbing + the form/nest/invite ACTIONS live in matrix_haven.py
# (/api/haven/create, /api/haven/{id}/nest, /api/haven/{id}/invite). The hub
# registrar (registry.py) owns the durable Haven records. THIS module is the
# read/aggregation layer the Cove Admin UI uses:
#
#   - GET /api/haven/mine   — does this Cove own a Haven? its name + public address.
#   - GET /api/haven/coves  — the member-Cove cards (this Cove + each connected Cove),
#                             with operator/Presences + an Open-Mission-Control link.
#
# Cards are sourced from the registrar (resolve_cove / resolve_haven), so they work
# for two Coves on one box WITH OR WITHOUT cross-homeserver Matrix federation proven
# yet — the Matrix nesting is the chat-federation layer on top, not a prerequisite
# for the Haven structure to render.
#
# Mounted BEFORE matrix_haven in app.py so the static paths /api/haven/mine and
# /api/haven/coves win over matrix_haven's /api/haven/{haven_id}.
# =============================================================================
import logging

from fastapi import APIRouter, Request, HTTPException

from src.env import env
from src.config import load_cove_config
from src.dashboard.routes.presence import get_current_presence
from src.dashboard.routes import registry_client

log = logging.getLogger(__name__)
router = APIRouter()


def _is_multi() -> bool:
    return (env("COVE_MODE") or "single").strip().lower() == "multi"


async def _require_admin(request: Request):
    """The Haven belongs to the Cove admin. Single-mode Coves are network-trusted
    (the one operator is the admin), so we don't hard-gate there."""
    p = await get_current_presence(request)
    if not _is_multi():
        return p  # network-trusted family Cove
    if not p:
        raise HTTPException(401, "Sign in to manage your Haven")
    if (p.get("cove_role") or "").strip().lower() != "admin":
        raise HTTPException(403, "Only the Cove admin can manage the Haven")
    return p


def _clean_domain(dom: str) -> str:
    return (dom or "").strip().lstrip("*").lstrip(".").lower()


def _haven_url() -> str:
    dom = _clean_domain(load_cove_config().get("domain") or "")
    return f"https://haven.{dom}" if dom else ""


async def _owned_haven() -> dict | None:
    """The Haven this Cove owns, from cove_haven (an operator owns at most one here)."""
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            r = await conn.execute("SELECT to_regclass('public.cove_haven') AS t")
            if not ((await r.fetchone()) or {}).get("t"):
                return None
            r = await conn.execute(
                "SELECT haven_id, name, space_id, commons_id FROM cove_haven ORDER BY created_at LIMIT 1")
            row = await r.fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.info("[haven] owned-haven lookup failed: %s", e)
        return None


async def _card_for_cove(cove_id: str, *, is_owner: bool,
                         local_name: str = "", local_domain: str = "") -> dict:
    """Build one Cove card. Registrar is the source of truth; falls back to local
    config for the owner's own Cove if the registrar is unreachable."""
    name, owner_handle, domain = local_name, "", _clean_domain(local_domain)
    if cove_id:
        info = await registry_client.resolve_cove(cove_id)
        if info.get("ok"):
            name = info.get("name") or name or cove_id
            owner_handle = (info.get("owner_handle") or "").lstrip("@")
            domain = _clean_domain(info.get("domain") or "") or domain
    name = name or cove_id or "This Cove"
    mc_url = f"https://{domain}" if domain else ""
    # "Just the Presences" — for now the operator/owner handle. Family Presences get
    # surfaced once each Cove publishes its roster to the registrar (follow-up).
    presences = [f"@{owner_handle}"] if owner_handle else []
    return {
        "cove_id": cove_id,
        "name": name,
        "operator": f"@{owner_handle}" if owner_handle else "—",
        "domain": domain,
        "mc_url": mc_url,
        "presences": presences,
        "is_owner": is_owner,
        "status": "registered" if (domain or owner_handle or is_owner) else "unknown",
    }


@router.get("/api/haven/mine")
async def haven_mine(request: Request):
    """Does this Cove own a Haven, and what's its public address?"""
    await _require_admin(request)
    cove = load_cove_config()
    h = await _owned_haven()
    if not h:
        return {"ok": True, "formed": False, "cove_name": cove.get("name") or ""}
    return {
        "ok": True, "formed": True,
        "haven_id": h["haven_id"], "name": h.get("name") or "",
        "url": _haven_url(), "space_id": h.get("space_id") or "",
    }


@router.get("/api/haven/coves")
async def haven_coves(request: Request):
    """Cards for the Coves in this Haven: this Cove first, then each connected Cove."""
    await _require_admin(request)
    cove = load_cove_config()
    cards = [await _card_for_cove(
        cove.get("id") or "", is_owner=True,
        local_name=cove.get("name") or "This Cove", local_domain=cove.get("domain") or "")]

    h = await _owned_haven()
    if not h:
        # batch-10 #4b — member-side ceremony: a Cove nested into someone else's Haven
        # doesn't own one, but should still SEE it. Ask the registrar which Haven this Cove
        # belongs to; if any, render a read-only "you're part of {Haven}" surface (no Manage).
        try:
            mem = await registry_client.resolve_cove_haven(cove.get("id") or "")
        except Exception:
            mem = {}
        if mem.get("ok") and mem.get("formed"):
            return {"ok": True, "formed": True, "member": True,
                    "haven": {"haven_id": (mem.get("haven") or {}).get("haven_id") or "",
                              "name": (mem.get("haven") or {}).get("name") or ""},
                    "coves": cards}
        return {"ok": True, "formed": False, "coves": cards}

    reg = await registry_client.resolve_haven(h["haven_id"])
    member_coves = (reg.get("member_coves") or []) if reg.get("ok") else []
    own_id = (cove.get("id") or "")
    for mc in member_coves:
        cid = mc.get("cove_id") or ""
        if not cid or cid == own_id:
            continue  # don't double-list the owner Cove
        cards.append(await _card_for_cove(cid, is_owner=False))

    return {
        "ok": True, "formed": True,
        "haven": {"haven_id": h["haven_id"], "name": h.get("name") or "", "url": _haven_url()},
        "coves": cards,
    }
