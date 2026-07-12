"""
Mission Control — config-driven FastAPI app.

Reads agent.yaml to determine:
- App title and branding
- Which core + agent-specific routes to register
- Scheduler class to use

COVE-CORE TEMPLATE: This app.py works for any agent. The agent's
deploy script overlays agent-specific routes and static files on top
of the cove-core base.
"""

import asyncio
import importlib
import os
import sys

# Make the repo-root packages importable app-wide. `provision/` (DNS, cert, Caddy, mesh —
# the whole address/networking layer) lives at the repo root next to `src/`, mounted at
# /cove-core. It is NOT under src, so without this `from provision import ...` raises
# ModuleNotFoundError and every address/DNS/cert/mesh call silently no-ops. Append (don't
# insert) so /app/src keeps precedence for `from src...`. Covers domain.py, registry.py
# (acme-credential / cove-dns on the hub), runtime_address, etc. — once, everywhere.
for _root in ("/cove-core",
              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
    if _root and os.path.isdir(os.path.join(_root, "provision")) and _root not in sys.path:
        sys.path.append(_root)

from src.env import env, env_bool
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import get_instance, get_routes, load_cove_config


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Add cache headers for static assets.

    All JS/CSS includes use ?v={build_version} query strings for cache-busting.
    When the build version changes (new deploy), browsers fetch fresh files
    automatically — no manual cache clear needed. Between deploys, files are
    cached aggressively for fast loads.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/avatars/"):
            # Avatars rarely change — cache 30 days
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        elif path.startswith("/static/") and any(
            path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".gif")
        ):
            # Other images — cache 7 days
            response.headers["Cache-Control"] = "public, max-age=604800"
        elif path.startswith("/static/") and path.endswith((".js", ".css")):
            # JS/CSS — versioned via ?v= param, cache 7 days
            response.headers["Cache-Control"] = "public, max-age=604800"
        elif path.startswith("/static/") and path.endswith(".html"):
            # HTML entry points are loaded directly by URL, NOT ?v=-busted — so the
            # browser must never serve a stale one (this is what broke the first-run
            # wizard across reinstalls: a cached page that only a hard refresh cleared).
            # Always revalidate; the server still answers 304 when nothing changed.
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.startswith("/api/"):
            # API data is dynamic — never cache. Static assets handle performance.
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


class OperatorAuthMiddleware(BaseHTTPMiddleware):
    """Gate all API routes in multi mode (app.lucidcove.org).

    In multi mode, ALL methods on /api/* require either:
      - A valid session cookie (presence_token), OR
      - X-Shared-Secret header matching SHARED_CONTAINER_SECRET

    Exceptions: PUBLIC_PATHS and PUBLIC_PREFIXES pass without auth
    (account creation, magic link, health, checkout, webhooks).
    OPTIONS always passes for CORS preflight.

    In single mode (Stuart/Atlas behind mesh): passes everything through.
    Network perimeter is the trust boundary in single mode.
    """

    # Endpoints that must remain public (no auth) even in multi mode
    PUBLIC_PATHS = {
        "/api/system/ping",
        "/api/system/health",
        "/api/config",
        "/api/account/create",
        "/api/account/signin",
        "/api/account/magic-link",
        "/api/account/verify-magic-link",
        "/api/account/ref",
        "/api/contact/submit",
        "/api/presence/me",
        # Onboarding availability reads — checked BEFORE the operator is authenticated
        # (naming the Cove + picking a handle in the first-run wizard). Read-only, safe.
        "/api/presence/handle-available",
        "/api/cove/name-available",
        # Quick-door archetype gallery — the first-run wizard loads this template list
        # before a session is reliably in hand (same class as the two reads above).
        # Static framework templates only (no user data, no secrets). Read-only, safe.
        "/api/flow/agent-presets",
        # CF-90b: connect-mesh.sh calls this on localhost right after the box joins
        # the mesh (no browser session in that shell). Takes NO input — it only
        # re-asserts the Cove's existing domain at its own current mesh IP, so an
        # outside caller can only make the Cove re-assert correct state.
        "/api/domain/reconcile-dns",
        # GPU-share verify (nottington A8): pipecat calls this service-to-service with
        # X-Pipecat-Secret — it has no session and no SHARED_CONTAINER_SECRET, so the
        # middleware 403'd every cross-Cove GPU job before the endpoint's own auth ran
        # (the documented middleware-vs-endpoint-secret gotcha). The route enforces
        # PIPECAT_INTERNAL_SECRET + the grant token itself (gpu_share.py) — not an
        # open oracle.
        "/api/gpu/verify",
        # Marketplace auto-grant (C2): the hub's S1 webhook posts here after a paid GPU
        # sale to open this Cove's GPU. No operator session — the route enforces the
        # fleet secret itself (gpu_share.marketplace_grant), same class as /api/gpu/verify.
        "/api/gpu/marketplace-grant",
    }

    # Prefixes that are public (webhooks, checkout — handled by Socrates, but
    # listed here defensively in case cove-core ever serves them)
    PUBLIC_PREFIXES = (
        "/api/commerce/checkout",
        "/api/commerce/webhook",
        # Hub registrar (#133): reachable by other Coves (no session/shared-secret).
        # Reads are open within the fleet; writes enforce LP_REGISTRY_SECRET inside
        # the route (registry.py), so passing the middleware here is safe.
        "/api/registry/",
        # Hosting trigger (#167): Socrates posts here after a hosted-Cove purchase.
        # The route enforces SHARED_CONTAINER_SECRET itself (provision_api.py).
        "/api/hosting/",
        # Credit on-ramp (#128/#169): Socrates posts here to mint credits after a
        # top-up clears. The route enforces SHARED_CONTAINER_SECRET itself (credits.py).
        "/api/credits/",
        # Public avatar serving (#169): profile pics load cross-domain from any Cove MC.
        "/avatars/",
        # Owner notifications (#167): Socrates posts here after a hosted-Cove purchase.
        # The route enforces SHARED_CONTAINER_SECRET itself (notify.py).
        "/api/notify/",
    )

    async def dispatch(self, request: Request, call_next):
        import hmac

        # Single mode = mesh-trusted, pass through
        if env("COVE_MODE", "single") != "multi":
            return await call_next(request)

        # OPTIONS always passes (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Not an API route — pass through (static files, HTML pages)
        if not path.startswith("/api/"):
            return await call_next(request)

        # Public endpoints — pass through
        if path in self.PUBLIC_PATHS:
            return await call_next(request)
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Check shared-secret header (inter-service calls)
        secret = env("SHARED_CONTAINER_SECRET")
        header = request.headers.get("X-Shared-Secret", "")
        if secret and header and hmac.compare_digest(header, secret):
            return await call_next(request)

        # Check session cookie (browser user)
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        if presence:
            return await call_next(request)

        # Self-onboard capability: an invitee running the wizard has NO session yet (they
        # become a Presence only at /complete). A valid, open invite cookie authorizes ONLY
        # the onboarding endpoints — the wizard's model/flow calls, the family read for the
        # showcase, and the token-gated completion. Nothing else. The invite is single-use +
        # expiring, so this capability closes itself. (2026-07-07, self-onboard.)
        _is_onboarding = (
            path.startswith("/api/flow/")
            or path == "/api/family"
            or (path.startswith("/api/presence/invite/") and path.endswith("/complete"))
        )
        if _is_onboarding:
            inv_tok = request.cookies.get("lp_invite", "")
            if inv_tok:
                try:
                    from src.dashboard.routes.presence_invite import _valid_invite
                    if await _valid_invite(inv_tok):
                        return await call_next(request)
                except Exception:
                    pass

        # No valid auth — reject
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=403,
            content={"detail": "Authentication required"},
        )


def _get_scheduler():
    """Try to import agent-specific scheduler, fall back to no-op."""
    try:
        from src.utils.scheduler import AgentScheduler
        return AgentScheduler()
    except ImportError:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    instance = get_instance()
    from src.utils.time_utils import app_tz
    tz = app_tz()
    ts = lambda: datetime.now(tz).strftime("[%Y-%m-%d %H:%M:%S]")
    name = instance.get("name", "Agent")

    scheduler = _get_scheduler()
    task = None
    # C3-13: the scheduler task used to have no watchdog — one exception escaping
    # scheduler.run() (e.g. an overlay's setup_agent_schedule raising) silently
    # killed EVERY scheduled job (06:05 KB sync, queue processors, backups) until
    # the next container restart. Log the death and recreate the task.
    _sched_state = {"task": None, "stopping": False}
    if scheduler:
        def _sched_done(t: "asyncio.Task"):
            if _sched_state["stopping"] or t.cancelled():
                return
            try:
                exc = t.exception()
            except Exception:
                exc = None
            print(f"{ts()} [app] SCHEDULER TASK DIED "
                  f"({exc!r}) — restarting in 10s.")
            async def _restart():
                await asyncio.sleep(10)
                if _sched_state["stopping"]:
                    return
                nt = asyncio.create_task(scheduler.run())
                nt.add_done_callback(_sched_done)
                _sched_state["task"] = nt
                print(f"{ts()} [app] Scheduler task restarted.")
            asyncio.create_task(_restart())
        task = asyncio.create_task(scheduler.run())
        task.add_done_callback(_sched_done)
        _sched_state["task"] = task
        print(f"{ts()} [app] {name} Mission Control online. Scheduler started.")
    else:
        print(f"{ts()} [app] {name} Mission Control online. No scheduler found.")

    # Load the Cove's BRAIN — the admin's connected model (Add Intelligence) — so every
    # agent + job uses it, persisting across restarts (the key lives on the admin account).
    async def _load_cove_brain() -> bool:
        """True once the brain is applied. False = not applied yet (DB not ready,
        or the admin hasn't connected intelligence) — worth retrying."""
        if env("COVE_MODE", "single") != "multi":
            return True   # single mode has no Cove brain — nothing to retry
        from src.memory.database import get_db
        from src.models.provider import apply_cove_model
        import json as _json
        async with get_db() as conn:
            _r = await conn.execute(
                "SELECT agent_config FROM accounts WHERE cove_role = 'admin' AND active = TRUE "
                "ORDER BY created_at ASC LIMIT 1")
            _row = await _r.fetchone()
        if not _row:
            return False
        _ac = _row.get("agent_config") or {}
        if isinstance(_ac, str):
            _ac = _json.loads(_ac or "{}")
        _prov = (_ac.get("model_provider") or "").strip()
        _key = (_ac.get("model_api_key") or "").strip()
        _model = (_ac.get("model_name") or "").strip()
        if not _prov:
            return False
        apply_cove_model(_prov, _key, model=_model)
        print(f"{ts()} [app] Cove brain loaded from admin account: {_prov} ({_model or 'default'})")
        return True

    # C3-1: this load used to be one boot-time shot — a cold-boot DB hiccup meant no
    # Cove brain until the admin re-saved the key or the container restarted (agents
    # silently fell to the env floor). Same treatment as the assignments cache below:
    # keep retrying in the background until it lands.
    _brain_task = None
    _brain_loaded = False
    try:
        _brain_loaded = await _load_cove_brain()
    except Exception as _e:
        print(f"{ts()} [app] Cove brain load failed (will retry): {_e}")
    if not _brain_loaded:
        async def _brain_refresher():
            while True:
                await asyncio.sleep(45)
                try:
                    if await _load_cove_brain():
                        return   # applied — presence.py re-applies on any later save
                except Exception:
                    pass
        _brain_task = asyncio.create_task(_brain_refresher())

    # Load DB-backed per-agent model assignments (Team-page model manager) into the cache,
    # so get_agent_model_assignment can serve them sync on the hot path.
    _assign_task = None
    try:
        from src.models.assignments import load_assignments_cache
        await load_assignments_cache()
        print(f"{ts()} [app] Agent model assignments loaded.")
        # Keep the cache fresh across workers — model changes are rare, 45s is plenty.
        async def _assign_refresher():
            while True:
                await asyncio.sleep(45)
                try:
                    await load_assignments_cache()
                except Exception:
                    pass
        _assign_task = asyncio.create_task(_assign_refresher())
    except Exception as _e:
        print(f"{ts()} [app] Agent model assignments load skipped: {_e}")

    # Populate knowledge base in background (non-blocking).
    # C3-2: this was boot-only — on a fresh box Ollama isn't up yet (or the embed
    # model isn't pulled, or the CF-6b sync hasn't landed the KB files) and the
    # vector index stayed empty until the next restart while search_knowledge
    # silently returned nothing. Retry with backoff until it settles; the KB
    # standup sync + 06:05 job additionally re-kick after files change.
    kb_task = None
    async def _kb_index_standup():
        from src.memory.knowledge import populate_knowledge_base
        delays = (0, 60, 120, 300, 600, 900)   # ~33 min of cover
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            try:
                if await populate_knowledge_base():
                    return   # indexed or up to date — settled
                print(f"{ts()} [app] KB indexing attempt {attempt} "
                      f"not settled (will retry).")
            except Exception as e:
                print(f"{ts()} [app] KB indexing attempt {attempt} "
                      f"errored (will retry): {e}")
        print(f"{ts()} [app] KB indexing gave up after {len(delays)} attempts — "
              f"the KB sync paths will re-kick it.")
    try:
        kb_task = asyncio.create_task(_kb_index_standup())
        print(f"{ts()} [app] Knowledge base indexing started (background).")
    except Exception as e:
        print(f"{ts()} [app] Knowledge base indexing skipped: {e}")

    # CF-6: mirror the canonical KB from the hub Drop into Nextcloud at standup,
    # not only at the 06:05 scheduler run, so a fresh Cove populates its NC KB
    # immediately. sync_kb() self-guards (no-ops when NC isn't configured or the
    # verify-key isn't set) and never raises, so this is safe to always kick.
    kb_sync_task = None
    async def _kb_standup_sync():
        # CF-6b: on a FRESH install Nextcloud's first boot installs itself (minutes),
        # so a single shot at +20s fails and the box has no KB until the 06:05 run —
        # the "WebDAV error: 404" a brand-new Cove hit opening the Knowledge Base.
        # Retry with backoff until the sync settles (synced or up-to-date), then stop.
        delays = (20, 40, 60, 120, 180, 300, 300, 600, 600)   # ~37 min of cover
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            try:
                from src.knowledge.kb_sync import sync_kb
                result = await sync_kb()
                if result.get("ok"):
                    if result.get("synced"):
                        print(f"{ts()} [app] KB standup sync -> "
                              f"{str(result.get('version',''))[:12]} "
                              f"({len(result.get('files', []))} files, "
                              f"attempt {attempt})")
                        # C3-2: new KB files just landed — re-kick the vector
                        # index (idempotent, hash-guarded) so search doesn't
                        # serve a boot-time index of the pre-sync files.
                        try:
                            from src.memory.knowledge import populate_knowledge_base
                            asyncio.create_task(populate_knowledge_base())
                        except Exception:
                            pass
                    return   # synced or up-to-date — settled either way
                print(f"{ts()} [app] KB standup sync attempt {attempt} "
                      f"not applied (will retry): "
                      f"{result.get('error') or result.get('skipped')}")
            except Exception as e:
                print(f"{ts()} [app] KB standup sync attempt {attempt} "
                      f"errored (will retry): {e}")
        print(f"{ts()} [app] KB standup sync gave up after {len(delays)} attempts — "
              f"the 06:05 scheduler run will retry.")
    try:
        kb_sync_task = asyncio.create_task(_kb_standup_sync())
    except Exception as e:
        print(f"{ts()} [app] KB standup sync not scheduled: {e}")

    # CF-65: hosted/provisioned Coves already hold an operator token — if the
    # local tuning history is empty (or the last import was partial), pull the
    # operator's practice data from the hub (background, self-guarding, never raises).
    carry_task = None
    async def _carry_standup():
        # C3-3: this was one shot at +25s — on a fresh hosted box the hub is often
        # unreachable that early (DNS propagating, proxy warming) and nothing retried
        # until the next container restart. Same backoff ladder as the KB standup
        # sync; stops as soon as a run settles (no-op guard or clean complete import).
        delays = (25, 60, 120, 300, 600, 900)   # ~33 min of cover
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            try:
                from src.dashboard.routes.carry_import import first_boot_carry
                res = await first_boot_carry()
                if res.get("settled"):
                    if res.get("sessions"):
                        print(f"{ts()} [app] first-boot carry done: "
                              f"{res['sessions']} sessions (attempt {attempt})")
                    return
                print(f"{ts()} [app] first-boot carry attempt {attempt} "
                      f"not settled (will retry): {res.get('reason')}")
            except Exception as e:
                print(f"{ts()} [app] first-boot carry attempt {attempt} "
                      f"errored (will retry): {e}")
        print(f"{ts()} [app] first-boot carry gave up after {len(delays)} attempts — "
              f"a restart or the connect panel can retry it.")
    try:
        carry_task = asyncio.create_task(_carry_standup())
    except Exception as e:
        print(f"{ts()} [app] first-boot carry not scheduled: {e}")

    # #D30: a delegated background turn killed by THIS restart can't file its own
    # failure report — its task row is left in_progress and looks alive forever. Sweep
    # those to 'blocked' with a report-back at boot. Best-effort, off the hot path.
    async def _delegation_sweep():
        try:
            from src.tools.delegation_tools import sweep_orphaned_delegations
            n = await sweep_orphaned_delegations()
            if n:
                print(f"{ts()} [app] swept {n} restart-orphaned delegation(s) → blocked.")
        except Exception as e:
            print(f"{ts()} [app] orphaned-delegation sweep error: {e}")
    deleg_sweep_task = asyncio.create_task(_delegation_sweep())

    # #D39: an async video job killed by THIS restart leaves a durable row stuck
    # 'running' while the browser keeps polling. Orphan-mark those to 'failed' so
    # the UI reports honestly instead of spinning on a job that will never finish.
    async def _video_job_sweep():
        try:
            from src.dashboard.routes.video_jobs import sweep_orphaned_video_jobs
            n = await sweep_orphaned_video_jobs()
            if n:
                print(f"{ts()} [app] swept {n} restart-orphaned video job(s) → failed.")
        except Exception as e:
            print(f"{ts()} [app] orphaned video-job sweep error: {e}")
    video_sweep_task = asyncio.create_task(_video_job_sweep())

    yield

    if _brain_task and not _brain_task.done():
        _brain_task.cancel()
        try:
            await _brain_task
        except asyncio.CancelledError:
            pass

    if _assign_task and not _assign_task.done():
        _assign_task.cancel()
        try:
            await _assign_task
        except asyncio.CancelledError:
            pass

    if kb_task and not kb_task.done():
        kb_task.cancel()
        try:
            await kb_task
        except asyncio.CancelledError:
            pass

    if carry_task and not carry_task.done():
        carry_task.cancel()
        try:
            await carry_task
        except asyncio.CancelledError:
            pass

    if kb_sync_task and not kb_sync_task.done():
        kb_sync_task.cancel()
        try:
            await kb_sync_task
        except asyncio.CancelledError:
            pass

    if scheduler:
        _sched_state["stopping"] = True   # C3-13: don't let the watchdog restart it
        scheduler.stop()
        task = _sched_state["task"] or task   # the watchdog may have replaced it
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    print(f"{ts()} [app] {name} offline.")


def create_app() -> FastAPI:
    """Build the FastAPI app from config."""
    instance = get_instance()
    name = instance.get("name", "Agent")

    app_instance = FastAPI(
        title=f"{name} — Mission Control",
        version="2.0.0",
        lifespan=lifespan,
    )

    # CORS — reference public sites + this Cove's own configured domain (replication-safe)
    _cors_origins = [
        "https://lucidtuner.com",
        "https://www.lucidtuner.com",
        "https://lucidcove.org",
        "https://www.lucidcove.org",
    ]
    _cove_domain = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".")
    if _cove_domain:
        _cors_origins += [f"https://{_cove_domain}", f"https://www.{_cove_domain}"]
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-Shared-Secret"],
    )

    # Auth middleware — gates all /api/* in multi mode (session 159, GET fix 160)
    app_instance.add_middleware(OperatorAuthMiddleware)

    # Cache middleware for static assets (must be added before mount)
    app_instance.add_middleware(StaticCacheMiddleware)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)

    # Always register core routes BEFORE static mount
    # (so dynamic routes like /static/manifest.json take priority)
    from src.dashboard.routes import core, chat, memory, tuning, tuning_request, system, logs, runbooks, vault, youtube, youtube_auth, xai_auth, action_board, flow_chat, flow_cove, creation, presence, quick_list, projects, mirrors, mirror_builder, canon, settings, account, contact, nextcloud, files, home, jules, backlog, sites, agents, agent_provision, agent_presets, agent_dictate, video_pipeline, video_processing, x_posting, posting, matrix, matrix_spaces, haven, matrix_haven, registry, market, market_rates, provision_api, credits, profile, notify, onboarding, cost, hire, capabilities, domain, wake_thread, gpu_share, carry_import, pipeline_keys, compute, video_jobs, backup, jules_process, presence_invite, reachability, watcher, steward_queue, ops_visibility

    all_routers = [core, chat, memory, tuning, tuning_request, system, logs, runbooks, vault, youtube, youtube_auth, xai_auth, action_board, flow_chat, flow_cove, creation, presence, quick_list, projects, mirrors, mirror_builder, canon, settings, account, contact, nextcloud, files, home, jules, backlog, sites, agents, agent_provision, agent_presets, agent_dictate, video_pipeline, video_processing, x_posting, posting, matrix, matrix_spaces, haven, matrix_haven, registry, market, market_rates, provision_api, credits, profile, notify, onboarding, cost, hire, capabilities, domain, wake_thread, gpu_share, carry_import, pipeline_keys, compute, video_jobs, backup, jules_process, presence_invite, reachability, watcher, steward_queue, ops_visibility]

    # ── Agent-gate (#191): on the PUBLIC shared app (LP_REGISTRY_MASTER) operators
    # have NO agents and NO Cove pipeline. Don't mount the agent-only routers at all,
    # so their endpoints can't leak Cove content or be used as shared scratch space.
    # Pure-agent routers only — mixed/operator routers (tuning, mirrors, core, system,
    # action_board, creation) stay mounted and self-gate per-endpoint. No-op for a real
    # Cove (single mode / not registry master).
    if env_bool("LP_REGISTRY_MASTER"):
        _cove_only = {
            chat, memory, youtube, youtube_auth, video_pipeline, video_processing,
            x_posting, posting, agents, agent_provision, agent_dictate, jules, sites,
            mirror_builder, creation, wake_thread, gpu_share, pipeline_keys,
            compute, video_jobs, backup, jules_process,
        }
        all_routers = [r for r in all_routers if r not in _cove_only]

    for router_module in all_routers:
        app_instance.include_router(router_module.router)

    # Register agent-specific routes from config
    for route_def in get_routes():
        module_path = route_def.get("module", "") if isinstance(route_def, dict) else route_def
        try:
            mod = importlib.import_module(f"src.{module_path}")
            if hasattr(mod, "router"):
                app_instance.include_router(mod.router)
                print(f"[app] Registered route: {module_path}")
        except Exception as e:
            print(f"[app] Warning: Failed to load route {module_path}: {e}")

    # Mount static files AFTER routes so dynamic overrides (manifest.json) win
    app_instance.mount("/static", StaticFiles(directory=str(static_dir), follow_symlink=True), name="static")

    # Serve index.html at root
    template_path = static_dir / "index.html"
    cove_mode = env("COVE_MODE", "single")
    cove_name = env("COVE_NAME", name)

    def _serve_landing(landing_path):
        """Return landing page HTML."""
        if landing_path.exists():
            return HTMLResponse(content=landing_path.read_text())
        return HTMLResponse(content=f"""<!DOCTYPE html>
        <html><body style="font-family: system-ui; padding: 2rem; background: #0a0a0f; color: #d8d8e0;">
        <h1>{cove_name} Cove</h1>
        <p>Welcome. You'll need an invitation link to access your Presence here.</p>
        <p>Ask the Cove operator for your personal link.</p>
        </body></html>""")

    @app_instance.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        # haven.{cove}.{domain} door — serve the Haven management view. Rides the
        # *.{domain} wildcard; the page + its APIs enforce admin. (No-op for the cove
        # root / handle / manager hosts and for domainless local Coves.)
        try:
            from src.dashboard.host_context import resolve_host_context, request_host
            from src.config import load_cove_config as _lcc_h
            if resolve_host_context(request_host(request), _lcc_h()).get("kind") == "haven":
                from fastapi.responses import RedirectResponse
                return RedirectResponse("/static/action-board/haven.html", status_code=307)
        except Exception:
            pass
        # Multi-Presence: check for auth cookie before showing MC
        if cove_mode == "multi":
            token = request.cookies.get("presence_token")
            landing_path = static_dir / "landing.html"
            if not token:
                # No auth — show landing page
                return _serve_landing(landing_path)

            # Validate the token — if stale, clear cookie and show landing
            from src.dashboard.routes.presence import get_current_presence
            account = await get_current_presence(request)
            if not account:
                import logging
                logging.warning("[AUTH] Stale cookie detected — clearing and redirecting to landing")
                response = _serve_landing(landing_path)
                response.delete_cookie("presence_token")
                return response

        if template_path.exists():
            from src.dashboard.routes.core import _get_build_version
            bv = _get_build_version()  # Read per-request, not frozen at startup
            html = template_path.read_text()
            # Inject build version for cache-busting static assets
            html = html.replace('dashboard.css">', f'dashboard.css?v={bv}">')
            html = html.replace('components.css">', f'components.css?v={bv}">')
            html = html.replace('chat.css">', f'chat.css?v={bv}">')
            html = html.replace('action-board.css">', f'action-board.css?v={bv}">')
            html = html.replace('tune-flow.css">', f'tune-flow.css?v={bv}">')
            html = html.replace('panels.js"></script>', f'panels.js?v={bv}"></script>')
            html = html.replace('core.js"></script>', f'core.js?v={bv}"></script>')
            return HTMLResponse(
                content=html,
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
        return HTMLResponse(content=f"""<!DOCTYPE html>
        <html><body style="font-family: system-ui; padding: 2rem;">
        <h1>{name}</h1>
        <p>Mission Control is loading... index.html not found.</p>
        <p><a href="/api/status">/api/status</a> | <a href="/api/config">/api/config</a></p>
        </body></html>""")

    return app_instance


# Create the app instance (uvicorn expects this)
app = create_app()
