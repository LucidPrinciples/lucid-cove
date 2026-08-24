"""Bookkeeping ledger helpers (BOOKLED1).

Pure functions so the P&L page and tests share one mapper. Financial bytes
stay in the Presence Bookkeeping/ tree; this module never logs amounts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

ORGANIZE_PREFIX = "Bookkeeping/Organize"
LEDGER_SUFFIX = ".mapped.json"
UNCATEGORIZED = "Uncategorized"

_DATE_FMTS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)


def clean_books_path(path: str) -> tuple[str | None, str | None]:
    """Allow only Bookkeeping/Organize/*.mapped.json (no traversal)."""
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw or "\x00" in raw:
        return None, "Invalid path"
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            return None, "Path escapes root"
        parts.append(seg)
    clean = "/".join(parts)
    if not clean.startswith(ORGANIZE_PREFIX + "/"):
        return None, "Books files must live under Bookkeeping/Organize"
    name = parts[-1] if parts else ""
    if not name.endswith(LEDGER_SUFFIX):
        return None, "Not a mapped ledger file"
    if len(clean) > 400:
        return None, "Path too long"
    return clean, None


def flatten_row(row: dict) -> dict:
    flat = dict(row)
    fields = row.get("fields")
    if isinstance(fields, dict):
        for k, v in fields.items():
            if k not in flat or flat[k] in (None, ""):
                flat[k] = v
    return flat


def parse_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("-") and len(s) > 1:
        neg = True
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    try:
        n = float(s)
    except ValueError:
        return None
    return -abs(n) if neg else n


def row_signed_amount(row: dict) -> float | None:
    """Signed amount: positive income/inflow, negative expense/outflow."""
    row = flatten_row(row)
    for key in ("Debit/Credit", "Amount", "amount", "debit_credit"):
        if key in row and row[key] not in (None, ""):
            parsed = parse_money(row[key])
            if parsed is not None:
                return parsed
    debit = parse_money(row.get("Debit") or row.get("debit"))
    credit = parse_money(row.get("Credit") or row.get("credit"))
    if debit is not None and debit != 0:
        return -abs(debit)
    if credit is not None:
        return abs(credit)
    return None


def parse_row_date(row: dict) -> datetime | None:
    row = flatten_row(row)
    raw = row.get("Date") or row.get("date") or row.get("Posted") or ""
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        try:
            ts = int(s)
            if ts > 10_000_000_000:
                ts //= 1000
            return datetime.utcfromtimestamp(ts)
        except (ValueError, OverflowError, OSError):
            return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def row_payee(row: dict) -> str:
    row = flatten_row(row)
    for key in ("Name", "Description", "description", "Payee", "Memo"):
        val = row.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def category_label(row: dict) -> str:
    lab = row.get("category_label")
    if isinstance(lab, str) and lab.strip():
        return lab.strip()
    return UNCATEGORIZED


def working_chart_from_payload(payload: dict) -> list[dict]:
    chart = payload.get("working_chart") if isinstance(payload, dict) else None
    out: list[dict] = []
    seen: set[str] = set()
    if isinstance(chart, list):
        for item in chart:
            if isinstance(item, str):
                label = item.strip()
                rec = {"label": label, "code": None, "layer": "official"}
            elif isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or "").strip()
                rec = {
                    "label": label,
                    "code": item.get("code"),
                    "layer": item.get("layer") or "official",
                }
            else:
                continue
            if not label or label.lower() in seen:
                continue
            seen.add(label.lower())
            out.append(rec)
    return out


def chart_lookup(chart: list[dict], label: str) -> dict | None:
    want = (label or "").strip().lower()
    if not want:
        return None
    for c in chart:
        if str(c.get("label") or "").strip().lower() == want:
            return c
    return None


def in_period(row: dict, year: int | None, month: int | None) -> bool:
    if year is None and month is None:
        return True
    dt = parse_row_date(row)
    if dt is None:
        return False
    if year is not None and dt.year != year:
        return False
    if month is not None and dt.month != month:
        return False
    return True


def available_periods(rows: list[dict]) -> dict:
    years: set[int] = set()
    months: set[tuple[int, int]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt = parse_row_date(row)
        if dt is None:
            continue
        years.add(dt.year)
        months.add((dt.year, dt.month))
    return {
        "years": sorted(years),
        "months": [{"year": y, "month": m} for y, m in sorted(months)],
    }


def line_preview(row: dict, source_path: str, index: int) -> dict:
    amt = row_signed_amount(row)
    dt = parse_row_date(row)
    return {
        "id": f"{source_path}#{index}",
        "source_path": source_path,
        "index": index,
        "date": dt.strftime("%Y-%m-%d") if dt else "",
        "payee": row_payee(row),
        "amount": amt,
        "category": category_label(row),
        "needs_review": bool(row.get("needs_review")),
        "account": (flatten_row(row).get("account") or "Bluevine"),
    }


def pnl_from_rows(rows: list[dict], year: int | None = None, month: int | None = None) -> dict:
    """Category rollup. Totals are floats for the UI; callers must not log them."""
    income: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    expense: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    uncat = {"total": 0.0, "count": 0}
    skipped = 0
    used = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        if not in_period(row, year, month):
            continue
        amt = row_signed_amount(row)
        if amt is None:
            skipped += 1
            continue
        used += 1
        label = category_label(row)
        if label == UNCATEGORIZED:
            uncat["total"] += amt
            uncat["count"] += 1
            continue
        bucket = income if amt >= 0 else expense
        bucket[label]["total"] += amt
        bucket[label]["count"] += 1

    def _lines(store: dict[str, dict], *, expenses: bool) -> list[dict]:
        items = []
        for label, rec in store.items():
            total = rec["total"]
            items.append({
                "label": label,
                "count": rec["count"],
                "total": -total if expenses else total,
            })
        items.sort(key=lambda x: (-abs(x["total"]), x["label"].lower()))
        return items

    income_lines = _lines(income, expenses=False)
    expense_lines = _lines(expense, expenses=True)
    income_total = sum(x["total"] for x in income_lines)
    expense_total = sum(x["total"] for x in expense_lines)
    return {
        "income": income_lines,
        "expenses": expense_lines,
        "uncategorized": {
            "label": UNCATEGORIZED,
            "count": uncat["count"],
            "total": uncat["total"],
        },
        "income_total": income_total,
        "expense_total": expense_total,
        "net": income_total - expense_total + uncat["total"],
        "row_count": used,
        "skipped": skipped,
    }


def apply_category(payload: dict, index: int, label: str) -> tuple[dict | None, str | None]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if index < 0 or index >= len(rows):
        return None, "Transaction not found"
    row = rows[index]
    if not isinstance(row, dict):
        return None, "Transaction not found"
    chart = working_chart_from_payload(payload)
    hit = chart_lookup(chart, label)
    if hit is None:
        return None, "Category is not on the working chart"
    row["category_label"] = hit["label"]
    row["category_code"] = hit.get("code")
    row["category_layer"] = hit.get("layer")
    row["needs_review"] = False
    row["match_rule"] = "operator"
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review")
    )
    return payload, None
