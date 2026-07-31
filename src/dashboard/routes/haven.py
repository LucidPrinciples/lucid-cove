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
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

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


# =============================================================================
# Haven Traffic — Umami stats proxy (Phase 2)
# Spec: umami-analytics-and-haven-stats.md
# GET /api/haven/stats/sites → today / 7d / 30d pageviews + visitors per site.
# Browser never sees UMAMI_API_KEY. Failures are explicit, never silent zeros.
# =============================================================================

_STATS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_STATS_TTL_SEC = 60.0

# v1 hard-code fallback when HAVEN_STATS_SITES env is empty (brand properties only).
_DEFAULT_STATS_SITES: list[dict[str, str]] = [
    {
        "name": "Lucid Principles",
        "domain": "lucidprinciples.com",
        "umami_website_id": "14603d08-8822-4283-a6fd-65268c089cb6",
    },
    {
        "name": "Lucid Tuner",
        "domain": "lucidtuner.com",
        "umami_website_id": "f80bbce8-68ae-4444-b71f-04d0c6b54a03",
    },
    {
        "name": "Lucid Cove",
        "domain": "lucidcove.org",
        "umami_website_id": "f0cf3dbf-c7bb-4f14-aeee-ebca5c971ba4",
    },
    {
        "name": "Chords of Truth",
        "domain": "chordsoftruth.com",
        "umami_website_id": "68fdf839-7185-485d-a876-1d08d470842f",
    },
]


def _stats_tz() -> ZoneInfo:
    try:
        return ZoneInfo(env("APP_TIMEZONE") or "America/New_York")
    except Exception:
        return ZoneInfo("America/New_York")


def _parse_stats_sites() -> list[dict[str, str]]:
    raw = (env("HAVEN_STATS_SITES") or "").strip()
    if not raw:
        return list(_DEFAULT_STATS_SITES)
    try:
        data = json.loads(raw)
    except Exception as e:
        log.warning("[haven-stats] HAVEN_STATS_SITES JSON invalid: %s", e)
        return list(_DEFAULT_STATS_SITES)
    if not isinstance(data, list):
        return list(_DEFAULT_STATS_SITES)
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        domain = (item.get("domain") or "").strip().lower()
        wid = (
            item.get("umami_website_id")
            or item.get("website_id")
            or item.get("id")
            or ""
        ).strip()
        name = (item.get("name") or domain or "Site").strip()
        if domain and wid:
            out.append({"name": name, "domain": domain, "umami_website_id": wid})
    return out or list(_DEFAULT_STATS_SITES)


def _umami_base() -> str:
    internal = (env("UMAMI_INTERNAL_URL") or "").strip().rstrip("/")
    if internal:
        return internal
    public = (env("UMAMI_PUBLIC_URL") or "").strip().rstrip("/")
    return public


def _range_ms(days: int, *, tz: ZoneInfo) -> tuple[int, int]:
    """Inclusive local-day window: start of (today - (days-1)) → now."""
    now = datetime.now(tz)
    start = (now - timedelta(days=max(days, 1) - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(start.timestamp() * 1000), int(now.timestamp() * 1000)


def _metric_value(blob: Any) -> int | None:
    if blob is None:
        return None
    if isinstance(blob, (int, float)):
        return int(blob)
    if isinstance(blob, dict):
        for k in ("value", "current", "x", "y"):
            if k in blob and isinstance(blob[k], (int, float)):
                return int(blob[k])
    return None


async def _umami_website_stats(
    client: httpx.AsyncClient,
    *,
    base: str,
    website_id: str,
    start_at: int,
    end_at: int,
    headers: dict[str, str],
) -> dict[str, Any]:
    url = f"{base}/api/websites/{website_id}/stats"
    try:
        resp = await client.get(
            url,
            params={"startAt": start_at, "endAt": end_at},
            headers=headers,
        )
    except Exception as e:
        return {"ok": False, "error": f"unreachable: {str(e)[:120]}"}
    if resp.status_code == 401 or resp.status_code == 403:
        return {"ok": False, "error": f"auth_failed ({resp.status_code})"}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"umami {resp.status_code}: {(resp.text or '')[:120]}",
        }
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        return {"ok": False, "error": "invalid_json"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "unexpected_shape"}
    pageviews = _metric_value(data.get("pageviews"))
    visitors = _metric_value(data.get("visitors"))
    # Some builds nest under visits
    if visitors is None:
        visitors = _metric_value(data.get("visits"))
    return {
        "ok": True,
        "pageviews": pageviews if pageviews is not None else 0,
        "visitors": visitors if visitors is not None else 0,
        "raw_keys": sorted(data.keys())[:12],
    }


async def _fetch_site_windows(
    client: httpx.AsyncClient,
    *,
    base: str,
    site: dict[str, str],
    headers: dict[str, str],
    tz: ZoneInfo,
) -> dict[str, Any]:
    wid = site["umami_website_id"]
    windows = {
        "today": _range_ms(1, tz=tz),
        "d7": _range_ms(7, tz=tz),
        "d30": _range_ms(30, tz=tz),
    }
    results = await asyncio.gather(
        *[
            _umami_website_stats(
                client,
                base=base,
                website_id=wid,
                start_at=start,
                end_at=end,
                headers=headers,
            )
            for start, end in windows.values()
        ]
    )
    labeled = dict(zip(windows.keys(), results))
    errors = [f"{k}:{v.get('error')}" for k, v in labeled.items() if not v.get("ok")]
    if len(errors) == 3:
        return {
            "name": site["name"],
            "domain": site["domain"],
            "umami_website_id": wid,
            "ok": False,
            "error": errors[0].split(":", 1)[-1],
        }

    def pick(window: str, field: str) -> int | None:
        block = labeled[window]
        if not block.get("ok"):
            return None
        return int(block.get(field) or 0)

    return {
        "name": site["name"],
        "domain": site["domain"],
        "umami_website_id": wid,
        "ok": True,
        # Compact keys the Haven Traffic card already understands
        "today": pick("today", "pageviews"),
        "d7": pick("d7", "pageviews"),
        "d30": pick("d30", "pageviews"),
        "pageviews_today": pick("today", "pageviews"),
        "pageviews_7d": pick("d7", "pageviews"),
        "pageviews_30d": pick("d30", "pageviews"),
        "visitors_today": pick("today", "visitors"),
        "visitors_7d": pick("d7", "visitors"),
        "visitors_30d": pick("d30", "visitors"),
        "partial_errors": errors,
    }


@router.get("/api/haven/stats/sites")
async def haven_stats_sites(request: Request):
    """Server-side Umami proxy for Haven Traffic cards."""
    await _require_admin(request)

    now = time.time()
    cached = _STATS_CACHE.get("payload")
    if cached is not None and (now - float(_STATS_CACHE.get("ts") or 0)) < _STATS_TTL_SEC:
        out = dict(cached)
        out["cached"] = True
        return out

    sites = _parse_stats_sites()
    base = _umami_base()
    api_key = (env("UMAMI_API_KEY") or "").strip()
    public_url = (env("UMAMI_PUBLIC_URL") or "").strip().rstrip("/") or base

    if not base:
        payload = {
            "ok": False,
            "error": "umami_not_configured",
            "detail": "Set UMAMI_INTERNAL_URL or UMAMI_PUBLIC_URL",
            "umami_public_url": public_url,
            "sites": [
                {
                    "name": s["name"],
                    "domain": s["domain"],
                    "umami_website_id": s["umami_website_id"],
                    "ok": False,
                    "error": "umami_not_configured",
                }
                for s in sites
            ],
            "updated_at": datetime.now(_stats_tz()).isoformat(),
            "cached": False,
        }
        return payload

    if not api_key:
        payload = {
            "ok": False,
            "error": "umami_api_key_missing",
            "detail": "Create an API key in Umami Settings → API and set UMAMI_API_KEY",
            "umami_public_url": public_url,
            "sites": [
                {
                    "name": s["name"],
                    "domain": s["domain"],
                    "umami_website_id": s["umami_website_id"],
                    "ok": False,
                    "error": "umami_api_key_missing",
                }
                for s in sites
            ],
            "updated_at": datetime.now(_stats_tz()).isoformat(),
            "cached": False,
        }
        return payload

    headers = {
        "Accept": "application/json",
        "User-Agent": "LucidCove-HavenStats/1.0",
        # Umami accepts Bearer API keys; some builds also read x-umami-api-key.
        "Authorization": f"Bearer {api_key}",
        "x-umami-api-key": api_key,
    }
    tz = _stats_tz()

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            site_rows = await asyncio.gather(
                *[
                    _fetch_site_windows(
                        client, base=base, site=s, headers=headers, tz=tz
                    )
                    for s in sites
                ]
            )
    except Exception as e:
        log.warning("[haven-stats] batch failed: %s", e)
        payload = {
            "ok": False,
            "error": "umami_unreachable",
            "detail": str(e)[:160],
            "umami_public_url": public_url,
            "sites": [
                {
                    "name": s["name"],
                    "domain": s["domain"],
                    "umami_website_id": s["umami_website_id"],
                    "ok": False,
                    "error": "umami_unreachable",
                }
                for s in sites
            ],
            "updated_at": datetime.now(tz).isoformat(),
            "cached": False,
        }
        return payload

    any_ok = any(r.get("ok") for r in site_rows)
    payload = {
        "ok": any_ok,
        "error": None if any_ok else "all_sites_failed",
        "umami_public_url": public_url,
        "sites": list(site_rows),
        "updated_at": datetime.now(tz).isoformat(),
        "cached": False,
        "ttl_seconds": int(_STATS_TTL_SEC),
    }
    _STATS_CACHE["ts"] = now
    _STATS_CACHE["payload"] = payload
    return payload
