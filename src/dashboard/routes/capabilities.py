"""capabilities.py — the unified Capability catalog read (#190).

One Capability object, two lenses. The Action Board (Flows/Tools tabs = type views) and
the Market lens both read THIS endpoint, so they render from a single source. Each item
carries a DERIVED card status computed for the current viewer (their tier + what they own
+ what they're building), which drives the card shading. No new status column on the hub —
the six states derive from kind + publish status + ownership + tier.

Spec: CLAUDE SKILLS/LP-Vault/Reference/capability-system-spec.md

Degrades cleanly: where the hub marketplace isn't wired (single-mode Coves until connected),
returns {"capabilities": [], "available": false} instead of erroring.
"""
import logging

from fastapi import APIRouter, Request

from src.dashboard.routes.presence import get_current_presence
from src.dashboard.routes import market as _market

router = APIRouter()
logger = logging.getLogger(__name__)

# Which Action Board tab a capability type renders under. Agent stations live on the
# tabs; products / services / mirrors / themes are Market-only until acquired, then they
# fulfill onto the right board (see _fulfillment).
_FLOW_TYPES = {"flow"}
_TOOL_TYPES = {"tool", "skill", "persona"}

# What a capability BECOMES when acquired (drives where a purchase lands).
_FULFILLMENT = {
    "tool": "install", "skill": "install", "persona": "install", "flow": "install",
    "mirror": "install", "theme": "install",
    "service": "appointment", "appointment": "appointment", "call": "appointment", "booking": "appointment",
    "download": "deliver", "access": "deliver", "external": "deliver", "book": "deliver", "file": "deliver",
    "hire": "hire",
}


def _fulfillment(ptype: str) -> str:
    return _FULFILLMENT.get(ptype, "deliver")


def _tab_for(ptype: str):
    """Action Board tab for a type, or None if it's Market-only (products/services)."""
    if ptype in _FLOW_TYPES:
        return "flows"
    if ptype in _TOOL_TYPES:
        return "tools"
    return None


_TIER_LEVEL = {"free": 0, "pro": 5, "operator": 10, "presence": 20, "cove": 30}


def _tier_level(tier: str) -> int:
    return _TIER_LEVEL.get((tier or "free").lower(), 0)


def derive_status(*, kind: str, publish_status: str, requires_agent: bool,
                  owned: bool, is_mine: bool, viewer_has_agent: bool) -> str:
    """The six card states, derived. Order matters (most-specific first).

    owned/active(installed) > your draft(building) > wanted gap > community draft(building)
    > first-party not-yet-live(coming_soon) > needs an agent > for sale(available).
    """
    if owned:
        return "active"                       # you hold it — installed
    if is_mine and publish_status == "active":
        return "active"                       # your own published listing
    if publish_status == "building":
        return "building"                     # claimed WIP — visible to all as "Building · @handle"
    if is_mine and publish_status == "draft":
        return "building"                     # your private draft, in progress
    if kind == "wanted" and publish_status == "wanted":
        return "wanted"                       # open gap — claimable "Build this"
    if kind == "first_party" and publish_status != "active":
        return "coming_soon"                  # LP building its anchor
    if requires_agent and not viewer_has_agent:
        return "needs_agent"                  # Operator can't run it yet
    if publish_status == "active":
        return "available"                    # for sale, you don't own it
    return "coming_soon"


def _to_capability(l: dict, *, owned: bool, is_mine: bool, viewer_has_agent: bool) -> dict:
    ptype = l.get("product_type") or "tool"
    status = derive_status(
        kind=l.get("kind") or "community",
        publish_status=l.get("status") or "active",
        requires_agent=bool(l.get("requires_agent")),
        owned=owned,
        is_mine=is_mine,
        viewer_has_agent=viewer_has_agent,
    )
    tiers = l.get("tiers", []) or []
    billing_note = next((t.get("billing_note") for t in tiers if t.get("billing_note")), None)
    # C3 — a purchase surfaces as an ACTIVE TOOL CARD: when owned, carry the "use it"
    # action + a pointer to the posted per-use rates (the display-only estimate, C5). No
    # wallet. GPU pre-wires the rent-gpu Use panel; other owned tools use their delivery link.
    use_url = None
    if owned:
        if ptype == "gpu":
            use_url = "/static/action-board/rent-gpu.html?embedded=1#use"
        elif l.get("link_url"):
            use_url = l.get("link_url")
    return {
        "id": l.get("id"), "slug": l.get("slug"), "title": l.get("title"),
        "promise": l.get("description"), "type": ptype, "category": l.get("category"),
        "status": status, "agent_owner": l.get("agent_owner"),
        "requires_agent": bool(l.get("requires_agent")), "build_flow": l.get("build_flow"),
        "tuned_safe": bool(l.get("tuned_safe")), "image_url": l.get("image_url"),
        "tiers": tiers, "billing_note": billing_note,
        "seller_handle": l.get("seller_handle"), "seller": l.get("seller"),
        "is_mine": is_mine, "owned": owned,
        "wanted": bool(l.get("wanted")) or (l.get("kind") == "wanted"),
        "issue_url": l.get("issue_url"), "spec_ref": l.get("spec_ref"),
        "fulfillment": _fulfillment(ptype),
        "tab": _tab_for(ptype),
        # Active-card extras (present when owned): the use action + the posted-rates pointer
        # the card reads to show a per-use estimate. Display only — never a balance gate.
        "use_url": use_url,
        "rates_ref": ("/api/market/rates" if owned else None),
    }


@router.get("/api/capabilities")
async def list_capabilities(request: Request):
    """Unified capability list with a per-viewer derived status. Both the Action Board and
    the Market lens consume this. Inert (empty) where the hub marketplace isn't configured."""
    presence = await get_current_presence(request)
    viewer_has_agent = _tier_level((presence or {}).get("tier")) >= 20

    try:
        catalog = (await _market._socrates("GET", "/api/marketplace/listings")).get("listings", [])
    except Exception:
        return {"capabilities": [], "available": False}

    owned_ids: set = set()
    mine_by_id: dict = {}
    if presence:
        try:
            _, cid, _ = await _market._current_presence_and_customer(request)
            lib = await _market._socrates("GET", "/api/marketplace/library", params={"buyer_id": cid})
            owned_ids = {it.get("listing_id") for it in lib.get("items", [])}
            mine = await _market._socrates("GET", "/api/marketplace/mine", params={"seller_id": cid})
            mine_by_id = {m.get("id"): m for m in mine.get("listings", [])}
        except Exception:
            pass  # identity/marketplace hiccup — still return the public catalog

    caps = []
    seen = set()
    for l in catalog:
        lid = l.get("id")
        seen.add(lid)
        caps.append(_to_capability(
            l, owned=lid in owned_ids, is_mine=lid in mine_by_id,
            viewer_has_agent=viewer_has_agent))
    # the viewer's own drafts/claimed items aren't in the public catalog — add them
    for lid, m in mine_by_id.items():
        if lid in seen:
            continue
        caps.append(_to_capability(
            m, owned=lid in owned_ids, is_mine=True, viewer_has_agent=viewer_has_agent))

    return {"capabilities": caps, "available": True}
