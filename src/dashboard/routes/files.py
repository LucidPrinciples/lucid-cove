"""
Files routes — WebDAV integration with Nextcloud file space.

In multi mode (COVE_MODE=multi), each user has their own Nextcloud account.
Credentials are resolved per-user via get_nc_creds() from nextcloud.py.
In single mode, uses NEXTCLOUD_USER/NEXTCLOUD_PASSWORD env vars.
"""

import os
from pathlib import PurePosixPath
from src.env import env
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from src.dashboard.routes.files_pack import (
    filter_pack_items,
    format_size_label,
    iter_zip_stored,
    pack_name_excluded,
    pack_progress_key,
    sort_pack_items_newest_first,
)

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")


# The canonical Knowledge Base lives ONCE per Cove — in the steward's (NC admin's)
# space, pulled from the signed Drop by kb_sync. Presences get NO copy of their own
# (one Drop source → one Cove copy → everyone READS it). Reading it through
# the CURRENT presence's own space PROPFINDs a folder that doesn't exist there →
# the "WebDAV error: 404" a non-steward presence hit on the Knowledge Base.
KB_PREFIX = "AgentSkills/Knowledge Base"
# Cove-level CSV tables (familiar-tools). Same single-object rule as KB:
# agents write Tables/ in the steward/admin NC space; every presence's
# /tables viewer must resolve there — not a per-presence shadow copy.
TABLES_PREFIX = "Tables"
# #CF-113 — operator-only handoff folder (canonical on admin for OCS only;
# each operator sees RW share at their NC root). Agents + steward/manager Files
# (admin creds) must not list or open it — video line: operators only.
OPERATOR_SHARED_PREFIX = "OperatorShared"
_LEGACY_OPERATOR_SHARED_PREFIX = "CoveShared"


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


def _is_tables_path(path: str) -> bool:
    """True if path is under Cove-level Tables/ (household CSV grids)."""
    p, err = _clean_webdav_path(path)
    if err is not None:
        return False
    return p == TABLES_PREFIX or p.startswith(TABLES_PREFIX + "/")


def _is_cove_shared_path(path: str) -> bool:
    """Paths that resolve to steward/admin NC for every presence (KB + Tables)."""
    return _is_kb_path(path) or _is_tables_path(path)


def _is_operator_shared_path(path: str) -> bool:
    """True if path is OperatorShared (or legacy CoveShared) after normalize."""
    p, err = _clean_webdav_path(path)
    if err is not None or not p:
        return False
    for name in (OPERATOR_SHARED_PREFIX, _LEGACY_OPERATOR_SHARED_PREFIX):
        if p == name or p.startswith(name + "/"):
            return True
    return False


def _item_is_operator_shared_name(name: str) -> bool:
    n = (name or "").strip().rstrip("/")
    return n in (OPERATOR_SHARED_PREFIX, _LEGACY_OPERATOR_SHARED_PREFIX)


async def _resolve_webdav(request: Request = None, path: str = ""):
    """Get WebDAV base URL and auth tuple for the current user.

    KB and Tables/ paths resolve to the SINGLE Cove copy (the steward/NC-admin
    space) no matter which presence is asking — same object agents write.
    Everything else resolves to the current presence's own space as before.

    #SEC4 H3: path is normalized first so ``KB/../../x`` cannot keep admin creds.
    """
    from src.dashboard.routes.nextcloud import (get_nc_creds, resolve_tab_nc_creds,
                                                NC_ADMIN_USER, NC_ADMIN_PASSWORD)
    clean, err = _clean_webdav_path(path)
    if err is not None:
        return None, None, None, err
    if _is_cove_shared_path(clean) and NC_ADMIN_PASSWORD:
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


async def _operator_shared_agent_guard(request: Request, path: str, webdav_user: str = ""):
    """#CF-113: block agent/admin access to OperatorShared.

    Operators reach it via their own NC user (share mount). Steward manager Files
    and team tools use admin NC — deny those so the folder stays operator-private
    even though the canonical object lives on admin for provision.
    """
    if not _is_operator_shared_path(path):
        return None
    from src.dashboard.routes.nextcloud import NC_ADMIN_USER
    user = (webdav_user or "").strip()
    if not user:
        try:
            from src.dashboard.routes.nextcloud import resolve_tab_nc_creds
            _, user, _ = await resolve_tab_nc_creds(request)
        except Exception:
            user = ""
    if user and user == NC_ADMIN_USER:
        return ("OperatorShared is private to Cove operators — not available "
                "to agents or steward Files.")
    return None


@router.get("/api/files/list")
async def list_files(request: Request, path: str = "/"):
    """List files and folders at a WebDAV path."""
    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"items": [], "error": path_err}

    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"items": [], "error": error}

    denied = await _operator_shared_agent_guard(request, clean_path or "", nc_user or "")
    if denied:
        return {"items": [], "error": denied}

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
        from urllib.parse import unquote
        root = ET.fromstring(response.text)

        items = []
        ns = {"D": "DAV:", "oc": "http://owncloud.org/ns"}
        base_path = f"/remote.php/dav/files/{nc_user}"
        parent_rel = (clean_path or "").strip("/")

        for resp in root.findall(".//D:response", ns):
            href = resp.findtext("D:href", namespaces=ns) or ""
            # Decode %20 etc before compare — raw href often differs from clean_path
            href_dec = unquote(href)
            # Skip the parent itself (encoded or plain)
            if base_path in href_dec:
                rel = href_dec.split(base_path, 1)[-1].strip("/")
            elif base_path in href:
                rel = unquote(href.split(base_path, 1)[-1]).strip("/")
            else:
                # Fallback: last path segment chain after /files/{user}
                marker = f"/files/{nc_user}"
                if marker in href_dec:
                    rel = href_dec.split(marker, 1)[-1].strip("/")
                else:
                    rel = unquote(href).strip("/")
            if rel == parent_rel:
                continue

            props = resp.find(".//D:propstat/D:prop", ns)
            if props is None:
                continue

            name = props.findtext("D:displayname", namespaces=ns) or (rel.split("/")[-1] if rel else "")
            # Prefer API path from href (decoded), not displayname alone
            item_path = rel or name
            resourcetype = props.find("D:resourcetype", ns)
            is_dir = resourcetype is not None and resourcetype.find("D:collection", ns) is not None
            size = props.findtext("D:getcontentlength", namespaces=ns) or props.findtext("oc:size", namespaces=ns)
            modified = props.findtext("D:getlastmodified", namespaces=ns) or ""
            content_type = props.findtext("D:getcontenttype", namespaces=ns) or ""

            if name:
                # #CF-113: hide OperatorShared from steward/admin Files listings
                from src.dashboard.routes.nextcloud import NC_ADMIN_USER
                if (nc_user == NC_ADMIN_USER
                        and _item_is_operator_shared_name(name)
                        and not (clean_path or "").strip("/")):
                    continue
                if (nc_user == NC_ADMIN_USER
                        and _is_operator_shared_path(item_path or name)):
                    continue
                items.append({
                    "name": name,
                    "path": item_path,
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


async def _webdav_stream_file(auth, url: str, timeout: float = 3600.0):
    """Open a streaming GET to WebDAV; caller must aclose the client/response.

    Returns (client, response) on HTTP 200, or (None, error_json_response).
    """
    client = httpx.AsyncClient(auth=auth, timeout=timeout)
    try:
        req = client.build_request("GET", url)
        response = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        return None, JSONResponse({"error": str(e)}, status_code=502)
    if response.status_code != 200:
        body = (await response.aread())[:300]
        await response.aclose()
        await client.aclose()
        return None, JSONResponse(
            {"error": f"File not found: {response.status_code}", "detail": body.decode("utf-8", "replace")},
            status_code=404 if response.status_code == 404 else 502,
        )
    return (client, response), None


def _content_disposition(filename: str) -> str:
    # Keep header simple ASCII filename; UTF-8 names fall back to basename slug.
    safe = "".join(ch if 32 <= ord(ch) < 127 and ch not in "\"" else "_" for ch in (filename or "download"))
    if not safe.strip("._"):
        safe = "download"
    return f'attachment; filename="{safe}"'


@router.get("/api/files/download")
async def download_file(request: Request, path: str):
    """Stream a file from Nextcloud WebDAV (chunked — safe for multi-GB)."""
    from urllib.parse import quote

    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return JSONResponse({"error": path_err}, status_code=400)

    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    denied = await _operator_shared_agent_guard(request, clean_path or "", nc_user or "")
    if denied:
        return JSONResponse({"error": denied}, status_code=403)

    url = f"{webdav_base}/{quote(clean_path, safe='/')}"
    opened, err = await _webdav_stream_file(auth, url)
    if err is not None:
        return err
    client, response = opened
    filename = clean_path.split("/")[-1] if clean_path else "download"
    content_type = response.headers.get("content-type", "application/octet-stream")
    content_length = response.headers.get("content-length")

    async def body():
        try:
            async for chunk in response.aiter_bytes(1024 * 1024):
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    headers = {"Content-Disposition": _content_disposition(filename)}
    if content_length:
        headers["Content-Length"] = content_length
    return StreamingResponse(body(), media_type=content_type, headers=headers)


DEFAULT_VIDEO_SHORTS = "AgentSkills/Content/video/shorts"
# Soft cap on zip members (not total bytes). Very large single files should use
# single-file download; browsers handle one stream better than a 30G zip.
PACK_ZIP_MAX_FILES = 80
PACK_ZIP_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB soft warn/reject for zip


@router.get("/api/files/pack")
async def list_download_pack(
    request: Request,
    path: str = DEFAULT_VIDEO_SHORTS,
    exclude_preview: bool = True,
    q: str = "",
):
    """List files in a folder as a download pack (presence NC via same WebDAV as Files).

    Defaults to video shorts. Excludes *preview* names unless exclude_preview=false.
    """
    clean_path, path_err = _clean_webdav_path(path or DEFAULT_VIDEO_SHORTS)
    if path_err is not None:
        return {"path": path, "items": [], "error": path_err}

    listing = await list_files(request, path=clean_path or "/")
    if listing.get("error"):
        return {
            "path": clean_path,
            "items": [],
            "error": listing["error"],
            "exclude_preview": exclude_preview,
        }

    items = filter_pack_items(
        listing.get("items") or [],
        exclude_preview=exclude_preview,
        query=q or "",
        files_only=True,
    )
    items = sort_pack_items_newest_first(items)
    total = sum(int(it.get("size") or 0) for it in items)
    return {
        "path": clean_path,
        "items": items,
        "count": len(items),
        "total_bytes": total,
        "total_label": format_size_label(total),
        "exclude_preview": exclude_preview,
        "zip_max_files": PACK_ZIP_MAX_FILES,
        "zip_max_total_bytes": PACK_ZIP_MAX_TOTAL_BYTES,
        "default_path": DEFAULT_VIDEO_SHORTS,
        "sort": "newest_first",
        "progress_key": pack_progress_key(clean_path or DEFAULT_VIDEO_SHORTS),
    }


@router.post("/api/files/pack/zip")
async def download_pack_zip(request: Request):
    """Stream a ZIP (store-only) of selected paths over presence WebDAV.

    Body JSON: { "paths": ["AgentSkills/.../a.mp4", ...], "exclude_preview": true }
    Or { "path": "AgentSkills/Content/video/shorts", "names": ["a.mp4"] }.
    Prefer modest packs; multi-GB single files should use /api/files/download.
    """
    from urllib.parse import quote

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    exclude_preview = body.get("exclude_preview", True)
    if isinstance(exclude_preview, str):
        exclude_preview = exclude_preview.strip().lower() not in ("0", "false", "no")

    paths = body.get("paths") or []
    if not paths and body.get("path"):
        base = (body.get("path") or "").strip().strip("/")
        names = body.get("names") or body.get("files") or []
        paths = [f"{base}/{n}".replace("//", "/") for n in names if n]

    if not isinstance(paths, list) or not paths:
        return JSONResponse({"error": "paths required"}, status_code=400)

    cleaned = []
    for raw in paths:
        c, err = _clean_webdav_path(str(raw or ""))
        if err or not c:
            return JSONResponse({"error": err or "Invalid path"}, status_code=400)
        base = c.split("/")[-1]
        if pack_name_excluded(base, exclude_preview=bool(exclude_preview)):
            continue
        cleaned.append(c)

    # de-dupe preserve order
    seen = set()
    uniq = []
    for c in cleaned:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)

    if not uniq:
        return JSONResponse({"error": "No files to pack after filters"}, status_code=400)
    if len(uniq) > PACK_ZIP_MAX_FILES:
        return JSONResponse(
            {
                "error": f"Too many files for zip (max {PACK_ZIP_MAX_FILES}). "
                "Download large sets as individual files or smaller batches.",
                "count": len(uniq),
            },
            status_code=400,
        )

    # Resolve auth from first path (all must be same presence space)
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, uniq[0])
    if error:
        return JSONResponse({"error": error}, status_code=400)
    denied = await _operator_shared_agent_guard(request, uniq[0], nc_user or "")
    if denied:
        return JSONResponse({"error": denied}, status_code=403)

    # Soft total size check via HEAD/list would be ideal; skip if unknown.
    # Reject OperatorShared / path escape already handled by clean.

    async def member_stream(rel: str):
        url = f"{webdav_base}/{quote(rel, safe='/')}"
        client = httpx.AsyncClient(auth=auth, timeout=3600.0)
        response = None
        try:
            req = client.build_request("GET", url)
            response = await client.send(req, stream=True)
            if response.status_code != 200:
                raise RuntimeError(f"WebDAV {response.status_code} for {rel}")
            async for chunk in response.aiter_bytes(1024 * 1024):
                yield chunk
        finally:
            if response is not None:
                await response.aclose()
            await client.aclose()

    members = []
    for rel in uniq:
        arc = PurePosixPath(rel).name
        # closure binding
        async def _gen(r=rel):
            async for chunk in member_stream(r):
                yield chunk
        members.append((arc, _gen()))

    zip_name = "cove-download-pack.zip"
    if len(uniq) == 1:
        zip_name = PurePosixPath(uniq[0]).stem + ".zip"
    elif body.get("path"):
        zip_name = PurePosixPath(str(body.get("path")).rstrip("/")).name or zip_name
        if not zip_name.endswith(".zip"):
            zip_name = f"{zip_name}.zip"

    return StreamingResponse(
        iter_zip_stored(members),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition(zip_name),
            "Cache-Control": "no-store",
        },
    )


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
    denied = await _operator_shared_agent_guard(request, clean_path or "", nc_user or "")
    if denied:
        return {"success": False, "error": denied}

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
    from urllib.parse import quote, unquote

    clean_path, path_err = _clean_webdav_path(path)
    if path_err is not None:
        return {"success": False, "error": path_err}

    guard = await _kb_write_guard(request, clean_path)
    if guard:
        return {"success": False, "error": guard}
    webdav_base, nc_user, auth, error = await _resolve_webdav(request, clean_path)
    if error:
        return {"success": False, "error": error}
    denied = await _operator_shared_agent_guard(request, clean_path or "", nc_user or "")
    if denied:
        return {"success": False, "error": denied}

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



# ── Direct Cloud pull + pack progress ──────────────────────────────────────
# Multi-GB social uploads must not hairpin Mac → MC → Nextcloud → MC → Mac.
# MC keeps list/filter/select; the browser GETs short-lived public share links
# on the Cove's public Cloud host (cloud.{domain}) so bytes never re-enter the app.


def _public_cloud_base() -> str:
    """Browser-reachable Nextcloud origin for this Cove."""
    try:
        from src.config import _cloud_public_url
        url = (_cloud_public_url() or "").rstrip("/")
        if url:
            return url
    except Exception:
        pass
    return (env("NEXTCLOUD_PUBLIC_URL") or env("NEXTCLOUD_URL") or "").rstrip("/")


def _parse_ocs_share_payload(body: str | dict | None) -> dict:
    """Best-effort parse of OCS share create/list (JSON preferred, XML fallback)."""
    import re as _re
    import json as _json
    out: dict = {}
    if isinstance(body, dict):
        data = body
    else:
        raw = body or ""
        try:
            data = _json.loads(raw) if raw and raw.lstrip().startswith("{") else None
        except Exception:
            data = None
        if data is None:
            for key in ("id", "token", "url", "share_type", "path", "expiration"):
                m = _re.search(rf"<{key}>([^<]*)</{key}>", raw, _re.I)
                if m:
                    out[key] = m.group(1)
            m = _re.search(r"<statuscode>(\d+)</statuscode>", raw)
            if m:
                out["statuscode"] = int(m.group(1))
            return out
    # JSON OCS shape: {ocs:{meta:{statuscode}, data:{...} or [..]}}
    try:
        ocs = data.get("ocs") if isinstance(data, dict) else None
        if isinstance(ocs, dict):
            meta = ocs.get("meta") or {}
            if "statuscode" in meta:
                out["statuscode"] = int(meta["statuscode"])
            payload = ocs.get("data")
            if isinstance(payload, list):
                out["_list"] = payload
            elif isinstance(payload, dict):
                for key in ("id", "token", "url", "share_type", "path", "expiration"):
                    if key in payload and payload[key] is not None:
                        out[key] = payload[key]
    except Exception:
        pass
    return out


async def _mint_public_file_share(
    *,
    nc_url: str,
    nc_user: str,
    nc_pass: str,
    file_path: str,
    expire_days: int = 1,
) -> dict:
    """Create (or reuse) a read-only public link share for one file.

    Returns {url, token, id, download_url} where download_url forces attachment.
    shareType 3 = public link. permissions 1 = read.
    """
    from urllib.parse import quote

    base = (nc_url or "").rstrip("/")
    if not base or not nc_user or not nc_pass:
        return {"error": "Nextcloud credentials missing"}

    rel = "/" + (file_path or "").lstrip("/")
    share_api = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

    # Prefer the browser-public origin for the link host when internal NC differs.
    public_base = _public_cloud_base() or base

    async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=45.0) as client:
        # Look for an existing public link on this exact path (avoid share spam).
        try:
            listed = await client.get(
                share_api,
                params={"path": rel, "reshares": "true"},
                headers=headers,
            )
            if listed.status_code == 200:
                parsed = _parse_ocs_share_payload(listed.text)
                candidates = parsed.get("_list") or []
                if not candidates and parsed.get("token"):
                    candidates = [parsed]
                if not candidates:
                    # XML multi-element fallback
                    for chunk in (listed.text or "").split("<element>")[1:]:
                        candidates.append(_parse_ocs_share_payload(chunk))
                for meta in candidates:
                    if not isinstance(meta, dict):
                        continue
                    # JSON entries are raw share dicts
                    st = str(meta.get("share_type") or meta.get("shareType") or "")
                    token = str(meta.get("token") or "")
                    share_url = str(meta.get("url") or "")
                    if st not in ("3", "3.0") and not (token and share_url):
                        # public links are type 3; skip user/group shares
                        if st and st not in ("3", "3.0"):
                            continue
                    if st in ("3", "3.0") or (token and "/s/" in share_url):
                        if public_base and share_url.startswith(base):
                            share_url = public_base + share_url[len(base):]
                        elif public_base and token:
                            share_url = f"{public_base}/s/{token}"
                        name = PurePosixPath(rel).name
                        dl = f"{public_base}/s/{token}/download/{quote(name)}" if token else share_url
                        return {
                            "url": share_url,
                            "token": token,
                            "id": meta.get("id"),
                            "download_url": dl,
                            "reused": True,
                        }
        except Exception:
            pass

        data = {
            "path": rel,
            "shareType": 3,  # public link
            "permissions": 1,  # read
        }
        # Optional expiry (days). NC accepts expireDate YYYY-MM-DD.
        if expire_days and expire_days > 0:
            from datetime import datetime, timedelta, timezone
            exp = (datetime.now(timezone.utc) + timedelta(days=int(expire_days))).date()
            data["expireDate"] = exp.isoformat()

        resp = await client.post(share_api, headers=headers, data=data)
        body = resp.text or ""
        meta = _parse_ocs_share_payload(body)
        code = meta.get("statuscode")
        if resp.status_code not in (200, 201) or (code is not None and code not in (100, 200)):
            return {
                "error": f"Share create failed HTTP {resp.status_code} OCS {code}",
                "detail": body[:300],
            }
        token = meta.get("token") or ""
        share_url = meta.get("url") or ""
        if token and not share_url:
            share_url = f"{public_base}/s/{token}"
        if public_base and share_url.startswith(base):
            share_url = public_base + share_url[len(base):]
        if token and public_base and "/s/" not in share_url:
            share_url = f"{public_base}/s/{token}"
        name = PurePosixPath(rel).name
        download_url = f"{public_base}/s/{token}/download/{quote(name)}" if token else share_url
        return {
            "url": share_url,
            "token": token,
            "id": meta.get("id"),
            "download_url": download_url,
            "reused": False,
            "expire_days": expire_days,
        }


@router.post("/api/files/pack/direct-urls")
async def pack_direct_urls(request: Request):
    """Mint short-lived public Cloud download URLs for selected pack paths.

    Body: { "paths": ["AgentSkills/.../a.mp4", ...], "expire_days": 1 }
    Returns items with download_url pointing at cloud.{domain} so the browser
    streams multi-GB files without proxying through Mission Control.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    paths = body.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return JSONResponse({"error": "paths required"}, status_code=400)

    expire_days = body.get("expire_days", 1)
    try:
        expire_days = max(1, min(14, int(expire_days)))
    except (TypeError, ValueError):
        expire_days = 1

    cleaned = []
    for raw in paths:
        c, err = _clean_webdav_path(str(raw or ""))
        if err or not c:
            return JSONResponse({"error": err or "Invalid path"}, status_code=400)
        if pack_name_excluded(PurePosixPath(c).name, exclude_preview=True):
            # still allow if operator explicitly selected; don't hard-block here
            pass
        cleaned.append(c)

    # de-dupe preserve order
    seen = set()
    uniq = []
    for c in cleaned:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)

    if len(uniq) > 40:
        return JSONResponse(
            {"error": "Select at most 40 files per direct-URL batch."},
            status_code=400,
        )

    webdav_base, nc_user, auth, error = await _resolve_webdav(request, uniq[0])
    if error:
        return JSONResponse({"error": error}, status_code=400)
    denied = await _operator_shared_agent_guard(request, uniq[0], nc_user or "")
    if denied:
        return JSONResponse({"error": denied}, status_code=403)

    nc_url = env("NEXTCLOUD_URL") or ""
    # Prefer public origin for OCS when possible — shares still created with
    # presence credentials against the in-network NC, then URL host rewritten.
    if not nc_url and webdav_base:
        # webdav_base like http://nc/remote.php/dav/files/user
        nc_url = webdav_base.split("/remote.php/")[0]

    nc_pass = auth[1] if auth and len(auth) > 1 else ""
    public_base = _public_cloud_base()

    results = []
    errors = []
    for rel in uniq:
        # re-check guard per path
        denied_p = await _operator_shared_agent_guard(request, rel, nc_user or "")
        if denied_p:
            errors.append({"path": rel, "error": denied_p})
            continue
        minted = await _mint_public_file_share(
            nc_url=nc_url,
            nc_user=nc_user or "",
            nc_pass=nc_pass or "",
            file_path=rel,
            expire_days=expire_days,
        )
        if minted.get("error"):
            errors.append({"path": rel, **minted})
            continue
        results.append({
            "path": rel,
            "name": PurePosixPath(rel).name,
            "download_url": minted.get("download_url"),
            "share_url": minted.get("url"),
            "token": minted.get("token"),
            "reused": bool(minted.get("reused")),
        })

    # Hint the UI when mint host is missing or still looks like an internal-only origin.
    egress = "ok"
    if not public_base:
        egress = "missing_public_base"
    else:
        try:
            from urllib.parse import urlparse

            host = (urlparse(public_base).hostname or "").lower()
        except Exception:
            host = ""
        if host in ("localhost", "nextcloud") or host.startswith("127."):
            egress = "loopback_base"
        elif host.endswith(".mesh.lucidcove.org"):
            egress = "mesh_base"

    return {
        "ok": len(results) > 0,
        "items": results,
        "errors": errors,
        "count": len(results),
        "cloud_base": public_base,
        "expire_days": expire_days,
        "mode": "direct_cloud",
        "egress": egress,
    }


@router.get("/api/files/pack/progress")
async def get_pack_progress(request: Request, path: str = DEFAULT_VIDEO_SHORTS):
    """Return last-pulled watermark for this presence + pack folder.

    Stored in the operator browser primarily; this endpoint is a thin optional
    server mirror when we later persist — for now reads nothing durable and
    returns the key the client should use in localStorage.
    """
    clean_path, path_err = _clean_webdav_path(path or DEFAULT_VIDEO_SHORTS)
    if path_err is not None:
        return JSONResponse({"error": path_err}, status_code=400)
    key = pack_progress_key(clean_path or DEFAULT_VIDEO_SHORTS)
    # Presence id if available
    presence_id = ""
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        if isinstance(p, dict):
            presence_id = str(p.get("id") or p.get("presence_id") or "")
    except Exception:
        presence_id = ""
    if not presence_id:
        try:
            from src.dashboard.routes.nextcloud import get_nc_creds
            _, u, _ = await get_nc_creds(request)
            presence_id = (u or "")
        except Exception:
            presence_id = ""
    storage_key = f"cove.packProgress.{presence_id or 'local'}.{key}"
    return {
        "path": clean_path,
        "progress_key": key,
        "storage_key": storage_key,
        "presence_id": presence_id,
        "note": "Client stores last_downloaded_path + last_modified in localStorage under storage_key.",
    }

