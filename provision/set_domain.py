#!/usr/bin/env python3
"""
set_domain.py — host-side reconciler: give an already-running, domainless Cove a
real address (DNS + Caddy + HTTPS) AFTER the fact.

WHY THIS IS A SEPARATE CLI (not done inside the app): the Cove app runs in a
container and must not hold the docker socket or write the host Caddy dir (a
container escape would become host-root). So the in-MC "Claim your address"
endpoint (routes/domain.py) only writes the chosen domain to cove.yaml (the
intent) and then hands the operator this one command to run on the Cove's HOST,
where Caddy + docker + the compose actually live. Same privileged-step-is-explicit
philosophy as provision_api.py.

It reuses the exact functions the provisioner uses at build time:
  - netconfig.build_cove_caddy_snippet  → the per-Cove Caddy block
  - netconfig.install_caddy_snippet     → drop into conf.d + `caddy reload`
  - netconfig.ensure_dns                → *.{domain} + apex A records → mesh IP
so the late-bound result is identical to having provisioned with a domain.

After Caddy reloads and the cert issues (~30-60s), https://{domain} is live and
the browser will grant the mic in that secure context (voice "just works").

Usage (on the Cove's host):
  python3 /cove-core/provision/set_domain.py --domain smith.lucidcove.org \\
      --cove-id smith --app-port 8204 --nextcloud-port 8081 --matrix-port 8018
  # add --no-matrix if this Cove has no homeserver; --mesh-ip to pin the A record;
  # --caddy-dir to point at a non-default Caddy directory.

Needs CLOUDFLARE_API_TOKEN in the environment for the DNS step (the same token
Caddy uses for the DNS-01 cert). Without it, DNS is skipped and you point records
manually; Caddy still issues the cert once the records resolve.

After Caddy is up, this CLI ALSO verifies the host can resolve and reach the domain.
Public A records for mesh IPs (100.64.0.0/10) are often filtered by local resolvers
(DNS rebinding protection) even when Cloudflare has the record — that is the install
hard-stop (NXDOMAIN in the browser while the Cove is healthy). We repair host resolve:
Tailscale accept-dns, DNS cache flush, then a scoped /etc/hosts pin if still broken.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# netconfig is a sibling module in this provision/ dir (same as centralized.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import netconfig  # noqa: E402


def _matrix_server_name(domain: str) -> str:
    return f"matrix.{domain}" if domain else ""


def _load_instance_env(*dirs) -> None:
    """set_domain runs on the HOST, but the hub creds (LP_REGISTRY_URL / LP_OPERATOR_TOKEN)
    live in the Cove's instance .env, not the host shell. Load them so the hub acme-credential
    call can authenticate. Never overrides an explicit host export."""
    import os
    from pathlib import Path
    for d in dirs:
        if not d:
            continue
        p = Path(str(d)).expanduser() / ".env"
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


def _operator_token_from_container(cove_id: str) -> str:
    """The from-scratch operator token is minted at runtime into the Cove's cove.yaml (via
    save_cove_config), read in-container by _op_token — it is NOT in the instance .env (that
    slot is stamped empty at provision). set_domain runs on the host, so read the token
    straight from the running app container (the authoritative source) for the hub call."""
    import subprocess
    if not cove_id:
        return ""
    try:
        r = subprocess.run(
            ["docker", "exec", f"{cove_id}-app", "sh", "-c",
             "grep -rhE '^[[:space:]]*operator_token:' /app/config /app/data 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=15)
        line = (r.stdout or "").strip()
        if ":" in line:
            v = line.split(":", 1)[1].strip().strip('"').strip("'")
            return v
    except Exception:
        pass
    return ""


def _acme_creds_via_hub(domain: str) -> dict:
    """Ask the HUB to mint the acme-dns credential (operator-token gated). A self-host box holds
    no Cloudflare token and can't reach the private acme-dns /register, so the hub (which has
    both) mints it. Self-contained mirror of centralized._acme_creds_via_hub so set_domain
    needn't import the heavy provisioner on the host. Headers match _hub_auth_headers exactly."""
    import os, json, urllib.request
    reg = (os.getenv("LP_REGISTRY_URL", "") or "").strip().rstrip("/")
    sec = (os.getenv("LP_REGISTRY_SECRET", "") or "").strip()
    tok = (os.getenv("LP_OPERATOR_TOKEN", "") or "").strip()
    if not reg or not (sec or tok):
        return {"ok": False, "reason": "no hub auth (need LP_REGISTRY_URL + operator token or fleet secret)"}
    headers = {"Content-Type": "application/json", "User-Agent": "LucidCove-Cove/1.0"}
    if sec:
        headers["X-Registry-Secret"] = sec
    if tok:
        headers["X-Operator-Token"] = tok
    body = json.dumps({"sub_domain": domain}).encode()
    req = urllib.request.Request(reg + "/api/registry/acme-credential",
                                 data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "reason": f"hub acme-credential failed: {str(e)[:160]}"}


def _resolve_acme_creds(args, domain: str, result: dict) -> dict:
    """acme-dns credential for a lucidcove.org subdomain. Ask the HUB first (mirrors the
    bundled self-host path) — the old local provision_subdomain_cert_delegation needed
    LP_ACMEDNS_URL on the box, so on a stranger's box it silently skipped and Caddy got NO
    cert (tlsv1 internal error on the claimed domain). Fall back to the local delegation for a
    founder/co-located box that DOES hold the creds. Returns the acmedns dict for the snippet."""
    acme = {}
    if not (domain == "lucidcove.org" or domain.endswith(".lucidcove.org")):
        return acme
    # The host shell doesn't have the Cove's env — load it so the hub call can authenticate.
    # The creds (LP_REGISTRY_URL/LP_OPERATOR_TOKEN) live in the INSTANCE .env at
    # out/<cove-id>-cove/.env (where docker-compose.yml is), NOT the clone root, so search
    # there first, then any explicit --cove-dir/--compose-dir, then cwd.
    from pathlib import Path as _P
    _cid = (getattr(args, "cove_id", "") or "").strip()
    _inst = str(_P("out") / f"{_cid}-cove") if _cid else ""
    _load_instance_env(_inst,
                       getattr(args, "cove_dir", "") or "",
                       getattr(args, "compose_dir", "") or "", ".")
    # A from-scratch Cove keeps its operator token in cove.yaml (not the .env). If the .env
    # didn't supply one, read it from the running container so the hub call can authenticate.
    import os as _os
    if not (_os.getenv("LP_OPERATOR_TOKEN", "") or "").strip():
        _tok = _operator_token_from_container(_cid)
        if _tok:
            _os.environ["LP_OPERATOR_TOKEN"] = _tok
    _ac = _acme_creds_via_hub(domain)
    if not (isinstance(_ac, dict) and _ac.get("ok")):
        try:
            from acmedns import provision_subdomain_cert_delegation
        except ImportError:
            from provision.acmedns import provision_subdomain_cert_delegation
        _local = provision_subdomain_cert_delegation(domain)
        if isinstance(_local, dict) and _local.get("ok"):
            _ac = _local
    result["acmedns"] = _ac
    if isinstance(_ac, dict) and _ac.get("ok"):
        acme = _ac.get("acmedns") or {}
    return acme


def _self_host_reconcile(args, domain: str, matrix_on: bool, result: dict) -> bool:
    """Self-host (bundled Caddy) path: render the Cove's own docker/Caddyfile (acme-dns
    DNS-01 for a lucidcove.org subdomain so no CF token is needed on this box; HTTP-01
    for an own domain), then (re)start the bundled caddy service. The Cove ships its own
    Caddy — we don't touch any host Caddy. Returns True on a successful caddy (re)start."""
    import subprocess
    from pathlib import Path
    compose_dir = Path(args.compose_dir).expanduser().resolve()
    if not (compose_dir / "docker-compose.yml").is_file():
        result["caddy"] = {"ok": False, "reason": f"no docker-compose.yml in {compose_dir} (pass --compose-dir)"}
        return False
    # acme-dns for a lucidcove.org subdomain — hub-minted (our token stays on the hub).
    acme = _resolve_acme_creds(args, domain, result)
    caddyfile = netconfig.build_selfhost_caddyfile(
        domain=domain, app_port=args.app_port,
        matrix_server_name=_matrix_server_name(domain), matrix_on=matrix_on, acmedns=acme)
    (compose_dir / "docker" / "Caddyfile").write_text(caddyfile)
    try:
        r = subprocess.run(["docker", "compose", "up", "-d", "--build", "caddy"],
                           cwd=str(compose_dir), capture_output=True, text=True, timeout=600)
        ok = r.returncode == 0
        result["caddy"] = {"ok": ok, "reloaded": ok,
                           "reason": "" if ok else (r.stderr or r.stdout).strip()[:300]}
        return ok
    except Exception as e:
        result["caddy"] = {"ok": False, "reason": f"compose up caddy error: {e}"}
        return False


def _shared_reconcile(args, domain: str, matrix_on: bool, result: dict) -> bool:
    """Shared-Caddy mode (multi-Cove box): write this Cove's haven snippet into the SHARED
    Caddy's conf.d + reload it. Container-name routes over the bridge; per-site TLS (acme-dns
    for a lucidcove.org subdomain, else default). The shared Caddy owns 80/443 for the whole
    box. Returns True on a successful reload."""
    acme = _resolve_acme_creds(args, domain, result)
    snippet = netconfig.build_haven_cove_snippet(
        cove_id=args.cove_id, domain=domain, app_port=args.app_port,
        matrix_server_name=_matrix_server_name(domain), matrix_on=matrix_on,
        voice_on=(not args.no_voice), acmedns=acme)
    install_kwargs = {}
    if args.caddy_dir.strip():
        install_kwargs["caddy_dir"] = args.caddy_dir.strip()
    result["caddy"] = netconfig.install_haven_cove_snippet(snippet, args.cove_id, **install_kwargs)
    return bool(result["caddy"].get("reloaded"))


def _reconcile_nextcloud_https(args, domain: str, result: dict) -> None:
    """Tell the already-running Nextcloud it lives behind Caddy's TLS termination, so the
    desktop "Add account" Login Flow hands back an https:// callback instead of the http://
    one the client rejects ("returned server URL does not start with HTTPS").

    provision/centralized.py bakes OVERWRITEPROTOCOL/OVERWRITEHOST/OVERWRITECLIURL/TRUSTED_PROXIES
    into the NC compose env, but ONLY when a domain is known at build time. A Cove that comes
    up domainless and claims an address in-browser later never got them. The container is
    already running here, and NC's image only reads those envs at create time — so we apply
    the runtime equivalent with `occ config:system:set`. config.php lives in the nextcloud_data
    volume, so these values survive future container recreates (durable, like the env path).

    Gated to a domain being set (mirrors centralized's `if domain` gate). Best-effort: a
    failure never fails the address claim — the reason is recorded under result["nextcloud_https"].
    """
    if not domain:
        return
    # The occ dispatch itself lives in netconfig.reconcile_nextcloud_https — SHARED with
    # the in-browser claim path (dashboard/routes/domain.py), which used to reconcile
    # DNS + Caddy but never NC (the CF-100 "claim reconciles nothing else" finding).
    result["nextcloud_https"] = netconfig.reconcile_nextcloud_https(
        cove_id=args.cove_id, domain=domain,
        nextcloud_container=(getattr(args, "nextcloud_container", "") or ""),
        trusted_proxies=(getattr(args, "trusted_proxies", "") or ""))


def _restamp_matrix_env(cove_dir: str, domain: str) -> dict:
    """After a real Matrix regen the app container's baked MATRIX_SERVER_NAME +
    MATRIX_PUBLIC_URL still name the OLD matrix.{cove-id}.localhost identity, so agent
    user-ids and the Connect client keep pointing at the wrong homeserver until the app
    is recreated. Rewrite both in the instance `.env` and hand back the recreate command.

    DESIGN CHOICE (env-restamp vs derive-user-ids-from-domain-at-runtime): we restamp the
    env because it's a contained host-side edit set_domain.py already owns and it fixes
    EVERY consumer of the two vars at once. Runtime derivation would touch the live Matrix
    client path (the matrix_token self-heal item's territory) and leave the two env vars
    lying about the identity for anything else that reads them. Restamp is the smaller,
    more honest change here."""
    server, public = f"matrix.{domain}", f"https://matrix.{domain}"
    if not cove_dir:
        return {"ok": False, "reason": (
            f"no --cove-dir: set MATRIX_SERVER_NAME={server} and MATRIX_PUBLIC_URL={public} "
            f"in the instance .env, then `docker compose up -d app`")}
    env_path = Path(cove_dir).expanduser() / ".env"
    updates = {"MATRIX_SERVER_NAME": server, "MATRIX_PUBLIC_URL": public}
    try:
        lines = env_path.read_text().splitlines() if env_path.is_file() else []
        seen = set()
        for i, ln in enumerate(lines):
            for k, v in updates.items():
                if re.match(rf"^\s*{re.escape(k)}\s*=", ln):
                    lines[i] = f"{k}={v}"
                    seen.add(k)
        for k, v in updates.items():
            if k not in seen:
                lines.append(f"{k}={v}")
        env_path.write_text("\n".join(lines) + "\n")
        return {"ok": True, "path": str(env_path), "server_name": server, "public_url": public,
                "recreate": f"(cd {cove_dir} && docker compose up -d app)  # pick up new Matrix identity"}
    except Exception as e:
        return {"ok": False, "reason": f"env restamp failed: {str(e)[:120]}"}


def _reconcile_matrix_identity(args, domain: str, result: dict) -> None:
    """Host-side Matrix server_name regen (virgin-only, gated). See the call site."""
    agents = [a.strip() for a in (args.agents or "").split(",") if a.strip()]
    agents += ["steward", "lt", "agent"]   # shared bot localparts (mirror domain.py)
    cove_dir = (getattr(args, "cove_dir", "") or getattr(args, "compose_dir", "") or "").strip()
    if cove_dir in (".", ""):
        cove_dir = os.getcwd() if cove_dir == "." else cove_dir
    mx = netconfig.reconcile_matrix_identity(
        cove_id=args.cove_id, domain=domain, agent_localparts=agents,
        postgres_container=(getattr(args, "postgres_container", "") or "").strip(),
        dendrite_container=(getattr(args, "dendrite_container", "") or "").strip(),
        cove_dir=cove_dir)
    result["matrix_identity"] = mx
    if mx.get("changed"):
        result["matrix_env"] = _restamp_matrix_env(cove_dir, domain)



def _is_mesh_ip(ip: str) -> bool:
    """True for Tailscale/Headscale CGNAT 100.64.0.0/10."""
    ip = (ip or "").strip()
    if not ip.startswith("100."):
        return False
    try:
        second = int(ip.split(".")[1])
    except Exception:
        return False
    return 64 <= second <= 127


def _detect_mesh_ip_host() -> str:
    """Best-effort mesh IPv4 on the host running set_domain."""
    import shutil
    import subprocess
    try:
        if shutil.which("tailscale"):
            out = subprocess.run(
                ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=8)
            if out.returncode == 0:
                for line in (out.stdout or "").splitlines():
                    cand = line.strip()
                    if _is_mesh_ip(cand):
                        return cand
    except Exception:
        pass
    return ""


def _resolve_a_system(host: str, timeout: float = 3.0) -> str:
    """System resolver (same path curl/Chrome use). Empty on failure."""
    import socket
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        return socket.gethostbyname(host) or ""
    except Exception:
        return ""
    finally:
        socket.setdefaulttimeout(old)


def _resolve_a_doh(host: str, timeout: float = 5.0) -> str:
    """Public DNS via Cloudflare DoH — bypasses local rebinding filters."""
    import json
    import urllib.request
    url = f"https://cloudflare-dns.com/dns-query?name={host}&type=A"
    req = urllib.request.Request(
        url, headers={"accept": "application/dns-json", "User-Agent": "LucidCove-set-domain/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
        for ans in data.get("Answer") or []:
            if ans.get("type") == 1 and ans.get("data"):
                return str(ans["data"]).strip()
    except Exception:
        pass
    return ""


def _tailscale_accept_dns() -> dict:
    """Prefer mesh DNS on this host so public rebinding filters matter less."""
    import shutil
    import subprocess
    if not shutil.which("tailscale"):
        return {"ok": False, "skipped": True, "reason": "tailscale not installed"}
    try:
        r = subprocess.run(
            ["tailscale", "set", "--accept-dns=true"],
            capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            r = subprocess.run(
                ["sudo", "tailscale", "set", "--accept-dns=true"],
                capture_output=True, text=True, timeout=20)
        ok = r.returncode == 0
        return {
            "ok": ok,
            "reason": "" if ok else ((r.stderr or r.stdout or "").strip()[:200] or "tailscale set failed"),
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}


def _flush_host_dns_cache() -> dict:
    """Best-effort OS DNS cache flush (macOS + common Linux)."""
    import platform
    import shutil
    import subprocess
    system = platform.system().lower()
    actions = []
    try:
        if system == "darwin":
            subprocess.run(["sudo", "dscacheutil", "-flushcache"],
                           capture_output=True, text=True, timeout=15)
            subprocess.run(["sudo", "killall", "-HUP", "mDNSResponder"],
                           capture_output=True, text=True, timeout=15)
            actions.append("macos-flush")
        elif system == "linux":
            if shutil.which("resolvectl"):
                subprocess.run(["sudo", "resolvectl", "flush-caches"],
                               capture_output=True, text=True, timeout=15)
                actions.append("resolvectl")
            elif shutil.which("systemd-resolve"):
                subprocess.run(["sudo", "systemd-resolve", "--flush-caches"],
                               capture_output=True, text=True, timeout=15)
                actions.append("systemd-resolve")
    except Exception as e:
        return {"ok": False, "actions": actions, "reason": str(e)[:120]}
    return {"ok": True, "actions": actions}


def _hosts_path() -> Path:
    return Path("/etc/hosts")


def _ensure_hosts_pin(domain: str, ip: str) -> dict:
    """Idempotent /etc/hosts pin for the Cove apex (install-host hard-stop escape hatch).

    Public DNS for mesh A records is often correct while the local resolver still
    returns NXDOMAIN (rebinding filters). Pinning on the Cove host unblocks Open my Cove
    on that machine. Other mesh devices still need Tailscale + working mesh/public DNS.
    """
    domain = (domain or "").strip().lower().rstrip(".")
    ip = (ip or "").strip()
    if not domain or not ip:
        return {"ok": False, "reason": "domain and ip required"}
    path = _hosts_path()
    marker = f"# lucidcove-set-domain {domain}"
    line = f"{ip} {domain} {marker}"
    try:
        raw = path.read_text() if path.is_file() else ""
    except Exception as e:
        return {"ok": False, "reason": f"cannot read {path}: {e}"}
    lines = raw.splitlines()
    kept = []
    for ln in lines:
        # Drop prior pins for this domain (ours or bare).
        parts = ln.split()
        if parts and not ln.strip().startswith("#"):
            names = {p.lower().rstrip(".") for p in parts[1:]}
            if domain in names:
                continue
        if marker in ln:
            continue
        kept.append(ln)
    kept.append(line)
    new_text = "\n".join(kept) + "\n"
    if new_text == (raw if raw.endswith("\n") or raw == "" else raw + "\n"):
        # Still ensure our line present
        if any(domain in (ln.split()[1:] if ln.split() and not ln.strip().startswith("#") else [])
               for ln in kept):
            return {"ok": True, "path": str(path), "action": "unchanged", "ip": ip, "domain": domain}
    try:
        path.write_text(new_text)
        return {"ok": True, "path": str(path), "action": "updated", "ip": ip, "domain": domain}
    except PermissionError:
        # Fall back to sudo tee
        import subprocess
        try:
            proc = subprocess.run(
                ["sudo", "tee", str(path)],
                input=new_text, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                return {"ok": True, "path": str(path), "action": "updated-sudo", "ip": ip, "domain": domain}
            return {"ok": False, "reason": (proc.stderr or proc.stdout or "sudo tee failed")[:200]}
        except Exception as e:
            return {"ok": False, "reason": f"cannot write {path} (need root): {e}"}
    except Exception as e:
        return {"ok": False, "reason": f"cannot write {path}: {e}"}


def ensure_host_resolves(domain: str, mesh_ip: str = "") -> dict:
    """Make THIS host resolve `domain` to the Cove mesh IP when public DNS is filtered.

    Install hard-stop (2026-07-15 Withers): Cloudflare DoH had the A record, mesh was
    up, Caddy/TLS healthy — but macOS system DNS returned NXDOMAIN until a hosts pin.
    Returns {ok, domain, expected_ip, system_ip, doh_ip, steps, method, message}.
    """
    domain = (domain or "").strip().lower().lstrip("*").lstrip(".").rstrip(".")
    out = {
        "ok": False,
        "domain": domain,
        "expected_ip": (mesh_ip or "").strip(),
        "system_ip": "",
        "doh_ip": "",
        "steps": [],
        "method": "",
        "message": "",
    }
    if not domain:
        out["message"] = "no domain"
        return out

    expected = out["expected_ip"] or _detect_mesh_ip_host()
    out["expected_ip"] = expected

    # 1) What the host already does
    sys_ip = _resolve_a_system(domain)
    out["system_ip"] = sys_ip
    out["doh_ip"] = _resolve_a_doh(domain)

    if sys_ip and (not expected or sys_ip == expected or _is_mesh_ip(sys_ip)):
        out["ok"] = True
        out["method"] = "system"
        out["message"] = f"host resolves {domain} → {sys_ip}"
        return out

    # 2) Prefer Tailscale DNS path
    ts = _tailscale_accept_dns()
    out["steps"].append({"tailscale_accept_dns": ts})
    flush = _flush_host_dns_cache()
    out["steps"].append({"flush_dns": flush})
    sys_ip = _resolve_a_system(domain)
    out["system_ip"] = sys_ip
    if sys_ip and (not expected or sys_ip == expected or _is_mesh_ip(sys_ip)):
        out["ok"] = True
        out["method"] = "system-after-tailscale-dns"
        out["message"] = f"host resolves {domain} → {sys_ip} after enabling Tailscale DNS"
        return out

    # 3) Public DNS has the mesh A record but local resolver still fails → hosts pin
    pin_ip = expected or out["doh_ip"]
    if pin_ip and _is_mesh_ip(pin_ip):
        pin = _ensure_hosts_pin(domain, pin_ip)
        out["steps"].append({"hosts_pin": pin})
        if pin.get("ok"):
            _flush_host_dns_cache()
            sys_ip = _resolve_a_system(domain)
            out["system_ip"] = sys_ip
            if sys_ip == pin_ip or (sys_ip and _is_mesh_ip(sys_ip)):
                out["ok"] = True
                out["method"] = "hosts"
                out["message"] = (
                    f"Local DNS was filtering the mesh address for {domain}. "
                    f"Pinned {pin_ip} in /etc/hosts on this host so https://{domain} loads. "
                    "Other devices: join the mesh (MESH.md). If a phone/laptop still "
                    "NXDOMAINs, check DNS rebinding filters or add the same pin."
                )
                return out
            out["message"] = (
                f"Wrote hosts pin for {domain} → {pin_ip} but system still resolves "
                f"to {sys_ip or 'nothing'}; flush DNS or check a VPN/filter."
            )
            return out
        out["message"] = pin.get("reason") or "hosts pin failed"
        return out

    # 4) Cannot repair
    if not expected and not out["doh_ip"]:
        out["message"] = (
            f"Cannot resolve {domain} on this host and public DNS has no A record yet. "
            "Join the mesh, re-claim the address, or set CLOUDFLARE_API_TOKEN / hub DNS."
        )
    else:
        out["message"] = (
            f"Host still cannot resolve {domain} (system={sys_ip or 'none'}, "
            f"DoH={out['doh_ip'] or 'none'}, expected={expected or 'unknown'}). "
            "Check Tailscale is up and DNS rebinding filters (NextDNS/AdGuard/Private Relay)."
        )
    return out



def main() -> int:
    ap = argparse.ArgumentParser(description="Attach a domain to a running Cove (DNS + Caddy + HTTPS).")
    ap.add_argument("--domain", help="e.g. smith.lucidcove.org")
    ap.add_argument("--cove-id", required=True, help="the Cove id (Caddy snippet filename)")
    ap.add_argument("--app-port", type=int, help="published MC app port")
    ap.add_argument("--nextcloud-port", type=int, default=8080, help="published Nextcloud port")
    ap.add_argument("--matrix-port", type=int, default=8008, help="published Dendrite port")
    ap.add_argument("--voice-port", type=int, default=0, help="published voice port (routes voice.{domain})")
    ap.add_argument("--no-matrix", action="store_true", help="this Cove has no homeserver")
    ap.add_argument("--no-voice", action="store_true",
                    help="this Cove has no voice service (skip the voice.{domain} block). "
                         "Voice is ON by default — a Cove always ships jules STT/TTS.")
    ap.add_argument("--mesh-ip", default="", help="mesh/public IP for the A records (auto if omitted)")
    ap.add_argument("--caddy-dir", default="",
                    help="override the Caddy dir (host-Caddy mode, or the SHARED Caddy conf.d "
                         f"dir in --shared mode; defaults to {netconfig.SHARED_CADDY_DIR})")
    ap.add_argument("--self-host", action="store_true",
                    help="bundled-Caddy mode: render this Cove's own docker/Caddyfile (acme-dns) "
                         "and restart its caddy service, instead of touching a host Caddy")
    ap.add_argument("--shared", action="store_true",
                    help="shared-Caddy mode: write this Cove's snippet into the ONE shared Caddy's "
                         "conf.d (container-name routes over lucidcove-net) and reload it")
    ap.add_argument("--compose-dir", default=".",
                    help="the Cove's compose dir (where docker-compose.yml + docker/ live); --self-host")
    ap.add_argument("--nextcloud-container", default="",
                    help="NC container to reconfigure for HTTPS (default: {cove-id}-nextcloud)")
    ap.add_argument("--trusted-proxies", default="",
                    help="trusted_proxies CIDR for the NC HTTPS reconfigure (default: 172.16.0.0/12)")
    ap.add_argument("--cove-dir", default="",
                    help="the Cove's instance dir on the host (holds docker/dendrite.yaml + .env); "
                         "used for the host-side Matrix server_name rewrite + env restamp. "
                         "Defaults to --compose-dir.")
    ap.add_argument("--agents", default="",
                    help="comma-separated agent localparts — the virgin-check basis for the "
                         "Matrix regen (any non-agent account present = NOT virgin, no regen)")
    ap.add_argument("--dendrite-container", default="",
                    help="override the Dendrite container name (default {cove-id}-dendrite)")
    ap.add_argument("--postgres-container", default="",
                    help="override the Postgres container name (default {cove-id}-postgres, "
                         "which holds Dendrite's db on a fresh single-stack Cove)")
    ap.add_argument("--remove-matrix-user", default="",
                    help="MAINTENANCE (batch-10 #5): fully remove a Dendrite localpart from ALL "
                         "userapi_* tables and EXIT (no DNS/Caddy). Fixes the register-200-ghost "
                         "from a partial delete that makes a steward/agent un-healable in-app. "
                         "Needs --cove-id (or --postgres-container). e.g. --remove-matrix-user steward")
    args = ap.parse_args()

    # Maintenance short-circuit: full-table Matrix user removal, then exit. Runs on the
    # HOST (the app container has no docker socket) — this is the command ensure_steward's
    # ghost error tells the operator to run.
    if args.remove_matrix_user.strip():
        res = netconfig.dendrite_remove_user(
            localpart=args.remove_matrix_user.strip(),
            postgres_container=args.postgres_container.strip(),
            cove_id=args.cove_id.strip(),
        )
        print(json.dumps({"remove_matrix_user": res}, indent=2))
        return 0 if res.get("ok") else 1

    if not args.domain or args.app_port is None:
        ap.error("--domain and --app-port are required (except with --remove-matrix-user)")

    domain = args.domain.strip().lower().lstrip("*").lstrip(".").rstrip(".")
    matrix_on = not args.no_matrix

    # CF-126 (RUN-4 smith): installer boxes (install.sh) run the SHARED Caddy at
    # ~/.lucidcove/caddy — but the claim card's command carried no mode flag, so this
    # defaulted to host-caddy mode, went looking for /opt/caddy (the founder-P620
    # convention), and died with "Caddy dir not found". Auto-detect: no explicit mode
    # + the shared stack exists + /opt/caddy doesn't = this is an installer box.
    if (not args.shared and not args.self_host and not args.caddy_dir.strip()
            and os.path.isdir(os.path.expanduser("~/.lucidcove/caddy"))
            and not os.path.isdir(netconfig.DEFAULT_CADDY_DIR)):
        args.shared = True

    _mode = "shared" if args.shared else ("self-host" if args.self_host else "host-caddy")
    result = {"domain": domain, "cove_id": args.cove_id, "mode": _mode}

    # DNS first so the cert can validate as soon as Caddy comes up.
    try:
        result["dns"] = netconfig.ensure_dns(domain, args.mesh_ip.strip())
    except Exception as e:
        result["dns"] = {"ok": False, "reason": f"DNS error: {e}"}

    if args.shared:
        # Shared-Caddy host fallback (multi-Cove box).
        try:
            reloaded = _shared_reconcile(args, domain, matrix_on, result)
        except Exception as e:
            result["caddy"] = {"installed": False, "reloaded": False, "reason": f"shared reconcile error: {e}"}
            reloaded = False
    elif args.self_host:
        # Bundled-Caddy self-host path (the Cove ships its own Caddy).
        reloaded = _self_host_reconcile(args, domain, matrix_on, result)
    else:
        # Host-Caddy path (co-located): drop the snippet into the host Caddy + reload.
        try:
            snippet = netconfig.build_cove_caddy_snippet(
                cove_id=args.cove_id, domain=domain,
                app_port=args.app_port, nextcloud_port=args.nextcloud_port,
                matrix_port=args.matrix_port, voice_port=args.voice_port,
                matrix_server_name=_matrix_server_name(domain), matrix_on=matrix_on,
            )
            install_kwargs = {}
            if args.caddy_dir.strip():
                install_kwargs["caddy_dir"] = args.caddy_dir.strip()
            result["caddy"] = netconfig.install_caddy_snippet(snippet, args.cove_id, **install_kwargs)
        except Exception as e:
            result["caddy"] = {"installed": False, "reloaded": False, "reason": f"Caddy error: {e}"}
        reloaded = bool(result.get("caddy", {}).get("reloaded"))

    # In-browser-claimed domain → reconfigure the running NC for HTTPS too (same overwrite
    # settings the provisioner bakes in when a domain is known at build time). Best-effort,
    # gated to a domain being set; never fails the address claim.
    try:
        _reconcile_nextcloud_https(args, domain, result)
    except Exception as e:
        result["nextcloud_https"] = {"ok": False, "errors": [f"reconcile error: {e}"]}

    # Matrix identity reconcile (CF-101 / B9): give a domainless Cove's Dendrite the real
    # server_name = matrix.{domain} while it's still virgin. HOST-side is the ONLY place this
    # actually works — the config file is read-only inside the container and the DB wipe +
    # stop/start need the docker socket. The in-browser claim can't do it (docker-socket-in-app
    # is a rejected escape surface); it hands the operator THIS command. Gated report-only by default
    # (LP_MATRIX_REGEN_ENABLED); best-effort, never fails the address claim.
    if matrix_on:
        try:
            _reconcile_matrix_identity(args, domain, result)
        except Exception as e:
            result["matrix_identity"] = {"ok": False, "reason": f"matrix reconcile error: {e}"}

    # Install hard-stop repair: Caddy can be healthy while THIS host still NXDOMAINs
    # the mesh A record (public DoH OK, system resolver filtered). Fix resolve here.
    mesh_for_resolve = (args.mesh_ip or "").strip() or _detect_mesh_ip_host()
    if not mesh_for_resolve:
        try:
            # Prefer IP from DNS step when ensure_dns / hub returned one
            mesh_for_resolve = (result.get("dns") or {}).get("ip") or ""
        except Exception:
            mesh_for_resolve = ""
    try:
        result["host_resolve"] = ensure_host_resolves(domain, mesh_for_resolve)
    except Exception as e:
        result["host_resolve"] = {"ok": False, "reason": f"host_resolve error: {e}"}

    result["ok"] = reloaded
    hr = result.get("host_resolve") or {}
    if reloaded and hr.get("ok"):
        extra = ""
        if hr.get("method") == "hosts":
            extra = " Host DNS was repaired via /etc/hosts (mesh A records are often filtered)."
        result["message"] = (
            f"Live on this host: https://{domain} "
            f"(cert ~30-60s; mic/voice works once HTTPS is up).{extra} "
            "Reachable from other devices only on the mesh — see MESH.md."
        )
    elif reloaded and not hr.get("ok"):
        result["message"] = (
            f"Caddy is up for https://{domain}, but THIS host still cannot resolve the name "
            f"({hr.get('message') or 'host_resolve failed'}). "
            "Open my Cove will NXDOMAIN until DNS works — re-run this command with sudo, "
            "enable Tailscale DNS, or pin the mesh IP in /etc/hosts. Not fully live."
        )
        # Resolve failure is an install hard-stop even if Caddy reloaded.
        result["ok"] = False
        result["code"] = "host_resolve_failed"
    else:
        result["message"] = (
            "Caddy did not come up — check the reason above "
            "(run on the Cove's host, with docker available)."
        )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
