"""
Vault routes — serve vault docs as formatted HTML for the operator.

Reads .md files from the vault mount and renders them as styled HTML.
If a .html version exists alongside the .md, serves that instead.
The vault mount is at /vault (maps to CLAUDE SKILLS on host).

Categories and doc catalog are defined here. The vault.js frontend
calls these endpoints to populate the dashboard cards and doc viewer.
"""

import os
from src.env import env
import re
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# Vault root inside the container
VAULT_ROOT = Path(env("VAULT_DIR", "/vault"))
LP_VAULT = VAULT_ROOT / "LP-Vault"

# Knowledge Base is hub-owned and synced into each Cove (#135) — resolve at read-time
# (synced location first; repo-bundled copy only as a founder/dev fallback).
from src.knowledge.kb_paths import resolve_kb_file


def _resolve_doc_path(rel_path: str) -> Path:
    """Resolve a doc path. Knowledge Base docs come from the synced KB, others from vault."""
    if rel_path.startswith("Knowledge Base/"):
        kb_filename = rel_path.replace("Knowledge Base/", "", 1)
        kb_path = resolve_kb_file(kb_filename)
        if kb_path.exists():
            return kb_path
    return LP_VAULT / rel_path


# =============================================================================
# Doc catalog — maps doc IDs to vault file paths + metadata
# =============================================================================

def _load_overlay_catalog():
    """Deployment-specific doc catalog (audit C7): workspace docs are a
    per-deployment concern, so an overlay ships its own catalog as JSON
    (same shape as the default list below). Shared src carries none."""
    import json as _json
    p = Path(env("VAULT_CATALOG_FILE", "/overlay/vault-catalog.json"))
    try:
        if p.exists():
            data = _json.loads(p.read_text())
            if isinstance(data, list) and data:
                return data
    except Exception:
        pass
    return None


def _doc_catalog():
    """Build the document catalog. The overlay catalog wins when present; the
    in-repo default lists only the hub-synced Knowledge Base docs every Cove
    actually has (the old baked list pointed at one operator's vault docs —
    a dead surface on every other Cove)."""
    overlay = _load_overlay_catalog()
    if overlay is not None:
        return overlay
    return [
        {
            "id": "specs",
            "name": "Specs & Architecture",
            "description": "Product specs, system architecture, color system",
            "icon": "\U0001f4d0",
            "color_freq": "Momentum",
            "docs": [
                {"id": "color-system", "title": "LP Color System", "description": "14 frequencies, 7 signals, 9 semantic roles", "path": "Knowledge Base/lp-color-system.md"},
            ],
        },
        {
            "id": "framework",
            "name": "Framework & Canon",
            "description": "Tuning keys, principles, manifesto, field theory",
            "icon": "\U0001f3b5",
            "color_freq": "Connection",
            "docs": [
                {"id": "tuning-keys", "title": "Tuning Keys", "description": "All 22 principles with tuning keys and frequency maps", "path": "Knowledge Base/tuning-keys.md"},
                {"id": "canon", "title": "The Canon", "description": "22 Lucid Principles — sacred text", "path": "Knowledge Base/lucid-canon.md"},
                {"id": "manifesto", "title": "Manifesto", "description": "The Lucid Principles framework architecture", "path": "Knowledge Base/manifesto.md"},
                {"id": "field-theory", "title": "Lucid Field Theory", "description": "The theoretical foundation — observer, field, signal", "path": "Knowledge Base/lucid-field-theory.md"},
                {"id": "digital-extension", "title": "Digital Extension", "description": "Framework extension to digital consciousness", "path": "Knowledge Base/digital-extension.md"},
                {"id": "love-equation", "title": "Love Equation (Cross-Substrate)", "description": "Brian Roemmele's equation adapted for the framework", "path": "Knowledge Base/love-equation-cross-substrate.md"},
            ],
        },
    ]


def _find_doc(doc_id: str):
    """Look up a doc by ID across all categories."""
    for cat in _doc_catalog():
        for doc in cat["docs"]:
            if doc["id"] == doc_id:
                return doc, cat
    return None, None


def _file_updated(path: Path) -> str:
    """Get human-readable last-modified date for a file."""
    if not path.exists():
        return ""
    mtime = path.stat().st_mtime
    dt = datetime.fromtimestamp(mtime)
    today = datetime.now().date()
    if dt.date() == today:
        return "today"
    return dt.strftime("%b %d")


# =============================================================================
# Markdown → HTML renderer (lightweight, no external deps)
# =============================================================================

def _md_to_html(text: str) -> str:
    """Convert markdown to HTML. Handles headers, bold, italic, code blocks,
    inline code, tables, links, lists, and horizontal rules."""
    lines = text.split("\n")
    html_lines = []
    in_code_block = False
    in_table = False
    in_list = False
    list_type = None

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f'<pre><code class="lang-{lang}">' if lang else "<pre><code>")
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(_esc(line))
            continue

        stripped = line.strip()

        # Horizontal rule
        if stripped in ("---", "***", "___") and len(stripped) >= 3:
            if in_list:
                html_lines.append(f"</{list_type}>")
                in_list = False
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append("<hr>")
            continue

        # Headers
        hdr_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if hdr_match:
            if in_list:
                html_lines.append(f"</{list_type}>")
                in_list = False
            level = len(hdr_match.group(1))
            content = _inline(hdr_match.group(2))
            html_lines.append(f"<h{level}>{content}</h{level}>")
            continue

        # Table rows
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # Skip separator rows (|---|---|)
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            if not in_table:
                html_lines.append("<table>")
                in_table = True
                # First row = header
                html_lines.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                html_lines.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # Unordered list
        li_match = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if li_match:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
                list_type = "ul"
            html_lines.append(f"<li>{_inline(li_match.group(2))}</li>")
            continue

        # Ordered list
        oli_match = re.match(r'^(\s*)\d+\.\s+(.*)', line)
        if oli_match:
            if not in_list:
                html_lines.append("<ol>")
                in_list = True
                list_type = "ol"
            html_lines.append(f"<li>{_inline(oli_match.group(2))}</li>")
            continue

        # End list if we hit a non-list line
        if in_list and stripped:
            html_lines.append(f"</{list_type}>")
            in_list = False

        # Blockquote
        if stripped.startswith(">"):
            content = _inline(stripped[1:].strip())
            html_lines.append(f"<blockquote>{content}</blockquote>")
            continue

        # Empty line
        if not stripped:
            continue

        # Paragraph
        html_lines.append(f"<p>{_inline(stripped)}</p>")

    # Close any open elements
    if in_code_block:
        html_lines.append("</code></pre>")
    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append(f"</{list_type}>")

    return "\n".join(html_lines)


def _esc(text: str) -> str:
    """Escape HTML entities."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """Process inline markdown: bold, italic, code, links."""
    # Inline code (must be first to protect contents)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Bold + italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', text)
    return text


# =============================================================================
# Routes
# =============================================================================

@router.get("/api/vault/overview")
async def vault_overview():
    """Return categories with doc counts and last-updated info."""
    catalog = _doc_catalog()
    result = []

    for cat in catalog:
        docs_meta = []
        latest_update = ""

        for doc in cat["docs"]:
            file_path = _resolve_doc_path(doc["path"])
            updated = _file_updated(file_path)
            exists = file_path.exists()

            docs_meta.append({
                "id": doc["id"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "status": "current" if exists else "missing",
                "updated": updated,
            })

            # Track most recent update in category
            if updated == "today":
                latest_update = "today"
            elif updated and latest_update != "today":
                latest_update = updated

        result.append({
            "id": cat["id"],
            "name": cat["name"],
            "description": cat["description"],
            "icon": cat["icon"],
            "color_freq": cat.get("color_freq", "Peace"),
            "doc_count": len(docs_meta),
            "last_updated": latest_update,
            "docs": docs_meta,
        })

    return JSONResponse({"categories": result})


@router.get("/api/vault/category/{cat_id}")
async def vault_category(cat_id: str):
    """Return docs in a specific category."""
    catalog = _doc_catalog()
    cat = next((c for c in catalog if c["id"] == cat_id), None)
    if not cat:
        return JSONResponse({"error": "Category not found"}, status_code=404)

    docs = []
    for doc in cat["docs"]:
        file_path = _resolve_doc_path(doc["path"])
        docs.append({
            "id": doc["id"],
            "title": doc["title"],
            "description": doc.get("description", ""),
            "status": "current" if file_path.exists() else "missing",
            "updated": _file_updated(file_path),
        })

    return JSONResponse({"docs": docs, "category": cat["name"]})


@router.get("/api/vault/doc/{doc_id}")
async def vault_doc(doc_id: str):
    """Serve a vault doc as HTML. Checks for .html version first, falls back to .md→HTML."""
    doc, cat = _find_doc(doc_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    md_path = _resolve_doc_path(doc["path"])

    # Check for native HTML version alongside the .md
    html_path = md_path.with_suffix(".html")
    if html_path.exists():
        return JSONResponse({
            "html": html_path.read_text(encoding="utf-8"),
            "title": doc["title"],
            "source": "html",
            "updated": _file_updated(html_path),
        })

    # Render .md as HTML
    if not md_path.exists():
        return JSONResponse({
            "html": f'<p style="color:var(--dim);">File not found: {doc["path"]}</p>',
            "title": doc["title"],
            "source": "missing",
        })

    try:
        md_content = md_path.read_text(encoding="utf-8")
        html_content = _md_to_html(md_content)
        return JSONResponse({
            "html": html_content,
            "title": doc["title"],
            "source": "md",
            "updated": _file_updated(md_path),
        })
    except Exception as e:
        return JSONResponse({
            "html": f'<p style="color:var(--red);">Error reading file: {e}</p>',
            "title": doc["title"],
            "source": "error",
        })
