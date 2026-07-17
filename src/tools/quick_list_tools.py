"""
Quick List Tools — agent tools for managing quick lists via conversation.

Wraps the Quick List API (dashboard/routes/quick_list.py) so agents can
create lists, add items, check items off, and clear completed items.

Approval tiers:
  AUTO   — get_quick_lists, get_list_items (reads)
  NOTIFY — create_quick_list, add_list_items, check_list_item, uncheck_list_item, clear_checked_items (writes)

Environment: runs inside the same container as the API. Calls DB directly
to avoid HTTP overhead and Request-object dependency.
"""

import contextvars as _ctxvars

from langchain_core.tools import tool

from src.tools.approval import auto, notify


# JF4 — the ACTING PRESENCE for quick-list reads/writes, bound request-scoped in
# chat.py at the same point as the Links-board tool. Unset (single mode / no presence)
# -> NULL scope (legacy behavior). Without this, agent-created lists landed at
# presence_id=NULL: invisible on the presence's Attention home AND leaking into
# manager (NULL-scoped) views.
_ql_presence_ctx = _ctxvars.ContextVar("ql_presence", default=None)


def set_request_quick_list_presence(presence_id: str):
    """Bind the acting presence for this request/task. Returns a reset token."""
    return _ql_presence_ctx.set(str(presence_id) if presence_id else None)


def clear_request_quick_list_presence(token) -> None:
    try:
        _ql_presence_ctx.reset(token)
    except Exception:
        pass


def _ql_scope(col: str = "presence_id"):
    """(sql_fragment, params) scoping quick lists to the acting presence (or NULL)."""
    pid = _ql_presence_ctx.get()
    if pid:
        return f"{col} = %s", (pid,)
    return f"{col} IS NULL", ()


# =============================================================================
# Read Tools — AUTO
# =============================================================================

@auto
@tool
async def get_quick_lists() -> str:
    """Get all quick lists with item counts.

    Returns a summary of every list: name, icon, unchecked/total items, pinned status.
    Use this to see what lists exist before adding items.
    """
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            _where, _params = _ql_scope("ql.presence_id")
            result = await conn.execute(
                f"""SELECT ql.id, ql.name, ql.icon, ql.color, ql.pinned,
                          COUNT(qli.id) FILTER (WHERE qli.checked = FALSE) AS unchecked,
                          COUNT(qli.id) AS total
                   FROM quick_lists ql
                   LEFT JOIN quick_list_items qli ON qli.list_id = ql.id
                   WHERE {_where}
                   GROUP BY ql.id
                   ORDER BY ql.position, ql.created_at""",
                _params
            )
            rows = await result.fetchall()

        if not rows:
            return "No quick lists found. Create one with create_quick_list."

        lines = ["Quick Lists:"]
        for r in rows:
            pin = " (pinned)" if r["pinned"] else ""
            lines.append(
                f"  {r['icon']} {r['name']} — {r['unchecked']}/{r['total']} unchecked{pin} [id={r['id']}]"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching lists: {e}"


@auto
@tool
async def get_list_items(list_name: str) -> str:
    """Get all items in a quick list.

    Args:
        list_name: The name of the list (case-insensitive) or its numeric ID.
    """
    from src.memory.database import get_db

    try:
        list_id = await _resolve_list(list_name)
        if list_id is None:
            return f"List '{list_name}' not found. Use get_quick_lists to see available lists."

        async with get_db() as conn:
            # Get list name for display
            lr = await conn.execute(
                "SELECT name, icon FROM quick_lists WHERE id = %s", (list_id,)
            )
            list_row = await lr.fetchone()

            result = await conn.execute(
                """SELECT id, text, checked, position
                   FROM quick_list_items
                   WHERE list_id = %s
                   ORDER BY checked ASC, position ASC, created_at ASC""",
                (list_id,)
            )
            rows = await result.fetchall()

        if not rows:
            return f"{list_row['icon']} {list_row['name']} is empty."

        lines = [f"{list_row['icon']} {list_row['name']}:"]
        for r in rows:
            check = "x" if r["checked"] else " "
            lines.append(f"  [{check}] {r['text']} [id={r['id']}]")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching items: {e}"


# =============================================================================
# Write Tools — NOTIFY
# =============================================================================

@notify
@tool
async def create_quick_list(
    name: str,
    icon: str = "📋",
    pinned: bool = True
) -> str:
    """Create a new quick list.

    Args:
        name: Name of the list (e.g. 'Groceries', 'Ideas', 'Errands')
        icon: Emoji icon for the list (default 📋)
        pinned: Whether to show on the home board (default True)
    """
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            # Get next position (scoped to the acting presence)
            _where, _params = _ql_scope("presence_id")
            result = await conn.execute(
                f"SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM quick_lists WHERE {_where}",
                _params
            )
            row = await result.fetchone()
            position = row["next_pos"]

            _pid = _ql_presence_ctx.get()
            result = await conn.execute(
                """INSERT INTO quick_lists (presence_id, name, icon, position, pinned)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, name, icon""",
                (_pid, name, icon, position, pinned)
            )
            created = await result.fetchone()

        return f"Created list: {created['icon']} {created['name']} [id={created['id']}]"
    except Exception as e:
        return f"Error creating list: {e}"


@notify
@tool
async def add_list_items(
    list_name: str,
    items: str
) -> str:
    """Add one or more items to a quick list.

    Args:
        list_name: Name of the list (case-insensitive) or its numeric ID.
        items: Items to add, separated by commas. Example: 'Milk, Eggs, Bread, Chicken'
    """
    from src.memory.database import get_db

    try:
        list_id = await _resolve_list(list_name)
        if list_id is None:
            return f"List '{list_name}' not found. Use get_quick_lists to see available lists, or create_quick_list to make a new one."

        texts = [t.strip() for t in items.split(",") if t.strip()]
        if not texts:
            return "No items provided. Pass a comma-separated list."

        async with get_db() as conn:
            # Get next position
            result = await conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM quick_list_items WHERE list_id = %s",
                (list_id,)
            )
            row = await result.fetchone()
            pos = row["next_pos"]

            added = []
            for text in texts:
                await conn.execute(
                    "INSERT INTO quick_list_items (list_id, text, position) VALUES (%s, %s, %s)",
                    (list_id, text, pos)
                )
                added.append(text)
                pos += 1

        return f"Added {len(added)} items to list: {', '.join(added)}"
    except Exception as e:
        return f"Error adding items: {e}"


@notify
@tool
async def check_list_item(
    list_name: str,
    item_text: str
) -> str:
    """Mark an item as checked (done) in a quick list.

    Args:
        list_name: Name of the list (case-insensitive) or its numeric ID.
        item_text: The text of the item to check off (partial match, case-insensitive).
    """
    from src.memory.database import get_db

    try:
        list_id = await _resolve_list(list_name)
        if list_id is None:
            return f"List '{list_name}' not found."

        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, text FROM quick_list_items
                   WHERE list_id = %s AND checked = FALSE AND LOWER(text) LIKE %s
                   ORDER BY position LIMIT 1""",
                (list_id, f"%{item_text.lower()}%")
            )
            row = await result.fetchone()

            if not row:
                return f"No unchecked item matching '{item_text}' found in that list."

            await conn.execute(
                "UPDATE quick_list_items SET checked = TRUE, checked_at = NOW() WHERE id = %s",
                (row["id"],)
            )

        return f"Checked off: {row['text']}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def uncheck_list_item(
    list_name: str,
    item_text: str
) -> str:
    """Uncheck an item in a quick list (mark as not done).

    Args:
        list_name: Name of the list (case-insensitive) or its numeric ID.
        item_text: The text of the item to uncheck (partial match, case-insensitive).
    """
    from src.memory.database import get_db

    try:
        list_id = await _resolve_list(list_name)
        if list_id is None:
            return f"List '{list_name}' not found."

        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, text FROM quick_list_items
                   WHERE list_id = %s AND checked = TRUE AND LOWER(text) LIKE %s
                   ORDER BY position LIMIT 1""",
                (list_id, f"%{item_text.lower()}%")
            )
            row = await result.fetchone()

            if not row:
                return f"No checked item matching '{item_text}' found in that list."

            await conn.execute(
                "UPDATE quick_list_items SET checked = FALSE, checked_at = NULL WHERE id = %s",
                (row["id"],)
            )

        return f"Unchecked: {row['text']}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def clear_checked_items(list_name: str) -> str:
    """Remove all checked (completed) items from a quick list.

    Args:
        list_name: Name of the list (case-insensitive) or its numeric ID.
    """
    from src.memory.database import get_db

    try:
        list_id = await _resolve_list(list_name)
        if list_id is None:
            return f"List '{list_name}' not found."

        async with get_db() as conn:
            result = await conn.execute(
                "DELETE FROM quick_list_items WHERE list_id = %s AND checked = TRUE",
                (list_id,)
            )
            count = result.rowcount if hasattr(result, "rowcount") else 0

        if count == 0:
            return "No checked items to clear."
        return f"Cleared {count} checked items from the list."
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Helpers
# =============================================================================

async def _resolve_list(name_or_id: str) -> int | None:
    """Resolve a list name (case-insensitive) or numeric ID to a list ID."""
    from src.memory.database import get_db

    # Try as numeric ID first (scoped to the acting presence)
    try:
        list_id = int(name_or_id)
        _where, _params = _ql_scope("presence_id")
        async with get_db() as conn:
            result = await conn.execute(
                f"SELECT id FROM quick_lists WHERE id = %s AND {_where}", (list_id, *_params)
            )
            if await result.fetchone():
                return list_id
    except (ValueError, TypeError):
        pass

    # Search by name (case-insensitive), scoped to the acting presence
    _where, _params = _ql_scope("presence_id")
    async with get_db() as conn:
        result = await conn.execute(
            f"SELECT id FROM quick_lists WHERE LOWER(name) = LOWER(%s) AND {_where}",
            (str(name_or_id), *_params)
        )
        row = await result.fetchone()
        if row:
            return row["id"]

    return None


# =============================================================================
# Tool Registry
# =============================================================================

ALL_QUICK_LIST_TOOLS = [
    get_quick_lists,
    get_list_items,
    create_quick_list,
    add_list_items,
    check_list_item,
    uncheck_list_item,
    clear_checked_items,
]
TOOLS = ALL_QUICK_LIST_TOOLS
