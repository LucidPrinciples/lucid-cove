"""
onboarding.py — first-run items that live in the home approvals area.

These are persistent cards (not a dismissable modal): they sit in the same
"primary driving spot" the operator will later use to approve agent activity,
and they stay until dealt with — first login or whenever.

  - add_intelligence : connect a model (BYOK key, or local Ollama). Clears once a
    model provider is set on the presence (then it lives in Settings).
  - jules_intro      : explain voice capture. Clears when acknowledged.
"""

import json
import os
from src.env import env_bool

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _is_public_app() -> bool:
    """The shared multi-tenant app (the registry master) has no per-user agents —
    its members are Tuner/Operator. Agent-driven first-run cards (add intelligence,
    jules, device mesh) belong to a Cove, not here. A Cove is never the master."""
    return env_bool("LP_REGISTRY_MASTER")


def _agent_config(p: dict) -> dict:
    ac = (p or {}).get("agent_config") or {}
    if isinstance(ac, str):
        try:
            ac = json.loads(ac) or {}
        except Exception:
            ac = {}
    return ac if isinstance(ac, dict) else {}


@router.get("/api/onboarding/items")
async def onboarding_items(request: Request):
    """The first-run cards still pending for the current operator."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return {"items": []}
    # Agent onboarding belongs to a Cove, not the shared public app (#leak).
    if _is_public_app():
        return {"items": []}
    ac = _agent_config(p)
    is_admin = (p.get("cove_role") or "") == "admin"

    # JOINER GATE: anyone who came in through an invite (their account was consumed by a
    # presence_invites row) never SET UP this Cove — the founder did. The cove-config
    # checklist (add intelligence / address / compute / mobile / backup) is founder-only;
    # a joiner (member OR admin-invitee) can't even complete those (the Cove is already
    # configured), so the nags are pure confusion. Suppress them — a joiner's orientation
    # is the agent's welcome in Chat (the spark), not owner setup cards.
    try:
        import uuid as _uuid
        from src.memory.database import get_db as _get_db
        async with _get_db() as _conn:
            _r = await _conn.execute(
                "SELECT 1 FROM presence_invites WHERE consumed_by = %s LIMIT 1",
                (_uuid.UUID(str(p["id"])),))
            if await _r.fetchone():
                return {"steps": [], "items": [], "done_count": 0,
                        "total": 0, "complete": True}
    except Exception:
        pass

    _cove_id = ""
    try:
        from src.config import load_cove_config
        _cc = load_cove_config()
        _domain_set = bool((_cc.get("domain") or "").strip())
        # The suggested lucidcove.org subdomain should be the chosen Cove NAME (set in the
        # wizard at finalize), not the random from-scratch stack id (cove-a7aa). Fall back
        # to the id only if the name isn't set yet.
        _cove_name = (_cc.get("name") or os.environ.get("COVE_NAME") or "").strip()
        _cove_slug = _cove_name.lower().replace(" ", "-")
        if not _cove_slug or _cove_slug == "new-cove":
            _cove_slug = (os.environ.get("COVE_ID") or _cc.get("id") or "").strip().lower().replace(" ", "-")
        _cove_id = _cove_slug
    except Exception:
        _domain_set = True  # fail safe: don't nag if we can't read config

    # ── The first-run checklist: 3 dependency-GATED steps ────────────────────
    # Each step unlocks only when the prior is done. The panel shows the rest LOCKED.
    #   1. Address  — the keystone (drives every URL + HTTPS so voice/mic work).
    #   2. Intelligence — the model/API that activates Agents + Tools (incl. jules).
    #   3. Device + jules — get the Cove on your phone (walk-around capture).
    # A member (non-admin) doesn't set the Cove address — it's already done for them.
    # jules 07-07 / reinstall 2230: a self-host claim SAVES the domain but isn't LIVE until
    # the host command runs (or operator attests via address-live). Keep the step OPEN so
    # the command stays reachable instead of collapsing on save.
    # Default: if a pending_host_command is still stored, treat as NOT live even when the
    # domain_live key is missing/stale. Only default-true when there is no pending command
    # (existing Coves without the marker stay done).
    try:
        _pending_cmd = (_cc.get("pending_host_command") or "").strip()
        if "domain_live" in _cc:
            _addr_live = bool(_cc.get("domain_live"))
        else:
            _addr_live = not bool(_pending_cmd)
        # Pending command always wins over a true flag left by a partial write.
        if _pending_cmd:
            _addr_live = False
    except Exception:
        _addr_live = True
    address_done = (_domain_set and _addr_live) or (not is_admin)
    # "Done" only on an EXPLICIT operator choice (Ollama or a key) or a real key —
    # not a provisioner default provider string (which would falsely show connected).
    intel_done = bool(ac.get("intelligence_configured") or (ac.get("model_api_key") or "").strip())
    device_done = bool(ac.get("onboarding_mesh_ack"))

    # Compute establishment (#12): surface what THIS box can run BEFORE the model
    # choice, so the operator understands their options. GPU sizing comes from the
    # cheap detect path (cove.yaml recorded value, then a live nvidia-smi) — NOT the
    # full provider probe (the Add-Intelligence card runs that client-side). Ack'd
    # once seen; connecting a model also settles it (you can't pick a model blind).
    _gpu = {"present": False}
    try:
        from src.models.machine_probe import gpu_from_config, detect_gpu
        _gpu = gpu_from_config()
        if not _gpu.get("present"):
            _gpu = detect_gpu()
    except Exception:
        pass
    # Compute is done when acknowledged (the operator's chooser acks) OR when the
    # Cove has a hand-set compute.llm mode (CF-94 grandfather: established Coves
    # predate this card). ONLY llm counts as a choice: the provisioner stamps
    # voice.mode="local" into EVERY fresh cove.yaml (shipping default) AND
    # video_asr.mode (local-if-GPU-else-cloud — detection, not a choice), so both
    # pre-checked this card on every stranger install (run-3 find; the recurring
    # "GPU auto-set"). Nothing provisioner-side writes llm.mode.
    _compute_configured = False
    try:
        from src.config import load_cove_config
        _csec = (load_cove_config().get("compute") or {})
        _llm = _csec.get("llm")
        _compute_configured = isinstance(_llm, dict) and bool((_llm.get("mode") or "").strip())
    except Exception:
        pass
    compute_done = bool(ac.get("onboarding_compute_ack")) or _compute_configured
    if _gpu.get("present"):
        _gb = round((_gpu.get("vram_mb") or 0) / 1024) if _gpu.get("vram_mb") else None
        _gname = (_gpu.get("name") or "").strip()
        _name_part = f" — {_gname}" if _gname else ""
        _vram_part = f" (~{_gb}GB)" if _gb else ""
        _compute_body = (f"This machine has a GPU{_name_part}{_vram_part}. Your agents can run "
                         f"models locally on it. Pick a local model in the next step, or bring a "
                         f"cloud key — your call.")
    else:
        _compute_body = ("No GPU detected on this box, so heavy local models will be slow. Two easy "
                         "options: connect a cloud model in the next step (fastest), or rent GPU time "
                         "from another Cove for jobs like video. You can change this anytime.")

    # CF-72: the compute choice's price tag — a typical starter month priced live
    # (cloud range + the $0-local note). Best-effort; the card renders without it.
    def _starter_cost():
        try:
            from src.dashboard.routes.cost import starter_month_estimate
            est = starter_month_estimate()
            return est if est.get("ok") else None
        except Exception:
            return None

    # CF-112 backup state — the card self-clears on configured + first green run.
    _backup_status, _backup_green = {}, False
    try:
        from src.utils.cove_backup import backup_green, get_backup_config, get_last_status
        _backup_status = {**get_backup_config(), "last": get_last_status()}
        _backup_green = backup_green()
    except Exception:
        pass

    # Order: intelligence → address → COMPUTE → mobile. Intelligence and
    # address are the independent openers; compute unlocks once BOTH are done (it
    # confirms how the box will run given the model choice + the now-real URLs);
    # mobile comes last, gated on the compute ack.
    steps = [
        {
            "id": "add_intelligence",
            "title": "Add intelligence",
            "unlocks": "Activates your agent + Tools (including jules)",
            "done": intel_done,
            "available": True,                       # always open — needs nothing first
            "body": ("Connect a model so your agent can think — bring your own key "
                     "(OpenRouter covers Claude, GPT and more; OpenAI, Google, Groq) or run a "
                     "local Ollama with no key. This switches on your Agent and the Tools."),
        },
        {
            "id": "claim_address",
            "title": "Set your address",
            "unlocks": "Drives every link + turns on HTTPS (so voice works) + mobile",
            "done": address_done,
            "available": True,                       # always open, independent of intelligence
            "admin_only": True,
            "cove_subdomain": (f"{_cove_id}.lucidcove.org" if _cove_id else ""),
            # B14 + batch-10 #2: once the address is live, the done card shows where the
            # Cove now lives. We link the Cove ROOT, not /p/{operator_token} — `/p/` tokens
            # are stored hashed only, so a stamped raw token can't be validated back and 401s
            # after rotation (the T3 first-click 401). The current signed-in link is minted
            # fresh from the live token store in Settings → Devices ("My door link").
            "domain": ((_cc.get("domain") or "").strip() if _domain_set else ""),
            "door": (f"https://{(_cc.get('domain') or '').strip()}" if _domain_set else ""),
            # jules 07-07: when saved-but-not-live, hand back the exact host command so the card can
            # keep showing it (never collapse it out of reach). Empty once live.
            "host_command": ((_cc.get("pending_host_command") or "").strip()
                             if (_domain_set and not _addr_live) else ""),
            "body": ("Set your Cove's address — your lucidcove.org subdomain, or your own "
                     "domain. Everyone here becomes {handle}.{your-address}, and it turns on "
                     "HTTPS so voice and the mic just work."),
        },
        {
            "id": "set_compute",
            "title": "Set up compute",
            "unlocks": "Where your agents run — local GPU, a rented GPU, cloud, or CPU-only",
            "done": compute_done,
            "available": (intel_done and address_done),   # after both openers, before mobile
            "gpu": _gpu,
            "body": _compute_body,
            # CF-72: the choice's price tag — a typical starter month, priced live.
            "cost": _starter_cost(),
        },
        {
            "id": "device_jules",
            "title": "Connect on mobile",
            "unlocks": "a claimed address unlocks access to your Cove, wherever you are",
            "done": device_done,
            "available": (address_done and compute_done),  # mobile is last — after compute
            "body": ("Get your Cove on your phone: join the private mesh with a one-time "
                     "code, then open it at your address — MC, jules, and your files, "
                     "wherever you are. Add jules to your home screen and capture by voice "
                     "anywhere; it lands straight in your Inbox."),
        },
        {
            # CF-112 — clears on configured + FIRST GREEN run (backup_green), no ack.
            # The card carries the exact GitHub walkthrough (operator decision
            # 2026-07-04: the instructions ARE the nag) — home.js renders `guide`
            # as ordered steps.
            "id": "protect_backup",
            "title": "Back up your Cove's work",
            "unlocks": "A daily off-site copy of everything that makes this Cove yours",
            "done": (_backup_green or bool(ac.get("onboarding_backup_ack"))),
            "available": intel_done,                  # meaningful once the Cove is real
            "admin_only": True,
            "backup": _backup_status,                 # {configured, green, remote_url, has_token, last}
            "body": ("Your Cove backs itself up every night to a private repo YOU own — "
                     "the database, your settings, and everyone's files (big videos "
                     "excluded). If this box dies, your Cove doesn't."),
            "guide": [
                "On GitHub: create a PRIVATE repo (e.g. my-cove-backup) — empty, no README.",
                "GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token.",
                "Repository access: \"Only select repositories\" → pick just your backup repo.",
                "Permissions: Contents → Read and write. Nothing else. Generate and copy the token.",
                "Paste the repo URL and the token below, then hit Back up now — the card clears on the first green run.",
            ],
        },
    ]
    # CF-78 close for existing Coves (C6): a Cove that SHARES its GPU must enforce the
    # token gate. Fresh installs stamp PIPECAT_INTERNAL_SECRET + GPU_GRANT_VERIFY_URL;
    # already-provisioned boxes may lack them. Detect sharing-on (this Cove has minted a
    # grant) + either env missing → surface the exact two lines to paste. The host .env
    # restamp is provisioner territory (set_domain._restamp_matrix_env is the precedent);
    # the app can't safely rewrite its own host .env, so this is the fix CARD, not an
    # auto-stamp. Only shows when it's actually needed (never nags a non-sharing Cove).
    _gpu_share_on, _cf78_missing = False, []
    if is_admin:
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                _r = await conn.execute("SELECT COUNT(*) AS n FROM gpu_grants")
                _gpu_share_on = bool((await _r.fetchone() or {}).get("n"))
        except Exception:
            _gpu_share_on = False
        if _gpu_share_on:
            if not (os.environ.get("PIPECAT_INTERNAL_SECRET") or "").strip():
                _cf78_missing.append("PIPECAT_INTERNAL_SECRET")
            if not (os.environ.get("GPU_GRANT_VERIFY_URL") or "").strip():
                _cf78_missing.append("GPU_GRANT_VERIFY_URL")
    if _gpu_share_on and _cf78_missing:
        try:
            from src.config import load_cove_config
            _cf78_domain = (load_cove_config().get("domain") or "").strip()
        except Exception:
            _cf78_domain = ""
        _verify_url = f"https://{_cf78_domain}/api/gpu/verify" if _cf78_domain else "https://<your-domain>/api/gpu/verify"
        steps.append({
            "id": "gpu_share_enforcement",
            "title": "Secure your shared GPU",
            "unlocks": "Closes the GPU token gate so only your grants can use it",
            "done": False,
            "available": True,
            "admin_only": True,
            "missing_env": _cf78_missing,
            "body": ("This Cove shares its GPU, but the enforcement secret isn't set on "
                     "this box, so the token gate can't run. Add the two lines below to "
                     "your Cove's .env (and set the SAME PIPECAT_INTERNAL_SECRET in your "
                     "pipecat config), then recreate the app + pipecat containers. Fresh "
                     "installs get this automatically; older boxes need it added once."),
            "guide": [
                "Generate a secret on the host:  openssl rand -hex 32",
                "In your Cove's .env add:  PIPECAT_INTERNAL_SECRET=<that value>",
                f"In your Cove's .env add:  GPU_GRANT_VERIFY_URL={_verify_url}",
                "Set the SAME PIPECAT_INTERNAL_SECRET in your pipecat service config.",
                "Recreate the containers:  docker compose up -d app pipecat",
            ],
        })

    done_count = sum(1 for s in steps if s["done"])
    # Back-compat `items` = the still-pending + available steps (old consumers).
    items = [s for s in steps if not s["done"] and s["available"]]
    return {"steps": steps, "items": items, "done_count": done_count,
            "total": len(steps), "complete": done_count >= len(steps)}


@router.post("/api/onboarding/ack")
async def onboarding_ack(request: Request):
    """Mark a first-run card done (currently the jules intro; add_intelligence clears
    itself once a model provider is saved)."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    body = await request.json()
    item = (body.get("item") or "").strip()
    ac = dict(_agent_config(p))
    if item in ("device_jules", "join_mesh"):
        ac["onboarding_mesh_ack"] = True
        ac["onboarding_jules_ack"] = True
    elif item == "jules_intro":
        ac["onboarding_jules_ack"] = True
    elif item == "set_compute":
        ac["onboarding_compute_ack"] = True
    elif item == "protect_backup":
        # jules 07-07: "Skip for now" on backup wasn't handled here, so it 400'd and the nag
        # never cleared. Ack it (it still self-clears for real on the first green backup run).
        ac["onboarding_backup_ack"] = True
    else:
        return JSONResponse(status_code=400, content={"ok": False, "error": "unknown item"})
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET agent_config = %s, updated_at = NOW() WHERE id = %s",
            (json.dumps(ac), str(p["id"])),
        )
    return {"ok": True}


@router.post("/api/onboarding/address-live")
async def onboarding_address_live(request: Request):
    """Operator-attested: they ran the host command for the address, so mark it live.
    In-container we can NEVER detect that the host command ran (no docker socket), so the
    'Ran the command? Refresh setup' click is the signal — it flips domain_live + clears the
    stored pending command so the claim step completes instead of showing the command forever."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    try:
        from src.config import save_cove_config
        save_cove_config({"domain_live": True, "pending_host_command": ""})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:200]})
    return {"ok": True}


@router.post("/api/onboarding/cove-door")
async def onboarding_cove_door(request: Request):
    """Mint a FRESH sign-in door for the current operator at the Cove's live domain, so
    'Open my Cove' always crosses over ALREADY LOGGED IN — even after a reload. The step-data
    door is a bare URL (a minted /p/{token} can't be persisted since tokens are stored hashed),
    so we mint it at click time here."""
    from src.dashboard.routes.presence import get_current_presence, mint_signin_door
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".").lower()
        if not dom:
            return JSONResponse(status_code=409, content={"ok": False, "error": "No address set yet"})
        door = await mint_signin_door(p["id"], dom, "https")
        if not door:
            return JSONResponse(status_code=500, content={"ok": False, "error": "Could not mint the door"})
        return {"ok": True, "door": door}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:200]})


@router.post("/api/onboarding/claim-operator")
async def claim_operator(request: Request):
    """Wizard step 1 for a from-scratch install: create the operator's identity inline.

    The provisioner seeded a PLACEHOLDER operator (a `needs_username` handle) so the
    wizard opens editable. When the operator enters their name + @handle + email and
    continues, this mints their identity on the hub (create-and-mint, returns an operator
    token), persists that token to cove.yaml (so the Cove can then reserve its name), and
    writes the chosen handle/name/email onto the seeded operator row. If they ALREADY have
    a real handle (an upgrader who arrived with an account), this is a no-op — the wizard
    just prefills + locks it. Off-network (no LP_REGISTRY_URL) it skips the hub and just
    sets the local handle (a fully-private Cove has no shared namespace)."""
    import re
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    body = await request.json()
    handle = (body.get("handle") or "").lstrip("@").strip().lower()
    name = (body.get("name") or body.get("display_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    cur = (p.get("username") or "").strip().lower()
    is_placeholder = bool(re.match(r'^.+-[0-9a-f]{4}$', cur))
    # Upgrader (already has a real handle) — nothing to claim.
    if cur and not is_placeholder:
        return {"ok": True, "handle": cur, "already": True}
    if not handle or len(handle) < 2:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Choose a handle."})

    # Referral edge (#169): whoever recruited this self-hoster, from config or env.
    referred_by = ""
    try:
        from src.config import load_cove_config
        referred_by = ((load_cove_config().get("affiliate") or {}).get("referred_by")
                       or os.environ.get("LP_REFERRED_BY", "") or "").strip()
    except Exception:
        pass

    # Mint the identity on the hub (when on-network) + capture the operator token.
    minted_token = ""
    from src.dashboard.routes import registry_client
    if registry_client.configured():
        # Email is OPTIONAL (#4). Only validate format if one was entered.
        if email and "@" not in email:
            return JSONResponse(status_code=400, content={"ok": False, "error": "If you provide an email, it must be valid."})
        rr = await registry_client.claim_operator(
            handle=handle, name=name, email=email, referred_by=referred_by)
        if not rr.get("ok"):
            # Pass the structured code through (e.g. email_exists → the wizard offers connect /
            # leave-blank instead of a dead-end alert). #211.
            return JSONResponse(status_code=409, content={
                "ok": False,
                "code": rr.get("code") or "",
                "error": rr.get("error") or rr.get("reason") or "That handle isn't available.",
            })
        handle = (rr.get("handle") or handle).lstrip("@")
        minted_token = (rr.get("operator_token") or "").strip()

    # Persist the minted token (so the cove-name reservation at finalize authenticates)
    # and write the chosen identity onto the seeded operator row.
    #
    # JOIN GUARD (self-onboard): a member/second-admin joining an EXISTING Cove must NOT
    # write cove.yaml's operator_token — that slot holds the FOUNDER's Cove token, which
    # authenticates every Cove-level hub write (register_cove, market, etc.). Clobbering it
    # would break the Cove's hub auth. The invitee's @handle is still reserved on the hub
    # (claim_operator above); their own token isn't persisted here (no per-account slot yet —
    # future work for when members act on the hub under their own handle). We treat it as a
    # join when the client says so OR when the Cove already holds an operator_token.
    _is_join = bool(body.get("join"))
    try:
        from src.config import load_cove_config as _lcc_guard
        if (_lcc_guard().get("operator_token") or "").strip():
            _is_join = True
    except Exception:
        pass
    try:
        if minted_token and not _is_join:
            from src.config import save_cove_config
            save_cove_config({"operator_token": minted_token})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Couldn't save your token: {e}"})
    try:
        from src.memory.database import get_db
        sets, params = ["username = %s"], [handle]
        if name:
            sets.append("display_name = %s"); params.append(name)
        if email:
            sets.append("email = %s"); params.append(email)
        params.append(str(p["id"]))
        async with get_db() as conn:
            await conn.execute(
                f"UPDATE accounts SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s",
                tuple(params))
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Couldn't save your handle: {e}"})
    return {"ok": True, "handle": handle}


@router.post("/api/onboarding/connect-operator")
async def connect_operator(request: Request):
    """Path B (#4): connect an EXISTING Lucid Principles handle (e.g. @jag) to this
    self-hosted Cove. The operator pastes their connect key (their account's operator
    token); we verify with the hub that the key owns the handle, then save it + write the
    handle onto the seeded operator row. No new account is minted — finalize then claims
    the Cove under that existing handle using the saved token."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    body = await request.json()
    handle = (body.get("handle") or "").lstrip("@").strip().lower()
    token = (body.get("connect_key") or body.get("token") or "").strip()
    name = (body.get("name") or body.get("display_name") or "").strip()
    if not handle or not token:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Enter your handle and connect key."})
    # Verify with the hub that this key owns this handle (no new account, no token rotation).
    from src.dashboard.routes import registry_client
    if registry_client.configured():
        vr = await registry_client.verify_operator(handle=handle, token=token)
        if not vr.get("ok"):
            return JSONResponse(status_code=403, content={"ok": False, "error": vr.get("reason") or f"That connect key doesn't match @{handle}."})
    # Save the verified token (so finalize authenticates as this handle) + write identity.
    try:
        from src.config import save_cove_config
        save_cove_config({"operator_token": token})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Couldn't save your key: {e}"})
    try:
        from src.memory.database import get_db
        sets, params = ["username = %s"], [handle]
        if name:
            sets.append("display_name = %s"); params.append(name)
        params.append(str(p["id"]))
        async with get_db() as conn:
            await conn.execute(
                f"UPDATE accounts SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s",
                tuple(params))
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Couldn't save your handle: {e}"})
    # CF-65 seamless carry: pull the operator's tuning history/streak/preferences
    # from the hub in the background. Best-effort — the identity connect NEVER
    # blocks or fails because carry failed. Progress: GET /api/onboarding/carry-status.
    try:
        from src.dashboard.routes.carry_import import start_carry
        start_carry(str(p["id"]), token)
    except Exception:
        pass
    return {"ok": True, "handle": handle, "connected": True}


@router.get("/api/onboarding/mesh-key")
async def mesh_join_key(request: Request):
    """#134 — mint a one-time mesh (Headscale) pre-auth key + join command for the
    operator's device. Degrades gracefully when Headscale isn't reachable from here
    (e.g. a hosted Cove): returns instructions instead of a key."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    # Host path of this Cove's folder (stamped by the provisioner on install.sh
    # runs) — the mesh-step UI renders the join one-liner with the FULL path so
    # the operator never digs for the right folder (run-2 4.1/4.2).
    _cove_dir = (os.environ.get("COVE_HOST_DIR", "") or "").strip()
    # 1) Try minting locally (founder/co-located, or a self-host running its own mesh).
    try:
        from provision.mesh import create_preauth_key
        res = create_preauth_key(expiry="1h")
        if res.get("ok"):
            res.setdefault("cove_dir", _cove_dir)
            return res
    except Exception:
        pass
    # 2) Fall back to the HUB mesh-key endpoint — the control plane mints it via the
    #    Headscale API and the self-host box never holds the Headscale key. Needs
    #    LP_REGISTRY_URL + the operator's token (LP_OPERATOR_TOKEN).
    reg = (os.environ.get("LP_REGISTRY_URL", "") or "").strip().rstrip("/")
    # Operator token: env (provisioned) OR cove.yaml (a from-scratch Cove mints it at
    # runtime and stores it there) — same source the registry/market clients use.
    try:
        from src.dashboard.routes.registry_client import _operator_token as _ot
        op_tok = _ot()
    except Exception:
        op_tok = (os.environ.get("LP_OPERATOR_TOKEN", "") or "").strip()
    if reg and op_tok:
        try:
            import urllib.request
            # Send the operator token AND, if this box has it, the fleet secret — the hub
            # accepts either (same as cove-dns/acme). Founder boxes authorize via the fleet
            # secret; the operator-token-only path is the stranger case (tracked separately).
            # UA required: Cloudflare blocks default python UAs (403/1010) on the hub.
            _hdrs = {"Content-Type": "application/json", "X-Operator-Token": op_tok,
                     "User-Agent": "LucidCove-Cove/1.0"}
            _fleet = (os.environ.get("LP_REGISTRY_SECRET", "") or "").strip()
            if _fleet:
                _hdrs["X-Registry-Secret"] = _fleet
            req = urllib.request.Request(
                reg + "/api/registry/mesh-key", data=b"{}", method="POST", headers=_hdrs)
            with urllib.request.urlopen(req, timeout=20) as r:
                _hub_res = json.loads(r.read().decode())
                if isinstance(_hub_res, dict):
                    _hub_res.setdefault("cove_dir", _cove_dir)
                return _hub_res
        except Exception as e:
            return {"ok": False, "reason": f"hub mesh-key failed: {str(e)[:160]}",
                    "instructions": "Ask your Cove host for a device join code."}
    return {"ok": False,
            "reason": "no local mesh + no hub token configured",
            "instructions": "Set LP_REGISTRY_URL + LP_OPERATOR_TOKEN, or run on your mesh "
                            "coordinator: headscale preauthkeys create --user lucid --expiration 1h"}
