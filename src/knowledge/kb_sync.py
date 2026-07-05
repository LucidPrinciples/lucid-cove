"""
kb_sync.py — Subscribe to the canonical Knowledge Base (the single source of truth).

The KB is authored once, at the hub (the `ltp-drop` repo's `kb-source/`), published
as a signed, versioned artifact at drop.lucidprinciples.com/kb/ (see Socrates
`kb_publisher.py`, which mirrors the LTP Drop pipeline). Every Cove SUBSCRIBES,
read-only — it never owns a fork.

This module pulls the published manifest, verifies its Ed25519 signature against
the publisher's public key, and (only if the version changed) mirrors the files
into the steward's Nextcloud `AgentSkills/Knowledge Base`. That steward folder is
already shared read-only to every presence (nextcloud.py::_share_kb_with_presence),
so the chain is read-only the whole way: hub -> steward -> presence. One version
everywhere, no drift. This is the moat.

ADDITIVE — touches no protected LTP files and no tuning-Drop schema. The KB is a
separate signed artifact that merely rides the same publish infrastructure.

Fail-closed: if the public key is missing or a signature/hash check fails, we do
NOT write anything. A bad or unverifiable feed never corrupts the locked KB; the
last good copy in the steward's space stands.

Config (env):
  LP_KB_MANIFEST_URL   default https://drop.lucidprinciples.com/kb/manifest.json
  LP_KB_PUBLIC_KEY     Ed25519 public key (PEM). Required — no key, no sync.
"""
import hashlib
import json
import os
from src.env import env
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from src.dashboard.routes.nextcloud import (
    NC_URL,
    NC_ADMIN_USER,
    NC_ADMIN_PASSWORD,
    STEWARD_KB_FOLDER,
    _ensure_steward_kb,
)

MANIFEST_URL = env("LP_KB_MANIFEST_URL", "https://drop.lucidprinciples.com/kb/manifest.json")
PUBLIC_KEY_PEM = env("LP_KB_PUBLIC_KEY").strip()

# Version marker stored alongside the mirrored KB, so we only re-sync on change.
VERSION_MARKER = f"{STEWARD_KB_FOLDER}/.kb-version"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("[%Y-%m-%d %H:%M:%SZ]")


def _canonical_json(obj: dict) -> str:
    """Match the publisher's canonicalization exactly (sorted keys, no spaces, ASCII)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _verify_manifest(manifest: dict) -> bool:
    """Verify the manifest's Ed25519 signature. Fail closed."""
    if not PUBLIC_KEY_PEM:
        print(f"{_ts()} [kb-sync] LP_KB_PUBLIC_KEY not set — refusing to sync unverified KB.")
        return False
    sig_b64 = manifest.get("signature")
    if not sig_b64:
        print(f"{_ts()} [kb-sync] Manifest has no signature — refusing.")
        return False
    try:
        import base64
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        # Accept the key as a full PEM (real or \n-escaped newlines) OR as the
        # bare base64 SubjectPublicKeyInfo body — so it's trivial to set as a
        # single-line env var on every Cove.
        pem = PUBLIC_KEY_PEM.replace("\\n", "\n").strip()
        if "BEGIN PUBLIC KEY" not in pem:
            pem = "-----BEGIN PUBLIC KEY-----\n" + pem + "\n-----END PUBLIC KEY-----"
        key = load_pem_public_key(pem.encode("utf-8"))
        if not isinstance(key, Ed25519PublicKey):
            print(f"{_ts()} [kb-sync] Configured key is not Ed25519 — refusing.")
            return False
        unsigned = {k: v for k, v in manifest.items() if k != "signature"}
        payload = _canonical_json(unsigned).encode("utf-8")
        key.verify(base64.b64decode(sig_b64), payload)
        return True
    except Exception as e:
        print(f"{_ts()} [kb-sync] Signature verification FAILED: {e}")
        return False


def _steward_dav(path: str) -> str:
    return f"{NC_URL}/remote.php/dav/files/{NC_ADMIN_USER}/{quote(path, safe='/')}"


async def _read_marker(client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(_steward_dav(VERSION_MARKER), auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD))
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return ""


async def _ensure_parent(client: httpx.AsyncClient, rel_path: str) -> None:
    """MKCOL every parent dir of a KB-relative file path, under the steward KB folder."""
    parts = rel_path.split("/")[:-1]
    cur = STEWARD_KB_FOLDER
    for p in parts:
        cur = f"{cur}/{p}"
        try:
            await client.request("MKCOL", _steward_dav(cur), auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD))
        except Exception:
            pass


async def sync_kb(force: bool = False) -> dict:
    """Pull, verify, and mirror the canonical KB into the steward's space.

    Returns {ok, synced, version, files, skipped?, error?}. Never raises.
    """
    if not NC_URL or not NC_ADMIN_PASSWORD:
        return {"ok": False, "error": "Nextcloud not configured"}

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # 1. Fetch + verify the manifest (signature gate — fail closed).
            r = await client.get(MANIFEST_URL)
            if r.status_code != 200:
                return {"ok": False, "error": f"manifest fetch HTTP {r.status_code}"}
            manifest = r.json()
            if not _verify_manifest(manifest):
                return {"ok": False, "error": "manifest signature invalid"}

            version = manifest.get("kb_version", "")
            files = manifest.get("files", [])
            base = MANIFEST_URL.rsplit("/", 1)[0]  # .../kb

            # 2. Skip if we already have this version.
            await _ensure_steward_kb(NC_ADMIN_USER, NC_ADMIN_PASSWORD)
            current = await _read_marker(client)
            if version and current == version and not force:
                return {"ok": True, "synced": False, "version": version, "skipped": "up-to-date"}

            # 3. Download each file, verify its hash, write into the steward KB.
            written, errors = [], []
            for f in files:
                rel = (f.get("path") or "").lstrip("/")
                want = f.get("sha256", "")
                if not rel:
                    continue
                fr = await client.get(f"{base}/files/{quote(rel, safe='/')}")
                if fr.status_code != 200:
                    errors.append(f"{rel}: HTTP {fr.status_code}")
                    continue
                body = fr.content
                if want and hashlib.sha256(body).hexdigest() != want:
                    errors.append(f"{rel}: hash mismatch — skipped")
                    continue
                await _ensure_parent(client, rel)
                pr = await client.put(
                    _steward_dav(f"{STEWARD_KB_FOLDER}/{rel}"),
                    auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD),
                    content=body,
                )
                if pr.status_code in (201, 204):
                    written.append(rel)
                else:
                    errors.append(f"{rel}: PUT HTTP {pr.status_code}")

            # 4. Pin the new version only if everything landed cleanly.
            if not errors and version:
                await client.put(
                    _steward_dav(VERSION_MARKER),
                    auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD),
                    content=version.encode("utf-8"),
                )

            ok = not errors
            print(f"{_ts()} [kb-sync] version={version} wrote={len(written)} errors={len(errors)}")
            return {
                "ok": ok,
                "synced": bool(written),
                "version": version,
                "files": written,
                "errors": errors,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}
