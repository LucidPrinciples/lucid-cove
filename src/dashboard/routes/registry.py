# =============================================================================
# registry.py — Hub network registrar (#133), the global source of truth.
# =============================================================================
# Replaces the hand-edited network.yaml. Runs on the registry MASTER (the shared
# app / hub — "App account = registry master"). Holds:
#   - global Cove-name uniqueness + @handle uniqueness (registry_coves/_handles)
#   - per-Cove federation facts (homeserver, space_id, mesh_ip) for resolution
#   - Haven records (space/commons ids + federated members + member Coves)
#   - canonical identity (#163): the @handle is durable; the Matrix account is a
#     per-Cove projection (matrix_user), so a handle survives a Cove moving hosts.
#
# AUTH MODEL (interim): reads are open within the mesh fleet (resolution needs the
# server_name + via to federate; private Spaces still require a Matrix invite to
# enter). Writes require LP_REGISTRY_SECRET (a dedicated hub secret the provisioner
# and fleet Coves carry). The middleware lets /api/registry/* through (PUBLIC_PREFIX)
# and the write endpoints enforce the secret themselves.
#   TODO (open-source / public registry): replace the shared write secret with the
#   operator's app-account token so any self-hoster can claim a name without the
#   fleet secret. Tracked under #133/#89.
# =============================================================================
import hmac
import json
import logging
import os
from src.env import env, env_bool

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter()


def _require_registry_secret(request: Request):
    secret = env("LP_REGISTRY_SECRET")
    header = request.headers.get("X-Registry-Secret", "")
    if not secret:
        raise HTTPException(501, "Registry master not configured (LP_REGISTRY_SECRET unset)")
    if not (header and hmac.compare_digest(header, secret)):
        raise HTTPException(403, "Invalid registry secret")


def _is_master() -> bool:
    return env_bool("LP_REGISTRY_MASTER")


def _hash_token(token: str) -> str:
    """Match the account auth_token storage scheme (sha256 of the raw token)."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


async def _authorize_write(request: Request, conn, owner_handle: str = "") -> dict:
    """Authorize a registry write. Two modes (#133/#89):

      - FLEET: `X-Registry-Secret` == LP_REGISTRY_SECRET. Full trust — the provisioner
        and founder fleet. May register any handle (e.g. seeding a hosted Cove's operator).
      - OPERATOR: `X-Operator-Token` = a self-hoster's app-account token (the raw token
        from their app.lucidcove.org account; hashed here to match accounts.auth_token).
        They may register ONLY their own handle (owner_handle must equal their username).
        This is what lets ANY self-hoster join the registry WITHOUT the fleet secret, and
        makes handle squatting impossible — only @you's token can claim @you.

    Returns {"mode": "fleet"} or {"mode": "operator", "account": row}. Raises 403 otherwise.
    """
    secret = env("LP_REGISTRY_SECRET")
    hdr = request.headers.get("X-Registry-Secret", "")
    if secret and hdr and hmac.compare_digest(hdr, secret):
        return {"mode": "fleet"}
    tok = (request.headers.get("X-Operator-Token", "") or "").strip()
    if tok:
        r = await conn.execute(
            "SELECT id, username FROM accounts WHERE auth_token = %s AND active = TRUE",
            (_hash_token(tok),))
        acct = await r.fetchone()
        if acct:
            uname = (acct.get("username") or "").lstrip("@").strip().lower()
            want = (owner_handle or "").lstrip("@").strip().lower()
            if want and want != uname:
                raise HTTPException(
                    403, f"Your account is @{uname} — you can only register that handle, not @{want}")
            return {"mode": "operator", "account": acct}
    raise HTTPException(403, "Registry write requires the fleet secret or a valid operator token")


# Brand / system Cove names no one may claim (anti-impersonation). Lowercased.
# Brand + system terms ONLY — NOT agent first names or roles (a real family may be
# surnamed "Stuart"; the Cove name is the family surname). The founder Cove "Cove" is
# here so it can't be squatted before it migrates (fleet/founder writes bypass this).
def _env_extra_reserved() -> set:
    """Deployment-specific reserved Cove/Haven names (e.g. a particular operator's
    Haven name), supplied by the operator via env so NO deployment-specific value is
    hardcoded in the open-source core. Comma-separated, lowercased. Empty by default."""
    raw = env("LP_EXTRA_RESERVED_COVE_NAMES", "") or ""
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


RESERVED_COVE_NAMES = {
    "lucid", "lucidcove", "lucid cove", "lucid principles", "lucidprinciples",
    "lucid tuner", "lucidtuner", "cove", "haven", "admin", "administrator",
    "system", "support", "official", "lucidprinciples official",
    # CF-119 (flip gate): brand + protocol names
    "lucidpath", "lucid path", "thelucidpath", "ltp", "lucidtunerprotocol",
    "drop", "thedrop", "founder",
    # system roles — load-bearing words in every Cove's UI
    "steward", "merchant", "agent", "operator", "moderator", "root",
    "security", "help", "api", "registry", "hub", "market",
    # the standard agent names (agent/handle namespaces meet in Matrix — the
    # run-3 register-collision class; a stranger claiming @stuart would shadow
    # every Cove's steward). Fleet/founder writes are trusted and unaffected.
    "stuart", "mercer", "atlas", "lt", "jules", "julian", "socrates",
    "archer", "archimedes", "arthur", "ezra", "gabe", "iris", "vera", "soren",
} | _env_extra_reserved()


# ── Availability (replaces the fake "Checking Haven availability" stub) ───────

@router.get("/api/registry/availability")
async def availability(request: Request, name: str = "", handle: str = ""):
    """Real global availability check for a Cove name and/or an @handle."""
    out = {"ok": True}
    from src.memory.database import get_db
    async with get_db() as conn:
        if name:
            n = name.strip().lower()
            # Cove name status: reserved (brand/system) | claimed (a live Cove owns it) |
            # available. (We deliberately DON'T check accounts.last_name — on the hub that's
            # app users' human surnames, and family Coves are legitimately named after
            # surnames, so that would block real names.)
            if n in RESERVED_COVE_NAMES:
                nstatus = "reserved"
            else:
                r = await conn.execute("SELECT 1 FROM registry_coves WHERE lower(name) = %s", (n,))
                nstatus = "claimed" if (await r.fetchone()) is not None else "available"
            out["name_status"] = nstatus
            out["name_available"] = (nstatus == "available")  # back-compat boolean
        if handle:
            h = handle.lstrip("@").lower()
            # CF-119: reserved handles report honestly here too — without this the
            # checker said "available" and signup then 409'd (lying UX).
            if h in RESERVED_COVE_NAMES:
                out["handle_status"] = "reserved"
                out["handle_available"] = False
                return out
            # Handle status (#4 — the @jag case):
            #   claimed            → already owned by a live Cove (registry_handles). Unavailable.
            #   account_unclaimed  → exists as an LP app-account username but NO Cove has claimed
            #                        it yet. The owner can "sign in to claim it for their Cove."
            #   available          → free to claim.
            # "claimed" = a LIVE Cove owns the handle (it has a cove_id). A bare reservation
            # (signup reserves the handle with NO cove_id, for anti-squat) is NOT a live Cove —
            # the OWNER can still claim it for their self-host Cove. Treating any registry_handles
            # row as "claimed" wrongly showed an account holder's own handle as "taken". (#211)
            rc = await conn.execute("SELECT cove_id FROM registry_handles WHERE lower(handle) = %s", (h,))
            reg_row = await rc.fetchone()
            has_live_cove = bool(reg_row) and bool((reg_row.get("cove_id") or "").strip())
            ra = await conn.execute(
                "SELECT 1 FROM accounts WHERE lower(username) = %s AND active = TRUE", (h,))
            in_accounts = (await ra.fetchone()) is not None
            if has_live_cove:
                hstatus = "claimed"
            elif in_accounts or reg_row is not None:
                hstatus = "account_unclaimed"
            else:
                hstatus = "available"
            out["handle_status"] = hstatus
            out["handle_available"] = (hstatus == "available")  # back-compat boolean
    return out


# Per-operator daily spark budget (in-memory: {account_id: (yyyymmdd, count)}). Cove
# creation is a handful of single-turn calls — the budget is a hard brake on a stuck
# or hostile client looping the endpoint, not a meter honest installs will ever see.
# In-memory is intentional: a hub restart forgiving the count is fine for a brake.
SPARK_DAILY_BUDGET = 40
_spark_usage: dict = {}


def _spark_budget_ok(account_id) -> bool:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    day, count = _spark_usage.get(account_id, (today, 0))
    if day != today:
        day, count = today, 0
    if count >= SPARK_DAILY_BUDGET:
        return False
    _spark_usage[account_id] = (day, count + 1)
    return True


@router.post("/api/registry/spark")
async def spark(request: Request):
    """The shared onboarding model — the spark. A registered Cove asks the hub to run a
    guided/onboarding completion (naming, wake, guided discovery) with LP's key, so a
    keyless stranger's agent can wake and the tour can run. The LP key lives ONLY on the
    hub, never in the repo or on the stranger's box. Auth: the operator's app-account
    token or the fleet secret. Creation-Flow inference only.

    THE SPARK BOUNDARY + PIN (2026-07-19): this endpoint runs LP's key, so it trusts
    nothing from the client. The model is PINNED to Kimi K2.5 via OpenRouter — the
    body's model_id is ignored (it used to be honored, and it used to default to the
    HUB's own Cove brain → provider mismatch → openrouter/auto → Opus 4.6 billed to
    the LP Cove Onboarding key). Requests are single short creation turns, capped in
    size and per-operator daily count. The Cove side additionally gates on creation
    state (spark.py spark_allowed); a modified Cove that lies still hits the budget."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    system_prompt = (body.get("system_prompt") or "").strip()
    messages = body.get("messages") or []
    if not system_prompt or not isinstance(messages, list) or not messages:
        raise HTTPException(400, "system_prompt and messages are required")
    # Abuse guard — creation calls are single-turn JSON generations, not conversations.
    from src.models.spark import spark_caps_ok, SPARK_MODEL_ID, SPARK_MODEL_STRING
    if not spark_caps_ok(system_prompt, messages):
        raise HTTPException(413, "spark request too large")

    # Gate to a valid operator token (or the fleet secret). Raises 403 otherwise.
    from src.memory.database import get_db
    async with get_db() as conn:
        _auth = await _authorize_write(request, conn)
    if _auth.get("mode") == "operator":
        _acct = _auth.get("account") or {}
        if not _spark_budget_ok(_acct.get("id")):
            raise HTTPException(429, "spark budget exhausted for today — connect your own intelligence to continue")

    # The dedicated onboarding key ONLY — never the hub's own OPENROUTER_API_KEY
    # (that fallback made hub-brain usage and onboarding usage indistinguishable).
    lp_key = (env("LP_GUIDED_OPENROUTER_KEY") or "").strip()
    if not lp_key:
        raise HTTPException(503, "spark not configured on the hub")

    model_id = SPARK_MODEL_ID
    try:
        temperature = float(body.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7

    import asyncio
    import re as _re
    from src.models.provider import (
        get_model_client, set_request_byok, clear_request_byok, _resolve_model_string,
    )
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    lc = [SystemMessage(content=system_prompt)]
    for m in messages:
        c = m.get("content", "")
        lc.append(AIMessage(content=c) if m.get("role") == "assistant" else HumanMessage(content=c))

    # Pin rides the BYOK context too — no resolution path can reroute off Kimi.
    tok = set_request_byok("openrouter", lp_key, model=SPARK_MODEL_STRING)
    try:
        client = get_model_client(model_id, temperature=temperature)
    finally:
        clear_request_byok(tok)

    try:
        resp = await asyncio.wait_for(client.ainvoke(lc), timeout=120)
    except asyncio.TimeoutError:
        raise HTTPException(504, "spark timed out")
    except Exception as e:
        raise HTTPException(502, f"spark inference failed: {type(e).__name__}")

    content = (resp.content or "").strip()
    content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
    provider, model_string = _resolve_model_string(model_id)
    return {"ok": True, "response": content, "model": model_string}


@router.post("/api/registry/claim-operator")
async def claim_operator(request: Request):
    """Open create-and-claim for a from-scratch self-host install (#133/#89).

    A stranger who clones the repo and runs the provisioner has no app account yet, so the
    FIRST wizard step collects their @handle + name + email and calls this to create the
    identity and mint an operator token the Cove stores. Same engine + uniqueness +
    reserved-brand guard as the public web signup (/api/account/create) — this just RETURNS
    the token (the web path emails it). So it's NOT a new abuse surface: open account
    creation already exists as the signup. A referral code is recorded if present but NOT
    required — direct GitHub installs are a first-class path. Runs on the registry master.
    """
    if not _is_master():
        raise HTTPException(400, "claim-operator runs on the registry master (hub) only")
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    handle = (body.get("handle") or "").lstrip("@").strip().lower()
    name = (body.get("name") or body.get("display_name") or "").strip()
    referred_by = (body.get("referred_by") or "").strip()
    # Email is OPTIONAL (#4). If given it must be valid; if blank, the operator token this
    # returns is the sole ownership proof (the wizard prompts "save it / add email later").
    if email and ("@" not in email or "." not in email):
        raise HTTPException(400, "If you provide an email, it must be a valid address")
    if len(handle) < 2 or len(handle) > 30 or not handle.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "Handle must be 2-30 characters: letters, numbers, hyphens, underscores")
    if handle in RESERVED_COVE_NAMES:
        raise HTTPException(409, "That handle is reserved.")

    import secrets as _secrets
    import uuid as _uuid
    raw_token = _secrets.token_urlsafe(32)
    pid = _uuid.uuid4()
    from src.memory.database import get_db
    async with get_db() as conn:
        # Global uniqueness: registry handles + existing app accounts (same as availability).
        r = await conn.execute(
            "SELECT 1 FROM registry_handles WHERE lower(handle) = %s "
            "UNION SELECT 1 FROM accounts WHERE lower(username) = %s AND active = TRUE",
            (handle, handle))
        if await r.fetchone():
            raise HTTPException(409, "That handle is already taken.")
        if email:
            r = await conn.execute("SELECT 1 FROM accounts WHERE email = %s", (email,))
            if await r.fetchone():
                # Don't silently fragment one person across two identities. Email is OPTIONAL
                # here (the @handle IS the identity), so guide them rather than hard-fail:
                # leave it blank, or connect this Cove to the existing account. The structured
                # `code` lets the wizard branch and surface the connect path. (#211)
                return JSONResponse(status_code=409, content={
                    "ok": False, "code": "email_exists",
                    "error": "That email already has a Lucid Principles account. Email is optional here — leave it blank to continue with your @handle, or connect this Cove to your existing account.",
                })
        referrer_id = None
        if referred_by:
            rr = await conn.execute(
                "SELECT id FROM accounts WHERE referral_code = %s AND active = TRUE",
                (referred_by.upper(),))
            row = await rr.fetchone()
            if row:
                referrer_id = row["id"]
        from src.dashboard.routes.account import _generate_referral_code
        referral_code = await _generate_referral_code(conn)
        await conn.execute(
            """INSERT INTO accounts (id, display_name, username, email, agent_name,
                                     last_name, tier, cove_role, auth_token, active,
                                     referred_by, referral_code)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)""",
            (pid, name or handle, handle, email or None, "", "", "free", "member",
             _hash_token(raw_token), referrer_id, referral_code))
        # Reserve the handle in the registry + record the affiliate edge (set-once) + store
        # the operator token hash as the ownership proof + heartbeat (#4/#161).
        try:
            await reserve_handle(conn, handle, referred_by=referred_by,
                                 owner_token_hash=_hash_token(raw_token))
        except Exception as e:
            log.warning("reserve_handle during claim-operator (non-fatal): %s", e)
    log.info("claim-operator: minted identity @%s (referred_by=%s)", handle, referred_by or "none")
    return {"ok": True, "handle": handle, "operator_token": raw_token}


@router.post("/api/registry/verify-claim")
async def verify_claim(request: Request):
    """Path B (#4 / #200): confirm a pasted 'connect key' (operator token) owns a given
    handle, so a self-hoster can connect an EXISTING Lucid Principles handle (@jag) to a new
    self-hosted Cove without minting a new identity. Open (the high-entropy token IS the
    proof) — distinct from the secret-gated server-to-server /verify-operator below.
    Returns {ok, handle} if the token's account username == handle."""
    if not _is_master():
        raise HTTPException(400, "verify-claim runs on the registry master (hub) only")
    body = await request.json()
    handle = (body.get("handle") or "").lstrip("@").strip().lower()
    token = (body.get("token") or "").strip()
    if not handle or not token:
        return {"ok": False, "reason": "handle and token are required"}
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT username FROM accounts WHERE auth_token = %s AND active = TRUE",
            (_hash_token(token),))
        acct = await r.fetchone()
    if acct and (acct.get("username") or "").lstrip("@").strip().lower() == handle:
        return {"ok": True, "handle": handle}
    return {"ok": False, "reason": "That connect key doesn't match this handle."}


# ── Handle reservation + the affiliate edge (#169) ───────────────────────────

async def _resolve_referrer_handle(conn, raw: str) -> str:
    """Resolve a captured referrer identifier to a canonical @handle. The live link
    format is an LP-XXXXXX referral CODE (cross-domain via /r/{code}); some callers
    pass a bare @handle. Codes win first (look up the owning account's username), else
    treat the value as a handle. Returns '' if it resolves to nothing."""
    raw = (raw or "").lstrip("@").strip()
    if not raw:
        return ""
    r = await conn.execute(
        "SELECT username FROM accounts WHERE referral_code = %s AND active = TRUE", (raw.upper(),))
    row = await r.fetchone()
    if row and row.get("username"):
        return row["username"].lower()
    return raw.lower()


async def _referrer_exists(conn, handle: str) -> bool:
    """A referrer is valid if they're a known account OR an existing registry handle
    (accounts covers referrers who signed up before they had a registry row)."""
    if not handle:
        return False
    r = await conn.execute("SELECT 1 FROM registry_handles WHERE handle = %s", (handle,))
    if await r.fetchone():
        return True
    r = await conn.execute("SELECT 1 FROM accounts WHERE lower(username) = %s AND active = TRUE", (handle,))
    return bool(await r.fetchone())


async def reserve_handle(conn, handle: str, *, cove_id: str = None,
                         matrix_user: str = None, referred_by: str = "",
                         owner_token_hash: str = None) -> str:
    """Insert/refresh a registry handle and set the affiliate edge SET-ONCE. Resolves
    referred_by (code or handle) → canonical handle and only records it if the referrer
    exists and isn't self. Used at Free/Operator signup (cove_id NULL) and Cove
    provisioning. Returns the stored referrer handle, or None."""
    handle = (handle or "").lstrip("@").strip().lower()
    if not handle:
        return None
    ref = await _resolve_referrer_handle(conn, referred_by)
    if ref == handle or not await _referrer_exists(conn, ref):
        ref = ""
    await conn.execute(
        """INSERT INTO registry_handles (handle, cove_id, matrix_user, referred_by,
                                         owner_token_hash, last_seen)
           VALUES (%s,%s,%s,%s,%s, NOW())
           ON CONFLICT (handle) DO UPDATE SET
             cove_id=COALESCE(EXCLUDED.cove_id, registry_handles.cove_id),
             matrix_user=COALESCE(EXCLUDED.matrix_user, registry_handles.matrix_user),
             referred_by=COALESCE(registry_handles.referred_by, EXCLUDED.referred_by),
             owner_token_hash=COALESCE(registry_handles.owner_token_hash, EXCLUDED.owner_token_hash),
             last_seen=NOW()""",
        (handle, cove_id, matrix_user, ref or None, owner_token_hash))
    return ref or None


# ── Cove registration + resolution ───────────────────────────────────────────

@router.post("/api/registry/cove")
async def register_cove(request: Request):
    """Register or update a Cove (idempotent on cove_id). Enforces global name +
    owner-handle uniqueness. Body: cove_id, name, owner_handle, domain, homeserver,
    space_id, mesh_ip, matrix_user."""
    body = await request.json()
    cid = (body.get("cove_id") or "").strip()
    name = (body.get("name") or "").strip()
    if not (cid and name):
        raise HTTPException(400, "cove_id and name are required")
    handle = (body.get("owner_handle") or "").lstrip("@").strip().lower()

    from src.memory.database import get_db
    async with get_db() as conn:
        # AUTH (#133/#89): fleet secret OR the operator's own app-account token. In
        # operator mode this also enforces that `handle` is the caller's own — so a
        # self-hoster can register without the fleet secret and nobody can claim a
        # handle that isn't theirs.
        auth = await _authorize_write(request, conn, handle)
        # name uniqueness (allow the same cove_id to keep its name)
        r = await conn.execute(
            "SELECT cove_id FROM registry_coves WHERE lower(name) = lower(%s) AND cove_id <> %s",
            (name, cid))
        if await r.fetchone():
            raise HTTPException(409, f"Cove name '{name}' is taken")
        # Public (operator) claims also can't take a reserved brand/system name. Fleet/
        # provisioner writes are trusted (they legitimately register founder coves).
        if auth["mode"] == "operator" and name.strip().lower() in RESERVED_COVE_NAMES:
            raise HTTPException(409, f"Cove name '{name}' is reserved")
        # handle uniqueness — taken if another Cove holds it in the registry, OR it's
        # another person's app-account username. Fleet writes may seed a handle whose
        # account lives on the Cove (not the hub), so the accounts check is operator-only;
        # operator mode already proved ownership in _authorize_write.
        if handle:
            # jules 07-07: a handle reserved with an EMPTY cove_id is the operator's OWN unclaimed
            # reservation (from claim-operator, before their Cove registered). Let them claim it for
            # their Cove — only a DIFFERENT, non-empty cove_id means genuinely taken. Operator-mode
            # already proved this handle is the caller's own in _authorize_write, and the accounts
            # check below still blocks another person's username. Without this, EVERY self-host Cove
            # 409'd on its own handle and never registered (so Haven nest could never resolve it).
            r = await conn.execute(
                "SELECT cove_id FROM registry_handles WHERE handle = %s "
                "AND COALESCE(cove_id,'') NOT IN ('', %s)",
                (handle, cid))
            if await r.fetchone():
                raise HTTPException(409, f"Handle '@{handle}' is taken")
            if auth["mode"] == "operator":
                _acct_id = (auth.get("account") or {}).get("id") or ""
                r = await conn.execute(
                    "SELECT 1 FROM accounts WHERE lower(username) = %s AND id <> %s AND active = TRUE",
                    (handle, _acct_id))
                if await r.fetchone():
                    raise HTTPException(409, f"Handle '@{handle}' belongs to another account")

        # Hijack / collision guard: never silently overwrite a cove_id that already exists
        # under a DIFFERENT owner. Matters at scale (millions of Coves) and blocks anyone
        # from brute-forcing or reusing someone else's structural id to seize their row.
        # Same-owner re-registration (rebuild / restore / domain change) still passes.
        r = await conn.execute(
            "SELECT owner_handle FROM registry_coves WHERE cove_id = %s", (cid,))
        _exist = await r.fetchone()
        if _exist:
            _eo = (_exist.get("owner_handle") or "").strip().lower()
            if _eo and handle and _eo != handle:
                raise HTTPException(
                    409, f"cove_id '{cid}' already belongs to @{_eo} — regenerate a new id")
        await conn.execute(
            """INSERT INTO registry_coves (cove_id, name, owner_handle, domain, homeserver, space_id, mesh_ip)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (cove_id) DO UPDATE SET
                 name=EXCLUDED.name,
                 owner_handle=COALESCE(NULLIF(EXCLUDED.owner_handle, ''), registry_coves.owner_handle),
                 domain=COALESCE(NULLIF(EXCLUDED.domain, ''), registry_coves.domain),
                 homeserver=COALESCE(NULLIF(EXCLUDED.homeserver, ''), registry_coves.homeserver),
                 space_id=COALESCE(EXCLUDED.space_id, registry_coves.space_id),
                 mesh_ip=COALESCE(NULLIF(EXCLUDED.mesh_ip, ''), registry_coves.mesh_ip),
                 updated_at=NOW()""",
            (cid, name, handle, body.get("domain", ""), body.get("homeserver", ""),
             body.get("space_id") or None, body.get("mesh_ip", "")))
        ref = None
        if handle:
            ref = await reserve_handle(
                conn, handle, cove_id=cid, matrix_user=body.get("matrix_user") or None,
                referred_by=body.get("referred_by", ""))
    return {"ok": True, "cove_id": cid, "name": name, "referred_by": ref}


@router.post("/api/registry/mesh-key")
async def mint_mesh_key(request: Request):
    """Mint a Headscale pre-auth join code for a self-host operator's device (#134).
    Runs on the HUB (where the Headscale API is reachable) so a self-host Cove never
    holds the Headscale API key — same control-plane pattern as acme-dns. Auth: the
    operator's own app-account token (X-Operator-Token) or the fleet secret. The hub
    must have HEADSCALE_API_URL + HEADSCALE_API_KEY set. Returns {ok, key, join_cmd}."""
    body = await request.json() if request.method == "POST" else {}
    from src.memory.database import get_db
    async with get_db() as conn:
        auth = await _authorize_write(request, conn)   # any valid operator token (or fleet)
        # Per-Cove mesh namespace: each Cove's devices register under their OWN Headscale
        # user (keyed by cove_id), so families stay isolated on the shared coordinator.
        mesh_user = ""
        if auth.get("mode") == "operator":
            uname = (auth.get("account", {}).get("username") or "").lstrip("@").strip().lower()
            if uname:
                r = await conn.execute(
                    "SELECT cove_id FROM registry_handles WHERE lower(handle) = %s", (uname,))
                row = await r.fetchone()
                cid = ((row or {}).get("cove_id") or "").strip() if row else ""
                mesh_user = cid or uname   # Cove namespace, else fall back to the handle
        else:  # fleet: the provisioner/founder may target a specific Cove
            mesh_user = (body.get("cove_id") or body.get("user") or "").strip()
        mesh_user = mesh_user or "lucid"   # back-compat default
    try:
        from provision.mesh import create_preauth_key
    except Exception as e:
        raise HTTPException(501, f"mesh tooling unavailable: {e}")
    res = create_preauth_key(user=mesh_user, expiry=(body.get("expiry") or "1h"))
    if not res.get("ok"):
        raise HTTPException(502, res.get("reason") or "could not mint a join code")
    return res


@router.post("/api/registry/approve-device")
async def approve_device(request: Request):
    """Approve a pending Tailscale device registration (the app's /register/<key> flow) for the
    requesting Cove's OWN mesh user. Runs on the HUB (Headscale API access); same auth +
    per-Cove namespace as /api/registry/mesh-key. Body: {key}. Returns {ok, node}."""
    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "registration code required")
    from src.memory.database import get_db
    async with get_db() as conn:
        auth = await _authorize_write(request, conn)
        mesh_user = ""
        if auth.get("mode") == "operator":
            uname = (auth.get("account", {}).get("username") or "").lstrip("@").strip().lower()
            if uname:
                r = await conn.execute(
                    "SELECT cove_id FROM registry_handles WHERE lower(handle) = %s", (uname,))
                row = await r.fetchone()
                cid = ((row or {}).get("cove_id") or "").strip() if row else ""
                mesh_user = cid or uname
        else:
            mesh_user = (body.get("cove_id") or body.get("user") or "").strip()
        mesh_user = mesh_user or "lucid"
    try:
        from provision.mesh import approve_node
    except Exception as e:
        raise HTTPException(501, f"mesh tooling unavailable: {e}")
    res = approve_node(key, user=mesh_user)
    if not res.get("ok"):
        raise HTTPException(502, res.get("reason") or "could not approve the device")
    return res


@router.post("/api/registry/verify-operator")
async def verify_operator(request: Request):
    """Server-to-server: validate an operator's app-account token → their @handle.
    Called by the hub Market (Socrates) to authorize a REGISTERED Cove to browse the
    catalog without sharing the fleet secret with member Coves (#200). Gated by the
    inter-service secret (only trusted services call this). Body: {token} → {ok, handle}."""
    secret = env("SHARED_CONTAINER_SECRET") or env("LP_REGISTRY_SECRET")
    hdr = request.headers.get("X-Shared-Secret", "")
    if not (secret and hdr and hmac.compare_digest(hdr, secret)):
        raise HTTPException(403, "service secret required")
    body = await request.json()
    tok = (body.get("token") or "").strip()
    if not tok:
        return {"ok": False}
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT id, username, email, display_name FROM accounts "
            "WHERE auth_token = %s AND active = TRUE",
            (_hash_token(tok),))
        row = await r.fetchone()
    if row:
        # account_id/email/name: additive (CF-87) — lets the Market seller path
        # upsert the customers row for a token-verified handle without a second call.
        return {"ok": True, "handle": (row.get("username") or "").lstrip("@"),
                "account_id": str(row.get("id") or ""),
                "email": row.get("email"), "name": row.get("display_name")}
    return {"ok": False}


@router.post("/api/registry/acme-credential")
async def mint_acme_credential(request: Request):
    """Mint an acme-dns credential + _acme-challenge CNAME for a self-host operator's
    lucidcove.org subdomain (#208 path #2). Hub-side, so the operator's box never holds
    our Cloudflare token and acme-dns /register stays private. Auth: the operator's own
    app-account token (X-Operator-Token) or the fleet secret. Body: {sub_domain}.
    Returns the acme-dns credential the Cove's bundled Caddy uses for DNS-01."""
    body = await request.json()
    sub = (body.get("sub_domain") or "").strip().lower().lstrip("*").lstrip(".")
    if not sub:
        raise HTTPException(400, "sub_domain required")
    from src.memory.database import get_db
    async with get_db() as conn:
        await _authorize_write(request, conn)   # operator token or fleet secret
    try:
        from provision.acmedns import provision_subdomain_cert_delegation
    except Exception as e:
        raise HTTPException(501, f"acme-dns tooling unavailable: {e}")
    res = provision_subdomain_cert_delegation(sub)
    if not isinstance(res, dict) or not res.get("ok"):
        raise HTTPException(502, (res or {}).get("reason") or "acme-dns provisioning failed")
    return res


@router.post("/api/registry/cove-dns")
async def mint_cove_dns(request: Request):
    """Create the cove + *.cove A records for a self-host operator's lucidcove.org
    subdomain → their box IP (DNS tier 1, zero-DNS). Hub-side (our zone + token),
    operator-token gated, so the user never touches DNS. lucidcove.org only.
    Body: {domain, ip} → {ok, ip, actions}."""
    body = await request.json()
    sub = (body.get("domain") or "").strip().lower().lstrip("*").lstrip(".")
    ip = (body.get("ip") or "").strip()
    if not (sub and ip):
        raise HTTPException(400, "domain and ip required")
    if not (sub == "lucidcove.org" or sub.endswith(".lucidcove.org")):
        raise HTTPException(400, "this endpoint manages lucidcove.org subdomains only")
    from src.memory.database import get_db
    async with get_db() as conn:
        await _authorize_write(request, conn)   # operator token or fleet secret
    try:
        from provision.cloudflare_dns import ensure_cove_dns
    except Exception as e:
        raise HTTPException(501, f"DNS tooling unavailable: {e}")
    try:
        return ensure_cove_dns(sub, ip)   # uses the hub's CLOUDFLARE_API_TOKEN
    except Exception as e:
        raise HTTPException(502, f"DNS create failed: {str(e)[:160]}")


@router.post("/api/registry/cove-dns/remove")
async def remove_cove_dns_endpoint(request: Request):
    """Deprovision mirror of /api/registry/cove-dns: delete apex + wildcard
    (+ _acme-challenge) records for a lucidcove.org Cove subdomain. Idempotent.
    Body: {domain} → {ok, domain, actions}. Does NOT touch registry rows — pair
    with DELETE /api/registry/cove/{key} for full cleanup."""
    body = await request.json()
    sub = (body.get("domain") or "").strip().lower().lstrip("*").lstrip(".")
    if not sub:
        raise HTTPException(400, "domain required")
    if sub == "lucidcove.org" or not sub.endswith(".lucidcove.org"):
        raise HTTPException(400, "this endpoint removes lucidcove.org Cove subdomains only")
    from src.memory.database import get_db
    async with get_db() as conn:
        await _authorize_write(request, conn)
    try:
        from provision.cloudflare_dns import remove_cove_dns
    except Exception as e:
        raise HTTPException(501, f"DNS tooling unavailable: {e}")
    try:
        return remove_cove_dns(sub)
    except ValueError as e:
        raise HTTPException(400, str(e)[:200])
    except Exception as e:
        raise HTTPException(502, f"DNS remove failed: {str(e)[:160]}")


@router.delete("/api/registry/cove/{key}")
async def delete_cove(key: str, request: Request):
    """Unregister a Cove from the hub registry and best-effort remove its DNS.

    key = cove_id OR name. Removes the registry_coves row (handles get cove_id
    SET NULL via FK). If the row had a lucidcove.org domain, calls remove_cove_dns
    so throwaway/test coves don't strand Cloudflare records. DNS failure does not
    block the registry delete (reported under dns.ok=false). Auth: fleet secret
    or operator token; operator mode may only delete their own Cove."""
    key = (key or "").strip()
    if not key:
        raise HTTPException(400, "cove key required")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT * FROM registry_coves WHERE cove_id = %s OR lower(name) = lower(%s)",
            (key, key))
        row = await r.fetchone()
        if not row:
            raise HTTPException(404, f"No Cove '{key}' in the registry")
        owner = (row.get("owner_handle") or "").lstrip("@").strip().lower()
        auth = await _authorize_write(request, conn, owner)
        if auth.get("mode") == "operator":
            uname = ((auth.get("account") or {}).get("username") or "").lstrip("@").strip().lower()
            if owner and uname and owner != uname:
                raise HTTPException(403, f"Only @{owner} (or fleet) can delete this Cove")
        cid = row.get("cove_id")
        domain = (row.get("domain") or "").strip().lower().rstrip(".")
        # Detach handles first so a partial failure never leaves a live cove_id
        # pointing at a deleted Cove; FK is ON DELETE SET NULL but explicit is clearer.
        await conn.execute(
            "UPDATE registry_handles SET cove_id = NULL WHERE cove_id = %s", (cid,))
        await conn.execute("DELETE FROM registry_coves WHERE cove_id = %s", (cid,))

    dns_result = {"ok": True, "skipped": True, "reason": "no lucidcove.org domain on row"}
    if domain and domain.endswith(".lucidcove.org") and domain != "lucidcove.org":
        try:
            from provision.cloudflare_dns import remove_cove_dns
            dns_result = remove_cove_dns(domain)
        except Exception as e:
            log.warning("delete_cove DNS remove failed for %s: %s", domain, e)
            dns_result = {"ok": False, "domain": domain, "reason": str(e)[:200]}
    return {
        "ok": True,
        "cove_id": cid,
        "name": row.get("name"),
        "domain": domain,
        "dns": dns_result,
    }


@router.get("/api/registry/resolve/cove/{key}")
async def resolve_cove(key: str):
    """Resolve a Cove by id OR name → its federation facts (homeserver, space_id,
    domain, mesh_ip). 'via' = the homeserver, used as the m.space.child via."""
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT * FROM registry_coves WHERE cove_id = %s OR lower(name) = lower(%s)", (key, key))
        row = await r.fetchone()
    if not row:
        raise HTTPException(404, f"No Cove '{key}' in the registry")
    d = dict(row)
    d["via"] = d.get("homeserver") or ""
    return d


@router.get("/api/registry/resolve/handle/{handle}")
async def resolve_handle(handle: str):
    """Canonical-identity resolution (#163): a global @handle → its Cove + Matrix
    projection. The durable thing is the handle; the matrix_user changes if the Cove
    moves hosts. Resolves to a federated `@handle:homeserver` for a Cove member, or a
    shared-app operator on the local Matrix server. Lets Connect invite by bare @handle."""
    h = handle.lstrip("@").lower()

    # Registry data lives on the hub (master); a Cove proxies the lookup.
    if not _is_master():
        base = env("LP_REGISTRY_URL").rstrip("/")
        if not base:
            raise HTTPException(501, "Registry not reachable (LP_REGISTRY_URL unset)")
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                rr = await client.get(f"{base}/api/registry/resolve/handle/{h}")
            if rr.status_code == 200:
                return rr.json()
            raise HTTPException(rr.status_code, f"No handle '@{h}' in the registry")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Registry unreachable: {str(e)[:100]}")

    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            """SELECT rh.handle, rh.cove_id, rh.matrix_user, rc.homeserver, rc.name AS cove_name
               FROM registry_handles rh LEFT JOIN registry_coves rc ON rc.cove_id = rh.cove_id
               WHERE rh.handle = %s""", (h,))
        row = await r.fetchone()
        if not row:
            raise HTTPException(404, f"No handle '@{h}' in the registry")
        d = dict(row)
        # Cove member → federated id on their Cove's homeserver.
        if not d.get("matrix_user") and d.get("homeserver"):
            d["matrix_user"] = f"@{h}:{d['homeserver']}"
        # Shared-app operator (no Cove) → their Matrix account on THIS (the hub's) server.
        if not d.get("matrix_user"):
            sn = env("MATRIX_SERVER_NAME")
            ar = await conn.execute(
                "SELECT matrix_username FROM accounts WHERE lower(username) = %s", (h,))
            arow = await ar.fetchone()
            mu = (arow or {}).get("matrix_username")
            if mu and sn:
                d["matrix_user"] = f"@{mu}:{sn}"
    return d


# ── Haven records ────────────────────────────────────────────────────────────

@router.post("/api/registry/haven")
async def upsert_haven(request: Request):
    """Create/update a Haven record (idempotent on haven_id). Body: haven_id, name,
    owner_handle, space_id, commons_id, members[], member_coves[].

    AUTH (#133/#89): fleet secret OR the operator's own app-account token. In operator
    mode the owner_handle must be the caller's own handle — so a self-hoster forms a
    Haven WITHOUT the fleet secret, and nobody can register a Haven under another's handle."""
    body = await request.json()
    hid = (body.get("haven_id") or "").strip()
    name = (body.get("name") or "").strip()
    if not (hid and name):
        raise HTTPException(400, "haven_id and name are required")
    owner_handle = (body.get("owner_handle") or "").lstrip("@").strip().lower()
    members = json.dumps(body.get("members") or [])
    member_coves = json.dumps(body.get("member_coves") or [])
    from src.memory.database import get_db
    async with get_db() as conn:
        auth = await _authorize_write(request, conn, owner_handle)
        r = await conn.execute(
            "SELECT haven_id FROM registry_havens WHERE lower(name)=lower(%s) AND haven_id<>%s", (name, hid))
        if await r.fetchone():
            raise HTTPException(409, f"Haven name '{name}' is taken")
        if auth["mode"] == "operator" and name.strip().lower() in RESERVED_COVE_NAMES:
            raise HTTPException(409, f"Haven name '{name}' is reserved")
        await conn.execute(
            """INSERT INTO registry_havens (haven_id, name, owner_handle, space_id, commons_id, members, member_coves)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
               ON CONFLICT (haven_id) DO UPDATE SET
                 name=EXCLUDED.name, owner_handle=EXCLUDED.owner_handle,
                 space_id=COALESCE(EXCLUDED.space_id, registry_havens.space_id),
                 commons_id=COALESCE(EXCLUDED.commons_id, registry_havens.commons_id),
                 members=EXCLUDED.members, member_coves=EXCLUDED.member_coves, updated_at=NOW()""",
            (hid, name, owner_handle,
             body.get("space_id") or None, body.get("commons_id") or None, members, member_coves))
    return {"ok": True, "haven_id": hid}


@router.get("/api/registry/resolve/haven/{haven_id}")
async def resolve_haven(haven_id: str):
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute("SELECT * FROM registry_havens WHERE haven_id = %s", (haven_id,))
        row = await r.fetchone()
    if not row:
        raise HTTPException(404, f"No Haven '{haven_id}' in the registry")
    return dict(row)


@router.get("/api/registry/resolve/cove-haven/{cove_id}")
async def resolve_cove_haven(cove_id: str):
    """batch-10 #4b: which Haven (if any) is this Cove a MEMBER of? Powers the member-side
    ceremony — a Cove nested into someone else's Haven should SEE it. Membership lives in
    registry_havens.member_coves (a JSONB array of {cove_id,...}); a `@>` containment match
    finds the owning Haven without a back-reference column."""
    cid = (cove_id or "").strip()
    if not cid:
        return {"ok": True, "formed": False}
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT haven_id, name FROM registry_havens "
            "WHERE member_coves @> %s::jsonb ORDER BY created_at LIMIT 1",
            (json.dumps([{"cove_id": cid}]),))
        row = await r.fetchone()
    if not row:
        return {"ok": True, "formed": False}
    return {"ok": True, "formed": True, "member": True,
            "haven": {"haven_id": row["haven_id"], "name": row.get("name") or ""}}


@router.post("/api/registry/haven/{haven_id}/member")
async def add_haven_member(haven_id: str, request: Request):
    """Add a member to a Haven: a federated @handle and/or a member Cove (cove_id,
    space_id, homeserver). Idempotent. The Haven owner's Cove then nests + invites.

    AUTH: fleet secret OR the Haven owner's own operator token (only the owner may add
    members to their Haven)."""
    body = await request.json()
    handle = (body.get("handle") or "").lstrip("@").strip()
    cove = body.get("cove")  # {cove_id, space_id, homeserver}
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT owner_handle, members, member_coves FROM registry_havens WHERE haven_id = %s", (haven_id,))
        row = await r.fetchone()
        if not row:
            raise HTTPException(404, f"No Haven '{haven_id}'")
        await _authorize_write(request, conn, (row.get("owner_handle") or ""))
        members = row["members"] if isinstance(row["members"], list) else (row["members"] or [])
        member_coves = row["member_coves"] if isinstance(row["member_coves"], list) else (row["member_coves"] or [])
        if handle and handle not in members:
            members.append(handle)
        if cove and cove.get("cove_id") and not any(c.get("cove_id") == cove["cove_id"] for c in member_coves):
            member_coves.append(cove)
        await conn.execute(
            "UPDATE registry_havens SET members=%s::jsonb, member_coves=%s::jsonb, updated_at=NOW() WHERE haven_id=%s",
            (json.dumps(members), json.dumps(member_coves), haven_id))
    return {"ok": True, "members": members, "member_coves": member_coves}
