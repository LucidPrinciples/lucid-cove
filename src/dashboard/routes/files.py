"""
Files routes — WebDAV integration with Nextcloud file space.

In multi mode (COVE_MODE=multi), each user has their own Nextcloud account.
Credentials are resolved per-user via get_nc_creds() from nextcloud.py.
In single mode, uses NEXTCLOUD_USER/NEXTCLOUD_PASSWORD env vars.
"""

import os
from src.env import env
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import StreamingResponse
import httpx

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")


# The canonical Knowledge Base lives ONCE per Cove — in the steward's (NC admin's)
# space, pulled from the signed Drop by kb_sync. Presences get NO copy of their own
# (one Drop source → one Cove copy → everyone READS it). Reading it through
# the CURRENT presence's own space PROPFINDs a folder that doesn't exist there →
# the "WebDAV error: 404" a non-steward presence hit on the Knowledge Base.
KB_PREFIX = "AgentSkills/Knowledge Base"


def _clean_webdav_path(path: str):
    """#SEC4 H3 — normalize a WebDAV relative path and reject traversal.

    Returns (clean_path, error). clean_path has no leading/trailing slash and no
    ``.`` / ``..`` segments. A path that would climb above the WebDAV root
    (``../x``, ``AgentSkills/Knowledge Base/../../secret``) returns an error
    instead of being forwarded to Nextcloud — critical because KB paths resolve
    to the steward/admin credentials; without normalization a ``..`` chain kept
    the admin-cred branch while escaping the KB tree.
    """
    if path is not None and "\x00" in str(path):
        return None, "Invalid path"
    raw = (path or "").replace("\\", "/")
    parts = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None, "Path escapes root"
            parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts), None


def _is_kb_path(path: str) -> bool:
    """True if path is under the Cove Knowledge Base (after normalization)."""
    p, err = _clean_webdav_path(path)
    if err is not None:
        # Unclean/escaping path is never treated as KB — never upgrade to admin creds.
        return False
    return p == KB_PREFIX or p.startswith(KB_PREFIX + "/")


async def _resolve_webdav(request: Request = None, path: str = ""):
    """Get WebDAV base URL and auth tuple for the current user.

    KB paths resolve to the SINGLE Cove copy (the steward/NC-admin space) no matter
    which presence is asking — the same source kb_sync writes. Everything else
    resolves to the current presence's own space as before.

    #SEC4 H3: path is normalized first so ``KB/../../x`` cannot keep admin creds.
    """
    from src.dashboard.routes.nextcloud import (get_nc_creds, resolve_tab_nc_creds,
                                                NC_ADMIN_USER, NC_ADMIN_PASSWORD)
    clean, err = _clean_webdav_path(path)
    if err is not None:
        return None, None, None, err
    if _is_kb_path(clean) and NC_ADMIN_PASSWORD:
        nc_url = env("NEXTCLOUD_URL")
        webdav_base = f"{nc_url}/remote.php/dav/files/{NC_ADMIN_USER}"
        return webdav_base, NC_ADMIN_USER, (NC_ADMIN_USER, NC_ADMIN_PASSWORD), None
    nc_url, nc_user, nc_pass = await resolve_tab_nc_creds(request)
    if not nc_pass:
        return None, None, None, "Nextcloud not configured"
    webdav_base = f"{nc_url}/remote.php/dav/files/{nc_user}"
    return webdav_base, nc_user, (nc_user, nc_pass), None


async def _kb_write_guard(request: Request, path: str):
    """The KB is curated by the steward and pulled from the Drop — a presence writing
    'into' it would land in their OWN space (invisible, drift). Allow writes only for
    the caller whose NC identity IS the steward/admin space; block everyone else with
    a clear message instead of a confusing 404/shadow-copy."""
    if not _is_kb_path(path):
        return None
    from src.dashboard.routes.nextcloud import get_nc_creds, NC_ADMIN_USER
    _, nc_user, nc_pass = await get_nc_creds(request)
    if nc_user == NC_ADMIN_USER and nc_pass:
        return None
    return ("The Knowledge Base is read-only here — it syncs from the Drop and is "
            "curated by the steward.")


@router.get("/api/files/list")
async def list_files(request: Request, path: str = "/"):
    """List files and folders at a WebDAV path."""
    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"items": [], "error": path_err}

    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"items": [], "error": error}

    url = f"{webdav_base}/{clean_path}" if clean_path else webdav_base

    propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:oc="http://owncloud.org/ns">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
    <D:getcontentlength/>
    <D:getlastmodified/>
    <D:getcontenttype/>
    <oc:size/>
  </D:prop>
</D:propfind>"""

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30) as client:
            response = await client.request(
                "PROPFIND",
                url,
                content=propfind_body,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            )

        if response.status_code not in (207, 200):
            # CF-6b: a 404 on the KB path means the Drop sync hasn't populated the
            # Cove copy yet (fresh install, NC still settling) — say that instead
            # of a raw WebDAV error.
            if response.status_code == 404 and _is_kb_path(clean_path):
                return {"items": [], "error": ("The Knowledge Base is still syncing "
                        "from the Drop — check back in a few minutes.")}
            return {"items": [], "error": f"WebDAV error: {response.status_code}"}

        # Parse XML response
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)

        items = []
        ns = {"D": "DAV:", "oc": "http://owncloud.org/ns"}
        base_path = f"/remote.php/dav/files/{nc_user}"

        for resp in root.findall(".//D:response", ns):
            href = resp.findtext("D:href", namespaces=ns) or ""
            # Skip the parent itself
            rel = href.replace(base_path, "").strip("/")
            if rel == clean_path.strip("/"):
                continue

            props = resp.find(".//D:propstat/D:prop", ns)
            if props is None:
                continue

            name = props.findtext("D:displayname", namespaces=ns) or rel.split("/")[-1]
            resourcetype = props.find("D:resourcetype", ns)
            is_dir = resourcetype is not None and resourcetype.find("D:collection", ns) is not None
            size = props.findtext("D:getcontentlength", namespaces=ns) or props.findtext("oc:size", namespaces=ns)
            modified = props.findtext("D:getlastmodified", namespaces=ns) or ""
            content_type = props.findtext("D:getcontenttype", namespaces=ns) or ""

            if name:
                items.append({
                    "name": name,
                    "path": rel or name,
                    "is_dir": is_dir,
                    "size": int(size) if size else 0,
                    "modified": modified,
                    "content_type": content_type,
                })

        # Sort: dirs first, then files alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {"items": items, "path": clean_path or "/"}

    except Exception as e:
        return {"items": [], "error": str(e)}


@router.get("/api/files/download")
async def download_file(request: Request, path: str):
    """Stream a file from Nextcloud WebDAV."""
    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"error": path_err}

    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"error": error}

    url = f"{webdav_base}/{clean_path}"

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return {"error": f"File not found: {response.status_code}"}

            filename = clean_path.split("/")[-1] if clean_path else "download"
            content_type = response.headers.get("content-type", "application/octet-stream")

            return StreamingResponse(
                iter([response.content]),
                media_type=content_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
    except Exception as e:
        return {"error": str(e)}


@router.post("/api/files/upload")
async def upload_file(request: Request, path: str, file: UploadFile = File(...)):
    """Upload a file to Nextcloud WebDAV."""
    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"success": False, "error": path_err}

    guard = await _kb_write_guard(request, clean_path)
    if guard:
        return {"success": False, "error": guard}
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"success": False, "error": error}

    # #SEC4 H3: basename only for the uploaded filename (no path segments)
    filename = Path_name_only(file.filename or "upload")
    url = f"{webdav_base}/{clean_path}/{filename}" if clean_path else f"{webdav_base}/{filename}"

    try:
        content = await file.read()
        async with httpx.AsyncClient(auth=auth, timeout=60) as client:
            response = await client.put(url, content=content)
            if response.status_code in (200, 201, 204):
                dest = f"{clean_path}/{filename}" if clean_path else filename
                return {"success": True, "path": dest}
            return {"success": False, "error": f"Upload failed: {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def Path_name_only(name: str) -> str:
    """Strip any directory components from an upload filename."""
    # Use pure string ops to avoid importing pathlib at module top for one call
    n = (name or "upload").replace("\\", "/").split("/")[-1].strip() or "upload"
    if n in (".", ".."):
        return "upload"
    return n


@router.delete("/api/files/delete")
async def delete_file(request: Request, path: str):
    """Retire a file or folder into AgentSkills/To-Delete (never hard-delete).

    Operator policy 2026-07-20: product deletes MOVE into a holding area so the
    operator can offload to external backup or empty when notified of size.
    WebDAV MOVE keeps one object; if MOVE fails we fall back to WebDAV DELETE
    (Nextcloud trashbin) — never a silent permanent wipe.
    """
    import time
    from urllib.parse import quote

    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"success": False, "error": path_err}

    guard = await _kb_write_guard(request, clean_path)
    if guard:
        return {"success": False, "error": guard}
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"success": False, "error": error}

    # Don't re-retire something already in To-Delete — then trash is OK.
    already = clean_path == "AgentSkills/To-Delete" or clean_path.startswith(
        "AgentSkills/To-Delete/"
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = clean_path.rstrip("/").split("/")[-1] or "item"
    dest_rel = f"AgentSkills/To-Delete/{stamp}__{base_name}"

    src_url = f"{webdav_base}/{quote(clean_path, safe='/')}"
    dest_url = f"{webdav_base}/{quote(dest_rel, safe='/')}"
    parent_url = f"{webdav_base}/{quote('AgentSkills/To-Delete', safe='/')}"

    try:
        async with httpx.AsyncClient(auth=auth, timeout=60) as client:
            if already:
                response = await client.delete(src_url)
                return {
                    "success": response.status_code in (200, 204, 207, 404),
                    "method": "nc_trash",
                    "dest": "",
                }
            # ensure To-Delete exists
            await client.request("MKCOL", parent_url)
            resp = await client.request(
                "MOVE",
                src_url,
                headers={"Destination": dest_url, "Overwrite": "T"},
            )
            if resp.status_code in (200, 201, 204):
                return {
                    "success": True,
                    "retired": True,
                    "method": "move",
                    "dest": dest_rel,
                }
            # Fallback: WebDAV DELETE → NC trashbin
            response = await client.delete(src_url)
            ok = response.status_code in (200, 204, 207, 404)
            return {
                "success": ok,
                "retired": ok,
                "method": "nc_trash" if ok else "failed",
                "dest": "",
                "error": None if ok else f"MOVE {resp.status_code}, DELETE {response.status_code}",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

