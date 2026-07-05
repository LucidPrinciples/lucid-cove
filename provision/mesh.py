#!/usr/bin/env python3
"""
mesh.py — device onboarding onto the private Headscale mesh (#134).

A Cove is a private mesh node: humans reach it over Tailscale/Headscale, not the
public internet. Onboarding therefore has to get the operator's device onto the
mesh. This mints a single-use, time-boxed Headscale pre-auth key the operator
pastes into the Tailscale app (or runs `tailscale up --login-server ... --authkey`).

Best-effort + stdlib-only. It shells out to the Headscale CLI (the coordinator runs
as a container on the VPS, so this is normally invoked there, or over SSH from the
provisioner). If Headscale isn't reachable it returns instructions instead of a key,
so onboarding degrades gracefully rather than failing.

Usage (CLI, on the Headscale host):
  python3 mesh.py [user] [--reusable] [--expiry 1h]
"""
import argparse
import json
import os
import shutil
import subprocess

DEFAULT_USER = "lucid"                  # the Headscale user all nodes register under
DEFAULT_CONTAINER = "headscale"         # the Headscale container name on the VPS
# Coordinator URL. Env-overridable so a self-host / alternate mesh isn't pinned to
# the founder coordinator. Falls back to the Lucid Cove default.
LOGIN_SERVER = os.environ.get("HEADSCALE_LOGIN_SERVER", "https://headscale.lucidcove.org")


def _hs_user(name: str) -> str:
    """Sanitize an arbitrary id (cove_id / handle) into a valid Headscale username —
    a lowercase DNS-ish label. Falls back to the default user if nothing survives."""
    import re
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")
    return s or DEFAULT_USER


def _ensure_user_via_api(api_url: str, api_key: str, name: str) -> None:
    """Create the Headscale user if it doesn't already exist (idempotent, best-effort).
    Lets each Cove's first device-join auto-provision its own mesh namespace."""
    import urllib.request
    try:
        ureq = urllib.request.Request(api_url + "/api/v1/user",
                                      headers={"Authorization": "Bearer " + api_key})
        with urllib.request.urlopen(ureq, timeout=15) as ur:
            users = json.loads(ur.read().decode()).get("users") or []
        if any((u.get("name") or "") == name for u in users):
            return
        body = json.dumps({"name": name}).encode()
        creq = urllib.request.Request(api_url + "/api/v1/user", data=body, method="POST",
                                      headers={"Authorization": "Bearer " + api_key,
                                               "Content-Type": "application/json"})
        urllib.request.urlopen(creq, timeout=15).read()
    except Exception:
        pass


def _headscale_cmd(container: str) -> list:
    """Prefer `docker exec <container> headscale`; fall back to a host `headscale`."""
    if shutil.which("docker"):
        return ["docker", "exec", container, "headscale"]
    if shutil.which("headscale"):
        return ["headscale"]
    return []


def _create_via_api(user: str, reusable: bool, expiry: str) -> dict | None:
    """Mint via the Headscale HTTP API (so a container that can't `docker exec` — e.g.
    the hub — can still mint). Uses HEADSCALE_API_URL + HEADSCALE_API_KEY. Returns the
    result dict, or None if the API isn't configured (caller falls back to the CLI)."""
    api_url = (os.environ.get("HEADSCALE_API_URL", "") or "").strip().rstrip("/")
    api_key = (os.environ.get("HEADSCALE_API_KEY", "") or "").strip()
    if not (api_url and api_key):
        return None
    # Make sure this Cove's mesh user exists before we mint into it.
    _ensure_user_via_api(api_url, api_key, str(user))
    import datetime
    import urllib.request
    # expiry like "1h"/"24h"/"30m" → an absolute RFC3339 timestamp.
    secs = 3600
    try:
        n = int(expiry[:-1]); unit = expiry[-1].lower()
        secs = n * {"h": 3600, "m": 60, "d": 86400}.get(unit, 3600)
    except Exception:
        pass
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=secs)
           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Headscale (>=0.26) scopes pre-auth keys by NUMERIC user id, not name. Resolve it.
    uid = str(user)
    if not uid.isdigit():
        try:
            ureq = urllib.request.Request(api_url + "/api/v1/user",
                                          headers={"Authorization": "Bearer " + api_key})
            with urllib.request.urlopen(ureq, timeout=15) as ur:
                for u in (json.loads(ur.read().decode()).get("users") or []):
                    if (u.get("name") or "") == user:
                        uid = str(u.get("id")); break
        except Exception:
            pass
    body = json.dumps({"user": uid, "reusable": reusable, "ephemeral": False,
                       "expiration": exp}).encode()
    req = urllib.request.Request(api_url + "/api/v1/preauthkey", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "reason": f"headscale API call failed: {str(e)[:160]}"}
    key = ((data.get("preAuthKey") or {}).get("key")) or data.get("key") or ""
    if not key:
        return {"ok": False, "reason": "headscale API returned no key"}
    return {"ok": True, "key": key, "login_server": LOGIN_SERVER,
            "join_cmd": f"tailscale up --login-server {LOGIN_SERVER} --authkey {key}"}


def create_preauth_key(user: str = DEFAULT_USER, *, reusable: bool = False,
                       expiry: str = "1h", container: str = DEFAULT_CONTAINER) -> dict:
    """Mint a Headscale pre-auth key. Returns {ok, key, login_server, join_cmd} or
    {ok: False, reason, instructions}. Tries the HTTP API first (works from any
    container, incl. the hub), then falls back to the local headscale CLI."""
    user = _hs_user(user)
    via_api = _create_via_api(user, reusable, expiry)
    if via_api is not None:
        return via_api
    base = _headscale_cmd(container)
    if not base:
        return {"ok": False, "reason": "Headscale CLI not reachable here",
                "instructions": "Run on the Headscale host: "
                                f"headscale preauthkeys create --user {user} --expiration {expiry}"}
    # Ensure the user exists on the local coordinator (idempotent; ignore "already exists").
    try:
        subprocess.run(base + ["users", "create", user], capture_output=True, text=True, timeout=15)
    except Exception:
        pass
    # Resolve the user name → numeric id (newer headscale CLI wants --user <id>).
    uid = str(user)
    if not uid.isdigit():
        try:
            ulist = subprocess.run(base + ["users", "list", "--output", "json"],
                                   capture_output=True, text=True, timeout=15)
            if ulist.returncode == 0:
                for u in (json.loads(ulist.stdout or "[]") or []):
                    if (u.get("name") or "") == user:
                        uid = str(u.get("id")); break
        except Exception:
            pass
    cmd = base + ["preauthkeys", "create", "--user", uid, "--expiration", expiry, "--output", "json"]
    if reusable:
        cmd.append("--reusable")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as e:
        return {"ok": False, "reason": f"headscale call failed: {str(e)[:120]}"}
    if out.returncode != 0:
        return {"ok": False, "reason": (out.stderr or out.stdout).strip()[:200]}
    raw = (out.stdout or "").strip()
    try:
        key = json.loads(raw).get("key", raw)
    except Exception:
        key = raw  # some versions print the bare key
    return {
        "ok": True,
        "key": key,
        "login_server": LOGIN_SERVER,
        "join_cmd": f"tailscale up --login-server {LOGIN_SERVER} --authkey {key}",
    }


def main():
    ap = argparse.ArgumentParser(description="Mint a Headscale pre-auth key for device onboarding (#134).")
    ap.add_argument("user", nargs="?", default=DEFAULT_USER)
    ap.add_argument("--reusable", action="store_true")
    ap.add_argument("--expiry", default="1h")
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    args = ap.parse_args()
    res = create_preauth_key(args.user, reusable=args.reusable, expiry=args.expiry, container=args.container)
    if res.get("ok"):
        print("Pre-auth key (single-use, expires %s):" % args.expiry)
        print("  " + res["key"])
        print("\nOn the new device:")
        print("  " + res["join_cmd"])
    else:
        print("Could not mint a key:", res.get("reason"))
        if res.get("instructions"):
            print(res["instructions"])


if __name__ == "__main__":
    main()
