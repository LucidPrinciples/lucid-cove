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


def _is_kb_path(path: str) -> bool:
    p = (path or "").strip("/")
    return p == KB_PREFIX or p.startswith(KB_PREFIX + "/")


async def _resolve_webdav(request: Request = None, path: str = ""):
    """Get WebDAV base URL and auth tuple for the current user.

    KB paths resolve to the SINGLE Cove copy (the steward/NC-admin space) no matter
    which presence is asking — the same source kb_sync writes. Everything else
    resolves to the current presence's own space as before."""
    from src.dashboard.routes.nextcloud import (get_nc_creds, NC_ADMIN_USER,
                                                NC_ADMIN_PASSWORD)
    if _is_kb_path(path) and NC_ADMIN_PASSWORD:
        nc_url = env("NEXTCLOUD_URL")
        webdav_base = f"{nc_url}/remote.php/dav/files/{NC_ADMIN_USER}"
        return webdav_base, NC_ADMIN_USER, (NC_ADMIN_USER, NC_ADMIN_PASSWORD), None
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
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
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, path)
    if error:
        return {"items": [], "error": error}

    clean_path = path.strip("/")
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
            if response.status_code == 404 and _is_kb_path(path):
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
        return {"items": items, "path": path}

    except Exception as e:
        return {"items": [], "error": str(e)}


@router.get("/api/files/download")
async def download_file(request: Request, path: str):
    """Stream a file from Nextcloud WebDAV."""
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, path)
    if error:
        return {"error": error}

    clean_path = path.strip("/")
    url = f"{webdav_base}/{clean_path}"

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return {"error": f"File not found: {response.status_code}"}

            filename = clean_path.split("/")[-1]
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
    guard = await _kb_write_guard(request, path)
    if guard:
        return {"success": False, "error": guard}
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, path)
    if error:
        return {"success": False, "error": error}

    clean_path = path.strip("/")
    filename = file.filename or "upload"
    url = f"{webdav_base}/{clean_path}/{filename}"

    try:
        content = await file.read()
        async with httpx.AsyncClient(auth=auth, timeout=60) as client:
            response = await client.put(url, content=content)
            if response.status_code in (200, 201, 204):
                return {"success": True, "path": f"{clean_path}/{filename}"}
            return {"success": False, "error": f"Upload failed: {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/api/files/delete")
async def delete_file(request: Request, path: str):
    """Delete a file or folder from Nextcloud WebDAV."""
    guard = await _kb_write_guard(request, path)
    if guard:
        return {"success": False, "error": guard}
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, path)
    if error:
        return {"success": False, "error": error}

    clean_path = path.strip("/")
    url = f"{webdav_base}/{clean_path}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30) as client:
            response = await client.delete(url)
            return {"success": response.status_code in (200, 204, 207)}
    except Exception as e:
        return {"success": False, "error": str(e)}
