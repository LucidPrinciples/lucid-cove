"""
Nextcloud provisioning and multi-user credential routing.

Handles:
  - Creating Nextcloud user accounts when users upgrade to Operator+
  - Looking up per-user NC credentials for WebDAV/CalDAV routing
  - Admin endpoints for manual provisioning and quota management

In multi mode (COVE_MODE=multi), each Operator+ user gets their own
Nextcloud account. Credentials are stored in the accounts table
(nc_username, nc_password). All file/calendar routes use get_nc_creds()
to resolve the correct credentials for the logged-in user.

In single mode, falls back to NEXTCLOUD_USER/NEXTCLOUD_PASSWORD env vars.

OCS API reference: https://docs.nextcloud.com/server/latest/admin_manual/configuration_user/user_provisioning_api.html

API:
  POST /api/admin/nextcloud/provision/{presence_id}  — provision NC user
  GET  /api/admin/nextcloud/status                   — NC connectivity check
"""

import os
from src.env import env
import re
import logging
import secrets
import string

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger("nextcloud")

COVE_MODE = env("COVE_MODE", "single")
NC_URL = env("NEXTCLOUD_URL")
NC_ADMIN_USER = env("NEXTCLOUD_ADMIN_USER", "admin")
NC_ADMIN_PASSWORD = env("NEXTCLOUD_ADMIN_PASSWORD")

# Cove-wide default quota lives in cove.yaml (storage.default_quota). Open-source
# default is "none" (unlimited, disk-bounded); the hosted layer overrides per tier.
# Resolved at provision time via get_default_quota() so config changes take effect
# without a code change. The string here is only a last-resort import-time fallback.
DEFAULT_QUOTA = "none"

# ---------------------------------------------------------------------------
# Folder structures by tier — kept separate so they evolve independently.
# Each list is additive: Presence gets BASE + PRESENCE, Steward gets BASE + STEWARD.
# ---------------------------------------------------------------------------

# Every paid account gets these (Operator+)
BASE_FOLDERS = [
    "Documents",
    "Projects",
    "Notes",
    "InstantUpload",  # Nextcloud mobile auto-upload (GrapheneOS Pixel default)
    "InstantUpload/Camera",
    "InstantUpload/Screenshots",
]

# Presence tier — personal agent workspace
PRESENCE_FOLDERS = [
    "AgentSkills",
    "AgentSkills/Inbox",
    "AgentSkills/Inbox/Archive",
    # batch8 #7b (CF-107): Flows/ + Actions/ (+Archive) are NO LONGER seeded —
    # zero readers, and Actions is superseded by the DB-backed board.
    # #CF-113: per-presence AgentSkills/Shared stubs retired — real share is
    # steward-owned root CoveShared (operator-only RW). Existing Coves may still
    # have an empty Shared/ folder; we stop seeding new ones.
    "AgentSkills/Content",
    "AgentSkills/Content/video",
    # #1524 — seed ONLY the folders the pipeline actually uses (inbox -> processing -> raw,
    # with transcripts/shorts/moments as products). Dropped processed/clips/thumbnails/done:
    # they were seeded but never read or written anywhere (the stale folders on the Files screen).
    "AgentSkills/Content/video/inbox",
    "AgentSkills/Content/video/processing",
    "AgentSkills/Content/video/raw",
    "AgentSkills/Content/video/transcripts",
    "AgentSkills/Content/video/shorts",
    "AgentSkills/Content/video/moments",
    # Operator policy 2026-07-20: retired content lands here (never hard-deleted).
    "AgentSkills/Content/video/to-delete",
    "AgentSkills/To-Delete",
    "AgentSkills/Content/images",
    "AgentSkills/Content/audio",
    "AgentSkills/Content/posts",
    "AgentSkills/Sites",
    "AgentSkills/Context",
    "AgentSkills/Context/sessions",
    "AgentSkills/Ops",
]

# Steward (Stuart-type) — same as Presence plus Knowledge Base
STEWARD_FOLDERS = PRESENCE_FOLDERS + [
    "AgentSkills/Knowledge Base",
    # Team deliverables + per-agent workspaces (agent-workspace-access-spec.md).
    # Reports = shared functional deliverables (organized by what they are, not who made
    # them). Team = parent for per-agent workspaces; Team/<agent>/ is created on first
    # use by the file tool, so only the parent is seeded here.
    "AgentSkills/Reports",
    "AgentSkills/Team",
]

# Backward compat alias
OPERATOR_FOLDERS = BASE_FOLDERS

# ---------------------------------------------------------------------------
# Steward access boundary — which Presence folders the steward can manage.
#
# PRIVATE (operator only, steward has NO access):
#   Inbox/        — Jules voice notes. Sacred. Never touches the cloud.
#   Inbox/Archive — Archived jules. Same rule.
#   Ops/          — Jules backlog, operator-level operational notes
#   Context/      — Agent session history, personal memory
#   (Flows/ retired batch8 #7b — no longer seeded, zero readers)
#
# TEAM-MANAGED (shared with steward for team operations):
#   Content/      — Video pipeline, images, audio, posts. Team produces.
#   (Actions/ retired batch8 #7b — DB-backed board superseded it)
#   Sites/        — Archimedes builds and manages sites.
#   Shared/       — retired stub (#CF-113 → root CoveShared for operators).
#
# The provisioner creates NC shares for team-managed folders when a
# Presence is provisioned. This is how Stuart writes to Presence data
# via WebDAV without accessing private folders.
# ---------------------------------------------------------------------------

# Stock Nextcloud skeleton junk left on the FIRST admin user (created before the
# post-install hook empties skeletondirectory). Presence users created later via
# OCS inherit the empty skeleton and stay clean; steward/admin does not. Remove
# these on every ensure_nc_shape pass so Files matches the presence experience.
DEFAULT_NC_JUNK = [
    "Documents/Example.md",
    "Templates",
    "Nextcloud intro.mp4",
    "Nextcloud Manual.pdf",
    "Nextcloud.png",
    "Readme.md",
    "Reasons to use Nextcloud.pdf",
    "Templates credits.md",
    "Photos",
]


STEWARD_SHARED_FOLDERS = [
    "AgentSkills/Content",
    # batch8 #7b: Actions/ no longer seeded (DB-backed board superseded it), so it's
    # not a shared folder either.
    "AgentSkills/Sites",
    # #CF-113: AgentSkills/Shared removed — operator handoffs use steward-owned
    # root CoveShared (see STEWARD_COVE_SHARED_FOLDER), not per-presence stubs.
]


def _generate_app_password(length=32):
    """Generate a secure random password for Nextcloud app accounts."""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def _parse_ocs_status(xml_text: str) -> int:
    """Extract the OCS statuscode from XML response.

    OCS v1 API always returns HTTP 200. The real status is in:
      <ocs><meta><statuscode>100</statuscode></meta></ocs>

    100 = success, 102 = user exists, 103 = invalid input, etc.
    Returns -1 if parsing fails.
    """
    match = re.search(r'<statuscode>(\d+)</statuscode>', xml_text)
    return int(match.group(1)) if match else -1


# =============================================================================
# Steward folder sharing — team access boundary
# =============================================================================

async def _share_folders_with_steward(
    nc_username: str,
    nc_password: str,
    steward_nc_user: str,
    presence_display_name: str = "",
) -> int:
    """Share team-managed folders from a Presence to the steward.

    Creates NC shares for each folder in STEWARD_SHARED_FOLDERS,
    then moves them into a per-Presence namespace in the steward's space:
      Presences/{presence_name}/Content/
      Presences/{presence_name}/Actions/
      etc.

    This prevents naming collisions when multiple Presences share
    folders with the same steward.

    Args:
        nc_username: The Presence's NC username (share owner)
        nc_password: The Presence's NC password
        steward_nc_user: The steward's NC username (share recipient)
        presence_display_name: Display name for the namespace folder

    Returns: number of folders successfully shared and namespaced.
    """
    if not presence_display_name:
        # Fall back to NC username without the op- prefix
        presence_display_name = nc_username.replace("op-", "")

    share_url = f"{NC_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    steward_dav = f"{NC_URL}/remote.php/dav/files/{steward_nc_user}"
    shared_count = 0

    # Step 1: Create shares (Presence authenticates as themselves)
    async with httpx.AsyncClient(timeout=30) as client:
        for folder in STEWARD_SHARED_FOLDERS:
            try:
                resp = await client.post(
                    share_url,
                    auth=(nc_username, nc_password),
                    headers={"OCS-APIRequest": "true"},
                    data={
                        "path": f"/{folder}",
                        "shareType": 0,  # 0 = user share
                        "shareWith": steward_nc_user,
                        "permissions": 31,  # read + write + create + delete + share
                    },
                )
                if resp.status_code == 200:
                    ocs_status = _parse_ocs_status(resp.text)
                    if ocs_status in (200, 100, 403):  # 403 = already shared
                        shared_count += 1
                        log.info("Shared %s with steward %s", folder, steward_nc_user)
                    else:
                        log.warning("Share %s failed (OCS %s): %s",
                                    folder, ocs_status, resp.text[:200])
                else:
                    log.warning("Share %s HTTP %s: %s",
                                folder, resp.status_code, resp.text[:200])
            except Exception as e:
                log.warning("Failed to share %s with steward: %s", folder, e)

    # Step 2: Create namespace in steward's space and move shares into it
    # Steward authenticates as themselves for WebDAV operations on their own space
    from urllib.parse import quote
    steward_pass = NC_ADMIN_PASSWORD  # Steward's NC user = admin

    async with httpx.AsyncClient(timeout=30) as dav_client:
        # Create Presences/ and Presences/{name}/ directories
        for ns_folder in ["Presences", f"Presences/{presence_display_name}"]:
            try:
                await dav_client.request(
                    "MKCOL",
                    f"{steward_dav}/{quote(ns_folder, safe='/')}",
                    auth=(steward_nc_user, steward_pass),
                )
            except Exception:
                pass  # Already exists is fine

        # Move each shared folder from root into the namespace
        for folder in STEWARD_SHARED_FOLDERS:
            folder_name = folder.split("/")[-1]  # "AgentSkills/Content" → "Content"
            src = f"{steward_dav}/{quote(folder_name, safe='/')}"
            dst = f"{steward_dav}/{quote(f'Presences/{presence_display_name}/{folder_name}', safe='/')}"
            try:
                move_resp = await dav_client.request(
                    "MOVE",
                    src,
                    auth=(steward_nc_user, steward_pass),
                    headers={"Destination": dst, "Overwrite": "T"},
                )
                if move_resp.status_code in (201, 204):
                    log.info("Namespaced share: %s → Presences/%s/%s",
                             folder_name, presence_display_name, folder_name)
                elif move_resp.status_code == 404:
                    # Share might already be namespaced from a previous run
                    log.info("Share %s not at root (may already be namespaced)", folder_name)
                else:
                    log.warning("Move %s failed (%s): %s",
                                folder_name, move_resp.status_code, move_resp.text[:200])
            except Exception as e:
                log.warning("Failed to namespace %s: %s", folder_name, e)

    return shared_count


# The canonical Knowledge Base lives ONCE in the steward's space and is shared
# READ-ONLY to every presence. All presences read the same source, only the
# steward curates it, so the KB never drifts across the Cove.
STEWARD_KB_FOLDER = "AgentSkills/Knowledge Base"


async def _ensure_steward_kb(steward_nc_user: str, steward_pass: str) -> None:
    """Make sure the steward's canonical Knowledge Base folder exists."""
    from urllib.parse import quote
    steward_dav = f"{NC_URL}/remote.php/dav/files/{steward_nc_user}"
    async with httpx.AsyncClient(timeout=30) as client:
        for folder in ["AgentSkills", STEWARD_KB_FOLDER]:
            try:
                await client.request(
                    "MKCOL",
                    f"{steward_dav}/{quote(folder, safe='/')}",
                    auth=(steward_nc_user, steward_pass),
                )
            except Exception:
                pass


async def _share_kb_with_presence(
    presence_nc_username: str,
    presence_password: str,
    steward_nc_user: str,
    steward_pass: str,
) -> bool:
    """Share the steward's canonical Knowledge Base to a presence, READ-ONLY.

    The KB exists once, in the steward's space. Every presence gets a read-only
    share of it, namespaced into their own AgentSkills/Knowledge Base, so they all
    read the same source with no drift. Only the steward can write to it.
    """
    from urllib.parse import quote
    await _ensure_steward_kb(steward_nc_user, steward_pass)

    share_url = f"{NC_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares"

    # Step 1: steward creates a READ-ONLY share with the presence
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                share_url,
                auth=(steward_nc_user, steward_pass),
                headers={"OCS-APIRequest": "true"},
                data={
                    "path": f"/{STEWARD_KB_FOLDER}",
                    "shareType": 0,            # 0 = user share
                    "shareWith": presence_nc_username,
                    "permissions": 1,          # READ ONLY — the anti-drift guarantee
                },
            )
            ocs = _parse_ocs_status(resp.text) if resp.status_code == 200 else -1
            if ocs not in (100, 200, 403):     # 403 = already shared
                log.warning("KB share to %s failed (OCS %s): %s",
                            presence_nc_username, ocs, resp.text[:200])
                return False
        except Exception as e:
            log.warning("KB share to %s error: %s", presence_nc_username, e)
            return False

    # Step 2: presence moves the incoming share into AgentSkills/Knowledge Base
    presence_dav = f"{NC_URL}/remote.php/dav/files/{presence_nc_username}"
    src = f"{presence_dav}/{quote('Knowledge Base', safe='/')}"
    dst = f"{presence_dav}/{quote('AgentSkills/Knowledge Base', safe='/')}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            mv = await client.request(
                "MOVE", src,
                auth=(presence_nc_username, presence_password),
                headers={"Destination": dst, "Overwrite": "F"},
            )
            if mv.status_code in (201, 204):
                log.info("KB shared read-only into %s/AgentSkills/Knowledge Base",
                         presence_nc_username)
            else:
                # 403/404/412 = already namespaced from a prior run, or mount not
                # yet at root. Non-fatal — the share itself succeeded.
                log.info("KB namespace move for %s: HTTP %s (likely already done)",
                         presence_nc_username, mv.status_code)
        except Exception as e:
            log.warning("KB namespace move for %s failed: %s", presence_nc_username, e)
    return True


# #CF-113 — CoveShared: one steward-owned folder per Cove, read-write for human
# operators only (not guests, not a general agent dump). Lives at the NC root so
# it shows up next to AgentSkills in Files / desktop sync — featured, not buried.
# Modelled on the KB share (single source on the steward) with edit permissions.
STEWARD_COVE_SHARED_FOLDER = "CoveShared"
# NC OCS permissions: 1 read + 2 update + 4 create + 8 delete = 15 (no re-share).
_COVE_SHARED_RW_PERMS = 15


async def _ensure_steward_cove_shared(steward_nc_user: str, steward_pass: str) -> None:
    """Make sure the steward's canonical CoveShared folder exists at NC root."""
    from urllib.parse import quote
    steward_dav = f"{NC_URL}/remote.php/dav/files/{steward_nc_user}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await client.request(
                "MKCOL",
                f"{steward_dav}/{quote(STEWARD_COVE_SHARED_FOLDER, safe='/')}",
                auth=(steward_nc_user, steward_pass),
            )
        except Exception:
            pass


async def _share_cove_shared_with_operator(
    presence_nc_username: str,
    presence_password: str,
    steward_nc_user: str,
    steward_pass: str,
) -> bool:
    """Share steward CoveShared to an operator presence, read-write, at NC root.

    Operators see /CoveShared in their own Files (and can selective-sync it).
    Guests are not callers — ensure_nc_shape skips them. Team agents use the
    admin NC space with path scopes and do not receive this share.
    """
    from urllib.parse import quote
    if not presence_nc_username or presence_nc_username == steward_nc_user:
        return True

    await _ensure_steward_cove_shared(steward_nc_user, steward_pass)

    share_url = f"{NC_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                share_url,
                auth=(steward_nc_user, steward_pass),
                headers={"OCS-APIRequest": "true"},
                data={
                    "path": f"/{STEWARD_COVE_SHARED_FOLDER}",
                    "shareType": 0,  # user share
                    "shareWith": presence_nc_username,
                    "permissions": _COVE_SHARED_RW_PERMS,
                },
            )
            ocs = _parse_ocs_status(resp.text) if resp.status_code == 200 else -1
            if ocs not in (100, 200, 403):  # 403 = already shared
                log.warning(
                    "CoveShared share to %s failed (OCS %s): %s",
                    presence_nc_username, ocs, resp.text[:200],
                )
                return False
            log.info(
                "CoveShared shared RW into %s/%s",
                presence_nc_username, STEWARD_COVE_SHARED_FOLDER,
            )
        except Exception as e:
            log.warning("CoveShared share to %s error: %s", presence_nc_username, e)
            return False
    return True


def _is_operator_presence(role: str, cove_role: str = "") -> bool:
    """Human operators get CoveShared; guests do not.

    NC users are only provisioned for people (team agents use admin + path scopes).
    cove_role guest = view-only family member — no operator handoff folder.
    """
    cr = (cove_role or "").strip().lower()
    if cr == "guest":
        return False
    r = (role or "").strip().lower()
    # steward / member / empty role (legacy) count as operator when not guest
    return r in ("steward", "member", "admin", "")


async def share_presence_folders_with_steward(
    presence_id: str,
    steward_nc_user: str = "",
) -> dict:
    """Public API: share a Presence's team-managed folders with the steward.

    Looks up the Presence's NC credentials from the DB, then creates shares.
    Used by provisioner (automatic) and as a manual fix-up endpoint.
    """
    if not steward_nc_user:
        steward_nc_user = NC_ADMIN_USER

    # Look up Presence NC creds from DB
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
                (presence_id,),
            )
            row = await result.fetchone()
            if not row or not row["nc_username"]:
                return {"ok": False, "error": f"No NC credentials for presence {presence_id}"}

            shared = await _share_folders_with_steward(
                row["nc_username"], row["nc_password"], steward_nc_user
            )
            return {"ok": True, "shared_count": shared, "steward": steward_nc_user}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Credential resolution — used by files.py, home.py, projects.py
# =============================================================================

import time as _nc_time
_NC_CREDS_CACHE: dict = {}
_NC_CREDS_TTL = 60.0  # short TTL: cut per-poll DB lookups + reconcile spam for Files/Calendar


async def get_nc_creds(request: Request = None):
    """Get Nextcloud credentials for the current user.

    Multi mode: looks up per-user nc_username/nc_password from accounts table.
    Single mode: uses NEXTCLOUD_USER/NEXTCLOUD_PASSWORD env vars.

    Returns: (nc_url, nc_user, nc_pass) tuple.
    """
    nc_url = env("NEXTCLOUD_URL")

    if COVE_MODE == "multi" and request:
        try:
            from src.dashboard.routes.presence import get_current_presence
            presence = await get_current_presence(request)
            if presence and presence.get("id"):
                _pid = str(presence["id"])
                _cc = _NC_CREDS_CACHE.get(_pid)
                if _cc and _cc[3] > _nc_time.monotonic():
                    # perf: serve fresh cached creds and skip the accounts query, the
                    # on-every-poll reconcile spawn, and any inline provisioning.
                    return _cc[0], _cc[1], _cc[2]
                from src.memory.database import get_db
                async with get_db() as conn:
                    result = await conn.execute(
                        "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
                        (presence["id"],)
                    )
                    row = await result.fetchone()
                    if row and row["nc_username"] and row["nc_password"]:
                        # C3-7: if the last provision recorded incomplete shape
                        # (folders/shares failed during NC warm-up), re-run the
                        # idempotent pass in the background — the creds guard
                        # used to block any reconcile forever.
                        try:
                            from src.utils.settings import get_setting
                            if (await get_setting(f"nc_shape_pending_{presence['id']}",
                                                  default="")) == "1":
                                import asyncio as _aio
                                _cr = (presence.get("cove_role") or "").strip().lower()
                                _aio.create_task(reconcile_nc_shape(
                                    str(presence["id"]),
                                    row["nc_username"], row["nc_password"],
                                    presence.get("display_name")
                                    or presence.get("username") or "Operator",
                                    presence.get("tier") or "cove",
                                    "steward" if _cr in ("admin", "steward") else "member",
                                    cove_role=_cr))
                        except Exception:
                            pass
                        _NC_CREDS_CACHE[_pid] = (nc_url, row["nc_username"], row["nc_password"], _nc_time.monotonic() + _NC_CREDS_TTL)
                        return nc_url, row["nc_username"], row["nc_password"]
                # No credentials yet — provision the NC user ON DEMAND and self-heal.
                # On a fresh box Nextcloud often isn't ready when finalize runs, so the
                # operator can end up with no NC user; the first time they touch Files /
                # Calendar / jules we create it (NC is up by then) and store the creds.
                # Idempotent: a second call just re-reads the stored creds.
                # C3-6: derive the role from cove_role — this used to hardcode
                # "member", so the exact case this heal exists for (finalize failed
                # on a fresh box) healed the founding operator WITHOUT the steward
                # folders/KB share, the wrong shape forever after.
                _cr = (presence.get("cove_role") or "").strip().lower()
                try:
                    res = await provision_nc_user(
                        str(presence["id"]),
                        presence.get("display_name") or presence.get("username") or "Operator",
                        tier=(presence.get("tier") or "cove"),
                        role=("steward" if _cr in ("admin", "steward") else "member"),
                        handle=(presence.get("username") or ""),
                        cove_role=_cr)
                    if res.get("ok"):
                        async with get_db() as conn:
                            r2 = await conn.execute(
                                "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
                                (presence["id"],))
                            row2 = await r2.fetchone()
                            if row2 and row2["nc_username"] and row2["nc_password"]:
                                _NC_CREDS_CACHE[_pid] = (nc_url, row2["nc_username"], row2["nc_password"], _nc_time.monotonic() + _NC_CREDS_TTL)
                                return nc_url, row2["nc_username"], row2["nc_password"]
                    else:
                        log.warning("On-demand NC provisioning not ready: %s", res.get("error"))
                except Exception as e:
                    log.warning("On-demand NC provisioning failed (NC not ready yet?): %s", e)
            # Still no credentials (NC not ready) — caller degrades gracefully.
            return nc_url, "", ""
        except Exception as e:
            log.warning("Failed to look up NC credentials: %s", e)
            return nc_url, "", ""

    # Single mode — env vars
    nc_user = env("NEXTCLOUD_USER")
    nc_pass = env("NEXTCLOUD_PASSWORD")
    return nc_url, nc_user, nc_pass


async def resolve_tab_nc_creds(request=None):
    """(url, user, password) for a HUMAN MC tab (Files/Calendar).

    Unlike get_nc_creds -- which keys purely off the login cookie -- this honors the
    DOOR (subdomain). A manager door (stuart./mercer.) viewed by an admin session
    resolves to the shared cove ADMIN NC space, so Stuart's/Mercer's MC Files+Calendar
    show the admin space, matching the agent tool path (set_team_nc_creds). Operator
    doors fall through to the cookie owner's own creds (an operator only passes
    host_match on their own door, so cookie owner == viewed presence there)."""
    try:
        from src.dashboard.host_context import (resolve_host_context, request_host,
                                                host_match)
        from src.config import load_cove_config
        _ctx = resolve_host_context(request_host(request), load_cove_config())
        if _ctx.get("kind") == "manager" and NC_ADMIN_USER and NC_ADMIN_PASSWORD:
            from src.dashboard.routes.presence import get_current_presence
            if host_match(_ctx, await get_current_presence(request)):
                # Once per process: strip stock NC skeleton from admin (Stuart Files).
                # Admin is image-created before skeletondirectory is emptied.
                try:
                    if not getattr(ensure_admin_nc_clean, "_done", False):
                        import asyncio as _aio
                        async def _bg_admin_clean():
                            try:
                                await ensure_admin_nc_clean()
                            finally:
                                ensure_admin_nc_clean._done = True
                        _aio.create_task(_bg_admin_clean())
                        ensure_admin_nc_clean._done = True  # prevent task spam; reset if clean fails inside
                except Exception:
                    pass
                return env("NEXTCLOUD_URL"), NC_ADMIN_USER, NC_ADMIN_PASSWORD
    except Exception:
        pass
    return await get_nc_creds(request)


async def ensure_nc_bruteforce_bypass() -> bool:
    """Whitelist THIS app container's own subnet in NC brute-force protection.

    The app is a trusted first-party service that authenticates to NC constantly. If a
    transient auth failure ever accumulates (an NC restart race, a creds mismatch), NC's
    brute-force protection throttles the app's IP with up to ~25s delay PER authenticated
    request -- which silently makes Files/Calendar and every agent NC tool crawl. Adding
    the app's /16 to NC's allowlist means the internal service is never throttled; real
    login protection (external IPs) is unaffected. Idempotent: writes a fixed slot.
    NC 29 stores this as bruteForce/whitelist_0 (+ _v4 mask). Returns True on success."""
    if not NC_ADMIN_PASSWORD:
        return False
    try:
        import socket
        ip = socket.gethostbyname(socket.gethostname())
        octets = ip.split(".")
        if len(octets) != 4 or not all(o.isdigit() for o in octets):
            return False
        subnet = f"{octets[0]}.{octets[1]}.0.0"  # /16 base of the container's subnet
        base = f"{NC_URL}/ocs/v2.php/apps/provisioning_api/api/v1/config/apps/bruteForce"
        async with httpx.AsyncClient(timeout=15) as client:
            for key, val in (("whitelist_0", subnet), ("whitelist_0_v4", "16")):
                r = await client.post(
                    f"{base}/{key}",
                    data={"value": val},
                    headers={"OCS-APIRequest": "true"},
                    auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD),
                )
                if r.status_code >= 400:
                    return False
        log.info("NC brute-force bypass ensured for %s/16", subnet)
        return True
    except Exception as e:
        log.warning("NC brute-force bypass ensure failed (non-fatal): %s", e)
        return False


# =============================================================================
# OCS Provisioning — create/manage Nextcloud users
# =============================================================================

def _nc_username_from_handle(handle: str) -> str:
    """Nextcloud username derived from the operator's permanent @handle, so handle =
    Matrix = NC all stay in sync (the handle never changes). NC userids allow
    [a-z0-9._-]; operator handles are flat, agent handles are first.cove (dots are
    fine). Lowercased + sanitized."""
    import re
    h = (handle or "").lstrip("@").strip().lower()
    return re.sub(r"[^a-z0-9._-]", "", h)


async def _remove_nc_default_files(
    dav_client, webdav_base: str, nc_username: str, nc_password: str,
) -> int:
    """Delete stock Nextcloud skeleton files/folders if present.

    Idempotent: 404 is success. Returns count of unexpected failures (not 2xx/404).
    Presence users usually have nothing to delete; admin/steward often does."""
    from urllib.parse import quote
    failures = 0
    for rel in DEFAULT_NC_JUNK:
        url = f"{webdav_base}/{quote(rel, safe='/')}"
        try:
            resp = await dav_client.request(
                "DELETE", url, auth=(nc_username, nc_password),
            )
            # 204/200 deleted, 404 already gone — both fine
            if resp.status_code >= 400 and resp.status_code != 404:
                log.warning(
                    "NC default cleanup DELETE %s for %s -> %s",
                    rel, nc_username, resp.status_code,
                )
                failures += 1
            else:
                log.info(
                    "NC default cleanup DELETE %s for %s -> %s",
                    rel, nc_username, resp.status_code,
                )
        except Exception as e:
            log.warning("NC default cleanup failed %s for %s: %s", rel, nc_username, e)
            failures += 1
    return failures


async def ensure_admin_nc_clean() -> dict:
    """Strip stock skeleton junk from the Cove admin NC user (Stuart Files).

    Manager doors and team tools auth as NC_ADMIN_USER, which is the image-created
    admin account — NOT the op-* steward user provision_nc_user creates. ensure_nc_shape
    only cleans the op-* account, so admin Files still showed Readme / intro.mp4 /
    Photos after #152. Call this on first Files access and after steward provision.
    Idempotent.
    """
    if not NC_URL or not NC_ADMIN_USER or not NC_ADMIN_PASSWORD:
        return {"ok": False, "error": "Nextcloud admin not configured"}
    webdav_base = f"{NC_URL}/remote.php/dav/files/{NC_ADMIN_USER}"
    try:
        async with httpx.AsyncClient(timeout=30) as dav_client:
            failures = await _remove_nc_default_files(
                dav_client, webdav_base, NC_ADMIN_USER, NC_ADMIN_PASSWORD)
        # #CF-113: canonical operator handoff folder lives on admin/steward space
        try:
            await _ensure_steward_cove_shared(NC_ADMIN_USER, NC_ADMIN_PASSWORD)
        except Exception as e:
            log.warning("CoveShared ensure during admin clean: %s", e)
            failures += 1
        return {"ok": failures == 0, "failures": failures, "user": NC_ADMIN_USER}
    except Exception as e:
        log.warning("ensure_admin_nc_clean failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}


async def ensure_nc_shape(nc_username: str, nc_password: str, display_name: str,
                          tier: str, role: str, cove_role: str = "") -> int:
    """Ensure this NC user's folders, Context seeds, steward shares, KB, CoveShared.

    Every operation tolerates "already exists" (MKCOL 405, share OCS 403, MOVE 404),
    so this pass is safe to re-run any time — that's the point (audit C3-7): a
    first-boot Apache warm-up 503 used to leave a presence permanently without
    folders/shares because nothing ever re-ran these idempotent ops.

    cove_role: accounts.cove_role (admin|member|guest). Guests skip CoveShared.
    #CF-113: steward-owned root CoveShared is RW-shared only to operator humans.

    Returns the number of FAILED operations — 0 means the shape is complete."""
    failures = 0
    from urllib.parse import quote

    folders = list(BASE_FOLDERS)
    if tier in ("presence", "cove") or role == "steward":
        folders += STEWARD_FOLDERS if role == "steward" else PRESENCE_FOLDERS

    webdav_base = f"{NC_URL}/remote.php/dav/files/{nc_username}"
    async with httpx.AsyncClient(timeout=30) as dav_client:
        # Admin is created by the NC image before skeletondirectory is emptied, so
        # steward space still gets Readme.md / intro.mp4 / Photos / etc. Presence
        # users created later do not. Clear junk for EVERY user on shape pass.
        try:
            junk_fail = await _remove_nc_default_files(
                dav_client, webdav_base, nc_username, nc_password)
            failures += junk_fail
        except Exception as e:
            log.warning("NC default cleanup pass failed for %s: %s", nc_username, e)
            failures += 1

        for folder in folders:
            try:
                mkr = await dav_client.request(
                    "MKCOL",
                    f"{webdav_base}/{quote(folder, safe='/')}",
                    auth=(nc_username, nc_password),
                )
                log.info("MKCOL %s for %s: %s", folder, nc_username, mkr.status_code)
                if mkr.status_code >= 400 and mkr.status_code != 405:
                    failures += 1
            except Exception as e:
                log.warning("Failed to create folder %s for %s: %s", folder, nc_username, e)
                failures += 1

        # Context seed files for Presence/Steward/Cove tiers
        if tier in ("presence", "cove") or role == "steward":
            try:
                await _seed_context_files(dav_client, webdav_base, nc_username,
                                          nc_password, display_name)
            except Exception as e:
                log.warning("Context seeding failed for %s: %s", nc_username, e)
                failures += 1

    # Share team-managed folders with the steward (non-steward presences only)
    if role != "steward" and tier in ("presence", "cove"):
        steward_nc_user = NC_ADMIN_USER  # Steward's NC user = the admin
        try:
            shared = await _share_folders_with_steward(
                nc_username, nc_password, steward_nc_user,
                presence_display_name=display_name,
            )
            log.info("Shared %d team folders with steward %s for %s",
                     shared, steward_nc_user, nc_username)
            missing = len(STEWARD_SHARED_FOLDERS) - int(shared or 0)
            if missing > 0:
                failures += missing
        except Exception as e:
            log.warning("Steward share pass failed for %s: %s", nc_username, e)
            failures += len(STEWARD_SHARED_FOLDERS)

        # Share the steward's canonical Knowledge Base DOWN to this presence,
        # read-only — one KB for the whole Cove, no drift. (Result was
        # discarded before — a failed KB share is a real failure.)
        try:
            if not await _share_kb_with_presence(
                    nc_username, nc_password, NC_ADMIN_USER, NC_ADMIN_PASSWORD):
                failures += 1
        except Exception as e:
            log.warning("KB share failed for %s: %s", nc_username, e)
            failures += 1

    # #CF-113: steward-owned CoveShared at NC root — RW for operators only.
    # Ensure the canonical folder always exists on the admin/steward space; share
    # it into this presence when they are an operator (not guest). Steward role
    # still gets the share on their op-* user so desktop sync sees it too.
    try:
        await _ensure_steward_cove_shared(NC_ADMIN_USER, NC_ADMIN_PASSWORD)
    except Exception as e:
        log.warning("CoveShared ensure on steward failed: %s", e)
        failures += 1
    if _is_operator_presence(role, cove_role) and nc_username != NC_ADMIN_USER:
        try:
            if not await _share_cove_shared_with_operator(
                    nc_username, nc_password, NC_ADMIN_USER, NC_ADMIN_PASSWORD):
                failures += 1
        except Exception as e:
            log.warning("CoveShared share failed for %s: %s", nc_username, e)
            failures += 1

    return failures


_SHAPE_INFLIGHT: set = set()


async def reconcile_nc_shape(presence_id: str, nc_username: str, nc_password: str,
                             display_name: str, tier: str, role: str,
                             cove_role: str = "") -> None:
    """Background re-run of the idempotent shape pass for a presence whose last
    provision recorded incomplete shape (C3-7). Clears the pending flag on a
    clean pass; never raises."""
    pid = str(presence_id)
    if pid in _SHAPE_INFLIGHT:
        return
    _SHAPE_INFLIGHT.add(pid)
    try:
        failures = await ensure_nc_shape(
            nc_username, nc_password, display_name, tier, role, cove_role=cove_role)
        from src.utils.settings import update_setting
        await update_setting(f"nc_shape_pending_{pid}", "1" if failures else "")
        log.info("NC shape reconcile for %s: %s",
                 nc_username, "complete" if not failures else f"{failures} failures (will retry)")
    except Exception as e:
        log.warning("NC shape reconcile errored for %s: %s", nc_username, e)
    finally:
        _SHAPE_INFLIGHT.discard(pid)


async def provision_nc_user(
    presence_id: str,
    display_name: str,
    quota: str = None,
    tier: str = "operator",
    role: str = "member",
    handle: str = "",
    cove_role: str = "",
) -> dict:
    """Create a Nextcloud user account and seed folder structure by tier/role.

    Args:
        presence_id: UUID from accounts table (used as NC username prefix)
        display_name: User's display name
        quota: Storage quota (e.g., "500 MB", "1 GB")
        tier: Account tier — determines which folders to create
        role: Cove role — "steward" gets Knowledge Base, others don't

    Folder logic:
        Operator:  BASE_FOLDERS only
        Presence:  BASE_FOLDERS + PRESENCE_FOLDERS + Context seed files
        Cove:      BASE_FOLDERS + PRESENCE_FOLDERS + Context seed files
        Steward:   BASE_FOLDERS + STEWARD_FOLDERS + Context seed files

    Returns: dict with nc_username, success status, and any error.
    """
    if not NC_URL or not NC_ADMIN_PASSWORD:
        return {"ok": False, "error": "Nextcloud not configured (missing URL or admin password)"}

    # Resolve quota: an explicit per-presence override wins; otherwise inherit the
    # cove-wide default from cove.yaml (open-source default "none" = unlimited).
    if quota is None:
        try:
            from src.config import get_default_quota
            quota = get_default_quota()
        except Exception:
            quota = DEFAULT_QUOTA

    # NC username = the operator's permanent @handle (synced with Matrix + the registry
    # handle). Falls back to op-{uuid} only when no handle is available (legacy callers).
    nc_username = _nc_username_from_handle(handle) or f"op-{presence_id[:8]}"
    nc_password = _generate_app_password()

    ocs_url = f"{NC_URL}/ocs/v1.php/cloud/users"

    try:
        async with httpx.AsyncClient(
            auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD),
            timeout=30,
            headers={"OCS-APIRequest": "true"}
        ) as client:

            # 1. Create user
            resp = await client.post(
                ocs_url,
                data={
                    "userid": nc_username,
                    "password": nc_password,
                    "displayName": display_name,
                    "quota": quota,
                },
            )

            ocs_status = _parse_ocs_status(resp.text)

            if ocs_status == 102:
                # User already exists — reset password
                log.info("NC user %s already exists, resetting password", nc_username)
                reset_resp = await client.put(
                    f"{ocs_url}/{nc_username}",
                    data={"key": "password", "value": nc_password},
                )
                reset_status = _parse_ocs_status(reset_resp.text)
                if reset_status != 100:
                    return {"ok": False, "error": f"User exists but password reset failed (OCS {reset_status}): {reset_resp.text[:200]}"}
            elif ocs_status != 100:
                return {"ok": False, "error": f"OCS create failed (status {ocs_status}): {resp.text[:200]}"}

            # 2. Set quota (in case it wasn't set on creation)
            await client.put(
                f"{ocs_url}/{nc_username}",
                data={"key": "quota", "value": quota},
            )

        # 3-6. Folders, Context seeds, steward shares, KB share — the "shape" pass.
        # C3-7: every operation here tolerates "already exists", but failures used
        # to be log-and-continue inside a provision that still returned ok:True —
        # a first-boot NC 503 left the presence permanently without folders/shares.
        # Now the pass counts failures and records incomplete shape for reconcile.
        import asyncio
        await asyncio.sleep(2)
        shape_failures = await ensure_nc_shape(
            nc_username, nc_password, display_name, tier, role,
            cove_role=cove_role)

        # Steward provision: also clean the image admin user (what Stuart Files uses).
        if role == "steward":
            try:
                await ensure_admin_nc_clean()
            except Exception as e:
                log.warning("admin NC clean after steward provision: %s", e)

        log.info("Provisioned NC user: %s (tier: %s, role: %s, quota: %s, shape failures: %d)",
                 nc_username, tier, role, quota, shape_failures)

    except Exception as e:
        return {"ok": False, "error": f"OCS API error: {e}"}

    # 7. Store credentials in accounts table
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                "UPDATE accounts SET nc_username = %s, nc_password = %s WHERE id = %s",
                (nc_username, nc_password, presence_id),
            )
        log.info("Stored NC credentials for presence %s -> %s", presence_id, nc_username)
    except Exception as e:
        return {"ok": False, "error": f"DB update failed: {e}", "nc_username": nc_username}

    # 7b. Record incomplete shape so the lazy get_nc_creds path re-runs the
    # idempotent pass later (C3-7) instead of the guards blocking it forever.
    try:
        from src.utils.settings import update_setting
        await update_setting(f"nc_shape_pending_{presence_id}",
                             "1" if shape_failures else "")
    except Exception:
        pass

    return {"ok": True, "nc_username": nc_username, "quota": quota,
            "shape_failures": shape_failures}


# =============================================================================
# Context file seeding — template files for new Presence/Steward accounts
# =============================================================================

# Template content for each Context file. These are the blank-slate versions
# that get populated as the operator and their agents work together.
# Structure matches AgentSkills/Context/ as built in the reference Cove.

_CONTEXT_TEMPLATES = {
    "AgentSkills/Context/memory.md": """\
# Memory Index

> **Agent: Read this file first every session.** It's your table of contents.
> Load topic files only when relevant to the current task.
> Keep this index under 200 lines. One line per entry, under 150 characters each.

## Identity & Preferences

- [About](about.md) -- Who you are, what you're building, how you work
- [Preferences](preferences.md) -- Communication style, patterns, conventions

## Work Context

- [Projects](projects.md) -- Active projects in priority order
- [Decisions](decisions.md) -- Settled questions. Don't re-litigate.
- [Working Memory](working-memory.md) -- Current focus, active work, open threads

## Learned Patterns

- [Feedback](feedback.md) -- What worked, what didn't, corrections to apply
- [Relationships](relationships.md) -- People you interact with

## System State

- [System](system-state.md) -- Infrastructure, services, deploy status

## Operations

- [Runbooks](../Ops/runbooks.md) -- Cove management commands (health, restart, cache, tuning)
- [Reference](../Ops/reference.md) -- This Cove's URLs, services, and key config

## Session History

- [Session Log](sessions/current.md) -- Most recent session summary
- Session archive: sessions/archive.md with topic index at sessions/index.md
""",

    "AgentSkills/Context/about.md": """\
---
name: about
description: Who the operator is, what they're building, how they work.
metadata:
  type: user
---

# About

(Fill this in with who you are, what you're building, and how you work.
Your agents read this to understand your perspective and goals.)
""",

    "AgentSkills/Context/preferences.md": """\
---
name: preferences
description: Communication style, writing rules, code patterns, naming conventions.
metadata:
  type: user
---

# Preferences

(How you like to work. Your agents follow these patterns.)

## Communication

(How should agents talk to you? Direct? Detailed? Brief?)

## Writing

(Any writing rules or style preferences?)

## Code and Systems

(How do you like code delivered? One step at a time? All at once?)
""",

    "AgentSkills/Context/projects.md": """\
---
name: projects
description: Active projects in priority order with current status.
metadata:
  type: project
---

# Active Projects

## Priority Order

(List your active projects here. Your agents use this to understand what matters most.)
""",

    "AgentSkills/Context/decisions.md": """\
---
name: decisions
description: Settled questions. Prevents re-litigating what's been decided.
metadata:
  type: project
---

# Key Decisions

Settled questions. Don't re-litigate these.

(Add decisions as they're made. Include brief reasoning so future sessions understand why.)
""",

    "AgentSkills/Context/working-memory.md": """\
---
name: working-memory
description: Current focus, active work, and open threads. Updated every session.
metadata:
  type: project
---

# Working Memory

> Last updated: (date)
> This file changes every session. It's the "what's happening right now" snapshot.

## Current Focus

(What are you actively working on?)

## Active Work

(Specific tasks in progress)

## Open Threads

(Things that need attention but aren't actively being worked)
""",

    "AgentSkills/Context/feedback.md": """\
---
name: feedback
description: Corrections and confirmations. What to repeat, what to avoid.
metadata:
  type: feedback
---

# Feedback

> Learned patterns from working together. Read these before starting work.
> Structure: Rule, then **Why**, then **How to apply**.

(Add entries as you work with your agents. Both corrections and confirmations belong here.)
""",

    "AgentSkills/Context/relationships.md": """\
---
name: relationships
description: People the operator interacts with. How agents should engage them.
metadata:
  type: reference
---

# Relationships

> People in your life that agents may interact with or need context about.

## Household

(Family members, housemates)

## Team

(Agents, collaborators)
""",

    "AgentSkills/Context/system-state.md": """\
---
name: system-state
description: Infrastructure status, service health, deploy state.
metadata:
  type: reference
---

# System State

> Last updated: (date)
> Quick reference for what's running.

(Your agents update this after deploys and infrastructure changes.)
""",

    "AgentSkills/Context/sessions/current.md": """\
---
name: session-current
description: Most recent session summary. Older sessions move to archive.md.
metadata:
  type: project
---

# Current Session

(Your agent logs session summaries here after meaningful work.)
""",

    "AgentSkills/Context/sessions/archive.md": """\
# Session Archive

> Older session summaries move here from current.md. Keep entries concise.
> Search by topic using sessions/index.md.

(No archived sessions yet.)
""",

    "AgentSkills/Context/sessions/index.md": """\
# Session Index

> Topic tags for finding past sessions quickly.

| Date | Topics | Summary |
|------|--------|---------|

(New sessions added at top.)
""",

    "AgentSkills/Ops/runbooks.md": """\
# Cove Runbooks

> Operator-level commands for managing your Cove day-to-day.
> Deploys and cross-Cove management are Haven-level (Haven MC).

## 1. System Health Check

```bash
curl -s https://{COVE_DOMAIN}/api/system/health | python3 -m json.tool
```

Shows: agent status, scheduler state, DB connectivity, Ollama, Nextcloud.

## 2. Today's Tuning

```bash
curl -s https://{COVE_DOMAIN}/api/tuning/today | python3 -m json.tool
```

## 3. Force Tuning Sweep

```bash
curl -s -X POST https://{COVE_DOMAIN}/api/system/tuning-sweep | python3 -m json.tool
```

## 4. Clear Cache

```bash
curl -s -X POST https://{COVE_DOMAIN}/api/system/cache-bust | python3 -m json.tool
```

## 5. Agent Schedule

```bash
curl -s https://{COVE_DOMAIN}/api/system/scheduler | python3 -m json.tool
```

## 6. Restart Services

SSH into your Cove host, then:

```bash
cd {COVE_PATH}
docker compose down && docker compose up -d
```

Wait 30 seconds, then run Health Check (Runbook 1).

## 7. Check Agent Logs

```bash
docker logs {CONTAINER_NAME} --tail 100
```

---

**Notes:** Replace `{COVE_DOMAIN}` with your Cove's URL. Haven-level ops (deploy, provision) are in Haven MC.
""",

    "AgentSkills/Ops/reference.md": """\
# Cove Reference

> System reference for this Cove. URLs, services, key config.
> Updated by your agent after infrastructure changes.

## This Cove

| Field | Value |
|-------|-------|
| **Cove Name** | (your Cove name) |
| **Steward** | (your steward agent) |
| **Domain** | (your Cove domain) |

## Services

(Your agent populates this after setup with your Cove's URLs and service list.)

## Key Config Files

| File | Purpose |
|------|---------|
| cove.yaml | Cove identity, tier gates, admin_ids |
| agent.yaml | Agent model chain, channels, schedule |
| .env | Secrets (DB passwords, API keys, NC creds) |
| docker-compose.yml | Container orchestration |

## Key APIs

| Endpoint | What |
|----------|------|
| /api/system/health | Full system health check |
| /api/tuning/today | Today's tuning status |
| /api/system/tuning-sweep | Force tuning sweep |
| /api/system/cache-bust | Clear dashboard cache |
| /api/system/scheduler | View scheduled tasks |
| /api/admin/nextcloud/status | NC connectivity + auth |
""",

    "AgentSkills/Ops/README.md": """\
# Ops

Cove-level operations. Commands and reference for managing your Cove day-to-day.

**runbooks.md** -- Management commands: health checks, tuning sweeps, cache busting, restarts.

**reference.md** -- This Cove's URLs, containers, services, and key config files.

## What's NOT Here

- **Deploys** -- Haven-level. Your Haven MC handles cross-Cove deployments.
- **Provisioning** -- Haven-level. New Presences are created through the Haven admin.
- **Credentials** -- Live in `.env` on the host, never in synced files.
""",

    "AgentSkills/Context/README.md": """\
# Context

Your second brain. This is how you and your agents stay in sync.

## How Agents Use This

1. **Read memory.md first** -- it's the index. Points to everything else.
2. **Load topic files on demand** -- only pull what's relevant to the current task.
3. **Update after meaningful work** -- log decisions, update working memory, append session history.

## Memory Categories

Files follow four categories:

- **user** -- about.md, preferences.md (who you are)
- **project** -- projects.md, decisions.md, working-memory.md (what's happening)
- **feedback** -- feedback.md (learned patterns from working together)
- **reference** -- relationships.md, system-state.md (external context)

Each file has frontmatter with name, description, and type for machine-readability.
""",

    "AgentSkills/Inbox/README.md": """\
# Inbox

Drop files, voice notes, and ideas here. Your agents pick them up and process them.

This folder is the **Jules hot path**. Recordings and screenshots land here
(`jules-…md` + audio, plus any file drops). The steward processes them and
archives to `Archive/`. Keep this path light and always included in desktop
sync if you use the Nextcloud client.

## Voice Notes (Jules)

Voice transcriptions land here as `jules-YYYY-MM-DD_HHMM.md` (and matching
audio). Your agent processes them: header, action items, board/context, then
archive.

## File Drops

Any file dropped here gets picked up by your agent on next interaction.

## Desktop sync (important)

Jules and multi‑GB video must not share one clogged client queue.

- **Always sync** `AgentSkills/Inbox` (and `Inbox/Archive` if you want local copies).
- **Prefer server-side** for bulk video: Mission Control Files + agents work on
  the Cove Nextcloud tree without pulling every original to a laptop.
- If you do desktop-sync video, use the client’s **selective sync** and exclude
  heavy trees under `AgentSkills/Content/video/` (especially `raw/`,
  `processing/`, full downloads, captioned masters). Sync only the small
  folders you actually edit on the laptop (often `shorts/` or nothing).
- A single multi‑GB pull in progress can block Inbox uploads; on conflict the
  client may drop local files that never finished their first upload. That is
  client queue behavior, not the agent deleting your drops.

Standard product path: work through agents and cloud Nextcloud. Desktop sync
of media is optional and should stay selective.
""",

    "AgentSkills/Content/video/README.md": """\
# Video pipeline folders

Server-side tree the video pipeline uses:

| Folder | Role |
|--------|------|
| `inbox/` | Untouched originals dropped for processing |
| `processing/` | Active original while the pipeline runs |
| `raw/` | Finished originals (graduated; kept, not auto-deleted) |
| `transcripts/` | Transcript / moment JSON |
| `shorts/` | Cut outputs |
| `moments/` | Moment markers |
| `to-delete/` | Retired user content (MOVE, never hard-delete) |

## Desktop sync

These folders hold multi‑GB files. **Do not** full-sync this tree next to
`AgentSkills/Inbox` on a laptop client unless you need local copies.

Recommended:

1. Leave bulk video on the server (Files in Mission Control, agents, pipeline UI).
2. If the Nextcloud desktop client is on, use **selective sync** — uncheck
   `raw/`, `processing/`, large masters; only enable folders you must have offline.
3. Keep Jules traffic on `AgentSkills/Inbox`, which stays small and interactive.

Product deletes retire into `to-delete/` (and `AgentSkills/To-Delete` for
non-video Files deletes). You get a daily notify when the holding area grows
past the configured size (default 100 GiB) so you can offload externally.
""",

    "AgentSkills/README.md": """\
# AgentSkills

Your working surface. This folder is the Cove file tree agents and Mission
Control use. Desktop sync is optional — the standard path is server-side
through agents and cloud Nextcloud.

**Inbox/** -- Jules hot path. Voice notes, screenshots, quick drops. Keep this
synced if you use a desktop client; keep it off the multi‑GB video queue
(selective-sync `Content/video` bulk folders).

**Content/** -- Media staging. `Content/video/` is the pipeline tree (often
multi‑GiB). Prefer server-side; selective-sync only what you need locally.

**To-Delete/** -- Recoverable holding area for product “deletes” (never a silent
hard wipe of user content). Offload when notified.

**Sites/** -- Website working files.

**Context/** -- Second brain for you and your agents.

**Ops/** -- Cove ops notes, backlog, runbooks.

**CoveShared/** -- (NC root, not under AgentSkills) Operator-only family handoff
folder. One per Cove, steward-owned, read-write for every operator. Not an
agent dump — humans share docs and assets here.

**Knowledge Base/** -- Steward-curated framework mirror (read-only for members).
""",
}


async def _seed_context_files(
    client: httpx.AsyncClient,
    webdav_base: str,
    nc_username: str,
    nc_password: str,
    display_name: str,
):
    """Upload template Context files into a newly provisioned AgentSkills folder.

    Only creates files that don't already exist (safe to re-run).
    """
    from urllib.parse import quote

    # C1 (CF-99 extension): the Ops templates shipped with literal
    # {COVE_DOMAIN}/{COVE_PATH}/{CONTAINER_NAME} placeholders — substitute the
    # values that are knowable here so the seed doesn't greet the operator with
    # template junk. Unknowable ones (no domain claimed yet; host compose path
    # is invisible in-container) stay as documented placeholders.
    subs = {}
    try:
        from src.config import load_cove_config
        _dom = (load_cove_config().get("domain") or "").strip()
        if _dom:
            subs["{COVE_DOMAIN}"] = _dom
    except Exception:
        pass
    _cid = (env("COVE_ID") or "").strip()
    if _cid:
        subs["{CONTAINER_NAME}"] = f"{_cid}-app"
        subs["{COVE_PATH}"] = f"<your Cove folder>/{_cid}-cove"

    for path, content in _CONTEXT_TEMPLATES.items():
        for _ph, _val in subs.items():
            content = content.replace(_ph, _val)
        url = f"{webdav_base}/{quote(path, safe='/')}"
        try:
            # Don't overwrite existing files
            check = await client.request("HEAD", url, auth=(nc_username, nc_password))
            if check.status_code != 404:
                log.info("Seed skip (exists): %s for %s", path, nc_username)
                continue

            resp = await client.put(
                url,
                auth=(nc_username, nc_password),
                content=content.encode("utf-8"),
                headers={"Content-Type": "text/markdown; charset=utf-8"},
            )
            if resp.status_code in (201, 204):
                log.info("Seeded: %s for %s", path, nc_username)
            else:
                log.warning("Seed failed %s for %s: HTTP %s", path, nc_username, resp.status_code)
        except Exception as e:
            log.warning("Seed error %s for %s: %s", path, nc_username, e)


# =============================================================================
# Admin API endpoints
# =============================================================================

@router.post("/api/admin/nextcloud/provision/{presence_id}")
async def api_provision_nc_user(presence_id: str, request: Request):
    """Provision a Nextcloud user for an account. Admin only."""
    from src.memory.database import get_db

    # Look up the account
    async with get_db() as conn:
        result = await conn.execute(
            "SELECT id, display_name, username, tier, cove_role FROM accounts WHERE id = %s",
            (presence_id,),
        )
        account = await result.fetchone()

    if not account:
        raise HTTPException(404, "Account not found")

    # Check if already provisioned
    if account.get("nc_username"):
        return {"ok": True, "nc_username": account["nc_username"], "already_provisioned": True}

    display_name = account.get("display_name") or account.get("username") or "User"
    tier = account.get("tier", "operator")
    cove_role = (account.get("cove_role") or "member").strip().lower()
    role = "steward" if cove_role in ("admin", "steward") else "member"
    result = await provision_nc_user(
        presence_id, display_name, tier=tier, role=role, cove_role=cove_role,
        handle=(account.get("username") or ""))
    return result


@router.post("/api/admin/kb/sync")
async def api_kb_sync(request: Request, force: bool = False):
    """Pull the canonical Knowledge Base from the hub into the steward's space.
    Read-only mirror of the single source of truth. Admin only."""
    from src.knowledge.kb_sync import sync_kb
    return await sync_kb(force=force)


@router.get("/api/admin/nextcloud/status")
async def nc_status():
    """Check Nextcloud connectivity and admin access."""
    if not NC_URL:
        return {"ok": False, "error": "NEXTCLOUD_URL not configured"}
    if not NC_ADMIN_PASSWORD:
        return {"ok": False, "error": "NEXTCLOUD_ADMIN_PASSWORD not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Check basic connectivity
            resp = await client.get(f"{NC_URL}/status.php")
            if resp.status_code != 200:
                return {"ok": False, "error": f"Status check failed: HTTP {resp.status_code}"}

            status = resp.json()

            # Check admin auth via OCS
            ocs_resp = await client.get(
                f"{NC_URL}/ocs/v1.php/cloud/users",
                auth=(NC_ADMIN_USER, NC_ADMIN_PASSWORD),
                headers={"OCS-APIRequest": "true"},
                params={"limit": 1},
            )
            admin_ok = ocs_resp.status_code == 200

        return {
            "ok": True,
            "installed": status.get("installed"),
            "version": status.get("versionstring"),
            "admin_auth": admin_ok,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
