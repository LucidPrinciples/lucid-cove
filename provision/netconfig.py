#!/usr/bin/env python3
"""
netconfig.py — co-located networking for a centralized Cove (#165 + #164 + #166).

When a Cove is provisioned ON a shared machine (deploy.target = p620 | vps), three
things have to happen automatically so the Cove is reachable + federation-ready with
NO manual ops (especially no manual DNS):

  1. Port preflight (#164) — pick host ports that are actually FREE on the machine,
     so a second Cove on the same box never collides with the first (the founder
     Dendrite already holds 8008, etc.). Self-host single-Cove keeps static defaults.

  2. Caddy snippet (#165) — emit one reverse-proxy block for the whole Cove
     (matrix.{domain} + .well-known, cloud.{domain}, *.{domain}, apex) and drop it
     into the machine's Caddy import dir (conf.d/*.caddy), then reload Caddy.

  3. Cloudflare DNS (#166) — one wildcard A record per Cove → the machine's mesh IP,
     via cloudflare_dns.ensure_cove_dns (reuses the token Caddy already uses).

ARCHITECTURE NOTE — why localhost, not container names:
  The machine's Caddy runs `network_mode: host`, so it CANNOT resolve Docker
  container names (no embedded DNS on the host network). Co-located Coves therefore
  bind their published ports to 127.0.0.1 (the VPS security pattern — not exposed on
  the LAN/mesh, only reachable through Caddy) and the snippet routes
  `reverse_proxy localhost:{port}`. This drops static-IP allocation entirely; port
  preflight keeps the localhost ports collision-free. Each Cove still talks to its
  own Dendrite over its private compose network (service name `dendrite`), so no
  cross-Cove Docker networking is needed — federation is over HTTPS/mesh.

Everything here is BEST-EFFORT and non-fatal: if the provisioner is running on the
Mac (not the host), or Docker / the Caddy dir / the Cloudflare token aren't present,
it writes the snippet into the Cove output folder and prints what to do, instead of
failing the provision.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

CO_LOCATED_TARGETS = ("p620", "vps")

# Co-located host-Caddy defaults. Deployment-specific — set via the deploy block
# (deploy.caddy_dir) or LP_CADDY_DIR; the old baked default was a founder home path.
DEFAULT_CADDY_DIR = os.getenv("LP_CADDY_DIR", "/opt/caddy")
DEFAULT_CADDY_CONTAINER = "caddy-proxy"
DEFAULT_CADDYFILE_IN_CONTAINER = "/etc/caddy/Caddyfile"

# ---------------------------------------------------------------------------
# Shared-Caddy ("Haven") model — multiple self-host Coves on ONE box
# ---------------------------------------------------------------------------
# A standalone box that wants to run MORE THAN ONE Cove (so the Coves can federate
# Matrix to each other with real addresses + HTTPS) can't have each Cove bundle its
# own Caddy on 80/443 — the second bind collides. Instead ONE shared Caddy per box
# owns 80/443, sits on an external Docker bridge network (lucidcove-net), and every
# Cove is Caddy-LESS and joined to that same network. The shared Caddy routes to each
# Cove by CONTAINER NAME over the bridge ({cid}-app, {cid}-dendrite, ...). Per-Cove
# routing lives in conf.d/{cid}.caddy snippets the shared Caddy imports.
SHARED_NET = "lucidcove-net"               # external Docker bridge all Coves + Caddy join
SHARED_CADDY_CONTAINER = "lucidcove-caddy"  # the one shared Caddy per box
# Where the provisioner writes the shared-Caddy stack (compose + base Caddyfile +
# conf.d/) so install.sh can `docker compose up` it. A fixed per-user dir, so every
# Cove generated on this box drives the SAME shared Caddy (not one per Cove output).
SHARED_CADDY_DIR = os.path.expanduser("~/.lucidcove/caddy")
SHARED_CADDY_ADMIN_IN_CONTAINER = "http://lucidcove-caddy:2019"  # admin API over the bridge


# ---------------------------------------------------------------------------
# #164 — Port preflight / auto-allocation
# ---------------------------------------------------------------------------
def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if `port` can be bound on `host` right now. Also checks 0.0.0.0 so we
    don't pick a port some other process holds on the wildcard address."""
    for h in (host, "0.0.0.0"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind((h, port))
        except OSError:
            s.close()
            return False
        s.close()
    return True


def _ports_in_use_by_docker() -> set:
    """Published host ports already claimed by running containers (so we don't pick
    a port that's mapped but momentarily un-bound). Best-effort — empty if no docker."""
    used = set()
    if not shutil.which("docker"):
        return used
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Ports}}"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return used
    for line in out.splitlines():
        # e.g. "0.0.0.0:8200->8200/tcp, 127.0.0.1:5434->5432/tcp"
        for part in line.split(","):
            part = part.strip()
            if "->" in part and ":" in part:
                hostside = part.split("->", 1)[0]
                try:
                    used.add(int(hostside.rsplit(":", 1)[1]))
                except (ValueError, IndexError):
                    pass
    return used


def _next_free(start: int, taken: set) -> int:
    p = start
    while p in taken or not _port_free(p):
        p += 1
        if p > 65000:
            raise RuntimeError(f"no free port found at/after {start}")
    return p


def preflight_ports(ports: dict, target: str = "") -> dict:
    """Return a copy of `ports` (name -> desired port) with any colliding ports bumped
    to the next free one. Runs for EVERY target (multi-Cove install hardening): a port
    is only changed when it's already in use, so the FIRST Cove on a fresh box keeps
    the static defaults the operator chose, while a SECOND or THIRD Cove on the same
    machine automatically takes the next free ports — multiple Coves per box with no
    hand-editing and no 8301-style collisions. (`target` kept for signature compat.)

    Resolution is deterministic and order-stable: app, then nextcloud, then matrix,
    then voice, each taking the next free port at/after its requested value, never
    reusing one already assigned in this pass.
    """
    taken = _ports_in_use_by_docker()
    out, assigned = {}, set()
    for name in ("app", "nextcloud", "matrix", "voice"):
        if name not in ports:
            continue
        want = int(ports[name])
        got = _next_free(want, taken | assigned)
        out[name] = got
        assigned.add(got)
    # carry through any other keys unchanged
    for k, v in ports.items():
        out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# #165 — Per-Cove Caddy snippet (full: matrix + cloud + wildcard + apex)
# ---------------------------------------------------------------------------
def build_cove_caddy_snippet(*, cove_id: str, domain: str, app_port: int,
                             nextcloud_port: int, matrix_port: int,
                             matrix_server_name: str, matrix_on: bool,
                             voice_port: int = 0) -> str:
    """One Caddy block for the whole Cove. Host-network Caddy → localhost:{port}
    (the Cove publishes those ports on 127.0.0.1). Specific blocks (cloud., matrix.,
    voice.) win over the *.{domain} wildcard, matching the founder Clearfield pattern.
    voice_port routes voice.{domain} → the Cove's own voice container (so it doesn't
    fall through the wildcard to the app)."""
    blocks = [
        f"# ============================================================",
        f"# {cove_id} Cove — generated by provision/netconfig.py (#165)",
        f"# Host-network Caddy → 127.0.0.1 published ports. No static IPs.",
        f"# ============================================================",
        "",
        f"# Apex (so {domain} itself loads too — fixes #25)",
        f"{domain} {{",
        f"    reverse_proxy localhost:{app_port}",
        f"}}",
        "",
        f"# Nextcloud (specific — wins over the wildcard below)",
        f"cloud.{domain} {{",
        f"    reverse_proxy localhost:{nextcloud_port}",
        f"}}",
    ]
    if matrix_on:
        blocks += [
            "",
            f"# Matrix homeserver ({matrix_server_name}) — Connect + federation.",
            f"# Caddy serves .well-known discovery; Dendrite serves /_matrix/* (it sets",
            f"# its own CORS on /_matrix, so do NOT add CORS at the site level there).",
            f"{matrix_server_name} {{",
            f"    handle /.well-known/matrix/server {{",
            f"        header Content-Type application/json",
            f'        respond `{{"m.server": "{matrix_server_name}:443"}}` 200',
            f"    }}",
            f"    handle /.well-known/matrix/client {{",
            f"        header Content-Type application/json",
            f"        header Access-Control-Allow-Origin *",
            f'        respond `{{"m.homeserver": {{"base_url": "https://{matrix_server_name}"}}}}` 200',
            f"    }}",
            f"    handle {{",
            f"        reverse_proxy localhost:{matrix_port}",
            f"    }}",
            f"}}",
        ]
    if voice_port:
        blocks += [
            "",
            f"# Voice (jules STT/TTS) — specific, wins over the wildcard so voice.{domain}",
            f"# reaches this Cove's OWN voice container, not the app.",
            f"voice.{domain} {{",
            f"    reverse_proxy localhost:{voice_port}",
            f"}}",
        ]
    blocks += [
        "",
        f"# Wildcard — every operator/presence subdomain ({{handle}}.{domain},",
        f"# stuart.{domain}, ...) host-routed inside the one Cove app.",
        f"*.{domain} {{",
        f"    reverse_proxy localhost:{app_port}",
        f"}}",
        "",
    ]
    return "\n".join(blocks)


def build_selfhost_caddyfile(*, domain: str, app_port: int,
                             nextcloud_internal_port: int = 80,
                             matrix_internal_port: int = 8008,
                             matrix_server_name: str = "", matrix_on: bool = False,
                             acmedns: dict | None = None,
                             own_dns_provider: str = "", own_dns_token: str = "",
                             app_service: str = "app", nextcloud_service: str = "nextcloud",
                             matrix_service: str = "dendrite",
                             voice_on: bool = True, voice_service: str = "voice",
                             voice_internal_port: int = 8300) -> str:
    """A COMPLETE standalone Caddyfile for a self-host Cove that ships its OWN Caddy
    container (not the founder's host Caddy). Differences from build_cove_caddy_snippet:
      - Caddy runs INSIDE the Cove's compose network, so it routes to container SERVICE
        names + internal ports (app:{app_port}, dendrite:8008, nextcloud:80) — no
        localhost, no host ports.
      - TLS via DNS-01 through acme-dns (global `acme_dns` option) so the box gets a
        real Let's Encrypt cert for {domain} + *.{domain} on a mesh-only/NAT host
        WITHOUT holding our Cloudflare token. One acme-dns credential + the
        _acme-challenge.{domain} CNAME (set hub-side) covers apex + wildcard.
    `acmedns` = {server_url, username, password, subdomain} from acmedns.py. If absent,
    Caddy falls back to its default (HTTP-01) — fine for a publicly-reachable box."""
    lines = []
    ad = acmedns or {}
    # Global options. The admin API is ALWAYS on so the app can live-reload Caddy the
    # moment the operator sets/changes the address from the browser (POST /load) — no
    # container restart, no terminal. It MUST be re-emitted on every render: a /load with
    # a config that omits `admin` would drop the API and we'd lose remote control.
    glines = ["    admin :2019"]
    if own_dns_provider and own_dns_token:
        # Self-hoster's OWN domain behind NAT: DNS-01 with THEIR own DNS token (e.g.
        # cloudflare). HTTP-01 can't work (no public :443), and acme-dns is for our
        # lucidcove.org subdomains — this is their domain, their token.
        glines.append(f"    acme_dns {own_dns_provider} {own_dns_token}")
    elif ad.get("username") and ad.get("server_url"):
        glines += [
            "    # DNS-01 via acme-dns — scoped credential, never our Cloudflare token.",
            "    acme_dns acmedns {",
            f"        username {ad.get('username')}",
            f"        password {ad.get('password')}",
            f"        subdomain {ad.get('subdomain')}",
            f"        server_url {ad.get('server_url')}",
            "    }",
        ]
    lines += ["{", *glines, "}", ""]

    # Domainless first-run (no address claimed yet): serve the MC on plain :80 so the
    # operator can reach it (over http) to claim an address. The admin API above lets that
    # claim swap in the full HTTPS config live. Returns early — no domain/cert blocks yet.
    if not domain:
        lines += [
            "# No address set yet — serve the MC on :80 so the operator can claim one.",
            "# Claiming an address live-reloads this Caddy with the HTTPS config below.",
            ":80 {",
            f"    reverse_proxy {app_service}:{app_port}",
            "}",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "# Keep the box reachable locally over plain http even with a public address set —",
        "# localhost is a secure context, so claiming an address never cuts off local access.",
        "http://localhost, http://127.0.0.1 {",
        f"    reverse_proxy {app_service}:{app_port}",
        "}",
        "",
        f"# {domain} — self-host Cove (bundled Caddy; routes to compose services).",
        f"{domain} {{",
        f"    reverse_proxy {app_service}:{app_port}",
        f"}}",
        "",
        f"cloud.{domain} {{",
        f"    reverse_proxy {nextcloud_service}:{nextcloud_internal_port}",
        f"}}",
    ]
    if matrix_on and matrix_server_name:
        lines += [
            "",
            f"{matrix_server_name} {{",
            f"    handle /.well-known/matrix/server {{",
            f"        header Content-Type application/json",
            f'        respond `{{"m.server": "{matrix_server_name}:443"}}` 200',
            f"    }}",
            f"    handle /.well-known/matrix/client {{",
            f"        header Content-Type application/json",
            f"        header Access-Control-Allow-Origin *",
            f'        respond `{{"m.homeserver": {{"base_url": "https://{matrix_server_name}"}}}}` 200',
            f"    }}",
            f"    handle {{",
            f"        reverse_proxy {matrix_service}:{matrix_internal_port}",
            f"    }}",
            f"}}",
        ]
    if voice_on:
        lines += [
            "",
            f"voice.{domain} {{",
            f"    reverse_proxy {voice_service}:{voice_internal_port}",
            f"}}",
        ]
    lines += [
        "",
        f"*.{domain} {{",
        f"    reverse_proxy {app_service}:{app_port}",
        f"}}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared-Caddy ("Haven") — base stack + per-Cove snippet
# ---------------------------------------------------------------------------
def build_shared_caddy_base_caddyfile() -> str:
    """The BASE Caddyfile for the one shared Caddy per box. It carries only the global
    block (admin API on, so a Cove can live-reload it over the bridge) and imports every
    per-Cove snippet from conf.d. Each Cove's routing (apex/cloud/matrix/voice/wildcard
    + its own TLS block) lives in its own conf.d/{cid}.caddy file — added/removed without
    touching this base. Re-emitting `admin` on every render is required so a /load that
    omitted it would never drop the API."""
    return "\n".join([
        "{",
        "    admin :2019",
        "}",
        "",
        "# Per-Cove routing snippets. The provisioner / the Cove's in-browser address",
        "# claim writes conf.d/{cove_id}.caddy; this import wires them all in.",
        "import /etc/caddy/conf.d/*.caddy",
        "",
    ])


def build_shared_caddy_compose() -> str:
    """Compose for the one shared Caddy per box. Joins the external lucidcove-net bridge
    (so it can resolve every Cove's container names), owns 80/443, builds from the repo's
    docker/caddy context (acme-dns + cloudflare plugins). Mounts a host conf.d/ dir (the
    per-Cove snippets) + caddy data/config volumes (certs survive recreate). The
    cove-core path is resolved at `docker compose up` time via the COVE_CORE env so this
    file is host-independent. Container name is fixed (lucidcove-caddy) so install.sh can
    check 'is it already running?' idempotently."""
    return f"""# Generated by provision/centralized.py — SHARED Caddy for multi-Cove on one box.
# ONE per machine. Owns 80/443, routes every Cove by container name over {SHARED_NET}.
# Brought up by install.sh (idempotent). Per-Cove routing lives in conf.d/*.caddy.
name: lucidcove-shared

services:
  caddy:
    build:
      context: ${{COVE_CORE:?set COVE_CORE to the cove-core repo path}}/docker/caddy
      dockerfile: Dockerfile
    image: lucid-cove-caddy:latest
    container_name: {SHARED_CADDY_CONTAINER}
    restart: unless-stopped
    networks:
      - {SHARED_NET}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./conf.d:/etc/caddy/conf.d:ro
      - caddy_data:/data
      - caddy_config:/config
    ports:
      - "80:80"
      - "443:443"

volumes:
  caddy_data:
  caddy_config:

networks:
  {SHARED_NET}:
    external: true
"""


def build_haven_cove_snippet(*, cove_id: str, domain: str, app_port: int,
                             matrix_server_name: str = "", matrix_on: bool = False,
                             nextcloud_internal_port: int = 80,
                             matrix_internal_port: int = 8008,
                             voice_on: bool = True, voice_internal_port: int = 8300,
                             acmedns: dict | None = None,
                             own_dns_provider: str = "", own_dns_token: str = "") -> str:
    """One conf.d/{cove_id}.caddy snippet for a Cove fronted by the SHARED Caddy on a
    multi-Cove box. Unlike build_cove_caddy_snippet (host-net Caddy → 127.0.0.1 ports)
    and build_selfhost_caddyfile (the Cove's OWN bundled Caddy → service names), this
    routes by CONTAINER NAME over the shared bridge lucidcove-net:
        {cid}-app:{app_port}, {cid}-dendrite:8008, {cid}-nextcloud:80, {cid}-voice:8300
    Each site carries its OWN tls block (the shared Caddy fronts many domains, so TLS is
    per-site, not a global option): for a lucidcove.org subdomain, DNS-01 via acme-dns
    (scoped credential); for the operator's own domain + token, DNS-01 with that token;
    otherwise default (HTTP-01). Mirrors the .well-known matrix handlers used everywhere."""
    ad = acmedns or {}

    def _tls_block(indent: str = "    ") -> list:
        """Per-site TLS. acme-dns (our subdomains) > own token > default HTTP-01."""
        if own_dns_provider and own_dns_token:
            return [f"{indent}tls {{",
                    f"{indent}    dns {own_dns_provider} {own_dns_token}",
                    f"{indent}}}"]
        if ad.get("username") and ad.get("server_url"):
            return [
                f"{indent}tls {{",
                f"{indent}    dns acmedns {{",
                f"{indent}        username {ad.get('username')}",
                f"{indent}        password {ad.get('password')}",
                f"{indent}        subdomain {ad.get('subdomain')}",
                f"{indent}        server_url {ad.get('server_url')}",
                f"{indent}    }}",
                f"{indent}}}",
            ]
        return []   # default issuer (HTTP-01) for a publicly-reachable own domain

    app_target = f"{cove_id}-app:{app_port}"
    nc_target = f"{cove_id}-nextcloud:{nextcloud_internal_port}"
    mx_target = f"{cove_id}-dendrite:{matrix_internal_port}"
    voice_target = f"{cove_id}-voice:{voice_internal_port}"

    lines = [
        "# ============================================================",
        f"# {cove_id} Cove — shared-Caddy (Haven) routing, generated by netconfig.py",
        f"# Shared Caddy → container names over {SHARED_NET}. Per-site TLS below.",
        "# ============================================================",
        "",
        f"# Apex",
        f"{domain} {{",
        *_tls_block(),
        f"    reverse_proxy {app_target}",
        f"}}",
        "",
        f"# Nextcloud (specific — wins over the wildcard)",
        f"cloud.{domain} {{",
        *_tls_block(),
        f"    reverse_proxy {nc_target}",
        f"}}",
    ]
    if matrix_on and matrix_server_name:
        lines += [
            "",
            f"# Matrix homeserver ({matrix_server_name}) — Connect + federation.",
            f"{matrix_server_name} {{",
            *_tls_block(),
            f"    handle /.well-known/matrix/server {{",
            f"        header Content-Type application/json",
            f'        respond `{{"m.server": "{matrix_server_name}:443"}}` 200',
            f"    }}",
            f"    handle /.well-known/matrix/client {{",
            f"        header Content-Type application/json",
            f"        header Access-Control-Allow-Origin *",
            f'        respond `{{"m.homeserver": {{"base_url": "https://{matrix_server_name}"}}}}` 200',
            f"    }}",
            f"    handle {{",
            f"        reverse_proxy {mx_target}",
            f"    }}",
            f"}}",
        ]
    if voice_on:
        lines += [
            "",
            f"# Voice (jules STT/TTS) — specific, wins over the wildcard.",
            f"voice.{domain} {{",
            *_tls_block(),
            f"    reverse_proxy {voice_target}",
            f"}}",
        ]
    lines += [
        "",
        f"# Wildcard — every operator/presence subdomain host-routed inside the one Cove app.",
        f"*.{domain} {{",
        *_tls_block(),
        f"    reverse_proxy {app_target}",
        f"}}",
        "",
    ]
    return "\n".join(lines)


def install_haven_cove_snippet(snippet_text: str, cove_id: str, *,
                               caddy_dir: str = SHARED_CADDY_DIR,
                               caddy_container: str = SHARED_CADDY_CONTAINER) -> dict:
    """Drop a per-Cove snippet into {caddy_dir}/conf.d/{cove_id}.caddy and reload the
    SHARED Caddy. Used by the host-side CLI (set_domain.py --shared). Best-effort, same
    contract as install_caddy_snippet but pointed at the shared stack + its reload."""
    conf_d = Path(caddy_dir) / "conf.d"
    result = {"installed": False, "reloaded": False, "path": str(conf_d / f"{cove_id}.caddy")}
    try:
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / f"{cove_id}.caddy").write_text(snippet_text)
        result["installed"] = True
    except OSError as e:
        result["reason"] = f"could not write snippet: {e}"
        return result
    if shutil.which("docker"):
        try:
            r = subprocess.run(
                ["docker", "exec", caddy_container, "caddy", "reload",
                 "--config", DEFAULT_CADDYFILE_IN_CONTAINER],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                result["reloaded"] = True
            else:
                result["reason"] = f"caddy reload failed: {(r.stderr or r.stdout).strip()[:200]}"
        except Exception as e:
            result["reason"] = f"caddy reload error: {e}"
    else:
        result["reason"] = "docker not available to reload Caddy"
    return result


def install_caddy_snippet(snippet_text: str, cove_id: str, *,
                          caddy_dir: str = DEFAULT_CADDY_DIR,
                          caddy_container: str = DEFAULT_CADDY_CONTAINER) -> dict:
    """Drop the snippet into {caddy_dir}/conf.d/{cove_id}.caddy and reload Caddy.
    Requires the founder Caddyfile to `import conf.d/*.caddy` and the compose to mount
    that dir (see ensure_caddy_import_dir + the Caddy compose). Best-effort: if the
    dir or docker isn't here (e.g. running on the Mac), returns installed=False with
    a reason and the caller falls back to the in-folder snippet + printed steps."""
    conf_d = Path(caddy_dir) / "conf.d"
    result = {"installed": False, "reloaded": False, "path": str(conf_d / f"{cove_id}.caddy")}
    if not Path(caddy_dir).is_dir():
        result["reason"] = f"Caddy dir not found at {caddy_dir} (not on the host?)"
        return result
    try:
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / f"{cove_id}.caddy").write_text(snippet_text)
        result["installed"] = True
    except OSError as e:
        result["reason"] = f"could not write snippet: {e}"
        return result
    if shutil.which("docker"):
        try:
            r = subprocess.run(
                ["docker", "exec", caddy_container, "caddy", "reload",
                 "--config", DEFAULT_CADDYFILE_IN_CONTAINER],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                result["reloaded"] = True
            else:
                result["reason"] = f"caddy reload failed: {(r.stderr or r.stdout).strip()[:200]}"
        except Exception as e:
            result["reason"] = f"caddy reload error: {e}"
    else:
        result["reason"] = "docker not available to reload Caddy"
    return result


def reconcile_nextcloud_https(*, cove_id: str, domain: str,
                              nextcloud_container: str = "",
                              trusted_proxies: str = "") -> dict:
    """Point the RUNNING Nextcloud at its claimed https:// domain via occ
    (overwriteprotocol / overwritehost / overwrite.cli.url / trusted_proxies), so the
    desktop-client Login Flow hands back an https:// callback instead of the
    http://localhost one the client rejects. These are the same four values
    provision/centralized.py bakes into the NC compose env when the domain is known at
    BUILD time — a Cove that comes up domainless and claims in-browser later needs this
    runtime path (config.php lives in the nextcloud_data volume, so it's durable).

    Shared by the CLI reconciler (provision/set_domain.py) and the in-browser claim
    (dashboard/routes/domain.py) — the claim path previously reconciled DNS + Caddy but
    never NC, leaving overwrite.cli.url at http://localhost on every stranger box
    (CF-100 finding / CF-101 cluster; hit live on the nottington A2 test 2026-07-02).

    Dispatched DETACHED (fire-and-forget) so a slow / still-booting NC can never block
    an address claim (#CF-11 — was blocking 4x120s). Best-effort: never raises."""
    if not domain:
        return {"ok": False, "reason": "no domain set"}
    if not shutil.which("docker"):
        return {"ok": False, "reason": "docker not available for the NC occ reconfigure"}
    import shlex
    container = (nextcloud_container or "").strip() or f"{cove_id}-nextcloud"
    nc_host = f"cloud.{domain}"
    proxies = (trusted_proxies or "").strip() or "172.16.0.0/12"
    # NB `overwrite.cli.url` is NC's real (dotted) key — the old `overwritecliurl` was a
    # junk key NC ignored (how Clearfield drifted to a stale host).
    occ_sets = [
        ("overwriteprotocol", ["config:system:set", "overwriteprotocol", "--value", "https"]),
        ("overwritehost", ["config:system:set", "overwritehost", "--value", nc_host]),
        ("overwrite.cli.url", ["config:system:set", "overwrite.cli.url", "--value", f"https://{nc_host}"]),
        ("trusted_proxies", ["config:system:set", "trusted_proxies", "0", "--value", proxies]),
    ]
    try:
        cmds = [" ".join(shlex.quote(a) for a in ["docker", "exec", "-u", "www-data", container, "php", "occ", *occ_args])
                for _key, occ_args in occ_sets]
        subprocess.Popen(["sh", "-c", " ; ".join(cmds)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "container": container, "host": nc_host,
                "dispatched": [k for k, _ in occ_sets], "mode": "background"}
    except Exception as e:
        return {"ok": False, "container": container, "reason": f"dispatch error: {e}"}


# ---------------------------------------------------------------------------
# CF-101 — Matrix identity reconcile on the in-browser claim (regen-while-virgin)
# ---------------------------------------------------------------------------
# A Cove provisioned domainless stamps its Dendrite server_name as
# matrix.{cove-id}.localhost. The in-browser claim reconciles DNS + Caddy + NC
# (as of 354129c) but NOT Matrix, so Connect spins forever against a homeserver
# whose server_name isn't matrix.{domain}. server_name is baked into every event,
# so it can only change while the homeserver is VIRGIN (no human has spoken —
# only the Cove's own agents, whose accounts/events are regenerable state: they
# re-register on next boot exactly as first boot).
#
# This module contributes the SAFE, TESTED core: the virgin DECISION (pure), a
# config regenerator (stamps server_name = matrix.{domain}), and an orchestrator
# that DETECTS the situation and reports it. The destructive apply (DB wipe +
# container recreate) is GATED OFF by default (LP_MATRIX_REGEN_ENABLED) so the
# first deploy is report-only — the operator watches the virgin detection before
# any DB is wiped; the live regen proof is a run-3 item. Best-effort, never raises.

def _localpart_of(mxid: str) -> str:
    """'@stuart:matrix.localhost' -> 'stuart'. Accepts a bare localpart too."""
    m = (mxid or "").strip()
    if not m:
        return ""
    if m.startswith("@"):
        m = m[1:]
    return m.split(":", 1)[0]


def matrix_virgin_from_senders(senders, agent_localparts, ) -> bool:
    """Pure decision: is the homeserver VIRGIN (only agents present)? `senders` is
    the list of DISTINCT account localparts (or full mxids) seen in Dendrite;
    `agent_localparts` is the Cove's own bot ids (stuart/atlas/lt/...). Any sender
    that isn't an agent = a human is present = NOT virgin. Empty = virgin. Fails
    SAFE elsewhere: callers treat an unreadable DB as NOT virgin (never wipe on
    uncertainty)."""
    agents = {(_localpart_of(a) or a).lower() for a in (agent_localparts or [])}
    for s in senders or []:
        lp = _localpart_of(s)
        if lp and lp.lower() not in agents:
            return False
    return True


def regenerate_dendrite_config(*, domain: str, db_password: str,
                               registration_shared_secret: str,
                               bot_user_ids=None) -> str:
    """dendrite.yaml stamped with server_name = matrix.{domain}. Thin wrapper over
    the provisioner's build_dendrite_config so the claim-time regen and the first
    provision produce identical config for a given server_name. Lazy import keeps
    netconfig dependency-light for the CLI provisioner."""
    from src.utils.provision_templates import build_dendrite_config
    return build_dendrite_config(
        server_name=f"matrix.{domain}", db_password=db_password,
        registration_shared_secret=registration_shared_secret,
        bot_user_ids=bot_user_ids)


def _dendrite_account_localparts(postgres_container: str) -> tuple:
    """Best-effort: DISTINCT account localparts from Dendrite's userapi_accounts.
    Returns (localparts|None, reason). None = couldn't read (caller fails SAFE).
    Account presence is a cleaner, more reliable virgin signal than parsing event
    JSON, and it errs toward NOT wiping: a human account that exists but never
    spoke still blocks the regen."""
    if not shutil.which("docker"):
        return None, "docker not available"
    try:
        r = subprocess.run(
            ["docker", "exec", "-u", "postgres", postgres_container,
             "psql", "-U", "dendrite", "-d", "dendrite", "-tAc",
             "SELECT localpart FROM userapi_accounts"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None, f"psql failed: {(r.stderr or r.stdout).strip()[:160]}"
        parts = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return parts, "ok"
    except Exception as e:
        return None, f"query error: {str(e)[:120]}"


def reconcile_matrix_identity(*, cove_id: str, domain: str,
                              agent_localparts=None,
                              postgres_container: str = "",
                              dendrite_container: str = "",
                              cove_dir: str = "",
                              enabled: bool = None) -> dict:
    """Reconcile the running Dendrite's server_name to matrix.{domain} on claim,
    but ONLY while virgin. Returns a structured result the claim surfaces:
      {ok, virgin, changed, gated, message, reason}
    - not virgin  -> changed False + "existing conversations" message.
    - virgin + not enabled (default) -> report-only: changed False, gated True,
      message says it WOULD regenerate (flip LP_MATRIX_REGEN_ENABLED to apply).
    - virgin + enabled -> attempt the regen (stop→wipe→config→start); changed True/False.
    Never raises; fires detached from the claim, never fatal.

    `postgres_container` default fix (run-3): a FRESH single-stack Cove has NO
    `{cove_id}-dendrite-postgres` — Dendrite's DB lives in the shared `{cove_id}-postgres`
    (database `dendrite`, user `dendrite`). `cove_dir` is the instance dir on the HOST so
    the destructive apply can rewrite the read-only-in-container dendrite.yaml."""
    if not domain:
        return {"ok": False, "reason": "no domain set"}
    new_server = f"matrix.{domain}"
    # FRESH stacks: dendrite DB is in `{cove_id}-postgres`, not a separate `-dendrite-postgres`.
    pg = (postgres_container or "").strip() or f"{cove_id}-postgres"
    dd = (dendrite_container or "").strip() or f"{cove_id}-dendrite"
    if enabled is None:
        enabled = os.getenv("LP_MATRIX_REGEN_ENABLED", "").strip() in ("1", "true", "yes")

    # VIRGIN CHECK (fails SAFE: unreadable DB -> treat as NOT virgin).
    parts, reason = _dendrite_account_localparts(pg)
    if parts is None:
        return {"ok": False, "virgin": None, "changed": False,
                "server_name": new_server, "reason": f"virgin check unavailable ({reason})",
                "message": "Couldn't verify the Matrix homeserver state; leaving Connect "
                           "address unchanged."}
    virgin = matrix_virgin_from_senders(parts, agent_localparts)
    if not virgin:
        return {"ok": True, "virgin": False, "changed": False, "server_name": new_server,
                "message": "Connect address can't change automatically (existing "
                           "conversations). Matrix identity stays as provisioned."}
    if not enabled:
        return {"ok": True, "virgin": True, "changed": False, "gated": True,
                "server_name": new_server,
                "message": f"Matrix homeserver is virgin — it can be regenerated to "
                           f"{new_server}. Enable LP_MATRIX_REGEN_ENABLED to apply "
                           f"(agents re-register on next boot)."}
    # ENABLED + virgin: attempt the regen. Best-effort, each step guarded.
    return _apply_matrix_regen(cove_id=cove_id, domain=domain, new_server=new_server,
                               postgres_container=pg, dendrite_container=dd,
                               cove_dir=cove_dir)


def _rewrite_dendrite_server_name(cove_dir: str, cove_id: str, new_server: str):
    """Host-side rewrite of the `server_name:` line in the instance's dendrite.yaml.
    Returns True on success, else a short reason string.

    WHY host-side (run-3 root fix): the app/dendrite containers bind-mount this file
    READ-ONLY, so the old in-container `sed` silently no-opped. set_domain.py runs on
    the HOST where the file is writable. The provisioner writes it at
    `{cove_dir}/docker/dendrite.yaml` (default layout `out/{cove_id}-cove/docker/...`)."""
    candidates = []
    if cove_dir:
        candidates.append(Path(cove_dir) / "docker" / "dendrite.yaml")
        candidates.append(Path(cove_dir) / "dendrite.yaml")
    candidates.append(Path(f"out/{cove_id}-cove/docker/dendrite.yaml"))
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return f"dendrite.yaml not found (looked: {', '.join(str(c) for c in candidates)})"
    try:
        text = path.read_text()
        new_text, n = re.subn(r"(?m)^([ \t]*server_name:).*$", rf"\1 {new_server}", text)
        if n == 0:
            return "no server_name: line in dendrite.yaml"
        path.write_text(new_text)
        return True
    except Exception as e:
        return f"config rewrite failed: {str(e)[:120]}"


def _apply_matrix_regen(*, cove_id: str, domain: str, new_server: str,
                        postgres_container: str, dendrite_container: str,
                        cove_dir: str = "") -> dict:
    """The DESTRUCTIVE regen (gated on by reconcile_matrix_identity): give the running
    Dendrite a new server_name = matrix.{domain} and wipe its regenerable DB so agents
    re-register on boot.

    ORDER MATTERS (run-3 fix): STOP dendrite → WIPE the DB → REWRITE the config
    host-side → START dendrite. The OLD order (a) seded server_name inside the
    container, where dendrite.yaml is a READ-ONLY bind mount (silent no-op), and
    (b) dropped the DB while Dendrite was still connected to it. Never raises.
    UNTESTED live in the sandbox (no docker) — the order/host-write logic is unit-tested;
    the live proof is the next fresh run."""
    if not shutil.which("docker"):
        return {"ok": False, "virgin": True, "changed": False, "server_name": new_server,
                "reason": "docker not available to apply the regen"}
    steps = {}
    try:
        # 1. STOP dendrite first — never wipe a DB out from under a live connection, and
        #    the container must be down before it re-reads the rewritten config on start.
        r_stop = subprocess.run(["docker", "stop", dendrite_container],
                                capture_output=True, text=True, timeout=60)
        steps["stop"] = (r_stop.returncode == 0) or (r_stop.stderr or "").strip()[:160]
        # 2. WIPE the regenerable Matrix DB (schema rebuilds on boot; agents re-register).
        #    Connect to the maintenance `postgres` db to drop/recreate `dendrite`.
        wipe = ("psql -U dendrite -d postgres -c 'DROP DATABASE IF EXISTS dendrite' && "
                "psql -U dendrite -d postgres -c 'CREATE DATABASE dendrite'")
        r_wipe = subprocess.run(["docker", "exec", "-u", "postgres", postgres_container,
                                 "sh", "-c", wipe], capture_output=True, text=True, timeout=40)
        steps["db_wipe"] = (r_wipe.returncode == 0) or (r_wipe.stderr or r_wipe.stdout).strip()[:160]
        # 3. REWRITE server_name in the HOST-side dendrite.yaml (in-container copy is ro).
        steps["config"] = _rewrite_dendrite_server_name(cove_dir, cove_id, new_server)
        # 4. START dendrite: it rebuilds schema on the fresh DB + re-registers agents
        #    against the new server_name.
        r_start = subprocess.run(["docker", "start", dendrite_container],
                                 capture_output=True, text=True, timeout=60)
        steps["start"] = (r_start.returncode == 0) or (r_start.stderr or "").strip()[:160]
        changed = all(v is True for v in steps.values())
        return {"ok": changed, "virgin": True, "changed": changed,
                "server_name": new_server, "steps": steps,
                "message": (f"Regenerated Matrix identity to {new_server}." if changed
                            else "Matrix regen partially applied — see steps.")}
    except Exception as e:
        return {"ok": False, "virgin": True, "changed": False, "server_name": new_server,
                "steps": steps, "reason": f"regen error: {str(e)[:140]}"}


# ---------------------------------------------------------------------------
# batch-10 #5 — full-table Dendrite user removal (the proven run-3 live fix)
# ---------------------------------------------------------------------------
# Matrix localparts allow [a-z0-9._=/-]; validate before it ever reaches SQL.
_LOCALPART_RE = re.compile(r"^[a-z0-9._=/\-]{1,255}$")


def _remove_user_statements(tables) -> list:
    """Pure: the DELETE statements that clear the localpart (bound as the psql var
    :'lp', which auto-quotes) from each userapi_* table that has a localpart column.
    Kept separate so the SQL-generating logic is unit-testable without docker/psql.
    Table names come from information_schema; the value is bound, never interpolated."""
    return [f"DELETE FROM {t} WHERE localpart = :'lp'" for t in tables]


def dendrite_remove_user(*, localpart: str, postgres_container: str = "",
                         cove_id: str = "") -> dict:
    """Delete a Dendrite account from ALL `userapi_*` tables that key on `localpart`.

    WHY all tables (run-3, proven live 2026-07-04): a PARTIAL delete (e.g. only
    userapi_accounts) leaves the register endpoint returning 200 for the localpart while
    the account never truly lands and login stays M_FORBIDDEN — the register-200-ghost that
    made muller's steward Connect un-healable in-app. Clearing every userapi_* row for the
    localpart is the only reliable reset. information_schema-driven so it survives Dendrite
    schema changes (new userapi_* tables are covered automatically).

    Host-side only (the app container has no docker socket): call from set_domain.py
    --remove-matrix-user. Never raises; returns a structured result."""
    lp = (localpart or "").strip().lower()
    if not _LOCALPART_RE.match(lp):
        return {"ok": False, "reason": f"invalid localpart {localpart!r}"}
    if not shutil.which("docker"):
        return {"ok": False, "reason": "docker not available"}
    pg = (postgres_container or "").strip() or (f"{cove_id}-postgres" if cove_id else "")
    if not pg:
        return {"ok": False, "reason": "no postgres container (pass --postgres-container or --cove-id)"}

    # 1. Discover userapi_* tables that have a localpart column.
    try:
        r = subprocess.run(
            ["docker", "exec", "-u", "postgres", pg,
             "psql", "-U", "dendrite", "-d", "dendrite", "-tAc",
             "SELECT table_name FROM information_schema.columns "
             "WHERE table_schema='public' AND column_name='localpart' "
             "AND table_name LIKE 'userapi\\_%'"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as e:
        return {"ok": False, "reason": f"table discovery error: {str(e)[:120]}"}
    if r.returncode != 0:
        return {"ok": False, "reason": f"table discovery failed: {(r.stderr or r.stdout).strip()[:160]}"}
    tables = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not tables:
        return {"ok": False, "reason": "no userapi_* tables with a localpart column found"}

    # 2. Delete the localpart from every one of them (one transaction). The value is
    #    bound via a psql var (:'lp' auto-quotes); lp is already validated to [a-z0-9._=/-]
    #    (no spaces), so a bare \set is safe.
    stmts = ";\n".join(_remove_user_statements(tables))
    sql = f"\\set lp {lp}\nBEGIN;\n{stmts};\nCOMMIT;"
    try:
        d = subprocess.run(
            ["docker", "exec", "-i", "-u", "postgres", pg,
             "psql", "-U", "dendrite", "-d", "dendrite", "-v", "ON_ERROR_STOP=1"],
            input=sql, capture_output=True, text=True, timeout=40,
        )
    except Exception as e:
        return {"ok": False, "reason": f"delete error: {str(e)[:140]}", "tables": tables}
    if d.returncode != 0:
        return {"ok": False, "reason": f"delete failed: {(d.stderr or d.stdout).strip()[:200]}",
                "tables": tables}
    return {"ok": True, "localpart": lp, "tables": tables,
            "message": f"Cleared {lp} from {len(tables)} userapi_* tables."}


# ---------------------------------------------------------------------------
# #166 — Cloudflare DNS (wildcard → mesh IP)
# ---------------------------------------------------------------------------
def ensure_dns(domain: str, target_ip: str = "") -> dict:
    """Create/refresh *.{domain} + {domain} A records → the machine's mesh IP, via
    cloudflare_dns. Best-effort: needs CLOUDFLARE_API_TOKEN (the token Caddy uses).
    Returns {ok, ...}. In production the Hub registrar (#133) calls the same function
    at provision/claim so the operator never touches DNS; here it runs when a
    co-located Cove is provisioned directly on the host and the token is present."""
    if not os.getenv("CLOUDFLARE_API_TOKEN", "").strip():
        return {"ok": False, "reason": "CLOUDFLARE_API_TOKEN not set (skipping auto-DNS)"}
    try:
        from cloudflare_dns import ensure_cove_dns  # sibling module
    except ImportError:
        try:
            from provision.cloudflare_dns import ensure_cove_dns  # packaged import
        except ImportError as e:
            return {"ok": False, "reason": f"cloudflare_dns import failed: {e}"}
    try:
        return ensure_cove_dns(domain, target_ip)
    except Exception as e:
        return {"ok": False, "reason": f"DNS provisioning failed: {e}"}


# ---------------------------------------------------------------------------
# #133 — register the Cove with the Hub registrar at provision time
# ---------------------------------------------------------------------------
def register_cove_with_hub(*, cove_id: str, name: str, owner_handle: str = "",
                           domain: str = "", homeserver: str = "", mesh_ip: str = "",
                           referred_by: str = "") -> dict:
    """Best-effort: tell the Hub registrar this Cove exists (name + @handle uniqueness,
    federation facts) so it can be resolved/nested into a Haven. Stdlib-only (the
    provisioner stays dependency-light). The space_id is registered later by the
    steward on first Connect (matrix_spaces.ensure_cove_space). Needs LP_REGISTRY_URL
    + LP_REGISTRY_SECRET in the environment; silently skips if unset."""
    base = (os.getenv("LP_REGISTRY_URL", "") or "").rstrip("/")
    secret = os.getenv("LP_REGISTRY_SECRET", "")
    if not (base and secret):
        return {"ok": False, "reason": "LP_REGISTRY_URL / LP_REGISTRY_SECRET not set (skipping registry)"}
    body = json.dumps({
        "cove_id": cove_id, "name": name, "owner_handle": owner_handle,
        "domain": domain, "homeserver": homeserver, "mesh_ip": mesh_ip,
        "referred_by": referred_by,   # affiliate edge (#169): who recruited this operator
    }).encode()
    req = urllib.request.Request(base + "/api/registry/cove", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Registry-Secret", secret)
    req.add_header("User-Agent", "LucidCove-Cove/1.0")  # Cloudflare blocks default python UAs (1010)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail", "")
        except Exception:
            detail = ""
        return {"ok": False, "status": e.code, "reason": detail or f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "reason": f"registry unreachable: {str(e)[:120]}"}
