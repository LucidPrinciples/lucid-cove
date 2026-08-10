"""Agent tools for CSV household tables (familiar tools v1).

Tables live as .csv files (default Cove path Tables/{name}.csv). Mission Control
renders them at /tables?path=…; briefs embed via ```csv path``` or [[csv:path]].
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from src.dashboard import csv_tables as ct
from src.tools.approval import auto, notify

logger = logging.getLogger(__name__)


async def _nc_read(path: str) -> str:
    from src.tools import nextcloud_tools as nc

    tool_obj = nc.nextcloud_read
    if hasattr(tool_obj, "ainvoke"):
        return await tool_obj.ainvoke({"path": path})
    fn = getattr(tool_obj, "coroutine", None)
    if fn is not None:
        return await fn(path=path)
    return "Error: nextcloud_read unavailable"


async def _nc_upload(path: str, content: str, overwrite: bool = True) -> str:
    from src.tools import nextcloud_tools as nc

    tool_obj = nc.nextcloud_upload
    if hasattr(tool_obj, "ainvoke"):
        return await tool_obj.ainvoke(
            {"path": path, "content": content, "overwrite": overwrite}
        )
    fn = getattr(tool_obj, "coroutine", None)
    if fn is not None:
        return await fn(path=path, content=content, overwrite=overwrite)
    return "Error: nextcloud_upload unavailable"


def _strip_file_prefix(raw: str) -> tuple[str | None, str | None]:
    """Parse nextcloud_read tool output → (body, error)."""
    text = raw or ""
    if text.startswith("Access denied") or text.startswith("Error"):
        return None, text
    if text.startswith("FILE:"):
        # FILE: path\n\nbody
        parts = text.split("\n\n", 1)
        body = parts[1] if len(parts) > 1 else ""
        return body, None
    if "HTTP 404" in text or "not found" in text.lower():
        return None, text
    return text, None


async def _load_table(path: str) -> tuple[dict | None, str | None, str]:
    clean, err = ct.normalize_table_path(path)
    if err:
        return None, err, path or ""
    raw = await _nc_read(clean)
    body, rerr = _strip_file_prefix(raw)
    if rerr:
        return None, rerr, clean
    try:
        parsed = ct.parse_csv_text(body or "")
    except ValueError as e:
        return None, str(e), clean
    return parsed, None, clean


def _format_table(path: str, parsed: dict) -> str:
    headers = parsed.get("headers") or []
    rows = parsed.get("rows") or []
    lines = [
        f"Table: {path}",
        f"Viewer: {ct.viewer_url(path)}",
        f"{parsed.get('row_count', len(rows))} rows · {parsed.get('col_count', len(headers))} cols"
        + (" (truncated)" if parsed.get("truncated") else ""),
        "",
    ]
    if not headers:
        lines.append("(no headers)")
        return "\n".join(lines)
    lines.append(" | ".join(headers))
    lines.append(" | ".join("---" for _ in headers))
    for i, r in enumerate(rows[:80]):
        cells = list(r) + [""] * max(0, len(headers) - len(r))
        lines.append(f"[{i}] " + " | ".join(str(c) for c in cells[: len(headers)]))
    if len(rows) > 80:
        lines.append(f"… {len(rows) - 80} more rows")
    return "\n".join(lines)


@auto
@tool
async def table_list(path_prefix: str = "Tables") -> str:
    """List CSV table files under a Nextcloud folder (default Tables/).

    Args:
        path_prefix: Folder to list (default 'Tables').
    """
    try:
        from src.tools import nextcloud_tools as nc

        prefix = (path_prefix or "Tables").strip().strip("/") or "Tables"
        tool_obj = nc.nextcloud_list
        if hasattr(tool_obj, "ainvoke"):
            listing = await tool_obj.ainvoke({"path": prefix})
        else:
            fn = getattr(tool_obj, "coroutine", None)
            listing = await fn(path=prefix) if fn else "Error listing"
        if not isinstance(listing, str):
            listing = str(listing)
        # Filter to csv names when possible
        lines = []
        for line in listing.splitlines():
            if ".csv" in line.lower() or line.startswith("Contents") or "empty" in line.lower() or line.startswith("Error"):
                lines.append(line)
        if lines:
            return "CSV tables:\n" + "\n".join(lines) + f"\n\nOpen viewer: /tables?path={prefix}/name.csv"
        return listing
    except Exception as e:
        logger.error("table_list failed: %s", e)
        return f"Error listing tables: {e}"


@auto
@tool
async def table_read(path: str) -> str:
    """Read a CSV table (headers + rows) from Nextcloud.

    Args:
        path: Path ending in .csv (e.g. Tables/who-brings-what.csv).
    """
    try:
        parsed, err, clean = await _load_table(path)
        if err:
            return f"Error reading table: {err}"
        return _format_table(clean, parsed or {})
    except Exception as e:
        logger.error("table_read failed: %s", e)
        return f"Error reading table: {e}"


@notify
@tool
async def table_create(
    name_or_path: str,
    headers_csv: str,
    pin_to_links: str = "true",
    title: str = "",
    note: str = "",
) -> str:
    """Create a new CSV table under Tables/ (or an explicit .csv path).

    Args:
        name_or_path: Short name (who-brings-what) or full path Tables/foo.csv.
        headers_csv: Comma-separated header names (e.g. 'item,who,notes').
        pin_to_links: 'true' pins a Links card that opens the table viewer.
        title: Optional Links card title (defaults to file name).
        note: Optional Links card note.
    """
    try:
        raw = (name_or_path or "").strip()
        if not raw:
            return "name_or_path is required"
        if raw.lower().endswith(".csv") and "/" in raw:
            clean, err = ct.normalize_table_path(raw)
        else:
            clean, err = ct.normalize_table_path(ct.default_tables_path(raw))
        if err or not clean:
            return err or "bad path"

        headers = [h.strip() for h in (headers_csv or "").split(",") if h.strip()]
        if not headers:
            return "headers_csv is required (comma-separated column names)"
        csv_text = ct.serialize_csv(headers, [])
        result = await _nc_upload(clean, csv_text, overwrite=False)
        if isinstance(result, str) and (
            result.startswith("Error")
            or result.startswith("Access denied")
            or result.startswith("Upload failed")
            or "already exists" in result.lower()
        ):
            return result

        viewer = ct.viewer_url(clean)
        pin_msg = ""
        if str(pin_to_links or "true").strip().lower() in ("1", "true", "yes", "y"):
            try:
                from src.tools import links_tools as lt

                card_title = (title or "").strip() or clean.rsplit("/", 1)[-1].replace(".csv", "")
                card_note = (note or "").strip() or "CSV table — open in Mission Control"
                tool_obj = lt.add_action_link
                if hasattr(tool_obj, "ainvoke"):
                    pin_msg = await tool_obj.ainvoke(
                        {
                            "title": card_title,
                            "url": viewer,
                            "note": card_note,
                            "icon": "📊",
                            "group": "Tables",
                        }
                    )
                else:
                    fn = getattr(tool_obj, "coroutine", None)
                    if fn:
                        pin_msg = await fn(
                            title=card_title,
                            url=viewer,
                            note=card_note,
                            icon="📊",
                            group="Tables",
                        )
            except Exception as e:
                pin_msg = f"(Links pin skipped: {e})"

        lines = [
            f"Created table: {clean}",
            f"Viewer: {viewer}",
            f"Headers: {', '.join(headers)}",
            "Briefs embed: ```csv",
            clean,
            "```",
            "or [[csv:" + clean + "]]",
        ]
        if pin_msg:
            lines.append(str(pin_msg))
        return "\n".join(lines)
    except Exception as e:
        logger.error("table_create failed: %s", e)
        return f"Error creating table: {e}"


@notify
@tool
async def table_add_row(path: str, values_json: str) -> str:
    """Append one row to a CSV table.

    Args:
        path: Path ending in .csv.
        values_json: JSON object of column→value, or JSON array matching headers.
    """
    try:
        parsed, err, clean = await _load_table(path)
        if err:
            return f"Error: {err}"
        try:
            values = json.loads(values_json)
        except json.JSONDecodeError as e:
            return f"values_json must be valid JSON: {e}"
        headers, rows = ct.apply_row_update(
            parsed["headers"], parsed["rows"], values=values, append=True
        )
        csv_text = ct.serialize_csv(headers, rows)
        result = await _nc_upload(clean, csv_text, overwrite=True)
        if isinstance(result, str) and (
            result.startswith("Error") or result.startswith("Access denied") or "failed" in result.lower()
        ):
            return result
        return (
            f"Appended row to {clean} (now {len(rows)} rows).\n"
            f"Viewer: {ct.viewer_url(clean)}\n"
            + _format_table(clean, ct.parse_csv_text(csv_text))
        )
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error("table_add_row failed: %s", e)
        return f"Error adding row: {e}"


@notify
@tool
async def table_update_row(path: str, row_index: int, values_json: str) -> str:
    """Update one data row (0-based index) in a CSV table.

    Args:
        path: Path ending in .csv.
        row_index: 0-based row number (not counting the header).
        values_json: JSON object of column→value (partial OK), or full array.
    """
    try:
        parsed, err, clean = await _load_table(path)
        if err:
            return f"Error: {err}"
        try:
            values = json.loads(values_json)
        except json.JSONDecodeError as e:
            return f"values_json must be valid JSON: {e}"
        headers, rows = ct.apply_row_update(
            parsed["headers"],
            parsed["rows"],
            row_index=int(row_index),
            values=values,
            append=False,
        )
        csv_text = ct.serialize_csv(headers, rows)
        result = await _nc_upload(clean, csv_text, overwrite=True)
        if isinstance(result, str) and (
            result.startswith("Error") or result.startswith("Access denied") or "failed" in result.lower()
        ):
            return result
        return (
            f"Updated row {row_index} in {clean}.\n"
            f"Viewer: {ct.viewer_url(clean)}\n"
            + _format_table(clean, ct.parse_csv_text(csv_text))
        )
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error("table_update_row failed: %s", e)
        return f"Error updating row: {e}"


ALL_TABLE_TOOLS = [
    table_list,
    table_read,
    table_create,
    table_add_row,
    table_update_row,
]

TOOLS = ALL_TABLE_TOOLS
