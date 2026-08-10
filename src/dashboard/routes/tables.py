"""CSV table viewer + light edit API (familiar tools v1).

GET  /tables              — static viewer shell (?path=Tables/foo.csv)
GET  /api/tables          — parse + return headers/rows (NC WebDAV)
PUT  /api/tables          — replace full table body
PATCH /api/tables/row     — append or update one row
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.dashboard import csv_tables as ct
from src.env import env

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_VIEWER = (
    Path(__file__).resolve().parents[1] / "static" / "tables" / "viewer.html"
)


async def _webdav_for_path(request: Request, path: str):
    """Reuse Files WebDAV resolution.

    Tables/ is Cove-level (steward/admin NC), same single-object rule as KB —
    so the browser session and agents see one file, not a per-presence 404.
    """
    from src.dashboard.routes.files import _resolve_webdav

    return await _resolve_webdav(request, path)


async def _read_csv_bytes(request: Request, path: str) -> tuple[str | None, str | None, int]:
    """Return (text, error, http_status)."""
    clean, err = ct.normalize_table_path(path)
    if err:
        return None, err, 400
    webdav_base, _user, auth, werr = await _webdav_for_path(request, clean)
    if werr or not webdav_base or not auth:
        return None, werr or "Nextcloud not configured", 503
    url = f"{webdav_base}/{clean}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.get(url)
    except Exception as e:
        logger.warning("tables read failed path=%s err=%s", clean, e)
        return None, f"Read failed: {e}", 502
    if resp.status_code == 404:
        return None, f"Not found: {clean}", 404
    if resp.status_code != 200:
        return None, f"WebDAV error: HTTP {resp.status_code}", 502
    if len(resp.content) > ct.MAX_BYTES:
        return None, f"CSV exceeds {ct.MAX_BYTES} bytes", 413
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "CSV must be UTF-8 text", 415
    return text, None, 200


async def _write_csv_bytes(request: Request, path: str, text: str) -> tuple[str | None, int]:
    """Write CSV text via WebDAV PUT. Returns (error, status)."""
    clean, err = ct.normalize_table_path(path)
    if err:
        return err, 400
    from src.dashboard.routes.files import _kb_write_guard, _operator_shared_agent_guard

    guard = await _kb_write_guard(request, clean)
    if guard:
        return guard, 403

    webdav_base, webdav_user, auth, werr = await _webdav_for_path(request, clean)
    if werr or not webdav_base or not auth:
        return werr or "Nextcloud not configured", 503

    oguard = await _operator_shared_agent_guard(request, clean, webdav_user or "")
    if oguard:
        return oguard, 403

    # Ensure parent collections exist (MKCOL chain).
    parent = "/".join(clean.split("/")[:-1])
    if parent:
        try:
            async with httpx.AsyncClient(auth=auth, timeout=20.0) as client:
                parts = parent.split("/")
                acc = ""
                for seg in parts:
                    acc = f"{acc}/{seg}" if acc else seg
                    mk = await client.request("MKCOL", f"{webdav_base}/{acc}")
                    # 201 created, 405 exists, 301/302 redirects — all ok enough
                    if mk.status_code not in (201, 405, 200, 301, 302, 204):
                        # continue anyway; PUT may still work if parent exists
                        pass
        except Exception as e:
            logger.info("tables MKCOL note path=%s err=%s", parent, e)

    url = f"{webdav_base}/{clean}"
    data = text.encode("utf-8")
    if len(data) > ct.MAX_BYTES:
        return f"CSV exceeds {ct.MAX_BYTES} bytes", 413
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.put(
                url,
                content=data,
                headers={"Content-Type": "text/csv; charset=utf-8"},
            )
    except Exception as e:
        logger.warning("tables write failed path=%s err=%s", clean, e)
        return f"Write failed: {e}", 502
    if resp.status_code not in (200, 201, 204):
        return f"WebDAV write error: HTTP {resp.status_code}", 502
    return None, 200


@router.get("/tables")
async def tables_viewer_page():
    if not STATIC_VIEWER.is_file():
        return HTMLResponse("Table viewer not found", status_code=404)
    return HTMLResponse(STATIC_VIEWER.read_text(encoding="utf-8"))


@router.get("/api/tables")
async def api_get_table(request: Request, path: str = ""):
    text, err, status = await _read_csv_bytes(request, path)
    if err:
        return JSONResponse({"ok": False, "error": err, "path": path}, status_code=status)
    try:
        parsed = ct.parse_csv_text(text or "")
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e), "path": path}, status_code=400)
    clean, _ = ct.normalize_table_path(path)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "viewer_url": ct.viewer_url(clean or path),
        "headers": parsed["headers"],
        "rows": parsed["rows"],
        "row_count": parsed["row_count"],
        "col_count": parsed["col_count"],
        "truncated": parsed["truncated"],
        "editable": True,
    })


@router.put("/api/tables")
async def api_put_table(request: Request):
    """Replace entire table. Body: {path, headers, rows} or {path, csv_text}."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON body required"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "JSON object required"}, status_code=400)
    path = body.get("path") or ""
    clean, err = ct.normalize_table_path(path)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    if "csv_text" in body and body.get("csv_text") is not None:
        try:
            parsed = ct.parse_csv_text(str(body.get("csv_text") or ""))
            csv_text = ct.serialize_csv(parsed["headers"], parsed["rows"])
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    else:
        headers = body.get("headers")
        rows = body.get("rows")
        if not isinstance(headers, list) or not isinstance(rows, list):
            return JSONResponse(
                {"ok": False, "error": "headers and rows arrays required (or csv_text)"},
                status_code=400,
            )
        try:
            csv_text = ct.serialize_csv(
                [str(h) for h in headers],
                [[str(c) for c in (r or [])] for r in rows if isinstance(r, (list, tuple))],
            )
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    werr, status = await _write_csv_bytes(request, clean, csv_text)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=status)
    parsed = ct.parse_csv_text(csv_text)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "viewer_url": ct.viewer_url(clean),
        "headers": parsed["headers"],
        "rows": parsed["rows"],
        "row_count": parsed["row_count"],
        "col_count": parsed["col_count"],
    })


@router.patch("/api/tables/row")
async def api_patch_row(request: Request):
    """Append or update one row. Body: path, values, optional row_index, append."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON body required"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "JSON object required"}, status_code=400)
    path = body.get("path") or ""
    clean, err = ct.normalize_table_path(path)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    text, rerr, status = await _read_csv_bytes(request, clean)
    if rerr and status == 404:
        # Creating via append: need headers
        headers = body.get("headers")
        if not isinstance(headers, list) or not headers:
            return JSONResponse(
                {"ok": False, "error": "Table not found; pass headers to create"},
                status_code=404,
            )
        parsed = {"headers": [str(h) for h in headers], "rows": []}
    elif rerr:
        return JSONResponse({"ok": False, "error": rerr, "path": clean}, status_code=status)
    else:
        try:
            parsed = ct.parse_csv_text(text or "")
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    append = bool(body.get("append"))
    row_index = body.get("row_index", None)
    if row_index is not None and not append:
        try:
            row_index = int(row_index)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "row_index must be an integer"}, status_code=400)
    else:
        row_index = None if append or row_index is None else int(row_index)

    values = body.get("values")
    try:
        headers, rows = ct.apply_row_update(
            parsed["headers"],
            parsed["rows"],
            row_index=None if append else row_index,
            values=values,
            append=append or row_index is None,
        )
        csv_text = ct.serialize_csv(headers, rows)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    werr, wstatus = await _write_csv_bytes(request, clean, csv_text)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    out = ct.parse_csv_text(csv_text)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "viewer_url": ct.viewer_url(clean),
        "headers": out["headers"],
        "rows": out["rows"],
        "row_count": out["row_count"],
        "col_count": out["col_count"],
    })
