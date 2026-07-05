# =============================================================================
# provision_api.py — host-with-us auto-provisioning trigger (#167 + #143).
# =============================================================================
# Runs on the HUB (the shared app / registry master, app.lucidcove.org). When a
# customer buys a HOSTED Cove, Stripe → Socrates commerce → this endpoint, which
# stands up that family's Cove on the VPS automatically: it calls the SAME
# centralized provisioner a self-hoster runs from the CLI (provision/centralized.py
# generate_cove), with deploy.target=vps so model calls offload to the P620 GPU over
# the mesh (#143). No manual ops: the operator buys, and a reachable, federated,
# claimable Cove comes up.
#
# AUTH: gated by SHARED_CONTAINER_SECRET (the inter-service secret Socrates already
# uses for /api/account/upgrade). The middleware allowlists /api/hosting/* the same
# way /api/registry/* is allowlisted, and this endpoint enforces the secret itself.
#
# SAFETY / best-effort (matches netconfig's philosophy): generation always runs
# (pure file writes into the hosting output dir). The actual `docker compose up` —
# the one privileged step — only fires when HOSTING_AUTO_DEPLOY is on AND docker is
# reachable here; otherwise the Cove folder is generated and the response says
# "run compose to finish". This keeps the privileged orchestration an explicit,
# deploy-time opt-in rather than something baked into the request path.
# =============================================================================
import hmac
import logging
import os
from src.env import env, env_bool
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()

# Where generated hosted-Cove folders land (a host-mounted volume on the VPS so the
# host's docker can `compose up` them). Base domain every hosted Cove gets a
# wildcard under: {cove_id}.{HOSTING_BASE_DOMAIN}.
HOSTING_OUTPUT_DIR = env("HOSTING_OUTPUT_DIR", "/hosting/coves")
HOSTING_BASE_DOMAIN = env("HOSTING_BASE_DOMAIN", "lucidcove.org")
HOSTING_AUTO_DEPLOY = env_bool("HOSTING_AUTO_DEPLOY")
HOSTING_SECRET = env("SHARED_CONTAINER_SECRET")
# Default cove-core path INSIDE the generated compose context on the VPS host.
HOSTING_COVE_CORE_PATH = env("HOSTING_COVE_CORE_PATH", "/docker/cove-core")


def _require_secret(request: Request, body: dict):
    """Accept the shared secret as a header or a body field (Socrates posts it in the
    body for /api/account/upgrade; we accept either)."""
    supplied = request.headers.get("X-Shared-Secret", "") or (body.get("secret") or "")
    if not HOSTING_SECRET:
        raise HTTPException(501, "Hosting not configured (SHARED_CONTAINER_SECRET unset)")
    if not (supplied and hmac.compare_digest(supplied, HOSTING_SECRET)):
        raise HTTPException(403, "Invalid secret")


def _load_generate_cove():
    """Import generate_cove from the mounted cove-core provision/ package. The dir is
    added to sys.path so centralized.py's sibling `import netconfig` resolves (the
    same way the CLI runs it)."""
    for cand in ("/cove-core/provision", HOSTING_COVE_CORE_PATH + "/provision",
                 str(Path(__file__).resolve().parents[3] / "provision")):
        if cand not in sys.path and Path(cand).is_dir():
            sys.path.insert(0, cand)
    import centralized  # noqa: E402  (resolved via the path inserted above)
    return centralized.generate_cove


def _slugify(name: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "-" for c in (name or "")).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s


@router.post("/api/hosting/provision")
async def provision_hosted_cove(request: Request):
    """Provision a hosted Cove on the VPS. Body:
        { secret, email, cove_id?, cove_name, handle, team?, mesh_ip?, kb_public_key? }
    Returns { ok, cove_id, domain, claim_url, deployed, output_dir, [reason] }.
    Idempotent enough for a webhook retry: a second call regenerates the same folder
    (secrets rotate, so prefer not to re-run after first boot — the registry/claim are
    the dedupe points; commerce passes a stable cove_id where possible)."""
    body = await request.json()
    _require_secret(request, body)

    cove_name = (body.get("cove_name") or "").strip()
    handle = (body.get("handle") or "").strip().lstrip("@")
    email = (body.get("email") or "").strip()
    if not cove_name or not handle:
        raise HTTPException(400, "cove_name and handle are required")
    cove_id = (body.get("cove_id") or "").strip() or _slugify(cove_name)
    team = body.get("team", True)
    domain = f"{cove_id}.{HOSTING_BASE_DOMAIN}"

    cfg = {
        "cove": {"id": cove_id, "name": cove_name, "domain": domain},
        "operator": {"name": cove_name, "handle": handle, "email": email},
        "affiliate": {"referred_by": (body.get("referred_by") or "").strip()},  # #169
        "team": "on" if team else "off",
        "model_providers": body.get("model_providers", ["openrouter"]),
        "deploy": {
            "target": "vps",
            "cove_core_path": HOSTING_COVE_CORE_PATH,
            "mesh_ip": (body.get("mesh_ip") or env("HOSTING_MESH_IP")).strip(),
            "caddy_dir": env("HOSTING_CADDY_DIR").strip() or None,
        },
        "matrix": {"enabled": True},
        "ltp": {"dry_run": True, "kb_public_key": body.get("kb_public_key", env("LP_KB_PUBLIC_KEY"))},
    }
    # Drop None deploy keys so the provisioner falls back to its own defaults.
    cfg["deploy"] = {k: v for k, v in cfg["deploy"].items() if v not in (None, "")}

    try:
        generate_cove = _load_generate_cove()
        res = generate_cove(cfg, Path(HOSTING_OUTPUT_DIR))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("hosted provision failed")
        raise HTTPException(500, f"provision failed: {e}")

    out = {
        "ok": True, "cove_id": cove_id, "domain": domain,
        "claim_url": res.get("claim_url"), "output_dir": str(res.get("root")),
        "ports": res.get("ports"), "deployed": False,
        "registry": (res.get("registry") or {}).get("ok", False),
    }

    # The one privileged step — explicit opt-in + best-effort, never fatal.
    if HOSTING_AUTO_DEPLOY and shutil.which("docker"):
        try:
            r = subprocess.run(
                ["docker", "compose", "up", "-d", "--build"],
                cwd=str(res["root"]), capture_output=True, text=True, timeout=600,
            )
            out["deployed"] = r.returncode == 0
            if r.returncode != 0:
                out["reason"] = (r.stderr or r.stdout).strip()[:300]
        except Exception as e:
            out["reason"] = f"compose up error: {e}"
    elif not HOSTING_AUTO_DEPLOY:
        out["reason"] = "generated only (HOSTING_AUTO_DEPLOY off) — run `docker compose up -d --build` in output_dir"
    else:
        out["reason"] = "docker not reachable from the hub container — run compose on the host"

    log.info("hosted Cove provisioned: %s (%s) deployed=%s", cove_id, domain, out["deployed"])
    return out
