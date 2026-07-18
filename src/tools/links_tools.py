"""
Links Board Tools — agents auto-populate the operator's Links board (CF-59).

Wraps the Links store (dashboard/routes/action_board.py) so an agent can pin a
link it found or built ("here's the dashboard I set up") straight onto the
operator's Links tab — the card appears on the board like any hand-added one.

Storage (same as the API): multi mode → the acting presence's
accounts.preferences["action_links"]; single mode → DATA_DIR/action-links.json
(cove-wide). All writes pass through the API's _sanitize_links, so the
XSS-safety guarantees are identical to the manual editor.

Card shapes (match the UI):
  - link (leaf): title/url/note/icon/group
  - bundle: title/icon + items[] of rows (label+linked text+url), subheads, spacers

The ACTING PRESENCE is carried by a request-scoped ContextVar set at the same
chat chokepoint that binds NC creds (CF-57 pattern). Unset (scheduler /
single-user), single mode uses the cove-wide file; multi mode refuses with a
clear message rather than guessing whose board to write.

Approval tiers:
  AUTO   — get_action_links (read)
  NOTIFY — add_action_link, remove_action_link, update_action_link,
           add_action_bundle (write)
"""

import contextvars as _ctxvars
import json
import logging
import re

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


def _card_type(c: dict) -> str:
    t = str((c or {}).get("type") or "link").strip().lower()
    return "bundle" if t == "bundle" else "link"


def _format_card(c: dict) -> str:
    """One card → multi-line summary agents can reason over (bundles included)."""
    icon = (c.get("icon") or "").strip()
    title = (c.get("title") or c.get("url") or "(untitled)").strip()
    head_icon = f"{icon} " if icon else ""
    if _card_type(c) == "bundle":
        bits = [f"- {head_icon}{title} [bundle]"]
        if c.get("wide"):
            bits[0] += " (wide)"
        if c.get("collapsed"):
            bits[0] += " (starts collapsed)"
        if c.get("note"):
            bits.append(f"    note: {c['note']}")
        items = c.get("items") or []
        if not items:
            bits.append("    (empty — no rows yet)")
        for it in items:
            kind = (it.get("kind") or "row").lower()
            if kind == "spacer":
                bits.append("    ---")
            elif kind == "subhead":
                bits.append(f"    ## {it.get('label') or ''}")
            else:
                label = (it.get("label") or "").strip()
                text = (it.get("text") or "").strip()
                url = (it.get("url") or "").strip()
                left = label or text or "(row)"
                right = text if (label and text and text != label) else ""
                if url and right:
                    bits.append(f"    · {left}: {right} → {url}")
                elif url:
                    bits.append(f"    · {left} → {url}")
                elif right:
                    bits.append(f"    · {left}: {right}")
                else:
                    bits.append(f"    · {left}")
        return "\n".join(bits)

    grp = f" [{c['group']}]" if c.get("group") else ""
    note = f" — {c['note']}" if c.get("note") else ""
    url = c.get("url") or ""
    return f"- {head_icon}{title}{grp}: {url}{note}"


def _match_card(cards: list, title_or_url: str):
    """First card whose title/url contains the needle (bundles match on title)."""
    needle = (title_or_url or "").strip().lower()
    if not needle:
        return None
    for c in cards:
        title = (c.get("title") or "").lower()
        url = (c.get("url") or "").lower()
        if needle in title or needle in url or _norm_url(url) == _norm_url(needle):
            return c
    return None


def _parse_bundle_items(items_text: str) -> list:
    """Parse a compact multi-line bundle body into items[].

    Lines (trim; blank ignored):
      ---                         → spacer
      sub: Name   |  ## Name      → subhead
      label | text | url          → row (url optional)
      label | url                 → row (text defaults empty; if 2nd looks like URL it is url)
      url                         → row with empty label (linked text from url)
    """
    items = []
    if not items_text or not str(items_text).strip():
        return items
    for raw in str(items_text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in ("---", "- - -", "—", "–"):
            items.append({"kind": "spacer", "label": "", "text": "", "url": ""})
            continue
        low = line.lower()
        if low.startswith("sub:") or low.startswith("##"):
            label = line.split(":", 1)[1].strip() if ":" in line[:5] else line.lstrip("#").strip()
            if label:
                items.append({"kind": "subhead", "label": label, "text": "", "url": ""})
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            items.append({
                "kind": "row",
                "label": parts[0],
                "text": parts[1],
                "url": parts[2],
            })
        elif len(parts) == 2:
            a, b = parts
            # If second looks like a URL/path, treat as label|url
            if b.startswith("/") or re.match(r"https?://", b, re.I) or "." in b:
                items.append({"kind": "row", "label": a, "text": "", "url": b})
            else:
                items.append({"kind": "row", "label": a, "text": b, "url": ""})
        else:
            only = parts[0]
            if only.startswith("/") or re.match(r"https?://", only, re.I):
                items.append({"kind": "row", "label": "", "text": "", "url": only})
            else:
                items.append({"kind": "row", "label": only, "text": "", "url": ""})
    return items


# =============================================================================
# Tools
# =============================================================================

@auto
@tool
async def get_action_links() -> str:
    """List the link cards currently on the operator's Links board.

    Shows leaf links AND bundles (with their rows). Use before adding a card
    so you don't duplicate, and so you can see in-card schedule lines on bundles
    like Monthly Bills — not just the bundle title.
    """
    try:
        cards, err = await _read_cards()
        if err:
            return err
        if not cards:
            return "The Links board is empty."
        blocks = [_format_card(c) for c in cards]
        n_bundle = sum(1 for c in cards if _card_type(c) == "bundle")
        n_link = len(cards) - n_bundle
        return (
            f"{len(cards)} card(s) on the board "
            f"({n_link} link, {n_bundle} bundle):\n" + "\n".join(blocks)
        )
    except Exception as e:
        logger.error("get_action_links failed: %s", e)
        return f"Error reading the Links board: {e}"


@notify
@tool
async def add_action_link(title: str, url: str, note: str = "",
                          icon: str = "", group: str = "") -> str:
    """Pin a leaf link onto the operator's Links board (it appears as a card).

    Use when you've found, built, or been asked to save something the operator
    will want one click away — a deployed site, a dashboard, a doc, a tool.
    For multi-row cards (bills, hosting stack), use add_action_bundle instead.

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
                if _card_type(c) == "bundle":
                    continue
                if _norm_url(c.get("url")) == target:
                    # Same destination → refresh the card, don't duplicate it.
                    c["title"] = (title or c["title"]).strip()[:120]
                    c["type"] = "link"
                    if note:
                        c["note"] = note.strip()[:200]
                    if icon:
                        c["icon"] = icon.strip()[:8]
                    if group:
                        c["group"] = group.strip()[:60]
                    err = await _write_cards(cards)
                    return err or f"Updated the existing card for {url} on the Links board."
        cards.append({
            "type": "link",
            "title": title,
            "url": url,
            "note": note,
            "icon": icon,
            "group": group,
            "items": [],
        })
        err = await _write_cards(cards)
        if err:
            return err
        grp = f" under '{group}'" if group else ""
        return f"Added '{title}' to the Links board{grp}."
    except Exception as e:
        logger.error("add_action_link failed: %s", e)
        return f"Error adding the link: {e}"


@notify
@tool
async def add_action_bundle(
    title: str,
    items_text: str,
    icon: str = "",
    note: str = "",
    wide: str = "false",
    collapsed: str = "false",
) -> str:
    """Add or replace a bundle card (multi-row in-card depth) on the Links board.

    Use for schedules and stacks — Monthly Bills, Hosting, Cloudflare — where
    each row is label + linked text + url (not a separate leaf tile).

    Args:
        title: Bundle title (e.g. "Monthly Bills").
        items_text: Multi-line body. Each line one of:
            `label | linked text | url`
            `label | url`
            `sub: Section name` or `## Section name`
            `---` for a spacer
        icon: Optional emoji.
        note: Optional one-line note.
        wide: 'true' to span two columns when space allows.
        collapsed: 'true' to start collapsed.
    """
    try:
        items = _parse_bundle_items(items_text)
        if not (title or "").strip() and not items:
            return "Provide a title and/or at least one row in items_text."

        cards, err = await _read_cards()
        if err:
            return err

        needle = (title or "").strip().lower()
        target = None
        if needle:
            for c in cards:
                if _card_type(c) == "bundle" and (c.get("title") or "").lower() == needle:
                    target = c
                    break
            if target is None:
                # Partial title match on bundles only
                for c in cards:
                    if _card_type(c) == "bundle" and needle in (c.get("title") or "").lower():
                        target = c
                        break

        wide_b = str(wide or "").strip().lower() in ("1", "true", "yes", "y")
        coll_b = str(collapsed or "").strip().lower() in ("1", "true", "yes", "y")

        if target is not None:
            target["type"] = "bundle"
            target["title"] = (title or target.get("title") or "Bundle").strip()[:120]
            target["url"] = ""
            target["items"] = items
            target["wide"] = wide_b
            target["collapsed"] = coll_b
            target["group"] = ""
            if icon:
                target["icon"] = icon.strip()[:8]
            if note != "":
                target["note"] = note.strip()[:200]
            err = await _write_cards(cards)
            if err:
                return err
            n = sum(1 for it in items if (it.get("kind") or "row") == "row")
            return (
                f"Updated bundle '{target['title']}' on the Links board "
                f"({n} row(s), {len(items)} line(s))."
            )

        cards.append({
            "type": "bundle",
            "title": (title or "Bundle").strip()[:120],
            "url": "",
            "note": (note or "").strip()[:200],
            "icon": (icon or "").strip()[:8],
            "group": "",
            "wide": wide_b,
            "collapsed": coll_b,
            "items": items,
        })
        err = await _write_cards(cards)
        if err:
            return err
        n = sum(1 for it in items if (it.get("kind") or "row") == "row")
        return (
            f"Added bundle '{title}' to the Links board "
            f"({n} row(s), {len(items)} line(s))."
        )
    except Exception as e:
        logger.error("add_action_bundle failed: %s", e)
        return f"Error adding the bundle: {e}"


@notify
@tool
async def remove_action_link(title_or_url: str) -> str:
    """Remove a link or bundle card from the operator's Links board.

    Args:
        title_or_url: Card title (partial OK) or exact/partial URL to remove.
    """
    try:
        cards, err = await _read_cards()
        if err:
            return err
        if not cards:
            return "The Links board is empty."

        needle = (title_or_url or "").strip().lower()
        if not needle:
            return "Provide a title or URL to remove."

        keep = []
        removed = None
        for c in cards:
            title = (c.get("title") or "").lower()
            url = (c.get("url") or "").lower()
            if removed is None and (
                needle in title
                or needle in url
                or _norm_url(url) == _norm_url(needle)
            ):
                removed = c
                continue
            keep.append(c)

        if removed is None:
            return f"No link matching '{title_or_url}' found on the board."

        err = await _write_cards(keep)
        if err:
            return err
        kind = "bundle" if _card_type(removed) == "bundle" else "link"
        return (
            f"Removed {kind} '{removed.get('title') or removed.get('url')}' "
            f"from the Links board."
        )
    except Exception as e:
        logger.error("remove_action_link failed: %s", e)
        return f"Error removing the link: {e}"


@notify
@tool
async def update_action_link(
    title_or_url: str,
    title: str = "",
    url: str = "",
    note: str = "",
    icon: str = "",
    group: str = "",
) -> str:
    """Update an existing Links board leaf card matched by title or URL.

    For bundle row edits, prefer add_action_bundle with the same title (replaces
    the bundle body). This tool updates leaf fields; on a bundle it can still
    change title/icon/note.

    Args:
        title_or_url: Existing card title (partial OK) or URL to find.
        title: New title (optional).
        url: New URL (optional; leaf only).
        note: New note (optional).
        icon: New emoji icon (optional).
        group: New group heading (optional; leaf only).
    """
    try:
        cards, err = await _read_cards()
        if err:
            return err
        if not cards:
            return "The Links board is empty."

        target = _match_card(cards, title_or_url)
        if target is None:
            return f"No link matching '{title_or_url}' found on the board."

        changed = []
        if title and title.strip():
            target["title"] = title.strip()[:120]
            changed.append("title")
        if url and url.strip() and _card_type(target) != "bundle":
            target["url"] = url.strip()
            changed.append("url")
        if note != "":
            target["note"] = note.strip()[:200]
            changed.append("note")
        if icon != "":
            target["icon"] = icon.strip()[:8]
            changed.append("icon")
        if group != "" and _card_type(target) != "bundle":
            target["group"] = group.strip()[:60]
            changed.append("group")

        if not changed:
            return "Nothing to update. Provide title, url, note, icon, and/or group."

        err = await _write_cards(cards)
        if err:
            return err
        return (
            f"Updated {_card_type(target)} "
            f"'{target.get('title') or target.get('url')}': "
            + ", ".join(changed)
        )
    except Exception as e:
        logger.error("update_action_link failed: %s", e)
        return f"Error updating the link: {e}"


ALL_LINKS_TOOLS = [
    get_action_links,
    add_action_link,
    add_action_bundle,
    remove_action_link,
    update_action_link,
]

TOOLS = ALL_LINKS_TOOLS
