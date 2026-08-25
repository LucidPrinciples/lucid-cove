"""Presence Bookkeeping P&L + ledger + vendor map.

GET   /books                    statement P&L page
GET   /api/books/summary        category rollup (optional year/month)
GET   /api/books/lines          transactions for one category
PATCH /api/books/line           set one category from the working chart
PATCH /api/books/lines          set many categories at once
GET   /api/books/map            vendor/phrase rules
PUT   /api/books/map            replace rules; apply to uncategorized
POST  /api/books/seed-map       fill map from already-placed payees

Bytes live in the logged-in Presence's Bookkeeping/Organize/*.mapped.json.
Do not log amounts, payees, or statement text.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.dashboard import books as bk

logger = logging.getLogger("bookkeeping")

router = APIRouter()

STATIC_PAGE = Path(__file__).resolve().parents[1] / "static" / "books.html"
MAX_LEDGER_BYTES = 2_000_000


async def _webdav(request: Request, path: str):
    from src.dashboard.routes.files import _resolve_webdav

    return await _resolve_webdav(request, path)


def _quote_path(path: str) -> str:
    return quote(path, safe="/")


async def _list_mapped_ledgers(request: Request) -> tuple[list[str], str | None]:
    webdav_base, _user, auth, werr = await _webdav(request, bk.organize_dir())
    if werr or not webdav_base or not auth:
        return [], werr or "Nextcloud not configured"
    url = f"{webdav_base}/{_quote_path(bk.organize_dir())}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.request("PROPFIND", url, headers={"Depth": "1"})
    except Exception as e:
        logger.warning("books list failed")
        return [], f"List failed: {e}"
    if resp.status_code == 404:
        return [], None
    if resp.status_code not in (207, 200):
        return [], f"WebDAV error: HTTP {resp.status_code}"
    names: list[str] = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return [], "Could not read Organize folder"
    ns = "{DAV:}"
    for node in root.findall(f"{ns}response"):
        href = unquote((node.findtext(f"{ns}href") or ""))
        name = href.rstrip("/").split("/")[-1]
        if node.find(f".//{ns}collection") is not None:
            continue
        if bk.is_mapped_name(name):
            names.append(name)
    names.sort()
    return names[: bk.MAX_LEDGER_LIST], None


async def _resolve_ledger_path(request: Request, path: str) -> tuple[str | None, list[str], str | None, int]:
    names, lerr = await _list_mapped_ledgers(request)
    if lerr:
        return None, names, lerr, 502
    if path:
        clean, err = bk.clean_books_path(path)
        if err or not clean:
            return None, names, err or "Invalid path", 400
        return clean, names, None, 200
    picked = bk.pick_default_ledger(names)
    if not picked:
        return None, names, "No mapped ledger in Bookkeeping/Organize", 404
    return picked, names, None, 200


async def _read_ledger(request: Request, path: str) -> tuple[dict | None, str, str | None, int]:
    clean, err = bk.clean_books_path(path)
    if err or not clean:
        return None, path, err or "Invalid path", 400
    webdav_base, _user, auth, werr = await _webdav(request, clean)
    if werr or not webdav_base or not auth:
        return None, clean, werr or "Nextcloud not configured", 503
    url = f"{webdav_base}/{_quote_path(clean)}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.get(url)
    except Exception as e:
        logger.warning("books read failed")
        return None, clean, f"Read failed: {e}", 502
    if resp.status_code == 404:
        return None, clean, f"Not found: {clean}", 404
    if resp.status_code != 200:
        return None, clean, f"WebDAV error: HTTP {resp.status_code}", 502
    if len(resp.content) > MAX_LEDGER_BYTES:
        return None, clean, "Ledger file is too large", 413
    try:
        data = json.loads(resp.content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, clean, "Ledger must be UTF-8 JSON", 415
    if not isinstance(data, dict):
        return None, clean, "Ledger JSON must be an object", 400
    return data, clean, None, 200


async def _write_ledger(request: Request, path: str, payload: dict) -> tuple[str | None, int]:
    clean, err = bk.clean_books_path(path)
    if err or not clean:
        return err or "Invalid path", 400
    from src.dashboard.routes.files import _kb_write_guard, _operator_shared_agent_guard

    guard = await _kb_write_guard(request, clean)
    if guard:
        return guard, 403
    webdav_base, webdav_user, auth, werr = await _webdav(request, clean)
    if werr or not webdav_base or not auth:
        return werr or "Nextcloud not configured", 503
    oguard = await _operator_shared_agent_guard(request, clean, webdav_user or "")
    if oguard:
        return oguard, 403
    url = f"{webdav_base}/{_quote_path(clean)}"
    body = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    if len(body) > MAX_LEDGER_BYTES:
        return "Ledger file is too large", 413
    try:
        async with httpx.AsyncClient(auth=auth, timeout=60.0) as client:
            resp = await client.put(
                url, content=body, headers={"Content-Type": "application/json"}
            )
    except Exception as e:
        logger.warning("books write failed")
        return f"Write failed: {e}", 502
    if resp.status_code not in (200, 201, 204):
        return f"Write failed: HTTP {resp.status_code}", 502
    return None, 200


def _parse_period(request: Request) -> tuple[int | None, int | None, str | None]:
    year_raw = (request.query_params.get("year") or "").strip()
    month_raw = (request.query_params.get("month") or "").strip()
    year = None
    month = None
    if year_raw:
        try:
            year = int(year_raw)
        except ValueError:
            return None, None, "year must be an integer"
        if year < 1990 or year > 2100:
            return None, None, "year out of range"
    if month_raw:
        try:
            month = int(month_raw)
        except ValueError:
            return None, None, "month must be an integer"
        if month < 1 or month > 12:
            return None, None, "month must be 1-12"
        if year is None:
            return None, None, "month requires year"
    return year, month, None


@router.get("/books", response_class=HTMLResponse)
async def serve_books_page():
    if not STATIC_PAGE.is_file():
        return HTMLResponse("Bookkeeping page not found", status_code=404)
    return HTMLResponse(STATIC_PAGE.read_text(encoding="utf-8"))


@router.get("/api/books/summary")
async def books_summary(request: Request, path: str = ""):
    year, month, perr = _parse_period(request)
    if perr:
        return JSONResponse({"ok": False, "error": perr}, status_code=400)
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr, "ledgers": ledgers}, status_code=rstatus)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean, "ledgers": ledgers}, status_code=status)
    seeded = 0
    if not bk.vendor_map_from_payload(payload):
        payload, stats, serr = bk.seed_vendor_map_from_placed(payload)
        if payload is not None and not serr and stats and stats.get("added"):
            payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            werr, _wstatus = await _write_ledger(request, clean, payload)
            if not werr:
                seeded = int(stats.get("added") or 0)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    chart = bk.working_chart_from_payload(payload)
    pnl = bk.pnl_from_rows(rows, year=year, month=month)
    periods = bk.available_periods(rows)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "entity": payload.get("entity") or "",
        "account": bk.account_label(payload, clean),
        "row_count": len(rows),
        "needs_review_count": payload.get("needs_review_count"),
        "year": year,
        "month": month,
        "periods": periods,
        "ledgers": [bk.ledger_path_from_name(n) for n in ledgers if bk.ledger_path_from_name(n)],
        "chart": chart,
        "vendor_map": bk.vendor_map_from_payload(payload),
        "seeded": seeded,
        "pnl": pnl,
    })


@router.get("/api/books/lines")
async def books_lines(request: Request, path: str = "", category: str = ""):
    year, month, perr = _parse_period(request)
    if perr:
        return JSONResponse({"ok": False, "error": perr}, status_code=400)
    label = (category or "").strip() or bk.UNCATEGORIZED
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    chart = bk.working_chart_from_payload(payload)
    lines = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if not bk.in_period(row, year, month):
            continue
        if bk.category_label(row) != label:
            continue
        lines.append(bk.line_preview(row, clean, i))
    return JSONResponse({
        "ok": True,
        "path": clean,
        "category": label,
        "year": year,
        "month": month,
        "chart": chart,
        "lines": lines,
        "count": len(lines),
    })


@router.patch("/api/books/line")
async def books_patch_line(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    path = (body.get("path") or "").strip()
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    path = ledger_path
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "index is required"}, status_code=400)
    label = (body.get("category") or body.get("category_label") or "").strip()
    if not label:
        return JSONResponse({"ok": False, "error": "category is required"}, status_code=400)

    payload, clean, err, status = await _read_ledger(request, path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.apply_category(payload, index, label)
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    row = updated["rows"][index]
    return JSONResponse({
        "ok": True,
        "path": clean,
        "line": bk.line_preview(row, clean, index),
        "needs_review_count": updated.get("needs_review_count"),
        "vendor_map": bk.vendor_map_from_payload(updated),
    })


@router.patch("/api/books/lines")
async def books_patch_lines(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    path = (body.get("path") or "").strip()
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    path = ledger_path
    raw_indexes = body.get("indexes") or body.get("indices") or []
    if not isinstance(raw_indexes, list):
        return JSONResponse({"ok": False, "error": "indexes is required"}, status_code=400)
    indexes: list[int] = []
    for item in raw_indexes:
        try:
            indexes.append(int(item))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "indexes must be integers"}, status_code=400)
    if len(indexes) > 200:
        return JSONResponse({"ok": False, "error": "Too many lines in one save"}, status_code=400)
    label = (body.get("category") or body.get("category_label") or "").strip()
    if not label:
        return JSONResponse({"ok": False, "error": "category is required"}, status_code=400)

    payload, clean, err, status = await _read_ledger(request, path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.apply_categories(payload, indexes, label)
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "updated": len(set(indexes)),
        "needs_review_count": updated.get("needs_review_count"),
        "vendor_map": bk.vendor_map_from_payload(updated),
    })


@router.get("/api/books/map")
async def books_get_map(request: Request, path: str = ""):
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "chart": bk.working_chart_from_payload(payload),
        "vendor_map": bk.vendor_map_from_payload(payload),
    })


@router.put("/api/books/map")
async def books_put_map(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    path = (body.get("path") or "").strip()
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    path = ledger_path
    rules = body.get("vendor_map") or body.get("rules") or []
    if not isinstance(rules, list):
        return JSONResponse({"ok": False, "error": "vendor_map must be a list"}, status_code=400)
    if len(rules) > 400:
        return JSONResponse({"ok": False, "error": "Too many map rules"}, status_code=400)

    payload, clean, err, status = await _read_ledger(request, path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.set_vendor_map(payload, rules)
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    applied = {"applied": 0, "skipped_placed": 0}
    if body.get("apply", True):
        updated, applied, aerr = bk.apply_vendor_map(updated)
        if aerr or updated is None:
            return JSONResponse({"ok": False, "error": aerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "vendor_map": bk.vendor_map_from_payload(updated),
        "applied": (applied or {}).get("applied", 0),
        "needs_review_count": updated.get("needs_review_count"),
    })


@router.post("/api/books/seed-map")
async def books_seed_map(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    path = (body.get("path") or "").strip()
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, stats, serr = bk.seed_vendor_map_from_placed(payload)
    if serr or updated is None:
        return JSONResponse({"ok": False, "error": serr, "path": clean}, status_code=400)
    apply_now = body.get("apply", True)
    applied = {"applied": 0, "skipped_placed": 0}
    if apply_now:
        updated, applied, aerr = bk.apply_vendor_map(updated)
        if aerr or updated is None:
            return JSONResponse({"ok": False, "error": aerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "vendor_map": bk.vendor_map_from_payload(updated),
        "seeded": (stats or {}).get("added", 0),
        "kept": (stats or {}).get("kept", 0),
        "applied": (applied or {}).get("applied", 0),
        "needs_review_count": updated.get("needs_review_count"),
    })
