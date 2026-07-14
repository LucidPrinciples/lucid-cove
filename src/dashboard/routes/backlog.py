"""Backlog Board — MC-served visual task board.

Reads jules-backlog.md from the Presence's vault (VAULT_DIR env var).
Each Presence has their own Workspace/jules-backlog.md.
Parses markdown into structured lanes and serves as interactive HTML board.
"""

import os
from src.env import env
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

VAULT_DIR = env("VAULT_DIR", "/vault")


def _find_backlog():
    """Find the backlog file from the Presence's vault."""
    candidates = [
        Path(VAULT_DIR) / "AgentSkills" / "Ops" / "jules-backlog.md",
        Path(VAULT_DIR) / "LP-Vault" / "Workspace" / "jules-backlog.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _parse_backlog(text: str) -> dict:
    """Parse the backlog markdown into structured lane data.

    #D43: Recognizes INTERACTIVE and BLOCKED as their own lanes. Unknown headers
    get their own lane group (never swallowed into NOW)."""
    lanes = {}
    current_lane_key = None

    # Known lane headers → internal key
    lane_map = {
        "now": "now",
        "soon": "soon",
        "later": "later",
        "projects": "projects",
        "completed": "done",
        "interactive": "interactive",
        "blocked": "blocked",
    }

    for line in text.split("\n"):
        stripped = line.strip()

        # Detect lane headers (## Now, ## Soon, etc.)
        if stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            matched_key = None
            for prefix, key in lane_map.items():
                if header.startswith(prefix):
                    matched_key = key
                    break
            if matched_key:
                current_lane_key = matched_key
            else:
                # #D43: Unknown headers get their own lane group, never merged into NOW.
                # Normalize: alphanumeric only, no spaces.
                current_lane_key = re.sub(r"[^a-z0-9]", "", header)
            if current_lane_key not in lanes:
                lanes[current_lane_key] = []
            continue

        # Detect items (- [ ] or - [x])
        if current_lane_key and (stripped.startswith("- [ ] ") or stripped.startswith("- [x] ")):
            done = stripped.startswith("- [x] ")
            rest = stripped[6:]

            # Extract title (bold), with optional #N number prefix
            title_match = re.match(r"\*\*(.+?)\*\*\.?\s*(.*)", rest)
            if title_match:
                title = title_match.group(1).rstrip(".")
                desc_part = title_match.group(2)
            else:
                title = rest.split(".")[0].strip("*").strip()
                desc_part = rest[len(title):].strip(". ")

            # Extract item number (#N) from title
            num_match = re.match(r"#(\d+)\s+(.*)", title)
            item_num = int(num_match.group(1)) if num_match else None
            if num_match:
                title = num_match.group(2)

            # Extract tags [tag]
            tags = re.findall(r"`\[(\w+)\]`", desc_part)
            desc_part = re.sub(r"\s*`\[\w+\]`", "", desc_part)

            # Extract source *(source)*
            source = ""
            source_match = re.search(r"\*\((.+?)\)\*\s*$", desc_part)
            if source_match:
                source = source_match.group(1)
                desc_part = desc_part[:source_match.start()].strip()

            desc = desc_part.strip().rstrip(".")

            item = {
                "title": title,
                "desc": desc,
                "tags": tags,
                "source": source,
                "done": done,
                "num": item_num,
            }

            # For projects, extract trigger
            if current_lane_key == "projects":
                trigger_match = re.search(r"Trigger:\s*(.+?)\.?\s*$", desc, re.IGNORECASE)
                if trigger_match:
                    item["trigger"] = trigger_match.group(1)
                    item["desc"] = desc[:trigger_match.start()].strip().rstrip(".")

            lanes[current_lane_key].append(item)

    return lanes


@router.get("/backlog")
async def serve_backlog():
    """Serve the backlog board HTML page."""
    static = Path(__file__).parent.parent / "static" / "backlog.html"
    if not static.exists():
        return HTMLResponse("Backlog page not found", status_code=404)
    content = static.read_text(encoding="utf-8")
    return HTMLResponse(content)


async def _read_backlog_nc(request) -> str | None:
    """Fallback for Coves with no /vault mount (every provisioned Cove): read
    the presence's AgentSkills/Ops/jules-backlog.md over NC WebDAV — the same
    file, path, and creds the jules processor writes with."""
    try:
        import httpx
        from urllib.parse import quote
        from src.dashboard.routes.nextcloud import get_nc_creds
        nc_url, nc_user, nc_pass = await get_nc_creds(request)
        if not (nc_url and nc_user and nc_pass):
            return None
        url = (f"{nc_url}/remote.php/dav/files/{nc_user}"
               f"/{quote('AgentSkills/Ops/jules-backlog.md')}")
        async with httpx.AsyncClient(timeout=20, auth=(nc_user, nc_pass)) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return resp.text
        return None
    except Exception:
        return None


@router.get("/api/backlog/items")
async def get_backlog_items(request: Request):
    """Return parsed backlog as JSON.

    Read order — PRESENCE-SCOPED source first: in multi mode each presence has
    their OWN AgentSkills/Ops/jules-backlog.md in their Nextcloud space, so the
    per-presence NC read comes first. The container-global /vault file is the
    single-mode / founder-legacy fallback ONLY — on a legacy box with a /vault
    mount it would otherwise serve ONE shared board to every presence.
    Empty lanes = a working empty board, not an error."""
    text = None
    if env("COVE_MODE", "single") == "multi":
        text = await _read_backlog_nc(request)
    if text is None:
        path = _find_backlog()
        if path:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                text = None
    if text is None:
        text = await _read_backlog_nc(request)
    if text is None:
        return JSONResponse({"ok": True, "lanes": {}, "empty": True,
                             "note": "No backlog yet — record a jules and it lands here."})
    try:
        return JSONResponse({"ok": True, "lanes": _parse_backlog(text)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "lanes": {}})


@router.post("/api/backlog/clear-completed")
async def clear_completed_backlog(request: Request):
    """Purge checklist items under ## Completed on the intake board.

    Board lifecycle: done items retain in COMPLETED until the operator clears.
    Uses the steward's CAS board write (If-Match) so concurrent edits can't
    stomp. Admin-gated in multi mode.
    """
    if env("COVE_MODE", "single") == "multi":
        try:
            from src.dashboard.routes.presence import get_current_presence
            p = await get_current_presence(request)
            if not p or p.get("cove_role") != "admin":
                return JSONResponse(status_code=403,
                                    content={"ok": False, "error": "Admin only."})
        except Exception:
            return JSONResponse(status_code=403,
                                content={"ok": False, "error": "Admin only."})
    try:
        from src.tools.backlog_tools import _cas_edit, clear_completed_items

        def _apply(t):
            nt, m = clear_completed_items(t)
            return nt, [m]

        msgs, label, saved = await _cas_edit(_apply)
        return JSONResponse({
            "ok": bool(saved),
            "message": " ".join(msgs),
            "board": label,
        })
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": str(e)})
