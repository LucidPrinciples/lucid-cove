# =============================================================================
# gabs.py — Gabs by Gabe (#GABS-V1 Phase 1 Quick)
#
# Spec: gab-workflow-spec-2026-07-26.md
# Product: paste URL (+ optional context) → Run now | Later → HTML Gab in History.
# v1 runner: Stuart Quick assess (single LLM pass). Full multi-agent is Phase 3.
#
# Jules:Julian :: Gabs:Gabe — MC Tools card + /gabs page.
# =============================================================================

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.env import env

log = logging.getLogger("gabs")

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")

# Hard caps (spec §5)
_MAX_PRIMARY_CHARS = 12000
_MAX_SEARCH_RESULTS = 6
_MAX_SOURCE_FETCH = 3
_MAX_SOURCE_CHARS = 2500
_RUN_TIMEOUT_S = 180
_GABS_NC_HISTORY = "AgentSkills/Gabs/History"

_ASSESS_SYSTEM = """You write a Gab — a short operator assessment of one link for Lucid Cove
(a private family AI / Lucid Principles product). Be direct. No filler. No invented facts.

Return ONLY valid JSON (no markdown fences) with these keys:
{
  "title": "short title for the Gab",
  "what_it_is": "1-3 sentences: what the source is",
  "bottom_line": "1-2 sentences: what matters for Cove, if anything",
  "fit": "matters" | "adjacent" | "noise",
  "key_points": ["bullet", "..."],
  "gaps": ["what is unverified or missing", "..."],
  "sources": [{"title": "...", "url": "..."}],
  "suggested_next": "ignore | field-watch | deep-thread | board-later — plus one short clause why"
}

Rules:
- fit=matters only if it clearly touches Cove product, ops, distribution, or Framework work.
- Prefer sources you were given; do not invent URLs.
- If the primary fetch failed, say so in gaps and still assess from search snippets if any.
"""


async def _get_presence_id(request: Request):
    if COVE_MODE != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        return presence["id"] if presence else None
    except Exception:
        return None


def _presence_filter(presence_id):
    if presence_id:
        return "presence_id = %s", (presence_id,)
    return "(presence_id IS NULL OR presence_id = 0)", ()


def _row(r) -> dict:
    return {
        "id": r["id"],
        "url": r["url"] or "",
        "context": r["context"] or "",
        "mode": r["mode"] or "quick",
        "status": r["status"],
        "title": r["title"] or "",
        "bottom_line": r["bottom_line"] or "",
        "fit": r["fit"] or "",
        "report_html": r["report_html"] or "",
        "report_path": r["report_path"] or "",
        "error": r["error"] or "",
        "sources": _parse_sources(r.get("sources_json")),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else "",
        "started_at": r["started_at"].isoformat() if r.get("started_at") else "",
        "finished_at": r["finished_at"].isoformat() if r.get("finished_at") else "",
    }


def _parse_sources(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _valid_url(url: str) -> tuple[bool, str]:
    u = (url or "").strip()
    if not u:
        return False, "URL required"
    if not re.match(r"^https?://", u, re.I):
        return False, "URL must start with http:// or https://"
    try:
        p = urlparse(u)
        if not p.netloc:
            return False, "URL missing host"
    except Exception:
        return False, "Invalid URL"
    return True, u


def _slug(s: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return (s or "gab")[:max_len]


# ── Research helpers (reuse research_tools SSRF path) ────────────────────────

async def _fetch_primary(url: str) -> str:
    from src.tools.research_tools import fetch_webpage
    try:
        # LangChain tool — prefer .ainvoke
        if hasattr(fetch_webpage, "ainvoke"):
            return await fetch_webpage.ainvoke({"url": url, "max_chars": _MAX_PRIMARY_CHARS})
        return await fetch_webpage.coroutine(url=url, max_chars=_MAX_PRIMARY_CHARS)
    except Exception as e:
        return f"Could not fetch primary: {e}"


async def _search(query: str) -> str:
    from src.tools.research_tools import web_search
    try:
        if hasattr(web_search, "ainvoke"):
            return await web_search.ainvoke({"query": query, "num_results": _MAX_SEARCH_RESULTS})
        return await web_search.coroutine(query=query, num_results=_MAX_SEARCH_RESULTS)
    except Exception as e:
        return f"Search unavailable: {e}"


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _build_html(data: dict, url: str, context: str, gab_id: int) -> str:
    title = html.escape(str(data.get("title") or "Gab"))
    what = html.escape(str(data.get("what_it_is") or ""))
    bottom = html.escape(str(data.get("bottom_line") or ""))
    fit = html.escape(str(data.get("fit") or ""))
    next_s = html.escape(str(data.get("suggested_next") or ""))
    ctx = html.escape(context or "")
    src_url = html.escape(url)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    points = data.get("key_points") or []
    if not isinstance(points, list):
        points = [str(points)]
    gaps = data.get("gaps") or []
    if not isinstance(gaps, list):
        gaps = [str(gaps)]
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        sources = []

    def _li(items):
        if not items:
            return "<li><em>None noted</em></li>"
        return "".join(f"<li>{html.escape(str(x))}</li>" for x in items if str(x).strip())

    src_html = []
    for s in sources:
        if isinstance(s, dict):
            t = html.escape(str(s.get("title") or s.get("url") or "source"))
            u = html.escape(str(s.get("url") or ""))
            if u:
                src_html.append(f'<li><a href="{u}" rel="noopener noreferrer">{t}</a></li>')
            else:
                src_html.append(f"<li>{t}</li>")
        else:
            src_html.append(f"<li>{html.escape(str(s))}</li>")
    if not src_html:
        src_html.append(f'<li><a href="{src_url}" rel="noopener noreferrer">{src_url}</a></li>')

    fit_color = {
        "matters": "#2ecc71",
        "adjacent": "#f39c12",
        "noise": "#888",
    }.get((data.get("fit") or "").lower(), "#7eb8da")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gab — {title}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0a0f;color:#e0e0e0;
  max-width:720px;margin:0 auto;padding:24px 18px 48px;line-height:1.55}}
h1{{font-size:1.35rem;margin:0 0 6px;color:#fff}}
.meta{{color:#666;font-size:0.8rem;margin-bottom:18px}}
.brand{{color:#7eb8da;font-size:0.78rem;letter-spacing:.04em;margin-bottom:10px}}
.fit{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.75rem;
  font-weight:600;border:1px solid {fit_color};color:{fit_color};margin-bottom:14px}}
section{{margin:18px 0;padding:14px 16px;background:#111118;border-radius:10px;border:1px solid #1a1a2e}}
h2{{font-size:0.72rem;text-transform:uppercase;letter-spacing:.08em;color:#7eb8da;margin:0 0 8px}}
p{{margin:0 0 8px}}
ul{{margin:0;padding-left:1.2rem}}
li{{margin:4px 0}}
a{{color:#7eb8da}}
.bottom{{font-size:1.02rem;color:#fff}}
</style>
</head>
<body>
<div class="brand">Gabs by Gabe · Quick · #{gab_id}</div>
<h1>{title}</h1>
<div class="meta">{now}<br>Source: <a href="{src_url}" rel="noopener noreferrer">{src_url}</a>
{f"<br>Context: {ctx}" if ctx else ""}</div>
<div class="fit">fit: {fit or "—"}</div>
<section><h2>What it is</h2><p>{what or "—"}</p></section>
<section><h2>Bottom line for Cove</h2><p class="bottom">{bottom or "—"}</p></section>
<section><h2>Key points</h2><ul>{_li(points)}</ul></section>
<section><h2>Gaps / unverified</h2><ul>{_li(gaps)}</ul></section>
<section><h2>Sources</h2><ul>{"".join(src_html)}</ul></section>
<section><h2>Suggested next</h2><p>{next_s or "—"}</p></section>
</body>
</html>
"""


async def _mirror_nc_creds(
    nc_url: str, nc_user: str, nc_pass: str, filename: str, content: str
) -> str:
    """Best-effort NC History mirror. Returns path or empty."""
    if not nc_url or not nc_user or not nc_pass:
        return ""
    try:
        import httpx
        base = f"{nc_url.rstrip('/')}/remote.php/dav/files/{nc_user}"
        async with httpx.AsyncClient(timeout=30, auth=(nc_user, nc_pass)) as client:
            for folder in ("AgentSkills", "AgentSkills/Gabs", _GABS_NC_HISTORY):
                await client.request("MKCOL", f"{base}/{folder}")
            path = f"{_GABS_NC_HISTORY}/{filename}"
            resp = await client.put(
                f"{base}/{quote(path)}",
                content=content.encode("utf-8"),
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
            if resp.status_code in (200, 201, 204):
                return path
            log.warning("gabs NC mirror HTTP %s", resp.status_code)
    except Exception as e:
        log.warning("gabs NC mirror failed: %s", e)
    return ""


async def _run_quick(
    gab_id: int,
    url: str,
    context: str,
    nc_creds: tuple[str, str, str] | None = None,
):
    """Background Quick pipeline. Updates DB; never raises to caller."""
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE gabs SET status=%s, started_at=NOW(), updated_at=NOW(), error='' WHERE id=%s",
                ("running", gab_id),
            )

        primary = await _fetch_primary(url)
        host = _host_of(url)
        q = context.strip() if context.strip() else f"what is {host or url}"
        search = await _search(q[:200])

        user_blob = (
            f"PRIMARY URL: {url}\n"
            f"OPERATOR CONTEXT: {context or '(none)'}\n\n"
            f"=== PRIMARY FETCH ===\n{primary[:_MAX_PRIMARY_CHARS]}\n\n"
            f"=== SEARCH ===\n{search[:6000]}\n"
        )

        from langchain_core.messages import HumanMessage, SystemMessage
        from src.models.provider import invoke_with_fallback

        raw = await asyncio.wait_for(
            invoke_with_fallback(
                [
                    SystemMessage(content=_ASSESS_SYSTEM),
                    HumanMessage(content=user_blob),
                ],
                temperature=0.3,
                timeout=120,
                label=f"gabs/quick#{gab_id}",
                agent_id="stuart",
                operation_type="task",
            ),
            timeout=_RUN_TIMEOUT_S,
        )
        if not raw or not str(raw).strip():
            raise RuntimeError("empty model response")

        text = str(raw).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise RuntimeError("model did not return JSON object")
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            raise RuntimeError("JSON root not object")

        fit = str(data.get("fit") or "").lower().strip()
        if fit not in ("matters", "adjacent", "noise"):
            fit = "adjacent"
            data["fit"] = fit

        report_html = _build_html(data, url, context, gab_id)
        title = str(data.get("title") or host or "Gab")[:200]
        bottom = str(data.get("bottom_line") or "")[:1000]
        sources = data.get("sources") if isinstance(data.get("sources"), list) else []
        if not any(isinstance(s, dict) and s.get("url") == url for s in sources):
            sources = [{"title": title, "url": url}] + list(sources)

        report_path = ""
        if nc_creds:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            fname = f"{day}-{_slug(title)}-{gab_id}.html"
            report_path = await _mirror_nc_creds(
                nc_creds[0], nc_creds[1], nc_creds[2], fname, report_html
            )

        async with get_db() as conn:
            await conn.execute(
                """UPDATE gabs SET
                    status=%s, title=%s, bottom_line=%s, fit=%s,
                    report_html=%s, report_path=%s, sources_json=%s,
                    error='', finished_at=NOW(), updated_at=NOW()
                   WHERE id=%s""",
                (
                    "done",
                    title,
                    bottom,
                    fit,
                    report_html,
                    report_path or "",
                    json.dumps(sources)[:8000],
                    gab_id,
                ),
            )
        log.info("gabs #%s done fit=%s title=%s", gab_id, fit, title[:60])
    except Exception as e:
        log.exception("gabs #%s failed: %s", gab_id, e)
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE gabs SET status=%s, error=%s, finished_at=NOW(), updated_at=NOW()
                       WHERE id=%s""",
                    ("failed", str(e)[:500], gab_id),
                )
        except Exception:
            log.exception("gabs #%s failed to record error", gab_id)


async def _schedule_run(gab_id: int, url: str, context: str, request: Request):
    """Snapshot NC creds then fire-and-forget Quick run."""
    nc_creds = None
    try:
        from src.dashboard.routes.nextcloud import get_nc_creds
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        if nc_user and nc_pass:
            nc_creds = (nc_url or "", nc_user, nc_pass)
    except Exception:
        pass

    async def _runner():
        await _run_quick(gab_id, url, context, nc_creds=nc_creds)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        asyncio.ensure_future(_runner())


# =============================================================================
# Page
# =============================================================================

@router.get("/gabs", response_class=HTMLResponse)
async def serve_gabs(request: Request):
    static_dir = Path(__file__).parent.parent / "static"
    path = static_dir / "gabs.html"
    if not path.exists():
        return HTMLResponse("<h1>Gabs not found</h1>", status_code=404)
    return HTMLResponse(path.read_text())


# =============================================================================
# API
# =============================================================================

@router.get("/api/gabs")
async def list_gabs(request: Request, status: str = ""):
    """List Gabs for this presence. status=queued|running|done|failed or empty=all open+recent."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)

    try:
        async with get_db() as conn:
            if status:
                st = status.strip().lower()
                r = await conn.execute(
                    f"""SELECT * FROM gabs WHERE {where} AND status = %s
                        ORDER BY updated_at DESC LIMIT 100""",
                    params + (st,),
                )
            else:
                # To process + recent History
                r = await conn.execute(
                    f"""SELECT * FROM gabs WHERE {where}
                        ORDER BY
                          CASE status
                            WHEN 'running' THEN 0
                            WHEN 'queued' THEN 1
                            WHEN 'failed' THEN 2
                            WHEN 'done' THEN 3
                            ELSE 4
                          END,
                          updated_at DESC
                        LIMIT 100""",
                    params,
                )
            rows = await r.fetchall()
    except Exception as e:
        log.warning("list gabs: %s", e)
        return {"items": [], "error": "gabs table missing or DB error — run migration 041"}

    return {"items": [_row(x) for x in rows]}


@router.get("/api/gabs/{gab_id}")
async def get_gab(request: Request, gab_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM gabs WHERE id = %s AND {where}",
                (gab_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "item": _row(row)}


@router.post("/api/gabs/capture")
async def capture_gab(request: Request):
    """
    Capture a Gab.
    Body: { url, context?, run_now?: bool, mode?: "quick" }
    run_now false → Later (queued). true → running + background Quick.
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    ok, url_or_err = _valid_url(body.get("url") or "")
    if not ok:
        return JSONResponse({"ok": False, "error": url_or_err}, status_code=400)
    url = url_or_err
    context = (body.get("context") or "").strip()[:2000]
    run_now = bool(body.get("run_now", True))
    mode = (body.get("mode") or "quick").strip().lower()
    if mode != "quick":
        # Phase 1: only Quick. Full reserved.
        mode = "quick"

    presence_id = await _get_presence_id(request)
    status = "queued" if not run_now else "queued"  # flip to running in worker

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO gabs (presence_id, url, context, mode, status, title)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    presence_id,
                    url,
                    context,
                    mode,
                    "queued",
                    _host_of(url) or "Gab",
                ),
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("capture insert failed")
        return JSONResponse(
            {"ok": False, "error": f"DB error (migration 041 applied?): {e}"},
            status_code=500,
        )

    item = _row(row)
    if run_now:
        await _schedule_run(item["id"], url, context, request)
        item["status"] = "running"  # optimistic for UI

    return {"ok": True, "item": item, "run_now": run_now}


@router.post("/api/gabs/{gab_id}/run")
async def run_gab(request: Request, gab_id: int):
    """Start Quick on a queued/failed Gab (Later → process)."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM gabs WHERE id = %s AND {where}",
                (gab_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if row["status"] == "running":
        return {"ok": True, "item": _row(row), "note": "already running"}
    if row["status"] == "done":
        return JSONResponse({"ok": False, "error": "already done — capture a new Gab"}, status_code=400)

    await _schedule_run(row["id"], row["url"], row["context"] or "", request)
    return {"ok": True, "item": {**_row(row), "status": "running"}}


@router.post("/api/gabs/{gab_id}/cancel")
async def cancel_gab(request: Request, gab_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"""UPDATE gabs SET status='cancelled', updated_at=NOW()
                    WHERE id = %s AND {where} AND status IN ('queued','failed','running')
                    RETURNING *""",
                (gab_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found or not cancellable"}, status_code=404)
    return {"ok": True, "item": _row(row)}
