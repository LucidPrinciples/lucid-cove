# =============================================================================
# matrix.py — Matrix SSO token endpoint (#137)
# =============================================================================
# Mints a Matrix access token for the CURRENT operator so Connect opens
# authenticated. Two modes:
#
#   multi  (shared/Operator tier, many operators per container):
#          per-operator identity on the account (accounts.matrix_username /
#          matrix_password). If the operator has none yet, AUTO-PROVISION a
#          Matrix account on the hub (MATRIX_HUB_URL) via shared-secret
#          registration (MATRIX_REG_SECRET) and store the creds. So a brand-new
#          paying operator's Chat works on first open — no manual setup.
#
#   single (a Cove Presence like Atlas, one operator per container):
#          one fixed operator login from env (MATRIX_HOMESERVER /
#          MATRIX_OPERATOR_USER / MATRIX_OPERATOR_PASSWORD).
#
# Server-side only; the password never reaches the browser. Gated by
# OperatorAuthMiddleware + the operator session.
# =============================================================================
import hashlib
import hmac
import logging
import os
from src.env import env
import re
import secrets as _secrets

import httpx
from fastapi import APIRouter, Request, HTTPException

from src.dashboard.routes.presence import get_current_presence

log = logging.getLogger(__name__)
router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")


def _is_local_url(url: str) -> bool:
    """True for URLs a Matrix CLIENT on another device can never reach: localhost,
    loopback, docker-internal hosts, or bare container names (no dot in the host)."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host:
        return True
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"):
        return True
    return "." not in host  # bare container name (e.g. {cid}-dendrite)


def _client_homeserver(request) -> str:
    """CF-93: the homeserver URL for the CALLING browser, host-aware (same rule as
    the Open-MC door: build on the host you're reaching the Cove through NOW).
    Reached over the claimed domain → https://matrix.{domain} (DNS proven for this
    client). Reached over localhost/mesh-IP/NAT → the provision-stamped
    MATRIX_PUBLIC_URL (pre-claim that's http://localhost:{matrix_port}, which is
    exactly what works on-box; hosted stamps are real URLs). Never the internal
    compose hostname."""
    try:
        from src.config import load_cove_config
        domain = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".")
    except Exception:
        domain = ""
    req_host = ((request.url.hostname if request else "") or "").lower()
    if domain and (req_host == domain.lower() or req_host.endswith("." + domain.lower())):
        return f"https://matrix.{domain}"
    pub = (env("MATRIX_PUBLIC_URL") or "").strip().rstrip("/")
    if pub:
        return pub
    return _public_homeserver()


def _public_homeserver() -> str:
    """The browser/client-reachable homeserver URL for the Settings creds block.
    The LIVE claimed domain wins — env values were stamped at provision time and go
    stale after an address claim/change; on a fresh local Cove they fall back to a
    useless localhost value (the Settings 'localhost:8009' leak). A non-local
    MATRIX_PUBLIC_URL is the fallback (hosted). Returns '' when there is nothing a
    client could actually reach — the caller shows a set-your-address state instead."""
    try:
        from src.config import load_cove_config
        domain = (load_cove_config().get("domain") or "").strip()
    except Exception:
        domain = ""
    if domain:
        return f"https://matrix.{domain}"
    pub = (env("MATRIX_PUBLIC_URL") or "").strip().rstrip("/")
    if pub and not _is_local_url(pub):
        return pub
    return ""


async def _try_login(homeserver: str, user: str, pw: str) -> dict:
    """Attempt a Matrix password login WITHOUT raising, so callers can branch on the
    errcode: M_FORBIDDEN → the stored creds are stale (self-heal by re-registering),
    M_LIMIT_EXCEEDED → Dendrite rate-limited us (back off, do NOT re-register — that
    compounds the storm that burned run-3). Returns
      {ok: True, data: {...}} | {ok: False, status, errcode, body, unreachable?}."""
    hs = (homeserver or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(hs + "/_matrix/client/v3/login", json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": user},
                "password": pw,
            })
    except Exception as e:
        return {"ok": False, "status": 502, "errcode": "M_UNREACHABLE",
                "body": str(e)[:120], "unreachable": True}
    if resp.status_code == 200:
        data = resp.json()
        return {"ok": True, "data": {
            "homeserver": hs,
            "user_id": data.get("user_id"),
            "access_token": data.get("access_token"),
            "device_id": data.get("device_id"),
        }}
    errcode, body = "", ""
    try:
        body = resp.text[:160]
        errcode = (resp.json() or {}).get("errcode", "") or ""
    except Exception:
        pass
    print(f"[matrix] login failed user={user} status={resp.status_code} errcode={errcode} body={body}")
    return {"ok": False, "status": resp.status_code, "errcode": errcode, "body": body}


async def _login(homeserver: str, user: str, pw: str) -> dict:
    """Raising wrapper over _try_login (single-mode + callers that don't self-heal)."""
    r = await _try_login(homeserver, user, pw)
    if r.get("ok"):
        return r["data"]
    if r.get("unreachable"):
        raise HTTPException(502, "Matrix homeserver unreachable: %s" % r.get("body"))
    raise HTTPException(502, f"Matrix login failed ({r.get('status')}): {r.get('body')}")


def _matrix_localpart(presence: dict) -> str:
    """Derive a valid Matrix localpart from the operator's handle/username."""
    base = (presence.get("username") or presence.get("display_name") or "").lower()
    base = re.sub(r"[^a-z0-9._=\-/]", "", base)
    if not base:
        base = "op" + str(presence.get("id", "")).replace("-", "")[:10]
    return base[:64]


async def register_matrix_account(localpart: str, *, admin: bool = False) -> tuple[str, str]:
    """Register a Matrix account on THIS Cove's homeserver via shared-secret
    (nonce + HMAC). Works on Dendrite (Synapse-compatible endpoint). Returns
    (username, password). Raises HTTPException on failure. Shared by operator
    auto-provisioning and the steward identity (matrix_spaces)."""
    hs = (env("MATRIX_HUB_URL") or "").rstrip("/")
    secret = env("MATRIX_REG_SECRET")
    if not (hs and secret):
        raise HTTPException(501, "Matrix provisioning not configured")

    user = localpart
    pw = _secrets.token_urlsafe(24)

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            nr = await client.get(hs + "/_synapse/admin/v1/register")
            nonce = (nr.json() or {}).get("nonce")
        except Exception as e:
            raise HTTPException(502, "Matrix nonce failed: %s" % str(e)[:100])
        if not nonce:
            raise HTTPException(502, "Matrix registration nonce missing")

        # Synapse/Dendrite shared-secret MAC: nonce\0user\0password\0(not)admin
        mac = hmac.new(secret.encode(), digestmod=hashlib.sha1)
        for part in (nonce, user, pw):
            mac.update(part.encode()); mac.update(b"\x00")
        mac.update(b"admin" if admin else b"notadmin")

        rr = await client.post(hs + "/_synapse/admin/v1/register", json={
            "nonce": nonce, "username": user, "password": pw,
            "admin": admin, "mac": mac.hexdigest(),
        })
    if rr.status_code != 200:
        raise HTTPException(502, "Matrix registration failed: %s" % rr.text[:140])
    return user, pw


async def _provision_operator_matrix(presence: dict) -> tuple[str, str]:
    """Register the operator's per-Cove Matrix account. Returns (username, password)."""
    return await register_matrix_account(_matrix_localpart(presence))


def _kick_space_ensure(handle: str) -> None:
    """Fire-and-forget Cove Space ensure/invite.

    Quietgrove Connect hang: invite_presence_to_cove_space ran ON the token request
    path. Exceptions were non-fatal, but a slow/stuck Dendrite call still held the
    HTTP response open — the browser sat on "Connecting…" with no stage signal.
    Token must return as soon as login succeeds; Space builds in the background.
    """
    if not handle:
        return
    import asyncio

    async def _run():
        t0 = __import__("time").monotonic()
        try:
            from src.dashboard.routes.matrix_spaces import invite_presence_to_cove_space
            await invite_presence_to_cove_space(handle)
            log.info(
                "matrix space ensure ok handle=%s elapsed=%.2fs",
                handle, __import__("time").monotonic() - t0,
            )
        except Exception as e:
            log.warning(
                "ensure/invite Cove Space (background, non-fatal) handle=%s elapsed=%.2fs: %s",
                handle, __import__("time").monotonic() - t0, e,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        # No running loop (tests / sync callers) — best-effort skip; Connect still opens.
        log.debug("matrix space ensure skipped (no event loop) handle=%s", handle)


@router.get("/api/matrix/token")
async def matrix_token(request: Request):
    """Return a fresh Matrix access token for the current operator (SSO for Connect)."""
    import time as _time
    t0 = _time.monotonic()
    if COVE_MODE == "multi":
        presence = await get_current_presence(request)
        if not presence:
            raise HTTPException(401, "Sign in to use Connect")
        hs = env("MATRIX_HUB_URL")
        if not hs:
            raise HTTPException(501, "Matrix not configured")

        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT matrix_username, matrix_password FROM accounts WHERE id = %s",
                (presence["id"],),
            )
            row = await r.fetchone()
        user = (row or {}).get("matrix_username")
        pw = (row or {}).get("matrix_password")

        async def _persist_creds(u: str, p: str) -> None:
            # DURABLE + isolated: the creds write gets its OWN connection scope so it
            # commits regardless of any later login/space step failing. Run-3: the app
            # held stale creds while Dendrite's account was gone, and the write was
            # entangled with steps that raised — so the corrected creds never stuck.
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE accounts SET matrix_username = %s, matrix_password = %s WHERE id = %s",
                    (u, p, presence["id"]),
                )

        if not (user and pw):
            # First Chat use → auto-provision this operator's Matrix account.
            user, pw = await _provision_operator_matrix(presence)
            await _persist_creds(user, pw)
            log.info("matrix token provisioned operator elapsed=%.2fs user=%s",
                     _time.monotonic() - t0, user)

        login = await _try_login(hs, user, pw)
        if not login.get("ok"):
            ec = (login.get("errcode") or "").upper()
            if ec == "M_LIMIT_EXCEEDED":
                # Dendrite rate-limited the login — re-registering would only add load.
                # Tell the client to back off (connect.js honors the 429 with a cooldown).
                log.info("matrix token rate-limited elapsed=%.2fs user=%s",
                         _time.monotonic() - t0, user)
                raise HTTPException(429, "Connect is warming up (rate limited) — retry in a few seconds.")
            if ec in ("M_FORBIDDEN", "M_USER_DEACTIVATED", "M_UNKNOWN"):
                # Stored creds no longer valid against the LIVE homeserver. Run-3 mystery:
                # a register 200'd but the account never landed / the DB was regenerated
                # under it, so login = M_FORBIDDEN while the app kept the old creds. Heal
                # ONCE: re-register a fresh password, persist DURABLY, retry the login.
                try:
                    user, pw = await register_matrix_account(user)
                    await _persist_creds(user, pw)
                    login = await _try_login(hs, user, pw)
                except HTTPException as re:
                    # Re-register itself failed (e.g. the localpart really does exist and
                    # isn't ours to reset) — surface the original login failure, clearly.
                    log.warning("matrix creds self-heal re-register failed: %s", re.detail)
            if not login.get("ok"):
                if login.get("unreachable"):
                    raise HTTPException(502, "Matrix homeserver unreachable: %s" % login.get("body"))
                raise HTTPException(502, f"Matrix login failed ({login.get('status')}): {login.get('body')}")
        result = login["data"]
        # Split-horizon: cove-core logs in over the internal compose hostname
        # (MATRIX_HUB_URL, e.g. http://dendrite:8008), but the browser's matrix-js-sdk
        # must sync against a URL THIS client can reach. CF-93: host-aware — on the
        # claimed domain → https://matrix.{domain}; on localhost/mesh-IP → the
        # provision-stamped local URL (the env stamp goes stale after a claim).
        result["homeserver"] = (_client_homeserver(request) or hs).rstrip("/")
        # Space ensure is BACKGROUND — never hold the token response on Dendrite room
        # create / invite (Connect hang root cause on Quietgrove first open).
        _kick_space_ensure(user)
        log.info(
            "matrix token ok mode=multi elapsed=%.2fs user=%s hs=%s",
            _time.monotonic() - t0, user, result.get("homeserver"),
        )
        return result

    # Single mode — one fixed operator from env (a Cove Presence like Atlas).
    hs = env("MATRIX_HOMESERVER")
    user = env("MATRIX_OPERATOR_USER")
    pw = env("MATRIX_OPERATOR_PASSWORD")
    if not (hs and user and pw):
        raise HTTPException(501, "Matrix not configured for this Presence")
    result = await _login(hs, user, pw)
    # Browser must reach the homeserver; env MATRIX_HOMESERVER can be docker-internal.
    result["homeserver"] = (_client_homeserver(request) or result.get("homeserver") or hs).rstrip("/")
    # Same background Space path as multi (single-mode Cove installs still use Connect).
    localpart = (user or "").lstrip("@").split(":")[0]
    _kick_space_ensure(localpart)
    log.info(
        "matrix token ok mode=single elapsed=%.2fs user=%s hs=%s",
        _time.monotonic() - t0, user, result.get("homeserver"),
    )
    return result


@router.get("/api/matrix/credentials")
async def matrix_credentials(request: Request):
    """The current operator's Matrix login (homeserver + username + password), to show
    in Settings like the Nextcloud creds — so they can connect Element or any Matrix
    client to their Cove chat. Read-only; the account is provisioned on first Connect open."""
    if COVE_MODE != "multi":
        hs = _public_homeserver()
        if not hs:
            _env_hs = (env("MATRIX_HOMESERVER") or "").strip().rstrip("/")
            hs = _env_hs if (_env_hs and not _is_local_url(_env_hs)) else ""
        user = env("MATRIX_OPERATOR_USER")
        if not user:
            return {"provisioned": False}
        if not hs:
            return {"provisioned": True, "homeserver": None, "username": user,
                    "password": None,
                    "note": "Your Matrix address activates once you set your Cove address."}
        return {"provisioned": True, "homeserver": hs, "username": user,
                "password": None, "note": "Set in this Cove's environment."}

    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in first")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT matrix_username, matrix_password FROM accounts WHERE id = %s",
            (presence["id"],),
        )
        row = await r.fetchone()
    user = (row or {}).get("matrix_username")
    pw = (row or {}).get("matrix_password")
    if not (user and pw):
        # Not set up yet — opening Connect once provisions it.
        return {"provisioned": False}
    # Never hand a Matrix client a localhost/in-network URL (the fresh-Cove
    # MATRIX_PUBLIC_URL-unset → MATRIX_HUB_URL fallback leaked http://localhost:8009).
    hs = _public_homeserver()
    if not hs:
        return {"provisioned": True, "homeserver": None, "username": user, "password": pw,
                "note": "Your Matrix address activates once you set your Cove address."}
    return {"provisioned": True, "homeserver": hs, "username": user, "password": pw}
