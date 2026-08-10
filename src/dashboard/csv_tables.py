"""CSV table helpers for familiar-tools grids (no OnlyOffice).

Source of truth is a .csv file (typically Nextcloud Tables/{name}.csv).
Mission Control renders and lightly edits the same bytes agents read/write.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

# Hard caps — household grids, not data warehouses.
MAX_BYTES = 1_500_000
MAX_ROWS = 2_000
MAX_COLS = 40
MAX_CELL = 2_000

_CSV_FENCE_RE = re.compile(
    r"```csv\s*\n([^\n`]+)\n```",
    re.IGNORECASE,
)
_CSV_INLINE_RE = re.compile(
    r"\[\[csv:\s*([^\]\n]+)\]\]",
    re.IGNORECASE,
)


def normalize_table_path(path: str) -> tuple[str | None, str | None]:
    """Return (clean relative path, error). Must end with .csv."""
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        return None, "path is required"
    if "\x00" in raw:
        return None, "Invalid path"
    parts: list[str] = []
    for seg in raw.strip("/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None, "Path escapes root"
            parts.pop()
            continue
        parts.append(seg)
    clean = "/".join(parts)
    if not clean.lower().endswith(".csv"):
        return None, "path must end with .csv"
    if len(clean) > 400:
        return None, "path too long"
    return clean, None


def default_tables_path(name: str) -> str:
    """Cove-level household table path from a short name."""
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-._")
    base = (base or "table")[:80]
    if not base.lower().endswith(".csv"):
        base = f"{base}.csv"
    return f"Tables/{base}"


def parse_csv_text(text: str) -> dict[str, Any]:
    """Parse CSV text into headers + rows. Raises ValueError on hard failures."""
    if text is None:
        raise ValueError("Empty CSV")
    raw = text
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    if len(raw.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise ValueError(f"CSV exceeds {MAX_BYTES} bytes")
    # Strip UTF-8 BOM if present as text
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    if not raw.strip():
        return {
            "headers": [],
            "rows": [],
            "row_count": 0,
            "col_count": 0,
            "truncated": False,
        }

    reader = csv.reader(io.StringIO(raw))
    try:
        all_rows = list(reader)
    except csv.Error as e:
        raise ValueError(f"Invalid CSV: {e}") from e

    if not all_rows:
        return {
            "headers": [],
            "rows": [],
            "row_count": 0,
            "col_count": 0,
            "truncated": False,
        }

    headers = [str(c)[:MAX_CELL] for c in (all_rows[0] or [])]
    if len(headers) > MAX_COLS:
        headers = headers[:MAX_COLS]
    # Ensure unique non-empty header labels for UI
    seen: dict[str, int] = {}
    norm_headers: list[str] = []
    for i, h in enumerate(headers):
        label = (h or "").strip() or f"col_{i + 1}"
        if label in seen:
            seen[label] += 1
            label = f"{label}_{seen[label]}"
        else:
            seen[label] = 1
        norm_headers.append(label[:MAX_CELL])
    headers = norm_headers
    col_count = len(headers)

    truncated = False
    body = all_rows[1:]
    if len(body) > MAX_ROWS:
        body = body[:MAX_ROWS]
        truncated = True

    rows: list[list[str]] = []
    for r in body:
        cells = [str(c)[:MAX_CELL] for c in (r or [])]
        if len(cells) < col_count:
            cells = cells + [""] * (col_count - len(cells))
        elif len(cells) > col_count:
            cells = cells[:col_count]
        rows.append(cells)

    return {
        "headers": headers,
        "rows": rows,
        "row_count": len(rows),
        "col_count": col_count,
        "truncated": truncated,
    }


def serialize_csv(headers: list[str], rows: list[list[str]]) -> str:
    """Serialize headers + rows to CSV text (UTF-8, excel-friendly)."""
    if len(headers) > MAX_COLS:
        raise ValueError(f"Too many columns (max {MAX_COLS})")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Too many rows (max {MAX_ROWS})")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    clean_headers = [str(h)[:MAX_CELL] for h in headers]
    writer.writerow(clean_headers)
    width = len(clean_headers)
    for r in rows:
        cells = [str(c)[:MAX_CELL] for c in (r or [])]
        if len(cells) < width:
            cells = cells + [""] * (width - len(cells))
        elif len(cells) > width:
            cells = cells[:width]
        writer.writerow(cells)
    return buf.getvalue()


def table_to_markdown(headers: list[str], rows: list[list[str]], *, max_preview_rows: int = 50) -> str:
    """GitHub-style markdown table for briefs embed fallback."""
    if not headers:
        return "_Empty table._"
    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(esc(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for r in rows[:max_preview_rows]:
        cells = list(r) + [""] * max(0, len(headers) - len(r))
        lines.append("| " + " | ".join(esc(c) for c in cells[: len(headers)]) + " |")
    if len(rows) > max_preview_rows:
        lines.append(f"\n_… {len(rows) - max_preview_rows} more rows — open the table viewer._")
    return "\n".join(lines)


def viewer_url(path: str) -> str:
    from urllib.parse import quote

    clean, err = normalize_table_path(path)
    if err or not clean:
        return "/tables"
    return f"/tables?path={quote(clean, safe='/')}"


def expand_csv_refs_in_markdown(md: str, resolve_csv) -> str:
    """Replace csv fences / [[csv:path]] with markdown tables.

    resolve_csv(path) -> (headers, rows) or raises / returns None.
    """
    if not md:
        return md

    def _replace(match: re.Match, group: int = 1) -> str:
        path = (match.group(group) or "").strip().strip("'\"")
        clean, err = normalize_table_path(path)
        if err or not clean:
            return f"\n\n> **Table error:** {err or 'bad path'} (`{path}`)\n\n"
        try:
            data = resolve_csv(clean)
        except Exception as e:
            return (
                f"\n\n> **Table error:** could not load `{clean}` — {e}\n\n"
                f"[Open table]({viewer_url(clean)})\n\n"
            )
        if not data:
            return (
                f"\n\n> **Table missing:** `{clean}`\n\n"
                f"[Open table]({viewer_url(clean)})\n\n"
            )
        headers = data.get("headers") or []
        rows = data.get("rows") or []
        title = clean.rsplit("/", 1)[-1]
        block = (
            f"\n\n**{title}** · [Open table]({viewer_url(clean)})\n\n"
            + table_to_markdown(headers, rows)
            + "\n\n"
        )
        return block

    out = _CSV_FENCE_RE.sub(lambda m: _replace(m, 1), md)
    out = _CSV_INLINE_RE.sub(lambda m: _replace(m, 1), out)
    return out


def apply_row_update(
    headers: list[str],
    rows: list[list[str]],
    *,
    row_index: int | None = None,
    values: dict[str, str] | list[str] | None = None,
    append: bool = False,
) -> tuple[list[str], list[list[str]]]:
    """Update or append a row. row_index is 0-based into data rows (not header)."""
    width = len(headers)
    if width == 0:
        raise ValueError("Table has no headers")

    def _row_from_values(vals) -> list[str]:
        if isinstance(vals, dict):
            out = []
            lower_map = {str(h).lower(): i for i, h in enumerate(headers)}
            cells = [""] * width
            for k, v in vals.items():
                key = str(k).strip()
                if key in headers:
                    idx = headers.index(key)
                elif key.lower() in lower_map:
                    idx = lower_map[key.lower()]
                else:
                    continue
                cells[idx] = str(v)[:MAX_CELL]
            return cells
        if isinstance(vals, list):
            cells = [str(c)[:MAX_CELL] for c in vals]
            if len(cells) < width:
                cells = cells + [""] * (width - len(cells))
            return cells[:width]
        raise ValueError("values must be a list or object of column→value")

    if append or row_index is None:
        if values is None:
            raise ValueError("values required to append a row")
        new_rows = list(rows) + [_row_from_values(values)]
        if len(new_rows) > MAX_ROWS:
            raise ValueError(f"Too many rows (max {MAX_ROWS})")
        return headers, new_rows

    if row_index < 0 or row_index >= len(rows):
        raise ValueError(f"row_index out of range (0..{max(0, len(rows) - 1)})")
    if values is None:
        raise ValueError("values required to update a row")
    updated = list(rows)
    if isinstance(values, dict):
        base = list(updated[row_index])
        if len(base) < width:
            base = base + [""] * (width - len(base))
        lower_map = {str(h).lower(): i for i, h in enumerate(headers)}
        for k, v in values.items():
            key = str(k).strip()
            if key in headers:
                idx = headers.index(key)
            elif key.lower() in lower_map:
                idx = lower_map[key.lower()]
            else:
                continue
            base[idx] = str(v)[:MAX_CELL]
        updated[row_index] = base[:width]
    else:
        updated[row_index] = _row_from_values(values)
    return headers, updated


def extract_table_paths_from_markdown(md: str) -> list[str]:
    """Return ordered unique .csv paths referenced in plan/brief markdown.

    Recognizes ```csv path fences and [[csv:path]] inline refs — same grammar
    as expand_csv_refs_in_markdown. Invalid paths are skipped.
    """
    if not md:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for rx in (_CSV_FENCE_RE, _CSV_INLINE_RE):
        for m in rx.finditer(md):
            raw = (m.group(1) or "").strip().strip("'\"")
            clean, err = normalize_table_path(raw)
            if err or not clean or clean in seen:
                continue
            seen.add(clean)
            found.append(clean)
    return found


def table_link_entries(paths: list[str]) -> list[dict[str, str]]:
    """Operator-facing {path, title, viewer_url} rows for project detail."""
    out: list[dict[str, str]] = []
    for p in paths or []:
        clean, err = normalize_table_path(p)
        if err or not clean:
            continue
        title = clean.rsplit("/", 1)[-1]
        if title.lower().endswith(".csv"):
            title = title[:-4]
        title = title.replace("-", " ").replace("_", " ").strip() or clean
        out.append({
            "path": clean,
            "title": title,
            "viewer_url": viewer_url(clean),
        })
    return out
