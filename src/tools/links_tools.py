"""
Links Board Tools — agents auto-populate the operator's Links board (CF-59).

Wraps the Links store (dashboard/routes/action_board.py) so an agent can pin a
link it found or built ("here's the dashboard I set up") straight onto the
operator's Links tab — the card appears on the board like any hand-added one.

Storage (same as the API): multi mode → the acting presence's
accounts.preferences["action_links"]; single mode → DATA_DIR/action-links.json
(cove-wide). All writes pass through the API's _sanitize_links, so the
XSS-safety guarantees are identical to the manual editor.

The ACTING PRESENCE is carried by a request-scoped ContextVar set at the same
chat chokepoint that binds NC creds (CF-57 pattern). Unset (scheduler /
single-user), single mode uses the cove-wide file; multi mode refuses with a
clear message rather than guessing whose board to write.

Approval tiers:
  AUTO   — get_action_links (read)
  NOTIFY — add_action_link (write)
"""

import contextvars as _ctxvars
import json
import logging

from langchain_core.tools import tool

from src.tools.approval import auto, notify

logger = logging.getLogger(__name__)

_links_presence_ctx: "_ctxvars.ContextVar" = _ctxvars.ContextVar(
    "links_presence", default=None)


def set_request_links_presence(presence_id: str):
    """Bind the acting presence for this request/task. Returns a reset token."""
    return _links_presence_ctx.set(str(presence_id) if presence_id else None)


def clear_request_links_presence(token) -> None:
    try:
        _links_presence_ctx.reset(token)
    except Exception:
        pass


def _cove_mode() -> str:
    from src.env import env
    return env("COVE_MODE", "single")


async def _read_cards() -> tuple[list, str]:
    """(cards, error). Reads the same store the API serves."""
    from src.dashboard.routes.action_board import (_LINKS_FILE, _LINKS_KEY,
                                                   _default_links,
                                                   _sanitize_links)
    if _cove_mode() == "multi":
        pid = _links_presence_ctx.get()
        if not pid:
            return [], ("No acting presence bound — the Links board is per-operator "
                        "here and I can't tell whose board to use.")
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT preferences FROM accounts WHERE id = %s", (pid,))
            row = await r.fetchone()
        prefs = (row or {}).get("preferences") or {}
        if isinstance(prefs, str):
            try:
                prefs = json.loads(prefs)
            except Exception:
                prefs = {}
        return (_sanitize_links(prefs.get(_LINKS_KEY) or {})["cards"]
                or _default_links()), ""
    data = {}
    if _LINKS_FILE.exists():
        try:
            data = json.loads(_LINKS_FILE.read_text())
        except Exception:
            data = {}
    return (_sanitize_links(data)["cards"] or _default_links()), ""


async def _write_cards(cards: list) -> str:
    """Persist the full card set (same shape/paths as the API). '' or error."""
    from src.dashboard.routes.action_board import _LINKS_FILE, _LINKS_KEY, _sanitize_links
    clean = _sanitize_links({"cards": cards})
    if _cove_mode() == "multi":
        pid = _links_presence_ctx.get()
        if not pid:
            return ("No acting presence bound — can't save to a per-operator "
                    "Links board.")
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """UPDATE accounts
                   SET preferences = COALESCE(preferences, '{}'::jsonb) || %s::jsonb,
                       updated_at = NOW()
                   WHERE id = %s""",
                (json.dumps({_LINKS_KEY: clean}), pid))
        return ""
    _LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LINKS_FILE.write_text(json.dumps(clean, indent=2))
    return ""


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


# =============================================================================
# Tools
# =============================================================================

@auto
@tool
async def get_action_links() -> str:
    """List the link cards currently on the operator's Links board.

    Use this before adding a link, to avoid duplicates and to pick a fitting
    group name (cards can be grouped, e.g. 'Sites', 'Dashboards').
    """
    try:
        cards, err = await _read_cards()
        if err:
            return err
        if not cards:
            return "The Links board is empty."
        lines = []
        for c in cards:
            grp = f" [{c['group']}]" if c.get("group") else ""
            note = f" — {c['note']}" if c.get("note") else ""
            lines.append(f"- {c.get('icon', '')} {c['title']}{grp}: {c['url']}{note}")
        return f"{len(cards)} link(s) on the board:\n" + "\n".join(lines)
    except Exception as e:
        logger.error("get_action_links failed: %s", e)
        return f"Error reading the Links board: {e}"


@notify
@tool
async def add_action_link(title: str, url: str, note: str = "",
                          icon: str = "", group: str = "") -> str:
    """Pin a link onto the operator's Links board (it appears as a card).

    Use when you've found, built, or been asked to save something the operator
    will want one click away — a deployed site, a dashboard, a doc, a tool.

    Args:
        title: Short card title (e.g. "Family Photos").
        url: The link (https://... or an in-Cove path like /backlog).
        note: One-line description shown under the title (optional).
        icon: A single emoji for the card (optional).
        group: Group heading to cluster the card under, e.g. "Sites" (optional).
    """
    try:
        cards, err = await _read_cards()
        if err:
            return err
        target = _norm_url(url)
        if target:
            for c in cards:
                if _norm_url(c.get("url")) == target:
                    # Same destination → refresh the card, don't duplicate it.
                    c["title"] = (title or c["title"]).strip()[:120]
                    if note:
                        c["note"] = note.strip()[:200]
                    if icon:
                        c["icon"] = icon.strip()[:8]
                    if group:
                        c["group"] = group.strip()[:60]
                    err = await _write_cards(cards)
                    return err or f"Updated the existing card for {url} on the Links board."
        cards.append({"title": title, "url": url, "note": note,
                      "icon": icon, "group": group})
        err = await _write_cards(cards)
        if err:
            return err
        grp = f" under '{group}'" if group else ""
        return f"Added '{title}' to the Links board{grp}."
    except Exception as e:
        logger.error("add_action_link failed: %s", e)
        return f"Error adding the link: {e}"


ALL_LINKS_TOOLS = [
    get_action_links,
    add_action_link,
]

TOOLS = ALL_LINKS_TOOLS
