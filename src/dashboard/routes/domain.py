# =============================================================================
# domain.py — Operator first-run "claim your address" (the onboarding keystone).
# =============================================================================
# Setting the Cove's domain is the single highest-leverage first-run step: the
# moment a domain is live, EVERY service URL derives from it (MC, Connect/Matrix,
# cloud, voice/jules, sign-in/claim) and Caddy issues HTTPS automatically — which
# is what finally lets the browser grant the mic in a secure context, so voice
# "just works." (Supersedes the bolt-on-HTTPS task, #208.)
#
# ARCHITECTURE — declare intent, a privileged reconciler applies it:
#   An app running INSIDE a container must never mutate host networking directly
#   (handing it the docker socket or the host Caddy dir is a container-escape =
#   host-root anti-pattern). So this endpoint PERSISTS the chosen domain to
#   cove.yaml (the declarative intent — which alone activates every domain-derived
#   URL the codebase already builds) and then BEST-EFFORTS the privileged DNS +
#   Caddy step. When that step can't run from here (the normal co-located /
#   self-host case: the host Caddy dir isn't mounted into this container), it
#   returns the exact host-side command to finish. Same philosophy as
#   provision_api.py and netconfig: the privileged step is explicit + never fatal.
#
#   - Hosted (we own Cloudflare DNS + Caddy): the hub provisioner already sets the
#     subdomain at purchase time; here the token is usually present so DNS + Caddy
#     can succeed in-place.
#   - Self-host: the operator runs ONE command on their own box (the reconciler
#     CLI provision/set_domain.py) — sovereign, no raw token shipped to user boxes.
# =============================================================================
import asyncio
import logging
import os
import posixpath
import re
import secrets as _secrets
import socket
import sys

# The DNS/cert/Caddy/mesh logic lives in `provision/` at the repo root, mounted at
# /cove-core (a sibling of src, NOT under it), so it isn't on the app's import path by
# default — `from provision import ...` then ModuleNotFoundError and the whole address
# claim silently no-ops. Put /cove-core on the path (appended, so /app/src keeps
# precedence for `from src...`). This is what makes the in-browser claim actually run.
for _cc in ("/cove-core", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))):
    if _cc and os.path.isdir(os.path.join(_cc, "provision")) and _cc not in sys.path:
        sys.path.append(_cc)

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from src.env import env
from src.config import load_cove_config, save_cove_config, resolve_voice_urls
from src.dashboard.routes.settings import _is_admin_presence

log = logging.getLogger(__name__)
router = APIRouter()

# A conservative public-hostname check: labels of [a-z0-9-], at least one dot,
# a 2+ char TLD. Lowercased before matching. Rejects schemes, paths, ports.
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _clean_domain(raw: str) -> str:
    """Normalize operator input → bare host. Strips scheme, path, port, leading
    '*.'/dots, trailing dot, whitespace, and lowercases."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)        # drop scheme
    d = d.split("/")[0].split(":")[0]        # drop path + port
    d = d.lstrip("*").lstrip(".").rstrip(".")
    return d


def _matrix_server_name(domain: str) -> str:
    """Founder convention: every service is {svc}.{domain}, so the homeserver is
    matrix.{domain} (matches matrix.cove.lucidcove.org)."""
    return f"matrix.{domain}" if domain else ""


def _ports() -> dict:
    """Best-effort published ports for the Caddy block. app_port is reliable
    (PORT is set + bound 1:1 by the provisioner); nextcloud/matrix are host-publish
    ports the app container may not know — fall back to the standard defaults, and
    the host reconciler can override with the real values from the compose."""
    return {
        "app": int(env("PORT", "8200") or "8200"),
        "nextcloud": int(env("NEXTCLOUD_PORT", "8080") or "8080"),
        "matrix": int(env("MATRIX_PORT", "8008") or "8008"),
        "voice": int(env("VOICE_PORT", "0") or "0"),
    }


def _cove_id(cove: dict) -> str:
    return (cove.get("id") or cove.get("name") or "cove").strip().lower().replace(" ", "-")


def _reachable(cove: dict) -> dict:
    """CF-90b: where DNS for this box could point RIGHT NOW, and how we know.
    Mesh-first order (matches the provisioner's _detect_host_ip): explicit config
    deploy.host_ip/mesh_ip → COVE_MESH_IP env (written by connect-mesh.sh) →
    live mesh detect (only works when the app can see the host's tailscale —
    normally it can't; harmless). ok=False means the address claim would refuse
    (home/NAT box that hasn't joined the mesh) — the UI puts the mesh step first."""
    import os as _os
    deploy = (cove.get("deploy") or {}) if isinstance(cove.get("deploy"), dict) else {}
    explicit = (str(deploy.get("host_ip") or "") or str(deploy.get("mesh_ip") or "")).strip()
    if explicit:
        return {"ok": True, "ip": explicit, "source": "config"}
    mesh_env = (_os.getenv("COVE_MESH_IP", "") or "").strip()
    if mesh_env:
        return {"ok": True, "ip": mesh_env, "source": "mesh"}
    try:
        from provision.centralized import _detect_mesh_ip
        live = _detect_mesh_ip()
        if live:
            return {"ok": True, "ip": live, "source": "mesh"}
    except Exception:
        pass
    return {"ok": False, "ip": "", "source": "none"}


def _status_payload() -> dict:
    cove = load_cove_config()
    domain = (cove.get("domain") or "").strip()
    voice = {}
    try:
        v = resolve_voice_urls()
        voice = {"enabled": v.get("enabled"), "http": v.get("http"),
                 "needs_https": bool(v.get("same_host_port")) and not domain}
    except Exception:
        voice = {"enabled": False}
    # CF-95: last claim-time DNS-01 probe — keeps "hub challenge DNS unreachable
    # on port 53" visible on the status card until it clears (the claim response
    # is gone after a refresh; this isn't).
    cert_probe = {}
    try:
        from provision.runtime_address import read_cert_probe
        cert_probe = read_cert_probe()
        if cert_probe.get("domain") and cert_probe.get("domain") != domain:
            cert_probe = {}   # stale record from a previous address
    except Exception:
        cert_probe = {}
    return {
        "domain": domain,
        "configured": bool(domain),
        "cert_probe": cert_probe,
        "subdomain_routing": bool(cove.get("subdomain_routing")),
        # With a real domain, Caddy serves HTTPS → the browser grants the mic.
        "https_expected": bool(domain),
        # CF-90b: mesh state for the two-step Set Address flow (mesh FIRST, then DNS).
        "reachable": _reachable(cove),
        "voice": voice,
        "service_urls": ({
            "mc": f"https://{domain}",
            "cloud": f"https://cloud.{domain}",
            "voice": f"https://voice.{domain}",
            "matrix": f"https://{_matrix_server_name(domain)}",
        } if domain else {}),
    }


@router.get("/api/domain/status")
async def domain_status():
    """Current address state for the first-run "Claim your address" card.
    Readable within the Cove; the set action is Admin-only."""
    try:
        return _status_payload()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"domain status failed: {e}"})


class DomainSet(BaseModel):
    domain: str
    target: Optional[str] = None        # "hosted" | "self_host" (hint; auto otherwise)
    mesh_ip: Optional[str] = None        # the box's mesh IP for the A records (optional)
    own_dns_token: Optional[str] = None  # own-domain power path: a Cloudflare token so we
                                         # auto-create DNS + cert in the operator's zone.
                                         # Never persisted; used only for this reconcile.
    confirm: Optional[bool] = False      # required to CHANGE an address that's already live
                                         # (the guard against silently repointing a working
                                         # Cove). Not needed for the first claim.


@router.post("/api/domain/set")
async def domain_set(body: DomainSet, request: Request):
    """Claim the Cove's address. ADMIN PRESENCE ONLY.

    Step 1 (always, in-container): validate + persist domain + subdomain_routing to
    cove.yaml — this alone makes every domain-derived URL (MC/Connect/cloud/voice/
    sign-in) resolve correctly the moment DNS + cert exist.
    Step 2 (best-effort): DNS A records (*.{domain} + apex → mesh IP) via Cloudflare,
    when CLOUDFLARE_API_TOKEN is present here.
    Step 3 (best-effort): the per-Cove Caddy block + reload, when the host Caddy dir
    is reachable from here. When it isn't (the normal case), return the exact
    host-side command to finish.
    """
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})

    domain = _clean_domain(body.domain)
    if not domain or not _DOMAIN_RE.match(domain):
        return JSONResponse(status_code=400, content={
            "error": "Enter a valid address, e.g. smith.lucidcove.org or coolfamily.org"})

    cove = load_cove_config()
    current_domain = (cove.get("domain") or "").strip()
    cove_id = _cove_id(cove)
    # Matrix routing flag: prefer the cove.yaml record, but derive from the
    # provision-stamped Matrix env when the yaml is silent — older installs never
    # wrote a matrix: block, and without this every in-browser claim omitted the
    # matrix.{domain} site + well-known handlers from the Caddy snippet (Connect
    # stayed dead on the claimed address even though Dendrite was up).
    matrix_on = bool((cove.get("matrix") or {}).get("enabled")) or bool(
        os.environ.get("MATRIX_SERVER_NAME") or os.environ.get("MATRIX_HUB_URL"))
    ports = _ports()
    mesh_ip = (body.mesh_ip or "").strip()

    # C2 (locked): changing an already-live address RE-RUNS the full claim/reconcile pipeline
    # (persist → DNS → Caddy → NC-HTTPS → Matrix), exactly like a first claim — the branches
    # below don't special-case a change, so confirming just flows through them again. The one
    # thing that can't silently follow is the Matrix/Connect IDENTITY: the regen is VIRGIN-ONLY
    # (safe only when no one has chatted). On a Cove with existing conversations we PLAIN-WARN
    # and leave Connect on the previous homeserver rather than wiping history — reconcile_matrix
    # _identity already returns exactly that verdict; here we surface the caveat on every path.
    # (Stale old registrar/DNS records for the previous address are CF-104's problem.)
    _is_change = bool(current_domain and current_domain != domain)
    _change_matrix_note = (
        "Changing the address: Connect (chat) moves to the new address only if no one has "
        "chatted on this Cove yet — otherwise your existing conversations stay on the previous "
        "address, and only new links use the new one."
        if (_is_change and matrix_on) else "")

    # The host-side reconciler command (the sovereign / co-located privileged step). Built
    # up-front so EVERY path — including the bundled/shared "live" paths below — can hand it
    # back when a step it can't do in-container (Matrix regen, NC HTTPS) is still owed. The
    # regen needs the docker socket + the host-side (read-only-in-container) dendrite.yaml,
    # which is exactly why the app can't do it here (docker-socket-in-app is a rejected surface).
    _mx_flag = "" if matrix_on else " --no-matrix"
    _mesh_flag = f" --mesh-ip {mesh_ip}" if mesh_ip else ""
    # CF-124 (RUN-4 smith): this command runs ON THE HOST, so it must NOT use the
    # container path (/cove-core/... doesn't exist on the host — Errno 2). The provisioner
    # stamps COVE_HOST_DIR = <clone>/out/<id>-cove; the clone root (where provision/ lives)
    # is two levels up. Fall back to the ~/cove-* glob for pre-stamp installs.
    # LP_MATRIX_REGEN_ENABLED is required for the regen half, so bake it in rather than
    # relying on checklist memory.
    _host_instance_dir = (os.environ.get("COVE_HOST_DIR", "") or "").strip()
    _host_clone_dir = (
        posixpath.dirname(posixpath.dirname(_host_instance_dir))
        if _host_instance_dir else "~/cove-*/"
    )
    host_command = (
        f"cd {_host_clone_dir} && LP_MATRIX_REGEN_ENABLED=1 "
        f"python3 provision/set_domain.py --domain {domain} "
        f"--cove-id {cove_id} --app-port {ports['app']} "
        f"--nextcloud-port {ports['nextcloud']} --matrix-port {ports['matrix']}"
        f"{_mx_flag}{_mesh_flag}"
    )

    # B14 + batch-10 #2: the domain door — once the Cove is live at https://{domain}, hand back
    # a WORKING door so a brand-new operator can cross from localhost into their Cove in ONE
    # click. History (keep the lesson): we must NOT stamp a bare cove.yaml operator_token —
    # `/p/` tokens are stored only as hashes, so a stamped raw token 401s on first click (the
    # T3 bug) because no matching session row exists. The fix is to MINT the token HERE via the
    # live store (mint_signin_door also creates the matching 'pending' auth_sessions row), so the
    # first click validates. Falls back to the bare Cove root if the mint fails, so a door-mint
    # hiccup can never block the claim.
    domain_door = f"https://{domain}"
    try:
        from src.dashboard.routes.presence import get_current_presence, mint_signin_door
        _op = await get_current_presence(request)
        if _op and _op.get("id"):
            _scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
            _minted = await mint_signin_door(_op["id"], domain, _scheme)
            if _minted:
                domain_door = _minted
    except Exception:
        pass  # never block the claim on a door-mint hiccup — the bare root still loads

    def _matrix_needs_host() -> bool:
        """Run-3 fix: the in-browser claim can reconcile DNS + Caddy live, but it can NEVER
        regenerate the Matrix identity from in-container (no docker socket, dendrite.yaml is
        a read-only bind mount). So whenever this Cove has Matrix on, the claim is NOT fully
        live until the operator runs set_domain.py on the host — a claim that reported
        fully_live while Connect stayed dead was the exact run-3 trap."""
        import shutil as _sh
        return bool(matrix_on) and _sh.which("docker") is None

    # ── Guard: changing an address that's ALREADY live is destructive (it repoints every
    # URL — MC, cloud, voice, matrix, sign-in links). Require an explicit confirm so a
    # working Cove can't be silently knocked offline by an accidental edit. First claim
    # (no domain yet) and a no-op re-set of the SAME domain don't need it. ──
    if current_domain and current_domain != domain and not body.confirm:
        return JSONResponse(status_code=200, content={
            "ok": False, "code": "confirm_change",
            "current_domain": current_domain, "domain": domain,
            "message": (f"This Cove already lives at {current_domain}. Changing it to {domain} "
                        f"repoints every link (MC, cloud, voice, sign-in) and won't work until "
                        f"DNS + the certificate exist for {domain}. Confirm to proceed."),
            "matrix_note": _change_matrix_note or None,
        })

    # save_cove_config returns False on a read-only mount; helper surfaces that as a 500
    # instead of a misleading success. We persist ONLY after a reconcile that didn't hard-fail
    # (see below), so a failed change leaves the current address intact.
    def _persist() -> bool:
        try:
            return bool(save_cove_config({"domain": domain, "subdomain_routing": True}))
        except Exception as e:
            log.error("save_cove_config(domain=%s) failed: %s", domain, e)
            return False

    _ro_error = JSONResponse(status_code=500, content={
        "error": "Could not save the address — the Cove config isn't writable. "
                 "(Is config mounted read-only? It must be read-write for settings to persist.)"})

    steps = {"config": {"ok": False}, "dns": {"ok": False, "skipped": True},
             "caddy": {"ok": False, "skipped": True}}

    # ── Bundled-Caddy self-host: do it ALL live, in-browser ──────────────────
    # The Cove ships its own Caddy with an admin API, so the whole reconcile (DNS +
    # cert + live Caddy reload) runs server-side from this one click — no host command,
    # no terminal. This is the "no hard setup" path. Co-located Coves fall through to
    # the host-Caddy reconcile below.
    try:
        from provision import runtime_address
    except Exception:
        runtime_address = None
    # Shared-Caddy box (multiple Coves, one Caddy on 80/443): write this Cove's conf.d
    # snippet into the shared Caddy + live-reload over the bridge — all in-app.
    # CF-90b (locked): mesh join is STEP 1 of Set Address — a claim that would write
    # unreachable records (home/NAT box, no mesh IP, no owned public IP) is refused
    # with a structured code so the UI walks the operator through the mesh step first.
    # A box that owns its public IP (rented VPS) or has an explicit deploy.host_ip
    # passes untouched — _auto_dns resolves those.
    def _mesh_required(live_result: dict):
        if ((live_result.get("dns") or {}).get("no_ip")):
            return JSONResponse(status_code=200, content={
                "ok": False, "code": "mesh_required", "domain": domain,
                "message": ((live_result.get("dns") or {}).get("reason")
                            or "Put this box on the mesh first, then claim the address."),
                "unchanged": current_domain or None,
            })
        return None

    if runtime_address is not None and runtime_address.shared_caddy():
        live = runtime_address.set_address_live_shared(
            domain, mesh_ip=mesh_ip,
            own_dns_token=(body.own_dns_token or "").strip(),
            matrix_on=matrix_on, matrix_server_name=_matrix_server_name(domain))
        _mr = _mesh_required(live)
        if _mr is not None:
            return _mr
        # Persist ONLY when the reconcile applied the routing (ok) or staged DNS records the
        # operator will add (records) — either way the address is genuinely claimed. A HARD
        # fail (no routing, no records) leaves the current address untouched, so a broken
        # change can't knock the Cove offline.
        if not (live.get("ok") or live.get("records")):
            return JSONResponse(status_code=502, content={
                "ok": False, "mode": "live-shared", "domain": domain, "live": live,
                "error": (live.get("reason") or (live.get("caddy") or {}).get("reason")
                          or "Couldn't apply the new address — routing didn't reload."),
                "unchanged": current_domain or None,
            })
        if not _persist():
            return _ro_error
        _mx_host = _matrix_needs_host()
        _next = list(live.get("next", []))
        if _change_matrix_note:
            _next.insert(0, _change_matrix_note)
        if _mx_host:
            _next.append(
                "Finish Connect (Matrix) by running this on the Cove's host — it regenerates the "
                "homeserver identity to matrix.%s so Connect works (it can't be done from the "
                "browser):\n  %s" % (domain, host_command))
        return {
            "ok": True,
            "domain": domain,
            "mode": "live-shared",
            "fully_live": bool(live.get("ok")) and not live.get("records") and not _mx_host,
            "live": live,
            "records": live.get("records", []),
            "next_steps": _next,
            "host_command": host_command if _mx_host else None,
            "door": domain_door,
            "status": _status_payload(),
        }
    if runtime_address is not None and runtime_address.bundled_caddy():
        live = runtime_address.set_address_live(
            domain, mesh_ip=mesh_ip,
            own_dns_token=(body.own_dns_token or "").strip(),
            matrix_on=matrix_on, matrix_server_name=_matrix_server_name(domain))
        _mr = _mesh_required(live)
        if _mr is not None:
            return _mr
        if not (live.get("ok") or live.get("records")):
            return JSONResponse(status_code=502, content={
                "ok": False, "mode": "live", "domain": domain, "live": live,
                "error": (live.get("reason") or (live.get("caddy") or {}).get("reason")
                          or "Couldn't apply the new address — Caddy didn't reload."),
                "unchanged": current_domain or None,
            })
        if not _persist():
            return _ro_error
        _mx_host = _matrix_needs_host()
        _next = list(live.get("next", []))
        if _change_matrix_note:
            _next.insert(0, _change_matrix_note)
        if _mx_host:
            _next.append(
                "Finish Connect (Matrix) by running this on the Cove's host — it regenerates the "
                "homeserver identity to matrix.%s so Connect works (it can't be done from the "
                "browser):\n  %s" % (domain, host_command))
        return {
            "ok": True,
            "domain": domain,
            "mode": "live",
            "fully_live": bool(live.get("ok")) and not live.get("records") and not _mx_host,
            "live": live,
            "records": live.get("records", []),
            "next_steps": _next,
            "host_command": host_command if _mx_host else None,
            "door": domain_door,
            "status": _status_payload(),
        }

    # ── Co-located host-Caddy path: the privileged DNS/Caddy step can't run in-container
    # (no token, host Caddy dir not mounted), so this DECLARES intent — persist the domain so
    # every URL derives from it — and hands back the host command to finish DNS + Caddy. The
    # confirm-on-change guard above means this can't silently repoint an already-live Cove. ──
    if not _persist():
        return _ro_error
    steps["config"]["ok"] = True

    # ── Steps 2 + 3: the privileged reconcile — best-effort, never fatal ─────
    # provision/ is NOT baked into the app image — only /app/src is merged by the
    # entrypoint. It lives on the /cove-core:ro mount (present on every deploy
    # shape), so fall back to importing from there. Run-3 find: the bare import
    # failed on every self-host install and the silent `except` swallowed it, so
    # the ENTIRE reconcile chain (DNS/Caddy/NC-HTTPS/Matrix regen) no-opped while
    # the claim returned 200. Never let this skip silently again.
    try:
        from provision import netconfig
    except Exception:
        try:
            import sys as _sys
            if "/cove-core" not in _sys.path:
                _sys.path.insert(0, "/cove-core")
            from provision import netconfig
        except Exception as _e:
            print(f"[domain/set] netconfig unavailable — privileged reconcile "
                  f"(DNS/Caddy/NC-HTTPS/Matrix) SKIPPED: {type(_e).__name__}: {_e}")
            netconfig = None

    if netconfig is not None:
        # DNS (needs CLOUDFLARE_API_TOKEN in this env; ensure_dns says so if absent)
        try:
            steps["dns"] = netconfig.ensure_dns(domain, mesh_ip)
        except Exception as e:
            steps["dns"] = {"ok": False, "reason": f"DNS step error: {e}"}
        # Caddy (needs the host Caddy dir mounted here; returns installed=False otherwise)
        try:
            snippet = netconfig.build_cove_caddy_snippet(
                cove_id=cove_id, domain=domain,
                app_port=ports["app"], nextcloud_port=ports["nextcloud"],
                matrix_port=ports["matrix"], voice_port=ports["voice"],
                matrix_server_name=_matrix_server_name(domain), matrix_on=matrix_on,
            )
            res = netconfig.install_caddy_snippet(snippet, cove_id)
            steps["caddy"] = {"ok": bool(res.get("reloaded")), **res}
        except Exception as e:
            steps["caddy"] = {"ok": False, "reason": f"Caddy step error: {e}"}
        # NC HTTPS reconcile (CF-101 cluster / CF-100 finding, hit live on nottington A2):
        # the in-browser claim configured DNS + Caddy but the running Nextcloud never
        # learned its https:// domain — overwrite.cli.url stayed http://localhost and the
        # desktop Login Flow hung. Same detached occ sets the CLI set_domain.py dispatches.
        # Best-effort: never fatal to the claim, not part of fully_live.
        try:
            steps["nextcloud_https"] = netconfig.reconcile_nextcloud_https(
                cove_id=cove_id, domain=domain)
        except Exception as e:
            steps["nextcloud_https"] = {"ok": False, "reason": f"NC step error: {e}"}
        # Matrix identity reconcile (CF-101 THE Connect fix): a domainless Cove
        # stamped its Dendrite server_name matrix.{cove-id}.localhost, so Connect
        # spins forever against a homeserver that isn't matrix.{domain}. If the
        # homeserver is still virgin (only agents), it can be regenerated to
        # matrix.{domain}. Best-effort + report-only by default (the DB wipe is gated
        # behind LP_MATRIX_REGEN_ENABLED); never fatal to the claim, not in fully_live.
        if matrix_on:
            try:
                from src.config import get_agents
                _agent_lps = [a.get("id") for a in (get_agents() or []) if a.get("id")]
                _agent_lps += ["steward", "lt", "agent"]  # steward + shared bot localparts
                steps["matrix_identity"] = netconfig.reconcile_matrix_identity(
                    cove_id=cove_id, domain=domain, agent_localparts=_agent_lps)
            except Exception as e:
                steps["matrix_identity"] = {"ok": False, "reason": f"Matrix step error: {e}"}

    dns_ok = bool(steps["dns"].get("ok"))
    caddy_ok = bool(steps["caddy"].get("ok"))
    # host_command was built up-front (used by the live paths above too).

    next_steps = []
    if _change_matrix_note:
        next_steps.append(_change_matrix_note)
    if not dns_ok:
        next_steps.append(
            f"Point DNS at this box: create A records for {domain} and *.{domain} → your mesh/public IP "
            f"({steps['dns'].get('reason') or 'no Cloudflare token here'})."
        )
    if not caddy_ok:
        next_steps.append(
            "Issue HTTPS + route the domain by running this on the Cove's host (it reloads Caddy):\n"
            f"  {host_command}"
        )
    if dns_ok and caddy_ok:
        next_steps.append(
            f"Done — once the cert is issued (~30-60s) load https://{domain}; the mic/voice will work."
        )
    # CF-101: surface the Matrix Connect-address outcome (virgin regen offer, or the
    # "existing conversations" block) so the operator knows why Connect may still point
    # at the provisioned homeserver.
    _mx = steps.get("matrix_identity") or {}
    if _mx.get("message"):
        next_steps.append("Connect (Matrix): " + _mx["message"])
    # Run-3 fix: if the Matrix regen can't run from here (no docker socket in-container),
    # the address isn't fully live until the host command runs — don't claim otherwise.
    _mx_host = _matrix_needs_host()
    if _mx_host and not any("set_domain.py" in s for s in next_steps):
        next_steps.append(
            "Finish Connect (Matrix) by running this on the Cove's host (regenerates the "
            "homeserver identity to matrix.%s):\n  %s" % (domain, host_command))

    return {
        "ok": steps["config"]["ok"],
        "domain": domain,
        "fully_live": steps["config"]["ok"] and dns_ok and caddy_ok and not _mx_host,
        "steps": steps,
        "host_command": host_command,
        "next_steps": next_steps,
        "door": domain_door,
        "status": _status_payload(),
    }


async def _resolve_a(host: str, timeout: float = 3.0) -> str:
    """Resolve one A record (stdlib, no dns library) with a bounded timeout, off the event
    loop. Returns the IP string or '' on any failure/timeout."""
    def _do() -> str:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            return socket.gethostbyname(host)
        except Exception:
            return ""
        finally:
            socket.setdefaulttimeout(old)
    try:
        return await asyncio.to_thread(_do)
    except Exception:
        return ""


@router.post("/api/domain/check-records")
async def check_records(request: Request):
    """C1 (locked): guided-manual 'Check my records' — resolve the operator's own-domain A
    records and report, per record, whether they point at this box yet. PURE DNS resolution,
    NO registrar API: the operator adds records at ANY registrar and verifies here. The
    expected IP is exactly what they were handed to enter (_reachable → the box's mesh/host
    IP), so the check is self-consistent with the records the claim returned. Admin-only."""
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    cove = load_cove_config()
    domain = (cove.get("domain") or "").strip()
    if not domain:
        return {"ok": False, "reason": "Set your address first, then add its records at your registrar."}
    expected = (_reachable(cove).get("ip") or "").strip()
    # apex + a random wildcard probe (any subdomain resolves when the *.{domain} record exists).
    probe = f"lpcheck-{_secrets.token_hex(3)}.{domain}"
    apex_ip = await _resolve_a(domain)
    wild_ip = await _resolve_a(probe)

    def _rec(label: str, name: str, resolved: str) -> dict:
        # ok = resolves AND (matches the expected box IP, or we don't have an expected to compare).
        ok = bool(resolved) and (not expected or resolved == expected)
        return {"label": label, "name": name,
                "expected": expected or "your box's IP",
                "resolved": resolved or "", "ok": ok}

    records = [
        _rec("Your address", domain, apex_ip),
        _rec("Everything under it", "*." + domain, wild_ip),
    ]
    all_ok = all(r["ok"] for r in records)
    return {
        "ok": True, "all_ok": all_ok, "domain": domain, "expected_ip": expected,
        "records": records,
        "message": (
            f"Both records point at this box — HTTPS for https://{domain} will issue shortly."
            if all_ok else
            "Not pointing here yet. After you add the records at your registrar, DNS can take "
            "a few minutes to propagate — check again shortly."),
    }


@router.post("/api/domain/reconcile-dns")
async def reconcile_dns(request: Request):
    """CF-90b self-heal: re-point the EXISTING address at the box's CURRENT mesh IP.

    Called by connect-mesh.sh on localhost right after the box joins the mesh (that
    shell has no browser session, so this is a PUBLIC_PATH). Deliberately takes NO
    input: it only re-asserts what the Cove already claims (its configured domain →
    its own current mesh/host IP), so the worst an outside caller can do is make the
    Cove re-assert correct state. No domain change, no persist, no confirm bypass —
    the claim-change guard in /api/domain/set is untouched."""
    cove = load_cove_config()
    domain = (cove.get("domain") or "").strip()
    if not domain:
        return {"ok": False, "reason": "no address set yet — claim one in the Cove first"}
    reach = _reachable(cove)
    if not reach.get("ok"):
        return {"ok": False, "reason": "no mesh/host IP visible here yet — is COVE_MESH_IP "
                                       "in the instance .env and the app recreated?"}
    try:
        from provision import centralized as C
    except Exception as e:
        return {"ok": False, "reason": f"provision modules unavailable: {str(e)[:120]}"}
    try:
        dns = C._auto_dns(domain, {"mesh_ip": reach["ip"]}, {})
    except Exception as e:
        dns = {"ok": False, "reason": f"auto-DNS error: {str(e)[:160]}"}
    return {"ok": bool(dns.get("ok")), "domain": domain, "ip": reach["ip"],
            "dns": {k: dns.get(k) for k in ("ok", "auto", "via", "reason") if k in dns},
            "records": ([] if dns.get("auto") else dns.get("records", []))}
