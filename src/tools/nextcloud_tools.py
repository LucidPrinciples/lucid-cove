"""
Nextcloud Tools — file operations and calendar/contacts via Nextcloud APIs.

File ops use WebDAV (standard, stable).
Calendar uses CalDAV (Nextcloud Calendar app).
Contacts uses CardDAV (Nextcloud Contacts app).

Approval tiers:
  AUTO   — list, read, search (non-destructive reads)
  NOTIFY — upload, mkdir, create event (writes, logged to MC)
  APPROVE — delete, overwrite existing files

Environment variables required (in docker/.env):
  NEXTCLOUD_URL      — e.g. http://nextcloud-app (internal docker network)
                       or http://100.65.124.97:8080 (host access)
  NEXTCLOUD_USER     — Nextcloud username for this agent
  NEXTCLOUD_PASSWORD — Nextcloud app password (generate in Nextcloud Security settings)
"""

import os
import unicodedata
from src.env import env
from typing import Optional
from urllib.parse import quote, unquote

import httpx
from langchain_core.tools import tool

from src.tools.approval import auto, notify

# =============================================================================
# Config
# =============================================================================

NEXTCLOUD_URL = env("NEXTCLOUD_URL", "http://nextcloud:80")
NEXTCLOUD_USER = env("NEXTCLOUD_USER")
NEXTCLOUD_PASSWORD = env("NEXTCLOUD_PASSWORD")
# Cove ADMIN NC identity -- the space cove-managers (steward/merchant) act in.
# Read from env here (not routes.nextcloud) to avoid a circular import; the
# values mirror routes/nextcloud.py exactly.
NEXTCLOUD_ADMIN_USER = env("NEXTCLOUD_ADMIN_USER", "admin")
NEXTCLOUD_ADMIN_PASSWORD = env("NEXTCLOUD_ADMIN_PASSWORD")

_OCS_BASE = f"{NEXTCLOUD_URL}/ocs/v2.php"

# ---------------------------------------------------------------------------
# Per-presence credential resolution (CF-57 / punch #3).
#
# These tools historically built every WebDAV/CalDAV URL from the module-global
# NEXTCLOUD_USER env var. That is correct for a single-user (legacy per-container)
# Cove, but on a centralized MULTI-presence Cove there is no single global NC user
# — each presence has its own nc_username/nc_password in the accounts table, so a
# global (often empty) user makes the agent calendar/file tools 500.
#
# A request-scoped ContextVar carries the REQUESTING presence's creds, set at the
# same chat/flow chokepoints that already inject BYOK model creds (chat.py /
# flow_chat.py via get_nc_creds(request)). When the var is unset — the scheduler,
# single-user installs, any non-request path — every resolver falls back to the
# env globals, so single-user behavior is byte-for-byte unchanged. This mirrors
# the proven request-scoped BYOK ContextVar in src/models/provider.py, so the
# same async-context propagation guarantees apply.
# ---------------------------------------------------------------------------
import contextvars as _ctxvars

_nc_creds_ctx: "_ctxvars.ContextVar" = _ctxvars.ContextVar("nc_creds", default=None)


def set_request_nc_creds(url: str, user: str, password: str):
    """Bind the acting presence's NC creds for this request/task. Returns a token
    to pass to clear_request_nc_creds() in a finally block."""
    return _nc_creds_ctx.set((url or NEXTCLOUD_URL, user, password))


def clear_request_nc_creds(token) -> None:
    """Reset the request-scoped NC creds (best-effort)."""
    try:
        _nc_creds_ctx.reset(token)
    except Exception:
        pass


def set_team_nc_creds():
    """Bind the cove ADMIN NC identity for a TEAM run (managers + build-team agents).

    Managers (steward/merchant) and build-team agents have no NC user of their own --
    they all share the cove admin NC space, NOT the requesting or founding operator's.
    Presences keep their own NC user. Returns a token for clear_request_nc_creds(), or
    None when admin creds are unset (caller then leaves creds unbound)."""
    if NEXTCLOUD_ADMIN_USER and NEXTCLOUD_ADMIN_PASSWORD:
        return set_request_nc_creds(NEXTCLOUD_URL, NEXTCLOUD_ADMIN_USER, NEXTCLOUD_ADMIN_PASSWORD)
    return None


# ---------------------------------------------------------------------------
# Phase 2 — role-scoped path access in the SHARED admin NC space
# ---------------------------------------------------------------------------
# Team agents authenticate as the cove admin user (set_team_nc_creds). NC itself
# cannot tell agents apart, so the tool layer scopes paths by the acting role.
# Presence own-space writes are untouched (no acting team channel / no scope).
# Pattern mirrors CF-57 (_nc_creds_ctx) and CF-59 (_links_presence_ctx):
# ContextVar set at the chat + delegation chokepoints, cleared in finally.
# ---------------------------------------------------------------------------

_acting_channel_ctx: "_ctxvars.ContextVar" = _ctxvars.ContextVar(
    "nc_acting_channel", default=None)

# agent_id -> role key (aligned with agent_tools.AGENT_TOOL_REGISTRY + matrix)
_AGENT_ROLE = {
    "stuart": "steward",
    "mercer": "merchant",
    "archimedes": "builder",
    "arthur": "analyst",
    "gabe": "scout",
    "ezra": "keeper",
    "julian": "scribe",
    "iris": "advocate",
    "vera": "auditor",
    "soren": "lens",
}

# Default matrix — approved in Working/Specs/role-nc-access-matrix.md (JAG).
# Paths are prefixes under AgentSkills/ in the admin space. "*" = unrestricted.
# Cove-overridable via cove.yaml `nc_path_scopes` (merged over these defaults).
_DEFAULT_NC_PATH_SCOPES = {
    "steward": {"rw": ["*"], "ro": []},
    "merchant": {
        "rw": ["Team/mercer/", "Reports/", "Shared/"],
        "ro": ["Knowledge Base/", "Content/", "Sites/", "Ops/"],
    },
    "builder": {
        "rw": ["Team/archimedes/", "Sites/", "Shared/"],
        "ro": ["Knowledge Base/", "Reports/", "Content/", "Ops/"],
    },
    "analyst": {
        "rw": ["Team/arthur/", "Shared/"],
        "ro": ["Knowledge Base/", "Reports/", "Content/", "Ops/"],
    },
    "scout": {
        "rw": ["Team/gabe/", "Shared/"],
        "ro": ["Knowledge Base/", "Content/", "Reports/", "Sites/", "Ops/"],
    },
    "keeper": {
        "rw": ["Team/ezra/", "Ops/", "Shared/"],
        "ro": ["Knowledge Base/", "Reports/", "Content/", "Sites/"],
    },
    "scribe": {
        "rw": ["Team/julian/", "Content/", "Shared/"],
        "ro": ["Knowledge Base/", "Reports/", "Sites/", "Ops/"],
    },
    "advocate": {
        "rw": ["Team/iris/", "Content/", "Shared/"],
        "ro": ["Knowledge Base/", "Reports/", "Sites/", "Ops/"],
    },
    "auditor": {
        "rw": ["Team/vera/"],
        "ro": ["Knowledge Base/", "Reports/", "Content/", "Sites/", "Ops/", "Shared/"],
    },
    "lens": {
        "rw": ["Team/soren/"],
        "ro": ["Knowledge Base/", "Reports/", "Ops/", "Shared/"],
    },
}

# Explicit denials for non-steward team agents (never RW even if misconfigured).
_HARD_DENY_PREFIXES = (
    "Context/",
    "Inbox/",
    "Knowledge Base/",
)


def set_acting_channel(channel: str | None):
    """Bind the acting channel for this request/task. Returns a reset token."""
    return _acting_channel_ctx.set(channel or None)


def clear_acting_channel(token) -> None:
    """Reset the acting-channel ContextVar (best-effort)."""
    try:
        if token is not None:
            _acting_channel_ctx.reset(token)
    except Exception:
        pass


def get_acting_channel() -> str | None:
    return _acting_channel_ctx.get()


def _role_for_agent(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    return _AGENT_ROLE.get(agent_id.lower())


def resolve_acting_role() -> tuple[str | None, str | None]:
    """Return (role, agent_id) for the current NC tool call, or (None, None).

    (None, None) means "no team path scoping" — presence/operator own-space, or
    an unbound context. Team managers + build-team agents always resolve a role.
    """
    ch = _acting_channel_ctx.get()
    if not ch:
        return None, None
    try:
        from src.graphs.channels import (
            _is_steward_channel, _is_merchant_channel, _team_agent_key,
        )
    except Exception:
        try:
            from src.config import _is_steward_channel, _is_merchant_channel
            _team_agent_key = lambda _c: None  # noqa: E731
        except Exception:
            return None, None
    if _is_steward_channel(ch):
        return "steward", "stuart"
    if _is_merchant_channel(ch):
        return "merchant", "mercer"
    key = None
    try:
        key = _team_agent_key(ch)
    except Exception:
        key = None
    if key:
        role = _role_for_agent(key) or "unknown"
        return role, key
    # Presence / non-team channel — no team scoping
    return None, None


def _load_nc_path_scopes() -> dict:
    """Defaults merged with cove.yaml `nc_path_scopes` (Cove-overridable)."""
    scopes = {k: {"rw": list(v.get("rw") or []), "ro": list(v.get("ro") or [])}
              for k, v in _DEFAULT_NC_PATH_SCOPES.items()}
    try:
        from src.config import load_cove_config
        override = (load_cove_config() or {}).get("nc_path_scopes") or {}
        if isinstance(override, dict):
            for role, cfg in override.items():
                if not isinstance(cfg, dict):
                    continue
                base = scopes.get(role, {"rw": [], "ro": []})
                if "rw" in cfg and isinstance(cfg["rw"], list):
                    base["rw"] = list(cfg["rw"])
                if "ro" in cfg and isinstance(cfg["ro"], list):
                    base["ro"] = list(cfg["ro"])
                scopes[role] = base
    except Exception:
        pass
    return scopes


def _norm_nc_path(path: str) -> str:
    """Normalize a tool path to a slash-free-leading relative form."""
    p = (path or "").replace("\\", "/").strip()
    while "//" in p:
        p = p.replace("//", "/")
    return p.lstrip("/")


def _under_agentskills(norm: str) -> tuple[bool, str]:
    """Return (is_under_agentskills, relative_under_agentskills).

    relative is the path after AgentSkills/ ('' for the root itself).
    Paths outside AgentSkills return (False, norm).
    """
    if norm == "AgentSkills" or norm.startswith("AgentSkills/"):
        rel = norm[len("AgentSkills"):].lstrip("/")
        return True, rel
    return False, norm


def _prefix_match(rel: str, prefix: str) -> bool:
    """True if rel is exactly the prefix folder or a path under it."""
    pref = (prefix or "").lstrip("/")
    if pref == "*":
        return True
    if not pref:
        return False
    if not pref.endswith("/"):
        pref = pref + "/"
    if rel == pref.rstrip("/"):
        return True
    return rel.startswith(pref)


def _scope_for_role(role: str | None, agent_id: str | None) -> dict:
    """rw/ro prefix lists for a role. Fail-safe: unknown role → Team/<agent>/ only."""
    scopes = _load_nc_path_scopes()
    if role and role in scopes:
        return scopes[role]
    # Fail-safe (restricted-functional): own Team folder only
    aid = (agent_id or "unknown").lower()
    return {"rw": [f"Team/{aid}/"], "ro": ["Knowledge Base/"]}


def _path_allowed(norm: str, prefixes: list[str], *, unrestricted: bool = False) -> bool:
    if unrestricted or "*" in prefixes:
        # Steward (and any role with rw:["*"]) is unrestricted in the admin space.
        return True
    under, rel = _under_agentskills(norm)
    if not under:
        return False
    for pref in prefixes:
        if _prefix_match(rel, pref):
            return True
    return False


def check_nc_path_access(path: str, write: bool = False) -> str | None:
    """Return an error message if the acting role may not access `path`, else None.

    No acting team role → no scoping (presence own-space unchanged).
    Steward → unrestricted under AgentSkills/.
    Writes need an rw prefix; reads allow rw + ro.
    """
    role, agent_id = resolve_acting_role()
    if role is None:
        return None  # presence / unbound — do not scope

    norm = _norm_nc_path(path)
    scope = _scope_for_role(role, agent_id)
    rw = list(scope.get("rw") or [])
    ro = list(scope.get("ro") or [])
    unrestricted = role == "steward" or "*" in rw

    # Hard denials for non-steward (Context/Inbox/KB writes, Team/<other>/)
    if write and not unrestricted:
        under, rel = _under_agentskills(norm)
        # Outside AgentSkills entirely
        if not under:
            return (f"Access denied: role '{role}' may only write under "
                    f"AgentSkills/ (path: {path}).")
        for deny in _HARD_DENY_PREFIXES:
            if _prefix_match(rel, deny):
                return (f"Access denied: '{rel or path}' is read-only / private "
                        f"for role '{role}'.")
        # Team/<other-agent>/ — never write into another agent's workspace
        if rel == "Team" or rel.startswith("Team/"):
            parts = rel.split("/")
            if len(parts) >= 2 and parts[1]:
                other = parts[1].lower()
                mine = (agent_id or "").lower()
                if other != mine:
                    return (f"Access denied: cannot write into Team/{other}/ "
                            f"(role '{role}' owns Team/{mine}/ only).")

    if write:
        if _path_allowed(norm, rw, unrestricted=unrestricted):
            return None
        return (f"Access denied: role '{role}' cannot write to '{path}'. "
                f"Allowed RW prefixes under AgentSkills/: {rw}.")

    # Read: rw ∪ ro
    if _path_allowed(norm, rw + ro, unrestricted=unrestricted):
        return None
    # Steward unrestricted already handled; non-steward outside allowlist
    return (f"Access denied: role '{role}' cannot read '{path}'. "
            f"Allowed prefixes under AgentSkills/: rw={rw}, ro={ro}.")


async def _ensure_own_team_workspace(path: str) -> None:
    """On first write under Team/<self>/, MKCOL the agent folder if missing.

    Best-effort: never raises into the caller; parent AgentSkills/Team is seeded
    by Phase 1. Only creates Team/<own-agent>/, never Team/<other>/.
    """
    role, agent_id = resolve_acting_role()
    if not agent_id or role is None:
        return
    norm = _norm_nc_path(path)
    under, rel = _under_agentskills(norm)
    if not under or not rel.startswith("Team/"):
        return
    mine = agent_id.lower()
    # Only when writing into our own team folder
    if not (rel == f"Team/{mine}" or rel.startswith(f"Team/{mine}/")):
        return
    folder = f"AgentSkills/Team/{mine}"
    url = _webdav_url(folder)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=10) as client:
            resp = await client.request("MKCOL", url)
            # 201 created, 405 already exists — both fine
            if resp.status_code not in (201, 405, 200):
                pass
    except Exception:
        pass


def _guard_or_error(path: str, write: bool = False) -> str | None:
    """Convenience: run check_nc_path_access; return error string or None."""
    return check_nc_path_access(path, write=write)


# Request-free fallback for multi-mode contexts where the ContextVar is unset
# (delegation/wake/scheduler turns, or a tool running off the request thread) AND the
# container env is empty (multi-mode). Loaded once at boot from the accounts table --
# the same source get_nc_creds uses -- as the FOUNDING operator's NC login, so a steward
# acting in the background (e.g. reading the operator's jules Inbox, which lives in the
# operator's NC area) authenticates as the operator instead of failing on empty creds.
_fallback_nc_creds: tuple[str, str, str] | None = None


async def load_fallback_nc_creds() -> None:
    """Populate the request-free NC creds fallback from the founding operator's accounts
    row. Best-effort: on any error the fallback stays unset and behavior is unchanged."""
    global _fallback_nc_creds
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT nc_username, nc_password FROM accounts "
                "WHERE nc_username IS NOT NULL AND nc_username <> '' "
                "AND nc_password IS NOT NULL AND nc_password <> '' "
                "ORDER BY created_at ASC LIMIT 1"
            )
            row = await result.fetchone()
        if row and row["nc_username"]:
            _fallback_nc_creds = (NEXTCLOUD_URL, row["nc_username"], row["nc_password"])
    except Exception:
        pass


def _current_creds() -> tuple[str, str, str]:
    """(url, user, password) for the acting presence. Priority: request-scoped ctx
    (interactive chat binds it) -> env globals (single-user/legacy) -> the founding
    operator's creds loaded at boot (multi-mode background/off-request-thread turns).
    The last fallback keeps file tools authenticating instead of failing on empty creds."""
    c = _nc_creds_ctx.get()
    if c and c[1]:
        return c
    if NEXTCLOUD_USER:
        return (NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASSWORD)
    if _fallback_nc_creds and _fallback_nc_creds[1]:
        return _fallback_nc_creds
    return (NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASSWORD)


def _nc_url() -> str:
    return _current_creds()[0]


def _nc_user() -> str:
    return _current_creds()[1]


def _webdav_base() -> str:
    return f"{_nc_url()}/remote.php/dav/files/{_nc_user()}"


def _caldav_base() -> str:
    return f"{_nc_url()}/remote.php/dav/calendars/{_nc_user()}"


def _auth() -> tuple[str, str]:
    _, user, password = _current_creds()
    return (user, password)


def _webdav_url(path: str) -> str:
    """Build WebDAV URL for a file/folder path. Path should start with /."""
    path = path.lstrip("/")
    return f"{_webdav_base()}/{quote(path, safe='/')}"


def _norm_ws(s: str) -> str:
    """Collapse every Unicode space separator (regular, U+00A0, and the U+202F
    narrow no-break space macOS screenshot filenames embed) to a plain space, so
    a name a model retyped with an ordinary space still matches the real file."""
    return "".join(" " if unicodedata.category(c) == "Zs" else c for c in (s or ""))


async def _find_sibling_by_ws(path: str) -> Optional[str]:
    """On a 404, find the real sibling of `path` whose filename matches after
    whitespace normalization. macOS screenshots ("...at 3.40.42<U+202F>PM.png")
    fail otherwise because the model passes back a normal space. Returns the
    corrected path or None. Only called on the miss path, so no extra latency
    on the common case."""
    p = (path or "").rstrip("/")
    parent, _, base = p.rpartition("/")
    if not base:
        return None
    want = _norm_ws(base)
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
            '<d:prop><d:displayname/></d:prop></d:propfind>')
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "PROPFIND", _webdav_url(parent or "/"),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=body)
        if resp.status_code != 207:
            return None
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}
        for r in root.findall(".//d:response", ns):
            href = r.findtext("d:href", namespaces=ns) or ""
            name = unquote(href.rstrip("/").split("/")[-1])
            if name and name != base and _norm_ws(name) == want:
                return f"{parent}/{name}" if parent else f"/{name}"
    except Exception:
        return None
    return None


# =============================================================================
# File Tools — AUTO (reads)
# =============================================================================

@auto
@tool
async def nextcloud_list(path: str = "/") -> str:
    """List files and folders in a Nextcloud directory.

    Args:
        path: Directory path (e.g. '/', '/Ideas', '/Business Docs')
    """
    denied = check_nc_path_access(path, write=False)
    if denied:
        return denied
    url = _webdav_url(path)
    propfind_body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:displayname/>
    <d:getcontenttype/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "PROPFIND", url,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=propfind_body
            )
        if resp.status_code == 207:
            # Parse XML to extract names and types
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:"}
            items = []
            for response in root.findall(".//d:response", ns):
                href = response.findtext("d:href", namespaces=ns) or ""
                name = unquote(href.rstrip("/").split("/")[-1])
                if not name:
                    continue
                resource_type = response.find(".//d:resourcetype/d:collection", ns)
                kind = "📁" if resource_type is not None else "📄"
                size_el = response.findtext(".//d:getcontentlength", namespaces=ns)
                size = f" ({int(size_el):,} bytes)" if size_el else ""
                items.append(f"{kind} {name}{size}")
            return f"Contents of {path}:\n" + "\n".join(items[1:]) if len(items) > 1 else f"{path} is empty"
        return f"Error listing {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@auto
@tool
async def nextcloud_read(path: str) -> str:
    """Read the contents of a text file from Nextcloud.

    Args:
        path: File path (e.g. '/Ideas/my-note.md')
    """
    denied = check_nc_path_access(path, write=False)
    if denied:
        return denied
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                alt = await _find_sibling_by_ws(path)
                if alt:
                    resp = await client.get(_webdav_url(alt)); path = alt
        if resp.status_code == 200:
            content = resp.text
            if len(content) > 8000:
                content = content[:8000] + "\n\n[... truncated at 8000 chars]"
            return f"FILE: {path}\n\n{content}"
        return f"Error reading {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@auto
@tool
async def nextcloud_search(query: str, path: str = "/") -> str:
    """Search for files in Nextcloud by name.

    Args:
        query: Search term
        path: Directory to search in (default: all files)
    """
    nc_user = _nc_user()
    url = f"{_nc_url()}/remote.php/dav"
    search_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:basicsearch>
    <d:select><d:prop><d:displayname/><d:getcontenttype/></d:prop></d:select>
    <d:from><d:scope><d:href>/files/{nc_user}</d:href><d:depth>infinity</d:depth></d:scope></d:from>
    <d:where>
      <d:like><d:prop><d:displayname/></d:prop><d:literal>%{query}%</d:literal></d:like>
    </d:where>
    <d:limit><d:nresults>20</d:nresults></d:limit>
  </d:basicsearch>
</d:searchrequest>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "SEARCH", url,
                headers={"Content-Type": "application/xml"},
                content=search_body
            )
        if resp.status_code in (200, 207):
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:"}
            results = []
            for response in root.findall(".//d:response", ns):
                href = response.findtext("d:href", namespaces=ns) or ""
                name = href.rstrip("/").split("/")[-1]
                if name:
                    # Strip the WebDAV prefix to get readable path
                    readable = unquote(href.replace(f"/remote.php/dav/files/{nc_user}", ""))
                    results.append(readable)
            if results:
                return f"Search results for '{query}':\n" + "\n".join(results)
            return f"No files found matching '{query}'"
        return f"Search error: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# File Tools — NOTIFY (writes)
# =============================================================================

@notify
@tool
async def nextcloud_upload(path: str, content: str, overwrite: bool = False) -> str:
    """Upload text content as a file to Nextcloud.

    Args:
        path: Destination path including filename (e.g. '/Ideas/note.md')
        content: Text content to write
        overwrite: Whether to overwrite if file exists (default False)
    """
    denied = check_nc_path_access(path, write=True)
    if denied:
        return denied
    await _ensure_own_team_workspace(path)
    url = _webdav_url(path)
    try:
        if not overwrite:
            # Check if file exists
            async with httpx.AsyncClient(auth=_auth(), timeout=10) as client:
                head = await client.head(url)
            if head.status_code == 200:
                return f"File already exists at {path}. Use overwrite=True to replace."

        async with httpx.AsyncClient(auth=_auth(), timeout=30) as client:
            resp = await client.put(url, content=content.encode("utf-8"),
                                    headers={"Content-Type": "text/plain; charset=utf-8"})
        if resp.status_code in (200, 201, 204):
            action = "Updated" if resp.status_code == 204 else "Created"
            return f"{action}: {path}"
        return f"Upload failed for {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def nextcloud_mkdir(path: str) -> str:
    """Create a folder in Nextcloud.

    Args:
        path: Folder path to create (e.g. '/Ideas/Projects/NewFolder')
    """
    denied = check_nc_path_access(path, write=True)
    if denied:
        return denied
    await _ensure_own_team_workspace(path)
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=10) as client:
            resp = await client.request("MKCOL", url, auth=_auth())
        if resp.status_code in (200, 201):
            return f"Created folder: {path}"
        if resp.status_code == 405:
            return f"Folder already exists: {path}"
        return f"Error creating folder {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def nextcloud_download(path: str, local_path: str) -> str:
    """Download a file from Nextcloud to the local filesystem (host).

    Args:
        path: Nextcloud file path (e.g. '/Business Docs/report.pdf')
        local_path: Local destination path (e.g. '/app/data/downloads/report.pdf')
    """
    denied = check_nc_path_access(path, write=False)
    if denied:
        return denied
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                alt = await _find_sibling_by_ws(path)
                if alt:
                    resp = await client.get(_webdav_url(alt)); path = alt
        if resp.status_code == 200:
            import os
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"Downloaded {path} → {local_path} ({len(resp.content):,} bytes)"
        return f"Download failed for {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Calendar Tools — CalDAV
# =============================================================================

@auto
@tool
async def calendar_list_events(calendar: str = "personal", days_ahead: int = 7) -> str:
    """List upcoming calendar events from Nextcloud Calendar.

    Args:
        calendar: Calendar name (default 'personal')
        days_ahead: How many days ahead to look (default 7)
    """
    from datetime import datetime, timezone, timedelta
    import xml.etree.ElementTree as ET

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    time_range_start = now.strftime("%Y%m%dT%H%M%SZ")
    time_range_end = end.strftime("%Y%m%dT%H%M%SZ")

    url = f"{_caldav_base()}/{calendar}/"
    report_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{time_range_start}" end="{time_range_end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "REPORT", url,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=report_body
            )
        if resp.status_code == 207:
            # Parse VCALENDAR data from response
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
            events = []
            for response in root.findall(".//d:response", ns):
                cal_data = response.findtext(".//c:calendar-data", namespaces=ns)
                if cal_data:
                    event = _parse_vevent(cal_data)
                    if event:
                        events.append(event)
            if not events:
                return f"No events in the next {days_ahead} days."
            events.sort(key=lambda e: e.get("dtstart", ""))
            lines = [f"Upcoming events ({days_ahead} days):"]
            for e in events:
                lines.append(f"  • {e.get('dtstart', '?')} — {e.get('summary', '(no title)')}")
                if e.get("description"):
                    lines.append(f"    {e['description'][:80]}")
            return "\n".join(lines)
        if resp.status_code == 404:
            return f"Calendar '{calendar}' not found. Create it in Nextcloud Calendar first."
        return f"Error fetching calendar: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


def _parse_vevent(ical_text: str) -> Optional[dict]:
    """Extract basic event fields from VEVENT ical text."""
    event = {}
    in_vevent = False
    for line in ical_text.splitlines():
        if line.strip() == "BEGIN:VEVENT":
            in_vevent = True
        elif line.strip() == "END:VEVENT":
            break
        elif in_vevent:
            if ":" in line:
                key, _, val = line.partition(":")
                # Extract TZID param if present (e.g., DTSTART;TZID=America/New_York)
                tzid = None
                if ";" in key:
                    parts = key.split(";")
                    key = parts[0]
                    for p in parts[1:]:
                        if p.startswith("TZID="):
                            tzid = p[5:]
                if key == "SUMMARY":
                    event["summary"] = val
                elif key in ("DTSTART", "DTEND"):
                    try:
                        if "T" in val:
                            from datetime import datetime
                            raw = val.rstrip("Z")[:15]
                            dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
                            # If UTC (Z suffix), convert to local for display
                            if val.endswith("Z") and not tzid:
                                from zoneinfo import ZoneInfo
                                from datetime import timezone
                                dt = dt.replace(tzinfo=timezone.utc)
                                try:
                                    from src.config import get_instance
                                    local_tz = get_instance().get("timezone", "America/New_York")
                                except Exception:
                                    local_tz = "America/New_York"
                                dt = dt.astimezone(ZoneInfo(local_tz))
                            if key == "DTSTART":
                                event["dtstart"] = dt.strftime("%a %b %d %H:%M")
                        else:
                            from datetime import datetime
                            dt = datetime.strptime(val[:8], "%Y%m%d")
                            if key == "DTSTART":
                                event["dtstart"] = dt.strftime("%a %b %d")
                    except Exception:
                        if key == "DTSTART":
                            event["dtstart"] = val
                elif key == "DESCRIPTION":
                    event["description"] = val
    return event if event else None


def _resolve_relative_date(date_str: str, tz_name: str = "America/New_York") -> str:
    """Resolve relative date strings to YYYY-MM-DD.

    Supports: 'today', 'tomorrow', 'monday'-'sunday' (next occurrence),
    'next monday'-'next sunday', '+N days', and passthrough for YYYY-MM-DD.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    s = date_str.strip().lower()

    # Already YYYY-MM-DD — pass through
    if len(s) == 10 and s[4] == '-' and s[7] == '-':
        return date_str

    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()

    today = now.date()

    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    # +N days
    if s.startswith("+") and "day" in s:
        import re
        m = re.search(r'\+\s*(\d+)', s)
        if m:
            return (today + timedelta(days=int(m.group(1)))).isoformat()

    # Day names: 'monday', 'next monday', etc.
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    check = s.replace("next ", "")
    if check in day_names:
        target_dow = day_names.index(check)
        current_dow = today.weekday()
        days_ahead = (target_dow - current_dow) % 7
        if days_ahead == 0:
            days_ahead = 7  # next week if today is that day
        return (today + timedelta(days=days_ahead)).isoformat()

    # Fallback — return as-is and let strptime handle it
    return date_str


@notify
@tool
async def calendar_create_event(
    title: str,
    date: str,
    start_time: str = "09:00",
    duration_minutes: int = 60,
    description: str = "",
    calendar: str = "personal",
    alarm_minutes_before: int = 15
) -> str:
    """Create a calendar event in Nextcloud Calendar.

    Args:
        title: Event title
        date: Date in YYYY-MM-DD format, OR relative like 'today', 'tomorrow', 'monday', 'next tuesday', '+3 days'
        start_time: Start time in HH:MM (24hr) format (default 09:00)
        duration_minutes: Event duration in minutes (default 60)
        description: Optional event description
        calendar: Calendar name (default 'personal')
        alarm_minutes_before: Minutes before event to fire a notification (default 15, set to -1 to disable)
    """
    import uuid
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    # Get the agent's configured timezone for proper iCal TZID
    try:
        from src.config import get_instance
        tz_name = get_instance().get("timezone", "America/New_York")
    except Exception:
        tz_name = "America/New_York"

    # ── Resolve relative dates so the model doesn't have to calculate ──
    date = _resolve_relative_date(date, tz_name)

    try:
        start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        uid = str(uuid.uuid4())
        dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
        dtend = end_dt.strftime("%Y%m%dT%H%M%S")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Compute UTC equivalents for verification
        tz = ZoneInfo(tz_name)
        start_aware = start_dt.replace(tzinfo=tz)
        start_utc = start_aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_aware = end_dt.replace(tzinfo=tz)
        end_utc = end_aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Build VALARM block for notifications
        alarm_block = ""
        if alarm_minutes_before >= 0:
            alarm_block = f"""
BEGIN:VALARM
TRIGGER:-PT{alarm_minutes_before}M
ACTION:DISPLAY
DESCRIPTION:Reminder
END:VALARM"""

        ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART;TZID={tz_name}:{dtstart}
DTEND;TZID={tz_name}:{dtend}
SUMMARY:{title}
DESCRIPTION:{description}{alarm_block}
END:VEVENT
END:VCALENDAR"""

        url = f"{_caldav_base()}/{calendar}/{uid}.ics"
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.put(
                url,
                content=ical.encode("utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"}
            )
        if resp.status_code in (200, 201, 204):
            return f"Created event: '{title}' on {date} at {start_time} ({duration_minutes}min) in '{calendar}'"
        if resp.status_code == 404:
            return f"Calendar '{calendar}' not found. Create it in Nextcloud Calendar first."
        return f"Error creating event: HTTP {resp.status_code} — {resp.text[:200]}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_NEXTCLOUD_TOOLS = [
    nextcloud_list,
    nextcloud_read,
    nextcloud_search,
    nextcloud_upload,
    nextcloud_mkdir,
    nextcloud_download,
    calendar_list_events,
    calendar_create_event,
]
TOOLS = ALL_NEXTCLOUD_TOOLS  # alias for cove-core channels.py loader
