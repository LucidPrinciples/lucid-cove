"""Presence Bookkeeping P&L + ledger + vendor map.

GET   /books                    statement P&L page
GET   /api/books/summary        category rollup (optional year/month)
POST  /api/books/setup          first-open book + ledger when Organize is empty
GET   /api/books/lines          transactions for one category
PATCH /api/books/line           set one category from the working chart
PATCH /api/books/lines          set many categories at once
GET   /api/books/map            vendor/phrase rules
PUT   /api/books/map            replace rules; apply to uncategorized
POST  /api/books/seed-map       fill map from already-placed payees
POST  /api/books/account        create a blank ledger + Drop folder
POST  /api/books/process        ingest CSV/text/PDF from that ledger's Drop folder
POST  /api/books/line           add a typed line on the Manual book
POST  /api/books/lines/paste    bulk-add copied statement rows on the Manual book
PATCH /api/books/line/state     disable or restore a statement or typed line
PATCH /api/books/line/book      move a line onto a filing book
DELETE /api/books/line          remove a typed line only

Bytes live in the logged-in Presence's Bookkeeping/Organize/*.mapped.json.
Do not log amounts, payees, or statement text.
"""

from __future__ import annotations

import asyncio
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
_TRANSIENT_DAV = {423, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (0.4, 0.8, 1.6, 3.2)


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
    if bk.is_all_transactions(path):
        if not names:
            return None, names, "No mapped ledger in Bookkeeping/Organize", 404
        return bk.ALL_TRANSACTIONS, names, None, 200
    if bk.is_manual_ledger(path):
        return bk.MANUAL_LEDGER_PATH, names, None, 200
    if not names:
        return None, names, "No mapped ledger in Bookkeeping/Organize", 404
    if path:
        clean, err = bk.clean_books_path(path)
        if err or not clean:
            return None, names, err or "Invalid path", 400
        return clean, names, None, 200
    return bk.ALL_TRANSACTIONS, names, None, 200


async def _load_named_ledgers(request: Request, names: list[str]) -> list[tuple[str, dict]]:
    loaded: list[tuple[str, dict]] = []
    for name in names:
        path = bk.ledger_path_from_name(name)
        if not path:
            continue
        payload, clean, err, _status = await _read_ledger(request, path)
        if err or payload is None:
            continue
        loaded.append((clean, payload))
    return loaded


async def _maybe_seed(request: Request, payload: dict, clean: str) -> tuple[dict, int]:
    if bk.vendor_map_from_payload(payload):
        return payload, 0
    seeded_payload, stats, serr = bk.seed_vendor_map_from_placed(payload)
    if seeded_payload is None or serr or not stats or not stats.get("added"):
        return payload, 0
    seeded_payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, _wstatus = await _write_ledger(request, clean, seeded_payload)
    if werr:
        return payload, 0
    return seeded_payload, int(stats.get("added") or 0)


def _choice_payloads(loaded: list[tuple[str, dict]]) -> list[dict]:
    labels: dict[str, str] = {}
    for path, payload in loaded:
        labels[path] = bk.account_label(payload, path)
    names = [p.split("/")[-1] for p, _ in loaded]
    return bk.ledger_choices(names, labels)


def _chart_with_accounts(
    loaded: list[tuple[str, dict]],
    current_path: str = "",
    current_payload: dict | None = None,
) -> list[dict]:
    others: list[str] = []
    seen: set[str] = set()
    current = (current_path or "").replace("\\", "/").strip().lower()
    skip_current = bool(current) and not bk.is_all_transactions(current_path)
    for path, payload in loaded:
        if bk.is_manual_ledger(path):
            continue
        if skip_current and path.replace("\\", "/").strip().lower() == current:
            continue
        lab = bk.account_label(payload, path)
        key = lab.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        others.append(lab)
    if skip_current and current_payload is not None:
        base = bk.working_chart_from_payload(current_payload)
        self_label = bk.account_label(current_payload, current_path)
        return bk.merge_chart_with_accounts(base, others, exclude_labels=[self_label])
    payloads = [payload for _p, payload in loaded]
    return bk.merge_chart_with_accounts(bk.merge_working_charts(payloads), others)



async def _ensure_manual_ledger(request: Request, loaded: list[tuple[str, dict]]) -> tuple[str, dict, str | None, int]:
    wanted = next((item for item in loaded if item[0] == bk.MANUAL_LEDGER_PATH), None)
    if wanted is not None:
        return wanted[0], wanted[1], None, 200
    source = loaded[0][1] if loaded else {}
    payload = bk.empty_manual_payload(source)
    payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, bk.MANUAL_LEDGER_PATH, payload)
    if werr:
        return bk.MANUAL_LEDGER_PATH, payload, werr, wstatus
    return bk.MANUAL_LEDGER_PATH, payload, None, 200


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
    last = "no response"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=60.0) as client:
            for delay in (0.0, *_RETRY_DELAYS):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    resp = await client.put(
                        url, content=body, headers={"Content-Type": "application/json"}
                    )
                except httpx.TimeoutException as e:
                    last = f"timeout: {e}"
                    continue
                if resp.status_code in (200, 201, 204):
                    return None, 200
                last = f"HTTP {resp.status_code}"
                if resp.status_code not in _TRANSIENT_DAV:
                    break
    except Exception as e:
        logger.warning("books write failed")
        return f"Write failed: {e}", 502
    return f"Write failed: {last}", 502


async def _mkcol(request: Request, path: str) -> tuple[str | None, int]:
    rel = (path or "").replace("\\", "/").strip().strip("/")
    if not rel or ".." in rel.split("/") or not rel.startswith("Bookkeeping"):
        return "Invalid path", 400
    webdav_base, _user, auth, werr = await _webdav(request, rel)
    if werr or not webdav_base or not auth:
        return werr or "Nextcloud not configured", 503
    url = f"{webdav_base}/{_quote_path(rel)}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.request("MKCOL", url)
    except Exception as e:
        logger.warning("books mkdir failed")
        return f"Folder create failed: {e}", 502
    if resp.status_code in (201, 204, 405):
        return None, 200
    return f"Folder create failed: HTTP {resp.status_code}", 502


async def _ensure_drop_folder(request: Request, folder: str) -> tuple[str | None, int]:
    parts = [p for p in (folder or "").replace("\\", "/").split("/") if p]
    if not parts or parts[0] != "Bookkeeping":
        return "Invalid path", 400
    built = ""
    for part in parts:
        built = f"{built}/{part}" if built else part
        err, status = await _mkcol(request, built)
        if err:
            return err, status
    return None, 200


async def _list_drop_files(request: Request, folder: str) -> tuple[list[str], str | None]:
    rel = (folder or "").replace("\\", "/").strip().strip("/")
    if not rel.startswith(bk.DROP_PREFIX):
        return [], "Invalid path"
    webdav_base, _user, auth, werr = await _webdav(request, rel)
    if werr or not webdav_base or not auth:
        return [], werr or "Nextcloud not configured"
    url = f"{webdav_base}/{_quote_path(rel)}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.request("PROPFIND", url, headers={"Depth": "1"})
    except Exception as e:
        logger.warning("books drop list failed")
        return [], f"List failed: {e}"
    if resp.status_code == 404:
        return [], None
    if resp.status_code not in (207, 200):
        return [], f"WebDAV error: HTTP {resp.status_code}"
    names: list[str] = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return [], "Could not read Drop folder"
    ns = "{DAV:}"
    folder_name = rel.rstrip("/").split("/")[-1].lower()
    for node in root.findall(f"{ns}response"):
        href = unquote((node.findtext(f"{ns}href") or ""))
        name = href.rstrip("/").split("/")[-1]
        if not name or name.lower() == folder_name:
            continue
        if node.find(f".//{ns}collection") is not None:
            continue
        if bk.drop_file_kind(name):
            names.append(name)
    names.sort()
    return names[: bk.MAX_DROP_FILES], None


async def _read_drop_bytes(request: Request, folder: str, name: str) -> tuple[bytes | None, str | None, int]:
    kind = bk.drop_file_kind(name)
    if kind not in ("text", "pdf", "image"):
        return None, "Not a statement file", 400
    rel = f"{folder.rstrip('/')}/{name.split('/')[-1]}"
    if ".." in rel.split("/"):
        return None, "Invalid path", 400
    webdav_base, _user, auth, werr = await _webdav(request, rel)
    if werr or not webdav_base or not auth:
        return None, werr or "Nextcloud not configured", 503
    url = f"{webdav_base}/{_quote_path(rel)}"
    try:
        async with httpx.AsyncClient(auth=auth, timeout=60.0) as client:
            resp = await client.get(url)
    except Exception as e:
        logger.warning("books drop read failed")
        return None, f"Read failed: {e}", 502
    if resp.status_code == 404:
        return None, "Not found", 404
    if resp.status_code != 200:
        return None, f"WebDAV error: HTTP {resp.status_code}", 502
    if len(resp.content) > bk.MAX_DROP_FILE_BYTES:
        return None, "Statement file is too large", 413
    return resp.content, None, 200


def _parse_book(request: Request, body: dict | None = None) -> str:
    raw = ""
    if isinstance(body, dict):
        raw = str(body.get("filing_book") or body.get("book") or "").strip()
    if not raw:
        raw = (request.query_params.get("book") or request.query_params.get("filing_book") or "").strip()
    if not raw:
        return "chords"
    return bk.normalize_filing_book(raw, default="chords")


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
    book = _parse_book(request)
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if (rerr or not ledger_path) and rstatus == 404 and not ledgers:
        return JSONResponse({
            "ok": True,
            "needs_setup": True,
            "path": "",
            "entity": "",
            "account": "",
            "filing_book": "",
            "filing_book_label": "",
            "books": bk.filing_book_choices(),
            "row_count": 0,
            "needs_review_count": 0,
            "year": year,
            "month": month,
            "periods": {"years": [], "months": []},
            "ledgers": [],
            "chart": bk.official_schedule_c_chart(),
            "vendor_map": [],
            "seeded": 0,
            "pnl": {"income": [], "expenses": [], "transfers": [], "income_total": 0, "expense_total": 0, "net": 0},
        })
    if rerr or not ledger_path:
        return JSONResponse({
            "ok": False,
            "error": rerr,
            "ledgers": bk.ledger_choices(ledgers),
        }, status_code=rstatus)
    loaded = await _load_named_ledgers(request, ledgers)
    if not loaded:
        return JSONResponse({
            "ok": True,
            "needs_setup": True,
            "path": "",
            "entity": "",
            "account": "",
            "filing_book": "",
            "filing_book_label": "",
            "books": bk.filing_book_choices(),
            "row_count": 0,
            "needs_review_count": 0,
            "year": year,
            "month": month,
            "periods": {"years": [], "months": []},
            "ledgers": [],
            "chart": bk.official_schedule_c_chart(),
            "vendor_map": [],
            "seeded": 0,
            "pnl": {"income": [], "expenses": [], "transfers": [], "income_total": 0, "expense_total": 0, "net": 0},
        })
    seeded = 0
    next_loaded: list[tuple[str, dict]] = []
    for clean, payload in loaded:
        payload, added = await _maybe_seed(request, payload, clean)
        seeded += added
        next_loaded.append((clean, payload))
    loaded = next_loaded
    choices = _choice_payloads(loaded)
    if bk.is_all_transactions(ledger_path):
        all_rows: list[dict] = []
        payloads = []
        review = 0
        entity = ""
        for clean, payload in loaded:
            payloads.append(payload)
            rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
            all_rows.extend(r for r in rows if isinstance(r, dict))
            review += int(payload.get("needs_review_count") or 0)
            if not entity:
                entity = str(payload.get("entity") or "")
        return JSONResponse({
            "ok": True,
            "path": bk.ALL_TRANSACTIONS,
            "entity": entity,
            "account": "All Transactions",
            "filing_book": book,
            "filing_book_label": bk.filing_book_label(book),
            "books": bk.filing_book_choices(payloads),
            "needs_setup": False,
            "row_count": len(all_rows),
            "needs_review_count": review,
            "year": year,
            "month": month,
            "periods": bk.available_periods(all_rows),
            "ledgers": choices,
            "chart": _chart_with_accounts(loaded),
            "vendor_map": bk.merge_vendor_maps(payloads),
            "seeded": seeded,
            "pnl": bk.pnl_from_rows(
                all_rows,
                year=year,
                month=month,
                filing_book=book,
                chart=_chart_with_accounts(loaded),
            ),
        })
    wanted = next((item for item in loaded if item[0] == ledger_path), None)
    if wanted is None:
        return JSONResponse({"ok": False, "error": "Ledger not found", "ledgers": choices}, status_code=404)
    clean, payload = wanted
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return JSONResponse({
        "ok": True,
        "path": clean,
        "entity": payload.get("entity") or "",
        "account": bk.account_label(payload, clean),
        "filing_book": book,
        "filing_book_label": bk.filing_book_label(book),
        "books": bk.filing_book_choices([p for _, p in loaded]),
        "needs_setup": False,
        "row_count": len(rows),
        "needs_review_count": payload.get("needs_review_count"),
        "year": year,
        "month": month,
        "periods": bk.available_periods(rows),
        "ledgers": choices,
        "chart": _chart_with_accounts(loaded, clean, payload),
        "vendor_map": bk.vendor_map_from_payload(payload),
        "seeded": seeded,
        "pnl": bk.pnl_from_rows(
            rows,
            year=year,
            month=month,
            filing_book=book,
            payload=payload,
            chart=_chart_with_accounts(loaded, clean, payload),
        ),
    })


@router.get("/api/books/lines")
async def books_lines(request: Request, path: str = "", category: str = ""):
    year, month, perr = _parse_period(request)
    if perr:
        return JSONResponse({"ok": False, "error": perr}, status_code=400)
    book = _parse_book(request)
    raw_label = (category or "").strip()
    label = raw_label or bk.ALL_LINES
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    loaded = await _load_named_ledgers(request, ledgers)
    if not loaded:
        return JSONResponse({"ok": False, "error": "No mapped ledger in Bookkeeping/Organize"}, status_code=404)
    if bk.is_all_transactions(ledger_path):
        lines: list[dict] = []
        payloads = [payload for _p, payload in loaded]
        for clean, payload in loaded:
            rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
            acct = bk.account_label(payload, clean)
            remain = bk.MAX_COMBINED_LINES - len(lines)
            if remain <= 0:
                break
            lines.extend(bk.collect_lines(
                rows, clean, year=year, month=month, category=label, account=acct, limit=remain,
                filing_book=book, payload=payload,
            ))
        lines.sort(key=lambda x: (x.get("date") or "", x.get("source_path") or "", x.get("index") or 0), reverse=True)
        return JSONResponse({
            "ok": True,
            "path": bk.ALL_TRANSACTIONS,
            "category": label,
            "year": year,
            "month": month,
            "chart": _chart_with_accounts(loaded),
            "lines": lines,
            "count": len(lines),
        })
    wanted = next((item for item in loaded if item[0] == ledger_path), None)
    if wanted is None:
        return JSONResponse({"ok": False, "error": "Ledger not found"}, status_code=404)
    clean, payload = wanted
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    acct = bk.account_label(payload, clean)
    lines = bk.collect_lines(rows, clean, year=year, month=month, category=label, account=acct, filing_book=book, payload=payload)
    lines.sort(key=lambda x: (x.get("date") or "", x.get("index") or 0), reverse=True)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "category": label,
        "year": year,
        "month": month,
        "chart": _chart_with_accounts(loaded, clean, payload),
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
    target = (body.get("source_path") or body.get("path") or "").strip()
    if bk.is_all_transactions(target):
        return JSONResponse({"ok": False, "error": "Pick the statement that owns this line"}, status_code=400)
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, target)
    if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
        return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
    path = ledger_path
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "index is required"}, status_code=400)
    label = (body.get("category") or body.get("category_label") or "").strip()
    details = any(k in body for k in ("date", "payee", "name", "amount", "filing_book"))
    if not label and not details:
        return JSONResponse({"ok": False, "error": "category is required"}, status_code=400)

    payload, clean, err, status = await _read_ledger(request, path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    loaded = await _load_named_ledgers(request, ledgers)
    chart = _chart_with_accounts(loaded, clean, payload)
    if details:
        payee = None
        if "payee" in body or "name" in body:
            payee = body.get("payee") or body.get("name") or ""
        updated, uerr = bk.update_row_details(
            payload,
            index,
            date=body.get("date") if "date" in body else None,
            payee=payee,
            amount=body.get("amount") if "amount" in body else None,
            category=label or None,
            filing_book=body.get("filing_book") if "filing_book" in body else None,
            chart=chart,
        )
    else:
        updated, uerr = bk.apply_category(payload, index, label, chart=chart)
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
    label = (body.get("category") or body.get("category_label") or "").strip()
    if not label:
        return JSONResponse({"ok": False, "error": "category is required"}, status_code=400)
    raw_items = body.get("items")
    groups: dict[str, list[int]] = {}
    if isinstance(raw_items, list) and raw_items:
        for item in raw_items:
            if not isinstance(item, dict):
                return JSONResponse({"ok": False, "error": "items must be objects"}, status_code=400)
            src = (item.get("source_path") or item.get("path") or "").strip()
            if bk.is_all_transactions(src):
                return JSONResponse({"ok": False, "error": "Pick the statement that owns this line"}, status_code=400)
            clean_src, _cerr = bk.clean_books_path(src)
            src = clean_src or src
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "indexes must be integers"}, status_code=400)
            groups.setdefault(src, []).append(idx)
    else:
        path = (body.get("source_path") or body.get("path") or "").strip()
        if bk.is_all_transactions(path):
            return JSONResponse({"ok": False, "error": "Pick the statement that owns this line"}, status_code=400)
        raw_indexes = body.get("indexes") or body.get("indices") or []
        if not isinstance(raw_indexes, list):
            return JSONResponse({"ok": False, "error": "indexes is required"}, status_code=400)
        indexes: list[int] = []
        for item in raw_indexes:
            try:
                indexes.append(int(item))
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "indexes must be integers"}, status_code=400)
        groups[path] = indexes
    total = sum(len(v) for v in groups.values())
    if total > 200:
        return JSONResponse({"ok": False, "error": "Too many lines in one save"}, status_code=400)
    if not groups:
        return JSONResponse({"ok": False, "error": "indexes is required"}, status_code=400)

    updated_n = 0
    last_map: list = []
    review = 0
    last_path = ""
    for src, indexes in groups.items():
        ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, src)
        if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
            return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
        payload, clean, err, status = await _read_ledger(request, ledger_path)
        if err or payload is None:
            return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
        loaded = await _load_named_ledgers(request, ledgers)
        chart = _chart_with_accounts(loaded, clean, payload)
        updated, uerr = bk.apply_categories(payload, indexes, label, chart=chart)
        if uerr or updated is None:
            return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
        updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        werr, wstatus = await _write_ledger(request, clean, updated)
        if werr:
            return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
        updated_n += len(set(indexes))
        last_map = bk.vendor_map_from_payload(updated)
        review += int(updated.get("needs_review_count") or 0)
        last_path = clean
    return JSONResponse({
        "ok": True,
        "path": last_path,
        "updated": updated_n,
        "needs_review_count": review,
        "vendor_map": last_map,
    })


@router.get("/api/books/map")
async def books_get_map(request: Request, path: str = ""):
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    loaded = await _load_named_ledgers(request, ledgers)
    if not loaded:
        return JSONResponse({"ok": False, "error": "No mapped ledger in Bookkeeping/Organize"}, status_code=404)
    if bk.is_all_transactions(ledger_path):
        payloads = [p for _c, p in loaded]
        return JSONResponse({
            "ok": True,
            "path": bk.ALL_TRANSACTIONS,
            "chart": bk.merge_working_charts(payloads),
            "vendor_map": bk.merge_vendor_maps(payloads),
        })
    wanted = next((item for item in loaded if item[0] == ledger_path), None)
    if wanted is None:
        return JSONResponse({"ok": False, "error": "Ledger not found"}, status_code=404)
    clean, payload = wanted
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
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    rules = body.get("vendor_map") or body.get("rules") or []
    if not isinstance(rules, list):
        return JSONResponse({"ok": False, "error": "vendor_map must be a list"}, status_code=400)
    if len(rules) > 400:
        return JSONResponse({"ok": False, "error": "Too many map rules"}, status_code=400)
    targets = [ledger_path]
    if bk.is_all_transactions(ledger_path):
        targets = [bk.ledger_path_from_name(n) for n in ledgers]
        targets = [t for t in targets if t]
    applied_n = 0
    review = 0
    last_map: list = []
    last_path = ledger_path
    for target in targets:
        payload, clean, err, status = await _read_ledger(request, target)
        if err or payload is None:
            return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
        updated, uerr = bk.set_vendor_map(payload, rules)
        if uerr or updated is None:
            return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
        applied = {"applied": 0}
        if body.get("apply", True):
            updated, applied, aerr = bk.apply_vendor_map(updated)
            if aerr or updated is None:
                return JSONResponse({"ok": False, "error": aerr, "path": clean}, status_code=400)
        updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        werr, wstatus = await _write_ledger(request, clean, updated)
        if werr:
            return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
        applied_n += int((applied or {}).get("applied", 0))
        review += int(updated.get("needs_review_count") or 0)
        last_map = bk.vendor_map_from_payload(updated)
        last_path = clean
    return JSONResponse({
        "ok": True,
        "path": bk.ALL_TRANSACTIONS if bk.is_all_transactions(ledger_path) else last_path,
        "vendor_map": last_map,
        "applied": applied_n,
        "needs_review_count": review,
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
    ledger_path, ledgers, rerr, rstatus = await _resolve_ledger_path(request, path)
    if rerr or not ledger_path:
        return JSONResponse({"ok": False, "error": rerr}, status_code=rstatus)
    targets = [ledger_path]
    if bk.is_all_transactions(ledger_path):
        targets = [bk.ledger_path_from_name(n) for n in ledgers]
        targets = [t for t in targets if t]
    seeded = 0
    applied_n = 0
    review = 0
    last_map: list = []
    last_path = ledger_path
    apply_now = body.get("apply", True)
    for target in targets:
        payload, clean, err, status = await _read_ledger(request, target)
        if err or payload is None:
            return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
        updated, stats, serr = bk.seed_vendor_map_from_placed(payload)
        if serr or updated is None:
            return JSONResponse({"ok": False, "error": serr, "path": clean}, status_code=400)
        applied = {"applied": 0}
        if apply_now:
            updated, applied, aerr = bk.apply_vendor_map(updated)
            if aerr or updated is None:
                return JSONResponse({"ok": False, "error": aerr, "path": clean}, status_code=400)
        updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        werr, wstatus = await _write_ledger(request, clean, updated)
        if werr:
            return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
        seeded += int((stats or {}).get("added") or 0)
        applied_n += int((applied or {}).get("applied") or 0)
        review += int(updated.get("needs_review_count") or 0)
        last_map = bk.vendor_map_from_payload(updated)
        last_path = clean
    return JSONResponse({
        "ok": True,
        "path": bk.ALL_TRANSACTIONS if bk.is_all_transactions(ledger_path) else last_path,
        "vendor_map": last_map,
        "seeded": seeded,
        "applied": applied_n,
        "needs_review_count": review,
    })



@router.post("/api/books/lines/paste")
async def books_paste_lines(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    text = body.get("text") or body.get("paste") or ""
    if not isinstance(text, str):
        return JSONResponse({"ok": False, "error": "Paste is required"}, status_code=400)
    year = body.get("year")
    try:
        year_i = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "year must be an integer"}, status_code=400)
    parsed, errors = bk.parse_pasted_lines(text, default_year=year_i)
    if not parsed:
        return JSONResponse({"ok": False, "error": (errors or ["No rows found"])[0], "skipped": len(errors)}, status_code=400)
    book = _parse_book(request, body)
    if book == bk.ALL_BOOKS:
        book = "pickleball"
    names, lerr = await _list_mapped_ledgers(request)
    if lerr:
        return JSONResponse({"ok": False, "error": lerr}, status_code=502)
    loaded = await _load_named_ledgers(request, names)
    clean, payload, err, status = await _ensure_manual_ledger(request, loaded)
    if err:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    chart = bk.working_chart_from_payload(payload)
    category = (body.get("category") or body.get("category_label") or "").strip()
    income = bool(body.get("income"))
    if income and not category:
        category = bk.gross_receipts_label(chart)
    if category and bk.chart_lookup(chart, category) is None:
        category = ""
    items = []
    for rec in parsed:
        amt = rec["amount"]
        if not income and amt > 0:
            amt = -amt
        if income and amt < 0:
            amt = abs(amt)
        items.append({
            "date": rec["date"],
            "payee": rec["payee"],
            "amount": amt,
            "category": category,
            "filing_book": book,
        })
    updated, added, uerr = bk.add_manual_rows(payload, items, filing_book=book, default_category=category)
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "added": added,
        "skipped": len(errors),
        "filing_book": book,
        "needs_review_count": updated.get("needs_review_count"),
    })


@router.patch("/api/books/line/book")
async def books_line_book(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    target = (body.get("source_path") or body.get("path") or "").strip()
    if bk.is_all_transactions(target):
        return JSONResponse({"ok": False, "error": "Pick the statement that owns this line"}, status_code=400)
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, target)
    if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
        return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "index is required"}, status_code=400)
    book = _parse_book(request, body)
    if book == bk.ALL_BOOKS:
        return JSONResponse({"ok": False, "error": "Pick a filing book"}, status_code=400)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.set_row_filing_book(payload, index, book)
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
    })


@router.post("/api/books/line")
async def books_create_line(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    names, lerr = await _list_mapped_ledgers(request)
    if lerr:
        return JSONResponse({"ok": False, "error": lerr}, status_code=502)
    loaded = await _load_named_ledgers(request, names)
    clean, payload, err, status = await _ensure_manual_ledger(request, loaded)
    if err:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    book = _parse_book(request, body)
    if book == bk.ALL_BOOKS:
        book = "chords"
    updated, line, uerr = bk.add_manual_row(
        payload,
        date=(body.get("date") or "").strip(),
        payee=body.get("payee") or body.get("name") or "",
        amount=body.get("amount"),
        category=(body.get("category") or body.get("category_label") or "").strip(),
        filing_book=book,
    )
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "line": line,
        "needs_review_count": updated.get("needs_review_count"),
    })


@router.patch("/api/books/line/state")
async def books_line_state(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    target = (body.get("source_path") or body.get("path") or "").strip()
    if bk.is_all_transactions(target):
        return JSONResponse({"ok": False, "error": "Pick the statement that owns this line"}, status_code=400)
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, target)
    if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
        return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "index is required"}, status_code=400)
    if "disabled" in body:
        disabled = bool(body.get("disabled"))
    else:
        disabled = True
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.set_row_disabled(payload, index, disabled)
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
    })


@router.delete("/api/books/line")
async def books_delete_line(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    target = (body.get("source_path") or body.get("path") or "").strip()
    if bk.is_all_transactions(target):
        return JSONResponse({"ok": False, "error": "Pick the typed line to remove"}, status_code=400)
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, target)
    if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
        return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
    if not bk.is_manual_ledger(ledger_path):
        return JSONResponse({"ok": False, "error": "Statement lines can be disabled, not removed"}, status_code=400)
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "index is required"}, status_code=400)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    updated, uerr = bk.remove_manual_row(payload, index)
    if uerr or updated is None:
        return JSONResponse({"ok": False, "error": uerr, "path": clean}, status_code=400)
    updated["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, clean, updated)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "removed": True,
        "needs_review_count": updated.get("needs_review_count"),
    })




@router.post("/api/books/setup")
async def books_setup(request: Request):
    """First-open only: name a book, create one empty ledger, seed Schedule C."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    names, lerr = await _list_mapped_ledgers(request)
    if lerr:
        return JSONResponse({"ok": False, "error": lerr}, status_code=502)
    if names:
        return JSONResponse({
            "ok": False,
            "error": "Books already has a ledger",
            "needs_setup": False,
            "ledgers": bk.ledger_choices(names),
        }, status_code=409)
    spec, serr = bk.first_open_setup_spec(
        str(body.get("book") or body.get("book_label") or body.get("name") or ""),
        str(body.get("account") or body.get("ledger") or ""),
    )
    if serr or spec is None:
        return JSONResponse({"ok": False, "error": serr or "Book name is required"}, status_code=400)
    acct = spec["account"]
    for folder in (bk.ORGANIZE_PREFIX, bk.DROP_PREFIX, bk.RETURNS_PREFIX, acct["drop_folder"]):
        ferr, fstatus = await _ensure_drop_folder(request, folder)
        if ferr:
            return JSONResponse({"ok": False, "error": ferr}, status_code=fstatus)
    payload = bk.empty_account_payload(acct["label"], {}, filing_book=spec["book_id"])
    payload["entity"] = spec["book_label"]
    payload["filing_book"] = spec["book_id"]
    payload["working_chart"] = bk.official_schedule_c_chart()
    payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    werr, wstatus = await _write_ledger(request, acct["path"], payload)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": acct["path"]}, status_code=wstatus)
    names.append(acct["filename"])
    return JSONResponse({
        "ok": True,
        "needs_setup": False,
        "path": acct["path"],
        "account": acct["label"],
        "drop_folder": acct["drop_folder"],
        "returns_folder": bk.RETURNS_PREFIX,
        "filing_book": spec["book_id"],
        "filing_book_label": spec["book_label"],
        "ledgers": bk.ledger_choices(names),
        "books": bk.filing_book_choices([payload]),
    })

@router.post("/api/books/account")
async def books_create_account(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    spec, serr = bk.new_account_spec(str(body.get("name") or body.get("label") or ""))
    if serr or spec is None:
        return JSONResponse({"ok": False, "error": serr or "Account name is required"}, status_code=400)
    book = _parse_book(request, body)
    if book == bk.ALL_BOOKS:
        return JSONResponse({"ok": False, "error": "Pick a filing book"}, status_code=400)
    names, lerr = await _list_mapped_ledgers(request)
    if lerr:
        return JSONResponse({"ok": False, "error": lerr}, status_code=502)
    wanted = spec["filename"].lower()
    if any(n.lower() == wanted for n in names):
        return JSONResponse({
            "ok": False,
            "error": "That ledger already exists",
            "path": spec["path"],
        }, status_code=409)
    loaded = await _load_named_ledgers(request, names)
    source = loaded[0][1] if loaded else {}
    payload = bk.empty_account_payload(spec["label"], source, filing_book=book)
    payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ferr, fstatus = await _ensure_drop_folder(request, spec["drop_folder"])
    if ferr:
        return JSONResponse({"ok": False, "error": ferr}, status_code=fstatus)
    werr, wstatus = await _write_ledger(request, spec["path"], payload)
    if werr:
        return JSONResponse({"ok": False, "error": werr, "path": spec["path"]}, status_code=wstatus)
    names.append(spec["filename"])
    return JSONResponse({
        "ok": True,
        "path": spec["path"],
        "account": spec["label"],
        "drop_folder": spec["drop_folder"],
        "filing_book": book,
        "ledgers": bk.ledger_choices(names),
    })


@router.post("/api/books/process")
async def books_process_drop(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    target = (body.get("path") or "").strip()
    if bk.is_all_transactions(target) or bk.is_manual_ledger(target):
        return JSONResponse({"ok": False, "error": "Pick a statement ledger to process"}, status_code=400)
    ledger_path, _ledgers, rerr, rstatus = await _resolve_ledger_path(request, target)
    if rerr or not ledger_path or bk.is_all_transactions(ledger_path):
        return JSONResponse({"ok": False, "error": rerr or "Ledger not found"}, status_code=rstatus if rerr else 400)
    if bk.is_manual_ledger(ledger_path):
        return JSONResponse({"ok": False, "error": "Paste rows on Manual instead"}, status_code=400)
    book = _parse_book(request, body)
    if book == bk.ALL_BOOKS:
        return JSONResponse({"ok": False, "error": "Pick a filing book"}, status_code=400)
    payload, clean, err, status = await _read_ledger(request, ledger_path)
    if err or payload is None:
        return JSONResponse({"ok": False, "error": err, "path": clean}, status_code=status)
    ledger_book_before = bk.infer_filing_book(payload)
    label = bk.account_label(payload, clean)
    folder = bk.drop_folder_for_stem(label)
    if not folder:
        return JSONResponse({"ok": False, "error": "Could not resolve Drop folder"}, status_code=400)
    ferr, fstatus = await _ensure_drop_folder(request, folder)
    if ferr:
        return JSONResponse({"ok": False, "error": ferr}, status_code=fstatus)
    files, lerr = await _list_drop_files(request, folder)
    if lerr:
        return JSONResponse({"ok": False, "error": lerr}, status_code=502)
    year = body.get("year")
    try:
        year_i = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "year must be an integer"}, status_code=400)
    added = 0
    skipped = 0
    pending = 0
    processed = 0
    ocr_used = 0
    last_err = None
    for name in files:
        kind = bk.drop_file_kind(name)
        if kind not in ("text", "pdf", "image"):
            continue
        blob, rerr2, _rstatus2 = await _read_drop_bytes(request, folder, name)
        if rerr2 or blob is None:
            last_err = rerr2
            pending += 1
            continue
        file_year = bk.year_from_drop_name(name, year_i)
        parsed, how, errors = bk.extract_and_parse_statement(
            blob, kind, default_year=file_year
        )
        if how in ("missing", "empty", "error"):
            last_err = {
                "missing": "PDF tools are not in this app image yet",
                "empty": "No readable text in that statement",
                "error": "Could not read that statement",
            }.get(how, "Could not read that statement")
            pending += 1
            continue
        if not parsed:
            last_err = (errors or ["No rows found"])[0]
            pending += 1
            continue
        updated, n_added, n_skipped, uerr = bk.append_imported_rows(
            payload, parsed, source_file=name, filing_book=book
        )
        if uerr or updated is None:
            last_err = uerr
            pending += 1
            continue
        payload = updated
        added += n_added
        skipped += n_skipped
        processed += 1
        if how == "ocr":
            ocr_used += 1
    applied, stamped, aerr = bk.apply_payload_filing_book(payload, book)
    if aerr or applied is None:
        return JSONResponse({"ok": False, "error": aerr or "Pick a filing book", "path": clean}, status_code=400)
    payload = applied
    if added or stamped or ledger_book_before != book:
        payload["filing_book"] = book
        payload["mapped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        werr, wstatus = await _write_ledger(request, clean, payload)
        if werr:
            return JSONResponse({"ok": False, "error": werr, "path": clean}, status_code=wstatus)
    elif processed == 0 and pending == 0 and last_err:
        return JSONResponse({"ok": False, "error": last_err, "path": clean, "drop_folder": folder}, status_code=400)
    return JSONResponse({
        "ok": True,
        "path": clean,
        "account": label,
        "drop_folder": folder,
        "filing_book": book,
        "added": added,
        "skipped": skipped,
        "pending": pending,
        "processed": processed,
        "stamped": stamped,
        "ocr": ocr_used,
        "files": len(files),
        "needs_review_count": payload.get("needs_review_count"),
    })

