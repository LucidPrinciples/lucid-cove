"""
runtime_address.py — claim a Cove's address LIVE, from inside the running app container.

This is what makes the in-browser "Claim your address" step real, with no hard setup: the
operator picks a lucidcove.org subdomain or types their own domain, clicks once, and this
does — server-side — everything they would otherwise do by hand at a registrar + on a box:

  1. DNS — point the address at this box.
       - lucidcove.org subdomain → ask the hub to create the records (zero DNS knowledge).
       - own domain + a Cloudflare token → create them in the operator's own zone.
       - otherwise → hand back the exact records for the operator to paste (the only case
         that ever needs a human, and even then it's copy-paste, not a config file).
  2. Cert credentials —
       - lucidcove.org subdomain → an acme-dns credential from the hub (DNS-01; our
         Cloudflare token never leaves the hub).
       - own domain + token → DNS-01 with that token.
       - own domain, no token → HTTP-01 (works on a publicly reachable box).
  3. Caddy — render the standalone Caddyfile and make it live by POSTing to the bundled
     Caddy's admin API (/load), which issues the cert with no restart; also persist it to
     the file Caddy reads so a future restart keeps the address.

Nextcloud needs no runtime step: the bundled-Caddy compose trusts `*` (NC is reachable
only via Caddy, which routes only known hosts), so cloud.{domain} works the moment the
cert is live.

Only runs when the Cove ships its own bundled Caddy (COVE_BUNDLED_CADDY=1). Co-located
Coves (founder p620/vps) keep the host-Caddy path in domain.py.
"""
import json
import logging
import os
import sys
import urllib.request

# Ensure the package root (/cove-core, where `provision/` lives) is importable, so the
# `from provision import ...` calls below resolve when this module is loaded by the app.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_ROOT, "provision")) and _ROOT not in sys.path:
    sys.path.append(_ROOT)

log = logging.getLogger(__name__)


def bundled_caddy() -> bool:
    """True when this Cove ships its own Caddy (set by the provisioner). The in-browser
    live-address path applies only here; otherwise domain.py uses the host-Caddy path."""
    return (os.getenv("COVE_BUNDLED_CADDY", "") or "").strip() == "1"


def shared_caddy() -> bool:
    """True when this Cove is on a multi-Cove box behind the ONE shared Caddy (set by the
    provisioner's shared_net path). The in-browser claim writes this Cove's conf.d snippet
    into the shared Caddy's conf.d (bind-mounted) and live-reloads it over the bridge."""
    return (os.getenv("COVE_SHARED_CADDY", "") or "").strip() == "1"


def _shared_confd() -> str:
    return (os.getenv("COVE_SHARED_CONFD", "") or "/app/shared-caddy-confd").strip()


# ── CF-95 follow-up: claim-time DNS-01 reachability probe ────────────────────
# "Hub returned acme creds" used to be treated as cert.ok, followed by an
# unconditional "the certificate is issuing now (~30-60s)" — while a hub-side
# firewall silently dropped port 53 and DNS-01 could never complete (the run-1
# find). Right after creds are fetched, test the acme-dns server on port 53 the
# way MESH.md's dig test does, and NAME the failure.

_CERT_PROBE_FILE = "/app/data/cert_probe.json"

_DNS53_MSG = ("hub challenge DNS unreachable on port 53 — usually a firewall in "
              "front of the hub, not your box")


def _probe_challenge_dns(acme: dict, timeout: float = 4.0) -> dict:
    """Send a minimal DNS TXT query for the challenge record to the acme-dns
    host on port 53/tcp (the same port cloud firewalls drop). Any DNS answer =
    reachable. Never raises."""
    import random
    import socket
    import struct
    from urllib.parse import urlparse
    try:
        host = urlparse((acme.get("server_url") or "").strip()).hostname or ""
        name = (acme.get("fulldomain") or "").strip().strip(".")
        if not host or not name:
            return {"ok": True, "skipped": "no acme-dns host/fulldomain to probe"}
        tid = random.randint(0, 0xFFFF)
        q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        for label in name.split("."):
            q += bytes([len(label)]) + label.encode()
        q += b"\x00" + struct.pack(">HH", 16, 1)   # QTYPE TXT, QCLASS IN
        with socket.create_connection((host, 53), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(struct.pack(">H", len(q)) + q)
            hdr = s.recv(2)
        if len(hdr) == 2:
            return {"ok": True}
        return {"ok": False, "reason": _DNS53_MSG}
    except Exception as e:
        return {"ok": False, "reason": f"{_DNS53_MSG} ({str(e)[:80]})"}


def _record_cert_probe(result: dict) -> None:
    """Persist the last probe so /api/domain/status keeps reporting it."""
    try:
        with open(_CERT_PROBE_FILE, "w") as f:
            json.dump(result, f)
    except Exception:
        pass


def read_cert_probe() -> dict:
    try:
        with open(_CERT_PROBE_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _apply_cert_probe(out: dict, acme: dict, domain: str) -> None:
    """Run the probe, fold the result into out['cert']/out['next'], persist it."""
    probe = _probe_challenge_dns(acme)
    _record_cert_probe({"domain": domain, **probe})
    out["cert"]["dns53"] = bool(probe.get("ok"))
    if not probe.get("ok"):
        out["cert"]["reason"] = probe.get("reason") or _DNS53_MSG
        out["next"].append(
            "Heads up: the hub's challenge DNS isn't answering on port 53, so the "
            "certificate can't issue yet (usually a hub-side firewall — not your box). "
            "Your Cove keeps retrying and picks it up automatically once it clears.")


def _cove_id() -> str:
    return (os.getenv("COVE_ID", "") or "cove").strip().lower().replace(" ", "-")


def _admin_url() -> str:
    return (os.getenv("COVE_CADDY_ADMIN", "") or "http://caddy:2019").strip().rstrip("/")


def _docker_dir() -> str:
    return (os.getenv("COVE_DOCKER_DIR", "") or "/app/cove-docker").strip()


def _app_port() -> int:
    try:
        return int(os.getenv("PORT", "8200") or "8200")
    except Exception:
        return 8200


def _voice_on() -> bool:
    return bool((os.getenv("VOICE_PORT", "") or "").strip())


def _caddy_admin_token() -> str:
    """#D35: the shared secret for the token-gated admin proxy (empty when the gate
    is off). Same env var the provisioner injects into the Caddy container."""
    return (os.getenv("LP_CADDY_ADMIN_TOKEN", "") or "").strip()


def _caddy_load(caddyfile_text: str) -> dict:
    """Push a full Caddyfile to the bundled/shared Caddy admin API → live reload.

    Caddy adapts the text (Content-Type: text/caddyfile) and issues the cert
    immediately, no restart.

    #D35 auth: when LP_CADDY_ADMIN_TOKEN is set, send Bearer. Install-pass find:
    if the app has a token but Caddy was started without the same secret (or the
    gate is still on an old value), /load returns HTTP 403 and set-address dies
    with an opaque error. Surface that mismatch explicitly so the operator (and
    #1628 later) can fix env alignment instead of re-clicking Set address.

    #D32 origin: urllib sends no Origin by default → Caddy enforce_origin sees
    origin '' and 403s even when Host would match. Always set Host + Origin from
    the admin URL so the #D32 bridge path (and the loopback admin behind the
    #D35 proxy) accepts the request.
    """
    import urllib.error
    import urllib.parse
    url = _admin_url() + "/load"
    headers = {"Content-Type": "text/caddyfile"}
    # Sanctioned Host/Origin for Caddy enforce_origin. Without Origin, a bare urllib
    # POST is rejected with: client is not allowed to access from origin ''.
    # #D35: when the token gate is on, reverse_proxy forwards to loopback admin
    # (origins = localhost:2018 only) — so Origin must be the loopback origin, not
    # the bridge host. #D32 (no token): Origin is the bridge admin host itself.
    _tok = _caddy_admin_token()
    try:
        _parsed = urllib.parse.urlparse(_admin_url())
        _host = _parsed.netloc  # e.g. lucidcove-caddy:2019 (urllib sets Host from URL)
        if _host:
            # Do NOT set Host here — urllib.request derives Host from the URL and a
            # manual Host header is unreliable. #D35 proxy rewrites Host to
            # localhost:2018; #D32 needs Host = bridge admin host from the URL.
            if _tok:
                # Loopback admin allowlist (origins localhost:2018 …)
                headers["Origin"] = "http://localhost:2018"
            else:
                headers["Origin"] = f"http://{_host}"
    except Exception:
        pass
    # #D35: when the admin proxy is token-gated, authenticate the /load. Harmless when
    # the gate is off (a plain #D32 bridge admin ignores an unexpected auth header).
    if _tok:
        headers["Authorization"] = f"Bearer {_tok}"
    req = urllib.request.Request(
        url, data=caddyfile_text.encode(),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return {"ok": True}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = (e.read() or b"")[:120].decode("utf-8", "replace")
        except Exception:
            body = ""
        if e.code == 403:
            hint = (
                "Caddy admin /load returned HTTP 403 Forbidden. "
                "The app's LP_CADDY_ADMIN_TOKEN must match the token in the "
                "lucidcove-caddy (shared) or caddy (bundled) container env, and "
                "the shared Caddyfile must include the #D35 token-gated :2019 "
                "proxy (rebuild/reload Caddy after setting the token). "
                f"admin={_admin_url()} token_set={'yes' if _tok else 'no'}"
            )
            if body:
                hint += f" body={body!r}"
            return {"ok": False, "reason": hint, "code": "caddy_admin_403"}
        return {
            "ok": False,
            "reason": f"Caddy admin /load failed: HTTP {e.code} {str(e)[:120]}",
            "code": f"caddy_admin_http_{e.code}",
        }
    except Exception as e:
        return {"ok": False, "reason": f"Caddy admin /load failed: {str(e)[:180]}"}


def _persist_caddyfile(caddyfile_text: str) -> dict:
    """Write the rendered Caddyfile to the file Caddy reads, so a restart keeps the address.
    Truncate-in-place (open 'w') keeps the SAME inode — critical, because Caddy file-binds
    it; a rename (what sed -i does) would break that bind. Best-effort: the admin /load is
    what makes it live now; persistence just survives a future restart."""
    path = os.path.join(_docker_dir(), "Caddyfile")
    try:
        with open(path, "w") as f:
            f.write(caddyfile_text)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "reason": f"persist to {path} failed: {str(e)[:160]}"}


def _write_shared_snippet(snippet_text: str) -> dict:
    """Write this Cove's routing snippet into the SHARED Caddy's conf.d (bind-mounted rw).
    The shared base Caddyfile `import`s conf.d/*.caddy, so a reload picks it up. Best-effort."""
    path = os.path.join(_shared_confd(), f"{_cove_id()}.caddy")
    try:
        os.makedirs(_shared_confd(), exist_ok=True)
        with open(path, "w") as f:
            f.write(snippet_text)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "reason": f"write {path} failed: {str(e)[:160]}"}


def _shared_caddy_reload() -> dict:
    """Live-reload the SHARED Caddy by POSTing its BASE Caddyfile (which `import`s
    conf.d/*.caddy) to the admin API /load. Caddy re-adapts and re-reads every snippet —
    including the one we just wrote — and issues any new certs, with no restart. We send
    the base text (admin /load needs a complete config; sending only this Cove's snippet
    would drop every other Cove's routing)."""
    try:
        from provision import netconfig
        base = netconfig.build_shared_caddy_base_caddyfile()
    except Exception as e:
        return {"ok": False, "reason": f"could not build base Caddyfile: {str(e)[:160]}"}
    return _caddy_load(base)


def set_address_live_shared(domain: str, *, mesh_ip: str = "", own_dns_token: str = "",
                            matrix_on: bool = False, matrix_server_name: str = "") -> dict:
    """Shared-Caddy (multi-Cove box) claim: DNS + cert creds + write this Cove's conf.d
    snippet (container-name routes over the bridge, per-site TLS) + live-reload the shared
    Caddy via its admin API. Returns the same shape as set_address_live."""
    out = {"ok": False, "dns": {}, "cert": {}, "caddy": {}, "records": [], "next": []}
    if not shared_caddy():
        out["reason"] = "not a shared-Caddy box"
        return out
    try:
        from provision import centralized as C, netconfig
    except Exception as e:
        out["reason"] = f"provision modules unavailable in-container: {e}"
        return out

    is_sub = (domain == "lucidcove.org" or domain.endswith(".lucidcove.org"))

    # ── 1) DNS (reuse the 3-tier auto-DNS; mesh-first) ──
    mesh_ip = (mesh_ip or os.getenv("COVE_MESH_IP", "") or "").strip()
    deploy = {"mesh_ip": mesh_ip} if mesh_ip else {}
    cfg = {"dns": {"token": own_dns_token}} if own_dns_token else {}
    try:
        dns = C._auto_dns(domain, deploy, cfg)
    except Exception as e:
        dns = {"ok": False, "auto": False, "reason": f"auto-DNS error: {str(e)[:160]}"}
    out["dns"] = dns
    if not dns.get("auto"):
        out["records"] = dns.get("records", [])

    # ── 2) Cert credentials ──
    acme = {}
    if is_sub:
        try:
            ac = C._acme_creds_via_hub(domain)
        except Exception as e:
            ac = {"ok": False, "reason": f"hub acme-credential error: {str(e)[:160]}"}
        out["cert"] = {"mode": "acme-dns (hub)", "ok": bool(ac.get("ok")), "reason": ac.get("reason")}
        if ac.get("ok"):
            acme = ac.get("acmedns") or {}
            _apply_cert_probe(out, acme, domain)   # CF-95: creds ≠ issuable
    elif own_dns_token:
        out["cert"] = {"mode": "dns-01 (your token)", "ok": True}
    else:
        out["cert"] = {"mode": "http-01", "ok": True}

    # ── 3) Build this Cove's snippet → write to shared conf.d → live-reload ──
    try:
        snippet = netconfig.build_haven_cove_snippet(
            cove_id=_cove_id(), domain=domain, app_port=_app_port(),
            matrix_server_name=matrix_server_name, matrix_on=bool(matrix_on),
            voice_on=_voice_on(), acmedns=acme,
            own_dns_provider=("cloudflare" if own_dns_token else ""),
            own_dns_token=(own_dns_token or ""))
    except Exception as e:
        out["reason"] = f"could not render snippet: {str(e)[:160]}"
        return out
    out["caddy"]["write"] = _write_shared_snippet(snippet)
    reload_res = _shared_caddy_reload()
    out["caddy"]["reloaded"] = bool(reload_res.get("ok"))
    if not reload_res.get("ok"):
        out["caddy"]["reason"] = reload_res.get("reason")
        if reload_res.get("code"):
            out["caddy"]["code"] = reload_res["code"]
            out["code"] = reload_res["code"]
        out["reason"] = reload_res.get("reason") or out.get("reason")
    out["ok"] = bool(reload_res.get("ok"))

    # ── Next steps ──
    if out["records"]:
        out["next"].append(
            "Add these DNS records at your registrar (A records → your box), then reload "
            "this page to finish.")
    if is_sub and not out["cert"].get("ok"):
        out["next"].append(
            "Couldn't reach the hub to set up the certificate — check the Cove's network "
            "and try again.")
    if out["ok"] and not out["records"]:
        if out["cert"].get("dns53") is not False:
            out["next"].append(
                f"Address set. The certificate is issuing now (~30-60s) — then https://{domain} "
                "is live and the mic/voice will work.")
        else:
            out["next"].append(
                f"Address set. https://{domain} goes live as soon as the certificate can "
                "issue (see the port-53 note above).")
    elif out["ok"] and out["records"]:
        out["next"].append(
            f"Once your DNS records resolve, https://{domain} goes live automatically.")
    return out


def set_address_live(domain: str, *, mesh_ip: str = "", own_dns_token: str = "",
                     matrix_on: bool = False, matrix_server_name: str = "") -> dict:
    """Do the full DNS + cert + live-Caddy reconcile for a bundled-Caddy self-host.
    Returns {ok, dns, cert, caddy, records, next} — `ok` means the cert is now issuing."""
    out = {"ok": False, "dns": {}, "cert": {}, "caddy": {}, "records": [], "next": []}
    if not bundled_caddy():
        out["reason"] = "not a bundled-Caddy self-host (use the host-Caddy path)"
        return out
    try:
        from provision import centralized as C, netconfig
    except Exception as e:
        out["reason"] = f"provision modules unavailable in-container: {e}"
        return out

    is_sub = (domain == "lucidcove.org" or domain.endswith(".lucidcove.org"))

    # ── 1) DNS — reuse the provisioner's 3-tier auto-DNS (hub / own token / records) ──
    # Mesh-first: point the record at the box's mesh IP (baked in at provision as
    # COVE_MESH_IP) so the Cove is reached over the mesh, not the public internet. An
    # explicit mesh_ip from the caller still wins.
    mesh_ip = (mesh_ip or os.getenv("COVE_MESH_IP", "") or "").strip()
    deploy = {"mesh_ip": mesh_ip} if mesh_ip else {}
    cfg = {"dns": {"token": own_dns_token}} if own_dns_token else {}
    try:
        dns = C._auto_dns(domain, deploy, cfg)
    except Exception as e:
        dns = {"ok": False, "auto": False, "reason": f"auto-DNS error: {str(e)[:160]}"}
    out["dns"] = dns
    if not dns.get("auto"):
        out["records"] = dns.get("records", [])

    # ── 2) Cert credentials ──────────────────────────────────────────────────────
    acme = {}
    if is_sub:
        try:
            ac = C._acme_creds_via_hub(domain)
        except Exception as e:
            ac = {"ok": False, "reason": f"hub acme-credential error: {str(e)[:160]}"}
        out["cert"] = {"mode": "acme-dns (hub)", "ok": bool(ac.get("ok")),
                       "reason": ac.get("reason")}
        if ac.get("ok"):
            acme = ac.get("acmedns") or {}
            _apply_cert_probe(out, acme, domain)   # CF-95: creds ≠ issuable
    elif own_dns_token:
        out["cert"] = {"mode": "dns-01 (your token)", "ok": True}
    else:
        out["cert"] = {"mode": "http-01", "ok": True}

    # ── 3) Render the Caddyfile + make it live (cert issues) + persist for restart ──
    try:
        caddyfile = netconfig.build_selfhost_caddyfile(
            domain=domain, app_port=_app_port(),
            matrix_server_name=matrix_server_name, matrix_on=bool(matrix_on),
            acmedns=acme,
            own_dns_provider=("cloudflare" if own_dns_token else ""),
            own_dns_token=(own_dns_token or ""),
            voice_on=_voice_on())
    except Exception as e:
        out["reason"] = f"could not render Caddyfile: {str(e)[:160]}"
        return out
    out["caddy"]["persist"] = _persist_caddyfile(caddyfile)
    load = _caddy_load(caddyfile)
    out["caddy"]["reloaded"] = bool(load.get("ok"))
    if not load.get("ok"):
        out["caddy"]["reason"] = load.get("reason")
        if load.get("code"):
            out["caddy"]["code"] = load["code"]
            out["code"] = load["code"]
        out["reason"] = load.get("reason") or out.get("reason")
    out["ok"] = bool(load.get("ok"))

    # ── Next steps for the operator ───────────────────────────────────────────────
    if out["records"]:
        out["next"].append(
            "Add these DNS records at your registrar (A records → your box), then reload "
            "this page to finish.")
    if is_sub and not out["cert"].get("ok"):
        out["next"].append(
            "Couldn't reach the hub to set up the certificate — check the Cove's network "
            "and try again.")
    if out["ok"] and not out["records"]:
        if out["cert"].get("dns53") is not False:
            out["next"].append(
                f"Address set. The certificate is issuing now (~30-60s) — then https://{domain} "
                "is live and the mic/voice will work.")
        else:
            out["next"].append(
                f"Address set. https://{domain} goes live as soon as the certificate can "
                "issue (see the port-53 note above).")
    elif out["ok"] and out["records"]:
        out["next"].append(
            f"Once your DNS records resolve, https://{domain} goes live automatically.")
    return out
