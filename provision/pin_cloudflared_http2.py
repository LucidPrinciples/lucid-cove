#!/usr/bin/env python3
"""
pin_cloudflared_http2.py — re-create an existing Cove cloudflared connector on HTTP/2.

Use when a tunnel already runs (token managed) but bulk egress through a public
hostname is ~KB/s while host uplink is fine. Common cause: QUIC/UDP to the CF
edge failing on dual-stack home hosts.

Does NOT print tokens. Idempotent: rm + run with the same TUNNEL_TOKEN already
on the live container (or TUNNEL_TOKEN in the environment).

Usage (on the host):
  python3 provision/pin_cloudflared_http2.py --name lucidcove-6f6f-cloudflared
  python3 provision/pin_cloudflared_http2.py --name lucidcove-6f6f-cloudflared --protocol http2
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _inspect_token(name: str) -> str:
    r = subprocess.run(
        ["docker", "inspect", name, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "inspect failed").strip()[:300])
    for line in (r.stdout or "").splitlines():
        if line.startswith("TUNNEL_TOKEN="):
            return line.split("=", 1)[1].strip()
    env_tok = (os.getenv("TUNNEL_TOKEN") or "").strip()
    if env_tok:
        return env_tok
    raise RuntimeError(f"no TUNNEL_TOKEN on container {name} or in env")


def _network_mode(name: str) -> str:
    r = subprocess.run(
        ["docker", "inspect", name, "--format", "{{.HostConfig.NetworkMode}}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return "host"
    return (r.stdout or "host").strip() or "host"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="cloudflared container name")
    ap.add_argument(
        "--protocol",
        default="http2",
        help="edge transport (default http2; quic only if measured better)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    proto = (args.protocol or "http2").strip().lower()
    if proto in ("h2", "tcp"):
        proto = "http2"
    if proto not in ("http2", "quic"):
        print(f"unknown protocol {proto!r} (use http2 or quic)")
        return 2

    try:
        tok = _inspect_token(args.name)
        network = _network_mode(args.name)
    except Exception as e:
        print(f"failed: {e}")
        return 1

    rm = ["docker", "rm", "-f", args.name]
    run = [
        "docker", "run", "-d",
        "--name", args.name,
        "--restart", "unless-stopped",
        "--network", network,
        "-e", "TUNNEL_TOKEN=" + tok,
        "-e", "TUNNEL_TRANSPORT_PROTOCOL=" + proto,
        "cloudflare/cloudflared:latest",
        "tunnel", "--no-autoupdate", "--protocol", proto, "run",
    ]
    print(f"pin {args.name} protocol={proto} network={network}")
    if args.dry_run:
        print("dry-run: would rm + run (token not printed)")
        return 0
    subprocess.run(rm, capture_output=True, text=True)
    r = subprocess.run(run, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"docker run failed: {(r.stderr or r.stdout).strip()[:400]}")
        return 1
    print(f"ok {args.name} recreated protocol={proto} id={(r.stdout or '').strip()[:12]}")
    logs = subprocess.run(
        ["docker", "logs", "--tail", "8", args.name],
        capture_output=True, text=True,
    )
    for line in ((logs.stdout or "") + (logs.stderr or "")).splitlines()[-8:]:
        low = line.lower()
        if "token" in low or "tunnel_token" in low:
            continue
        print("log:", line[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
