# =============================================================================
# registry_client.py — talk to the Hub network registrar (#133) from any Cove.
# =============================================================================
# Thin async client over the registrar API. A Cove points at the hub via
# LP_REGISTRY_URL (e.g. https://app.lucidcove.org) and authenticates writes with
# LP_REGISTRY_SECRET (X-Registry-Secret header). All calls are best-effort and
# return {ok: False, reason} instead of raising, so registry hiccups never block
# the local flow (a Cove still boots if the hub is briefly down).
# =============================================================================
import logging
import os
from src.env import env

import httpx

log = logging.getLogger(__name__)


def _base() -> str:
    return (env("LP_REGISTRY_URL") or "").rstrip("/")


def _secret() -> str:
    return env("LP_REGISTRY_SECRET")


def _operator_token() -> str:
    """The self-hoster's operator token for registry writes. Env wins (provisioned Coves
    bake LP_OPERATOR_TOKEN), but a from-scratch install MINTS its token at runtime (the
    wizard's claim-operator step) and persists it to cove.yaml — read that as the fallback
    so the freshly-claimed token authorizes the cove-name reservation without a restart."""
    t = (env("LP_OPERATOR_TOKEN") or "").strip()
    if t:
        return t
    try:
        from src.config import load_cove_config
        return (load_cove_config().get("operator_token") or "").strip()
    except Exception:
        return ""


def configured() -> bool:
    return bool(_base())


async def _req(method: str, path: str, *, body: dict = None, auth: bool = False,
               timeout: float = 15.0) -> dict:
    base = _base()
    if not base:
        return {"ok": False, "reason": "LP_REGISTRY_URL not set"}
    # Cloudflare fronting the hub blocks default library User-Agents (python-urllib/httpx
    # → 403 "error 1010"), so always present a real UA on Cove→hub calls.
    headers = {"Content-Type": "application/json", "User-Agent": "LucidCove-Cove/1.0"}
    if auth:
        # Fleet/provisioner coves carry LP_REGISTRY_SECRET; a self-hoster authenticates
        # registry writes with their own app-account token (LP_OPERATOR_TOKEN) instead —
        # the hub maps it to their account and only lets them claim their own handle
        # (#133/#89). No fleet secret needed for a public self-host.
        if _secret():
            headers["X-Registry-Secret"] = _secret()
        elif _operator_token():
            headers["X-Operator-Token"] = _operator_token()
        else:
            return {"ok": False, "reason": "no registry auth (set LP_REGISTRY_SECRET or LP_OPERATOR_TOKEN)"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, base + path, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            # Pass through any STRUCTURED error fields the hub returned (e.g. code/error),
            # not just `detail` — so callers can branch (e.g. #211 email_exists → connect).
            out = dict(data) if isinstance(data, dict) else {}
            out["ok"] = False
            out["status"] = resp.status_code
            if not out.get("reason"):
                out["reason"] = ((data.get("detail") if isinstance(data, dict) else None)
                                 or (resp.text[:160] if resp.text else "request failed"))
            return out
        if isinstance(data, dict):
            data.setdefault("ok", True)
            return data
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "reason": f"registry unreachable: {str(e)[:120]}"}


async def check_availability(name: str = "", handle: str = "") -> dict:
    params = {}
    if name:
        params["name"] = name
    if handle:
        params["handle"] = handle.lstrip("@")
    qs = ("?" + str(httpx.QueryParams(params))) if params else ""
    return await _req("GET", "/api/registry/availability" + qs)


async def claim_operator(*, handle: str, name: str = "", email: str = "",
                         referred_by: str = "") -> dict:
    """Create-and-mint a brand-new operator identity on the hub (from-scratch install).
    Open endpoint (no prior token) — returns {ok, handle, operator_token}."""
    return await _req("POST", "/api/registry/claim-operator", body={
        "handle": handle.lstrip("@"), "name": name, "email": email,
        "referred_by": referred_by})


async def verify_operator(*, handle: str, token: str) -> dict:
    """Path B (#4): verify a pasted connect key (operator token) owns a handle on the hub."""
    return await _req("POST", "/api/registry/verify-claim", body={
        "handle": handle.lstrip("@"), "token": token})


async def register_cove(*, cove_id, name, owner_handle="", domain="", homeserver="",
                        space_id="", mesh_ip="", matrix_user="", referred_by="") -> dict:
    return await _req("POST", "/api/registry/cove", auth=True, body={
        "cove_id": cove_id, "name": name, "owner_handle": owner_handle, "domain": domain,
        "homeserver": homeserver, "space_id": space_id, "mesh_ip": mesh_ip,
        "matrix_user": matrix_user, "referred_by": referred_by})


async def spark_complete(*, system_prompt: str, messages: list, model_id: str = "kimi-k2.5",
                         temperature: float = 0.7, flow_id: str = None,
                         timeout: float = 110.0) -> dict:
    """Ask the hub to run a guided/onboarding completion with LP's key (the spark).

    The stranger path: the Cove holds no model key, so the hub runs the inference and
    returns the text. Authenticated by the operator token; the LP key never leaves the
    hub. Returns {ok, response, model} or {ok: False, reason}."""
    return await _req("POST", "/api/registry/spark", auth=True, timeout=timeout, body={
        "system_prompt": system_prompt, "messages": messages,
        "model_id": model_id, "temperature": temperature, "flow_id": flow_id})


async def resolve_cove(key: str) -> dict:
    return await _req("GET", f"/api/registry/resolve/cove/{key}")


async def resolve_handle(handle: str) -> dict:
    return await _req("GET", f"/api/registry/resolve/handle/{handle.lstrip('@')}")


async def upsert_haven(*, haven_id, name, owner_handle="", space_id="", commons_id="",
                       members=None, member_coves=None) -> dict:
    return await _req("POST", "/api/registry/haven", auth=True, body={
        "haven_id": haven_id, "name": name, "owner_handle": owner_handle,
        "space_id": space_id, "commons_id": commons_id,
        "members": members or [], "member_coves": member_coves or []})


async def resolve_haven(haven_id: str) -> dict:
    return await _req("GET", f"/api/registry/resolve/haven/{haven_id}")


async def resolve_cove_haven(cove_id: str) -> dict:
    """Which Haven this Cove is a member of (batch-10 #4b). {ok, formed, member, haven}."""
    return await _req("GET", f"/api/registry/resolve/cove-haven/{cove_id}")


async def add_haven_member(haven_id: str, *, handle: str = "", cove: dict = None) -> dict:
    return await _req("POST", f"/api/registry/haven/{haven_id}/member", auth=True,
                      body={"handle": handle, "cove": cove or {}})
