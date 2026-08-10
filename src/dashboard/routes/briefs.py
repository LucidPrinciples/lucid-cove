"""Briefs — operator-facing readable docs (brief → plan → spec).

Markdown remains the source of truth (on disk under DATA_DIR/briefs, optional
vault/NC path for power users). The default operator surface is an in-Cove
reader with calm typography. Agents publish via tools; chat and Links both
deep-link to /briefs/{slug}.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.env import env

router = APIRouter()

_KINDS = ("brief", "plan", "spec")
_STATUSES = ("draft", "active", "archived")

DATA_DIR = Path(env("DATA_DIR", "/app/data"))
BRIEFS_ROOT = DATA_DIR / "briefs"
BRIEFS_DOCS = BRIEFS_ROOT / "docs"
BRIEFS_INDEX = BRIEFS_ROOT / "index.json"
STATIC_READER = (
    Path(__file__).resolve().parents[1] / "static" / "briefs" / "reader.html"
)
STATIC_LIBRARY = (
    Path(__file__).resolve().parents[1] / "static" / "briefs" / "library.html"
)
VAULT_ROOT = Path(env("VAULT_DIR", "/vault"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_dirs() -> None:
    BRIEFS_DOCS.mkdir(parents=True, exist_ok=True)
    if not BRIEFS_INDEX.exists():
        BRIEFS_INDEX.write_text(
            json.dumps({"docs": []}, indent=2) + "\n", encoding="utf-8"
        )


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    base = (base or "brief")[:60]
    return base


def _load_index() -> dict:
    _ensure_dirs()
    try:
        data = json.loads(BRIEFS_INDEX.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"docs": []}
        docs = data.get("docs")
        if not isinstance(docs, list):
            data["docs"] = []
        return data
    except Exception:
        return {"docs": []}


def _save_index(data: dict) -> None:
    _ensure_dirs()
    docs = data.get("docs") if isinstance(data, dict) else []
    if not isinstance(docs, list):
        docs = []
    clean = []
    for d in docs[:500]:
        if not isinstance(d, dict):
            continue
        slug = str(d.get("slug") or "").strip()[:80]
        if not slug or not re.match(r"^[a-z0-9][a-z0-9\-]*$", slug):
            continue
        kind = str(d.get("kind") or "brief").strip().lower()
        if kind not in _KINDS:
            kind = "brief"
        status = str(d.get("status") or "active").strip().lower()
        if status not in _STATUSES:
            status = "active"
        proj = str(d.get("project_slug") or "").strip().lower()[:80]
        if proj and not re.match(r"^[a-z0-9][a-z0-9\-]*$", proj):
            proj = ""
        clean.append({
            "slug": slug,
            "title": str(d.get("title") or slug).strip()[:200],
            "kind": kind,
            "status": status,
            "summary": str(d.get("summary") or "").strip()[:400],
            "source_path": str(d.get("source_path") or "").strip()[:500],
            "project_slug": proj,
            "created_at": str(d.get("created_at") or "").strip()[:40],
            "updated_at": str(d.get("updated_at") or "").strip()[:40],
            "published_by": str(d.get("published_by") or "").strip()[:80],
        })
    BRIEFS_INDEX.write_text(
        json.dumps({"docs": clean}, indent=2) + "\n", encoding="utf-8"
    )


def _doc_path(slug: str) -> Path:
    return BRIEFS_DOCS / f"{slug}.md"


def _find_meta(slug: str) -> dict | None:
    slug = (slug or "").strip().lower()
    for d in _load_index().get("docs") or []:
        if isinstance(d, dict) and (d.get("slug") or "").lower() == slug:
            return d
    return None


def _safe_vault_path(rel: str) -> Path | None:
    """Resolve an optional power-user source under VAULT_DIR only."""
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = VAULT_ROOT.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() in (".md", ".markdown", ".txt"):
        return candidate
    return None


def _read_body(meta: dict) -> str:
    """Prefer published body under briefs/docs; fall back to vault source_path."""
    slug = meta.get("slug") or ""
    path = _doc_path(slug)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    src = _safe_vault_path(meta.get("source_path") or "")
    if src is not None:
        return src.read_text(encoding="utf-8")
    return ""


def _expand_csv_embeds(md: str) -> str:
    """Turn ```csv path``` / [[csv:path]] into markdown tables when readable.

    Best-effort: reads from VAULT_DIR only (same confinement as source_path).
    Live NC-backed tables still open via /tables?path=… link in the embed.
    """
    from src.dashboard import csv_tables as ct

    def resolve(path: str):
        # Prefer vault-relative file for published docs that snapshot CSVs
        src = _safe_vault_path(path)
        if src is None:
            # Allow .csv under vault even though _safe_vault_path is md-only
            rel = (path or "").strip().lstrip("/")
            if not rel or ".." in rel.split("/"):
                return None
            root = VAULT_ROOT.resolve()
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            if candidate.is_file() and candidate.suffix.lower() == ".csv":
                src = candidate
            else:
                return None
        try:
            return ct.parse_csv_text(src.read_text(encoding="utf-8-sig"))
        except Exception:
            return None

    return ct.expand_csv_refs_in_markdown(md or "", resolve)


def _render_html(md: str) -> str:
    from src.dashboard.routes.vault import _md_to_html

    expanded = _expand_csv_embeds(md or "")
    return _md_to_html(expanded)


def _unique_slug(title: str, existing: set[str]) -> str:
    base = _slugify(title)
    if base not in existing:
        return base
    suffix = uuid.uuid4().hex[:4]
    candidate = f"{base}-{suffix}"
    while candidate in existing:
        suffix = uuid.uuid4().hex[:4]
        candidate = f"{base}-{suffix}"
    return candidate


def _norm_project_slug(project_slug: str = "") -> str:
    s = (project_slug or "").strip().lower()[:80]
    if s and re.match(r"^[a-z0-9][a-z0-9\-]*$", s):
        return s
    return ""


def publish_doc(
    *,
    title: str,
    content_markdown: str,
    kind: str = "brief",
    summary: str = "",
    source_path: str = "",
    status: str = "active",
    published_by: str = "",
    slug: str | None = None,
    project_slug: str = "",
) -> dict:
    """Create or update a brief/plan/spec. Returns meta dict."""
    _ensure_dirs()
    title = (title or "").strip()[:200] or "Untitled"
    kind = (kind or "brief").strip().lower()
    if kind not in _KINDS:
        kind = "brief"
    status = (status or "active").strip().lower()
    if status not in _STATUSES:
        status = "active"
    summary = (summary or "").strip()[:400]
    source_path = (source_path or "").strip()[:500]
    project_slug = _norm_project_slug(project_slug)
    if source_path and _safe_vault_path(source_path) is None:
        # Keep the path for display only if invalid — do not read it.
        pass

    data = _load_index()
    docs = [d for d in (data.get("docs") or []) if isinstance(d, dict)]
    existing_slugs = {(d.get("slug") or "").lower() for d in docs}

    target = None
    if slug:
        slug_l = slug.strip().lower()
        for d in docs:
            if (d.get("slug") or "").lower() == slug_l:
                target = d
                break
    if target is None and project_slug:
        # One living plan per project: update the linked doc when republishing
        for d in docs:
            if _norm_project_slug(d.get("project_slug") or "") == project_slug:
                if (d.get("status") or "active") == "archived":
                    continue
                target = d
                break
    if target is None:
        # Update by exact title match when republishing the same living doc
        for d in docs:
            if (d.get("title") or "").strip().lower() == title.lower() and (
                d.get("kind") or "brief"
            ) == kind:
                target = d
                break

    now = _now_iso()
    if target is None:
        new_slug = _unique_slug(title, existing_slugs)
        target = {
            "slug": new_slug,
            "title": title,
            "kind": kind,
            "status": status,
            "summary": summary,
            "source_path": source_path,
            "project_slug": project_slug,
            "created_at": now,
            "updated_at": now,
            "published_by": (published_by or "").strip()[:80],
        }
        docs.append(target)
    else:
        target["title"] = title
        target["kind"] = kind
        target["status"] = status
        if summary:
            target["summary"] = summary
        if source_path:
            target["source_path"] = source_path
        if project_slug:
            target["project_slug"] = project_slug
        target["updated_at"] = now
        if published_by:
            target["published_by"] = published_by.strip()[:80]
        new_slug = target["slug"]

    body = content_markdown if content_markdown is not None else ""
    # If body empty but source_path readable, snapshot it into docs/
    if not body.strip() and source_path:
        src = _safe_vault_path(source_path)
        if src is not None:
            body = src.read_text(encoding="utf-8")
    _doc_path(new_slug).write_text(body, encoding="utf-8")

    data["docs"] = docs
    _save_index(data)
    return dict(target)


def brief_for_project(project_slug: str) -> dict | None:
    """Return the active brief/plan/spec linked to a project slug, if any."""
    needle = _norm_project_slug(project_slug)
    if not needle:
        return None
    matches = []
    for d in _load_index().get("docs") or []:
        if not isinstance(d, dict):
            continue
        if _norm_project_slug(d.get("project_slug") or "") != needle:
            continue
        if (d.get("status") or "active") == "archived":
            continue
        matches.append(dict(d))
    if not matches:
        return None
    # Prefer plan/spec over brief; then newest update
    kind_rank = {"spec": 0, "plan": 1, "brief": 2}
    matches.sort(
        key=lambda x: (
            kind_rank.get((x.get("kind") or "brief"), 9),
            x.get("updated_at") or "",
        ),
        reverse=False,
    )
    # sort kind asc (spec first), then updated_at desc
    matches.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    matches.sort(key=lambda x: kind_rank.get((x.get("kind") or "brief"), 9))
    return matches[0]


def promote_doc(slug_or_title: str, to_kind: str) -> tuple[dict | None, str]:
    to_kind = (to_kind or "").strip().lower()
    if to_kind not in _KINDS:
        return None, f"to_kind must be one of: {', '.join(_KINDS)}"
    needle = (slug_or_title or "").strip().lower()
    if not needle:
        return None, "Provide a slug or title."
    data = _load_index()
    docs = [d for d in (data.get("docs") or []) if isinstance(d, dict)]
    target = None
    for d in docs:
        if (d.get("slug") or "").lower() == needle:
            target = d
            break
    if target is None:
        for d in docs:
            if (d.get("title") or "").strip().lower() == needle:
                target = d
                break
    if target is None:
        return None, f"No brief found matching '{slug_or_title}'."
    order = {k: i for i, k in enumerate(_KINDS)}
    cur = target.get("kind") or "brief"
    if order.get(to_kind, 0) < order.get(cur, 0):
        return None, (
            f"Cannot demote {cur} → {to_kind}. "
            "Promote forward only (brief → plan → spec)."
        )
    target["kind"] = to_kind
    target["updated_at"] = _now_iso()
    data["docs"] = docs
    _save_index(data)
    return dict(target), ""


def list_docs(kind: str = "", status: str = "active") -> list[dict]:
    kind = (kind or "").strip().lower()
    status = (status or "").strip().lower()
    out = []
    for d in _load_index().get("docs") or []:
        if not isinstance(d, dict):
            continue
        if kind and (d.get("kind") or "") != kind:
            continue
        if status and status != "all" and (d.get("status") or "active") != status:
            continue
        out.append(dict(d))
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def reader_url(slug: str) -> str:
    return f"/briefs/{slug}"


@router.get("/briefs")
async def briefs_library_page():
    if not STATIC_LIBRARY.is_file():
        return HTMLResponse("Briefs library not found", status_code=404)
    return HTMLResponse(STATIC_LIBRARY.read_text(encoding="utf-8"))


@router.get("/briefs/{slug}")
async def briefs_reader_page(slug: str):
    if not STATIC_READER.is_file():
        return HTMLResponse("Briefs reader not found", status_code=404)
    # Shell only — content loaded via API so one HTML works for all slugs
    return HTMLResponse(STATIC_READER.read_text(encoding="utf-8"))


@router.get("/api/briefs")
async def api_list_briefs(kind: str = "", status: str = "active"):
    docs = list_docs(kind=kind, status=status or "active")
    return JSONResponse({"ok": True, "docs": docs})


@router.get("/api/briefs/{slug}")
async def api_get_brief(slug: str, raw: int = 0):
    meta = _find_meta(slug)
    if not meta:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    body = _read_body(meta)
    payload = {
        "ok": True,
        "slug": meta.get("slug"),
        "title": meta.get("title"),
        "kind": meta.get("kind"),
        "status": meta.get("status"),
        "summary": meta.get("summary") or "",
        "source_path": meta.get("source_path") or "",
        "project_slug": meta.get("project_slug") or "",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "published_by": meta.get("published_by") or "",
        "url": reader_url(meta.get("slug") or slug),
        "html": _render_html(body),
    }
    if raw:
        payload["markdown"] = body
    return JSONResponse(payload)
