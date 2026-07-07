# =============================================================================
# reachability.py — make a self-host Cove PUBLICLY reachable (for remote invites).
# =============================================================================
# A home Cove is mesh-only behind NAT: a /join link works on-mesh but an off-mesh
# phone times out. To invite someone REMOTE, the Cove must sit at a public address.
# The clean, NAT-friendly way is a Cloudflare named tunnel (outbound only, no
# port-forward, hides the home IP). Same architecture as domain.py: the app declares
# intent + hands back the exact HOST-side command; the privileged docker/CF step runs
# on the box (an in-container app must never drive docker or hold the raw CF token).
#
# This is a ONE-TIME owner setup, orthogonal to the invite flow. The invitee never
# sees any of it — reachability is a property of the Cove.
#
# Hosted (VPS) Coves are public by default (the hosting tier's whole point) and don't
# need this. So the option only surfaces for a self-host Cove.
# =============================================================================
import logging
import os
import posixpath

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import load_cove_config, save_cove_config
from src.dashboard.routes.settings import _is_admin_presence

log = logging.getLogger(__name__)
router = APIRouter()


def _cove_id(cove: dict) -> str:
    try:
        from src.config import get_instance
        return (get_instance().get("id") or cove.get("id") or os.environ.get("COVE_ID") or "").strip()
    except Exception:
        return (cove.get("id") or os.environ.get("COVE_ID") or "").strip()


def _host_command(domain: str, cove_id: str) -> str:
    """The command the owner runs ON THE BOX to bring the tunnel up. Mirrors domain.py:
    COVE_HOST_DIR points at the instance dir; the clone root (where provision/ lives) is two
    levels up. Falls back to the ~/cove-* glob for pre-stamp installs."""
    host_instance_dir = (os.environ.get("COVE_HOST_DIR", "") or "").strip()
    host_clone_dir = (
        posixpath.dirname(posixpath.dirname(host_instance_dir))
        if host_instance_dir else "~/cove-*/"
    )
    return (f"cd {host_clone_dir} && python3 provision/enable_tunnel.py "
            f"--domain {domain} --cove-id {cove_id}")


def _status(cove: dict) -> dict:
    domain = (cove.get("domain") or "").strip()
    pub = cove.get("public") or {}
    if not isinstance(pub, dict):
        pub = {}
    return {
        "domain": domain,
        "eligible": bool(domain),                       # need an address before going public
        "mode": pub.get("mode") or "mesh",              # mesh | tunnel
        "tunnel_requested": bool(pub.get("requested")),
        "provider": pub.get("provider") or "",          # 'cloudflare' once enabled
    }


@router.get("/api/reachability/status")
async def reachability_status():
    """Is this Cove public yet, and can it be? Readable within the Cove."""
    try:
        return _status(load_cove_config())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"reachability status failed: {e}"})


@router.post("/api/reachability/public")
async def enable_public(request: Request):
    """Admin: request making this Cove publicly reachable via a Cloudflare tunnel.

    Persists the intent to cove.yaml and returns the one host-side command that finishes
    it (create the tunnel, run cloudflared, repoint DNS). The app can't run docker or hold
    the raw CF token, so — like the address claim — the owner runs one command on the box."""
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    cove = load_cove_config()
    domain = (cove.get("domain") or "").strip()
    if not domain:
        return JSONResponse(status_code=400, content={
            "error": "Claim your Cove's address first — a public tunnel needs a domain to route."})

    # Record the intent (mode flips to 'tunnel' once the host step reports success; that
    # confirmation isn't wired back yet, so we mark it 'requested' here).
    pub = dict(cove.get("public") or {}) if isinstance(cove.get("public"), dict) else {}
    pub.update({"requested": True, "provider": "cloudflare"})
    try:
        save_cove_config({"public": pub})
    except Exception as e:
        log.warning("reachability intent save failed (non-fatal): %s", e)

    return {
        "ok": True,
        "domain": domain,
        "host_command": _host_command(domain, _cove_id(cove)),
        "note": ("Run this once on the Cove's machine. It needs CLOUDFLARE_API_TOKEN "
                 "(Tunnel:Edit + DNS:Edit) and CLOUDFLARE_ACCOUNT_ID in the environment "
                 "or the Cove's docker/.env. After it finishes, remote /join links resolve "
                 "from any device. This exposes only what Caddy already serves; your home "
                 "IP stays hidden behind Cloudflare."),
    }
