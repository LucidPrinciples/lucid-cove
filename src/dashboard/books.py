"""Bookkeeping ledger helpers.

Pure functions so the P&L page and tests share one mapper. Financial bytes
stay in the Presence Bookkeeping/ tree; this module never logs amounts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

ORGANIZE_PREFIX = "Bookkeeping/Organize"
DROP_PREFIX = "Bookkeeping/Drop"
MANUAL_LEDGER_NAME = "manual.mapped.json"
MANUAL_LEDGER_PATH = f"{ORGANIZE_PREFIX}/{MANUAL_LEDGER_NAME}"
RETURNS_PREFIX = "Bookkeeping/Returns"
LEDGER_SUFFIX = ".mapped.json"
UNCATEGORIZED = "Uncategorized"
ALL_TRANSACTIONS = "all"
ALL_LINES = "*"
ALL_BOOKS = "all-books"
DISABLED_LINES = "__disabled__"
MIN_MAP_PHRASE = 4
MAX_LEDGER_LIST = 40
MAX_COMBINED_LINES = 4000
MAX_PASTE_LINES = 200
MAX_ACCOUNT_LABEL = 40
MAX_CATEGORY_LABEL = 60
WRITE_IN_EXPENSE = "__write_in_expense__"
MAX_DROP_FILES = 40
MAX_DROP_FILE_BYTES = 8_000_000
MAX_IMPORT_LINES = 2000
MAX_PDF_PAGES = 24
MIN_STATEMENT_PAYEE = 3
MIN_PDF_TEXT_CHARS = 120
MIN_PDF_TEXT_LINES = 8
TEXT_DROP_EXTS = (".csv", ".txt", ".tsv")
PDF_DROP_EXTS = (".pdf",)
IMAGE_DROP_EXTS = (".png", ".jpg", ".jpeg", ".heic", ".webp")
_RESERVED_STEMS = frozenset({
    "manual",
    "all",
    "all-transactions",
    "all-books",
    "organize",
    "drop",
    "returns",
})
GROSS_RECEIPTS_LABELS = (
    "gross receipts or sales",
    "gross receipts",
    "gross sales",
)
INCOME_KIND = "income"
EXPENSE_KIND = "expense"
TRANSFER_KIND = "transfer"
OWNER_LOAN_LABEL = "Owner Loan"
MERRICK_LABEL = "MERRICK"
INCOME_LABEL_NEEDLES = (
    "gross receipts",
    "gross sales",
    "other income",
    "returns and allowance",
)
TRANSFER_LABEL_NEEDLES = (
    "owner loan",
    "shareholder loan",
    "due to owner",
    "due from owner",
    "merrick",
)
BUILTIN_TRANSFER_LABELS = (
    OWNER_LOAN_LABEL,
    MERRICK_LABEL,
)

FILING_BOOKS = (
    {"id": "chords", "label": "Chords of Truth, LLC", "form": "Schedule C"},
    {"id": "pickleball", "label": "Pickleball", "form": "Schedule C"},
    {"id": "personal", "label": "Personal", "form": "not on a C"},
)
_FILING_IDS = {b["id"] for b in FILING_BOOKS}
_MONTH_PREFIX = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

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
    "%b %d",
    "%B %d",
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


def organize_dir() -> str:
    return ORGANIZE_PREFIX


def drop_dir() -> str:
    return DROP_PREFIX


def drop_folder_for_stem(stem: str) -> str | None:
    s = account_stem(stem)
    if not s:
        return None
    return f"{DROP_PREFIX}/{s}"


def account_stem(label: str) -> str:
    raw = (label or "").strip()
    raw = raw.replace("\\", "/").split("/")[-1]
    if raw.lower().endswith(LEDGER_SUFFIX):
        raw = raw[: -len(LEDGER_SUFFIX)]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    if len(cleaned) > MAX_ACCOUNT_LABEL:
        cleaned = cleaned[:MAX_ACCOUNT_LABEL].rstrip("-._")
    return cleaned


def ledger_filename_for_stem(stem: str) -> str | None:
    s = account_stem(stem)
    if not s:
        return None
    return f"{s}{LEDGER_SUFFIX}"


def is_reserved_account_stem(stem: str) -> bool:
    s = account_stem(stem).lower()
    return not s or s in _RESERVED_STEMS


def new_account_spec(label: str) -> tuple[dict | None, str | None]:
    """Name a blank ledger + Drop folder. Does not write."""
    wanted = (label or "").strip()
    if not wanted:
        return None, "Account name is required"
    if len(wanted) > MAX_ACCOUNT_LABEL:
        return None, f"Account name is over {MAX_ACCOUNT_LABEL} characters"
    stem = account_stem(wanted)
    if not stem:
        return None, "Account name needs a letter or number"
    if is_reserved_account_stem(stem):
        return None, "That name is reserved"
    filename = ledger_filename_for_stem(stem)
    folder = drop_folder_for_stem(stem)
    if not filename or not folder:
        return None, "Account name is not valid"
    return {
        "label": stem,
        "stem": stem,
        "filename": filename,
        "path": f"{ORGANIZE_PREFIX}/{filename}",
        "drop_folder": folder,
    }, None


def empty_account_payload(
    label: str,
    source: dict | None = None,
    filing_book: str = "",
) -> dict:
    src = source if isinstance(source, dict) else {}
    stem = account_stem(label) or "Account"
    chosen = normalize_filing_book(filing_book, default="")
    book = chosen if chosen in _FILING_IDS else infer_filing_book(src)
    return {
        "entity": src.get("entity") or "",
        "account": stem,
        "origin": "account",
        "filing_book": book,
        "working_chart": working_chart_from_payload(src),
        "vendor_map": vendor_map_from_payload(src) if src else [],
        "rows": [],
        "needs_review_count": 0,
    }


def drop_file_kind(name: str) -> str:
    n = (name or "").replace("\\", "/").split("/")[-1].strip().lower()
    if not n or n.startswith("."):
        return ""
    for ext in TEXT_DROP_EXTS:
        if n.endswith(ext):
            return "text"
    for ext in PDF_DROP_EXTS:
        if n.endswith(ext):
            return "pdf"
    for ext in IMAGE_DROP_EXTS:
        if n.endswith(ext):
            return "image"
    return ""


def extract_pdf_text(data: bytes) -> tuple[str, str]:
    """Pull selectable text from a PDF. Never log the contents."""
    if not data:
        return "", "empty"
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "missing"
    try:
        import io

        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)[:MAX_PDF_PAGES]
        parts: list[str] = []
        for page in pages:
            chunk = page.extract_text() or ""
            if chunk.strip():
                parts.append(chunk)
        text = "\n".join(parts).strip()
        return text, "text" if pdf_text_looks_usable(text) else "empty"
    except Exception:
        return "", "error"


def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from pdf2image import convert_from_bytes  # noqa: F401
    except ImportError:
        return False
    return True


def extract_ocr_text(data: bytes, kind: str) -> tuple[str, str]:
    """OCR a PDF or image. Never log the contents."""
    if not data:
        return "", "empty"
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        from PIL import Image
        import io
    except ImportError:
        return "", "missing"
    try:
        images = []
        if kind == "pdf":
            images = convert_from_bytes(
                data,
                dpi=200,
                first_page=1,
                last_page=MAX_PDF_PAGES,
            )
        else:
            images = [Image.open(io.BytesIO(data))]
        parts: list[str] = []
        for im in images[:MAX_PDF_PAGES]:
            work = im.convert("L") if hasattr(im, "convert") else im
            chunk = pytesseract.image_to_string(work, config="--psm 6") or ""
            if chunk.strip():
                parts.append(chunk)
        text = "\n".join(parts).strip()
        return text, "text" if len(text) >= 40 else "empty"
    except Exception:
        return "", "error"


def extract_statement_text(data: bytes, kind: str) -> tuple[str, str]:
    """Return (text, how). how is text, ocr, empty, missing, or error."""
    if kind == "text":
        try:
            return data.decode("utf-8-sig"), "text"
        except UnicodeDecodeError:
            return "", "error"
    if kind == "pdf":
        text, mode = extract_pdf_text(data)
        if mode == "text":
            return text, "text"
        ocr_text, omode = extract_ocr_text(data, "pdf")
        if omode == "text":
            return ocr_text, "ocr"
        if mode == "missing" or omode == "missing":
            return "", "missing"
        return "", omode if omode != "empty" else mode
    if kind == "image":
        text, mode = extract_ocr_text(data, "image")
        if mode == "text":
            return text, "ocr"
        return "", mode
    return "", "empty"


def extract_and_parse_statement(
    data: bytes,
    kind: str,
    default_year: int | None = None,
) -> tuple[list[dict], str, list[str]]:
    """Pick digital text or OCR by how many real rows parse. Never log contents."""
    if kind == "text":
        text, how = extract_statement_text(data, kind)
        if not text:
            return [], how or "empty", ["No readable text in that statement"]
        rows, errors = parse_pasted_lines(
            text, default_year=default_year, max_lines=MAX_IMPORT_LINES
        )
        return rows, how, errors
    candidates: list[tuple[list[dict], str, list[str]]] = []
    if kind == "pdf":
        dtext, dmode = extract_pdf_text(data)
        if dmode == "missing":
            return [], "missing", ["PDF tools are not in this app image yet"]
        if dtext:
            drows, derr = parse_pasted_lines(
                dtext, default_year=default_year, max_lines=MAX_IMPORT_LINES
            )
            candidates.append((drows, "text", derr))
    ocr_kind = "pdf" if kind == "pdf" else "image"
    otext, omode = extract_ocr_text(data, ocr_kind)
    if omode == "missing":
        if not candidates:
            return [], "missing", ["PDF tools are not in this app image yet"]
    elif otext:
        orows, oerr = parse_pasted_lines(
            otext, default_year=default_year, max_lines=MAX_IMPORT_LINES
        )
        candidates.append((orows, "ocr", oerr))
    if not candidates:
        return [], omode if kind != "pdf" else "empty", ["No readable text in that statement"]
    best = max(candidates, key=lambda item: len(item[0]))
    if best[0]:
        return best
    return [], best[1], best[2] or ["No rows found"]


def statement_fingerprint(date: str, payee: str, amount: Any) -> str:
    dt = str(date or "").strip()
    who = re.sub(r"\s+", " ", str(payee or "").strip().lower())
    amt = parse_money(amount)
    cents = "" if amt is None else f"{amt:.2f}"
    return f"{dt}|{who}|{cents}"


def row_fingerprint(row: dict) -> str:
    flat = flatten_row(row) if isinstance(row, dict) else {}
    date = flat.get("Date") or flat.get("date") or ""
    payee = flat.get("Name") or flat.get("payee") or ""
    amt = row_signed_amount(row) if isinstance(row, dict) else None
    return statement_fingerprint(str(date), str(payee), amt)


def append_imported_rows(
    payload: dict,
    items: list[dict],
    source_file: str = "",
    filing_book: str = "",
) -> tuple[dict | None, int, int, str | None]:
    """Append parsed statement rows; skip exact date/payee/amount dupes."""
    if not isinstance(payload, dict):
        return None, 0, 0, "Ledger JSON must be an object"
    if not isinstance(items, list):
        return None, 0, 0, "No rows to add"
    rows = payload.get("rows")
    if rows is None:
        rows = []
        payload["rows"] = rows
    if not isinstance(rows, list):
        return None, 0, 0, "Ledger has no rows"
    chosen = normalize_filing_book(filing_book, default="")
    book = chosen if chosen in _FILING_IDS else infer_filing_book(payload)
    if book in _FILING_IDS:
        payload["filing_book"] = book
    seen = {row_fingerprint(r) for r in rows if isinstance(r, dict)}
    added = 0
    skipped = 0
    src = (source_file or "").replace("\\", "/").split("/")[-1].strip()
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        date = str(item.get("date") or "").strip()
        payee = str(item.get("payee") or item.get("name") or "").strip()
        amt = parse_money(item.get("amount"))
        if not date or not payee or amt is None:
            skipped += 1
            continue
        key = statement_fingerprint(date, payee, amt)
        if key in seen:
            skipped += 1
            continue
        if len(rows) >= MAX_COMBINED_LINES:
            return None, added, skipped, "Ledger is full"
        row = {
            "origin": "statement",
            "disabled": False,
            "needs_review": True,
            "match_rule": "import",
            "source_file": src,
            "fields": {
                "Date": date,
                "Name": payee[:200],
                "Debit/Credit": amt,
            },
        }
        if book in _FILING_IDS:
            row["filing_book"] = book
        rows.append(row)
        seen.add(key)
        added += 1
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review") and not row_is_disabled(r)
    )
    return payload, added, skipped, None


def apply_payload_filing_book(
    payload: dict,
    book: str,
    stamp_unstamped_rows: bool = True,
) -> tuple[dict | None, int, str | None]:
    """Set the ledger Book and stamp rows that have none yet."""
    if not isinstance(payload, dict):
        return None, 0, "Ledger JSON must be an object"
    bid = normalize_filing_book(book, default="")
    if bid not in _FILING_IDS:
        return None, 0, "Pick a filing book"
    payload["filing_book"] = bid
    stamped = 0
    if not stamp_unstamped_rows:
        return payload, 0, None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return payload, 0, None
    for row in rows:
        if not isinstance(row, dict):
            continue
        existing = row.get("filing_book") or row.get("book") or row.get("schedule")
        if existing not in (None, ""):
            continue
        row["filing_book"] = bid
        stamped += 1
    payload["rows"] = rows
    return payload, stamped, None


def is_mapped_name(name: str) -> bool:
    n = (name or "").replace("\\", "/").split("/")[-1].strip()
    return bool(n) and n.endswith(LEDGER_SUFFIX) and ".." not in n


def ledger_path_from_name(name: str) -> str | None:
    n = (name or "").replace("\\", "/").split("/")[-1].strip()
    if not is_mapped_name(n):
        return None
    return f"{ORGANIZE_PREFIX}/{n}"


def pick_default_ledger(names: list[str]) -> str | None:
    mapped = [n for n in names if is_mapped_name(n)]
    if not mapped:
        return None
    mapped.sort()
    return ledger_path_from_name(mapped[0])


def is_all_transactions(path: str) -> bool:
    p = (path or "").strip().lower().replace("_", "-")
    return p in ("", ALL_TRANSACTIONS, "all-transactions")


def is_manual_ledger(path: str) -> bool:
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return False
    name = raw.split("/")[-1].lower()
    return name in (MANUAL_LEDGER_NAME, "manual") or raw.lower() == MANUAL_LEDGER_PATH.lower()


def filing_book_choices() -> list[dict]:
    return [{"id": ALL_BOOKS, "label": "All books"}] + [dict(b) for b in FILING_BOOKS]


def normalize_filing_book(value: Any, default: str = "chords") -> str:
    raw = str(value or "").strip().lower()
    if raw in (ALL_BOOKS, "all-books", "*"):
        return ALL_BOOKS
    if not raw:
        if default == ALL_BOOKS or default in _FILING_IDS:
            return default
        return "chords"
    aliases = {
        "chords of truth": "chords",
        "chords of truth, llc": "chords",
        "chords of truth llc": "chords",
        "cot": "chords",
        "llc": "chords",
        "pickle": "pickleball",
        "pickle ball": "pickleball",
        "not on a c": "personal",
        "off c": "personal",
    }
    if raw in _FILING_IDS:
        return raw
    compact = " ".join(raw.replace(",", " ").split())
    if compact in aliases:
        return aliases[compact]
    if "pickle" in raw:
        return "pickleball"
    if "chord" in raw:
        return "chords"
    if "personal" in raw:
        return "personal"
    if default == ALL_BOOKS or default in _FILING_IDS:
        return default
    return "chords"


def filing_book_label(book_id: str) -> str:
    bid = normalize_filing_book(book_id, default="chords")
    if bid == ALL_BOOKS:
        return "All books"
    for item in FILING_BOOKS:
        if item["id"] == bid:
            return item["label"]
    return "Chords of Truth, LLC"


def infer_filing_book(payload: dict | None = None, row: dict | None = None) -> str:
    if isinstance(row, dict):
        for key in ("filing_book", "book", "schedule"):
            val = row.get(key)
            if val not in (None, ""):
                hit = normalize_filing_book(val, default="")
                if hit in _FILING_IDS:
                    return hit
    if isinstance(payload, dict):
        for key in ("filing_book", "book", "default_filing_book"):
            val = payload.get(key)
            if val not in (None, ""):
                hit = normalize_filing_book(val, default="")
                if hit in _FILING_IDS:
                    return hit
        ent = str(payload.get("entity") or "").strip().lower()
        if ent:
            hit = normalize_filing_book(ent, default="")
            if hit in _FILING_IDS:
                return hit
            if "chord" in ent:
                return "chords"
            if "pickle" in ent:
                return "pickleball"
            if "jason" in ent or "personal" in ent or "garriotte" in ent:
                return "personal"
    return "chords"


def row_filing_book(row: dict, payload: dict | None = None) -> str:
    return infer_filing_book(payload, row)


def filter_rows_by_book(rows: list, book: str, payload: dict | None = None) -> list[dict]:
    want = normalize_filing_book(book, default=ALL_BOOKS)
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if want == ALL_BOOKS or row_filing_book(row, payload) == want:
            out.append(row)
    return out


def set_row_filing_book(payload: dict, index: int, book: str) -> tuple[dict | None, str | None]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if not isinstance(index, int) or index < 0 or index >= len(rows):
        return None, "Transaction not found"
    row = rows[index]
    if not isinstance(row, dict):
        return None, "Transaction not found"
    bid = normalize_filing_book(book, default="")
    if bid not in _FILING_IDS:
        return None, "Unknown filing book"
    row["filing_book"] = bid
    payload["rows"] = rows
    return payload, None


def gross_receipts_label(chart: list[dict] | None = None) -> str:
    items = chart or []
    for needle in GROSS_RECEIPTS_LABELS:
        for item in items:
            lab = str(item.get("label") or "").strip()
            if not lab:
                continue
            low = lab.lower()
            if low == needle or needle in low:
                return lab
    return "Gross receipts or sales"


def parse_loose_date(value: str, default_year: int | None = None) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    hit = parse_row_date({"Date": s})
    if hit is not None:
        return hit
    year = default_year or datetime.utcnow().year
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", s)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2})", s)
    if not m:
        return None
    month = _MONTH_PREFIX.get(m.group(1)[:3].lower())
    if not month:
        return None
    try:
        return datetime(year, month, int(m.group(2)))
    except ValueError:
        return None


def coalesce_month_day(cells: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(cells):
        tok = cells[i]
        if i + 1 < len(cells) and tok[:3].lower() in _MONTH_PREFIX and re.fullmatch(r"\d{1,2}", cells[i + 1] or ""):
            out.append(f"{tok} {cells[i + 1]}")
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def split_paste_cells(line: str) -> list[str]:
    raw = (line or "").strip()
    if not raw:
        return []
    if "\t" in raw:
        return [c.strip() for c in raw.split("\t")]
    if "," in raw and re.search(r"\d", raw):
        return [c.strip() for c in raw.split(",")]
    parts = re.split(r"\s{2,}", raw)
    if len(parts) >= 3:
        return [c.strip() for c in parts if c.strip()]
    return raw.split()


def looks_like_header(cells: list[str]) -> bool:
    blob = " ".join(cells).lower()
    if "amount" in blob and "date" in blob:
        return True
    if blob.startswith("trans date") or blob.startswith("post date"):
        return True
    return blob in ("date", "description", "amount")


def pdf_text_looks_usable(text: str) -> bool:
    """Thin PDF text layers (a few glyphs) should lose to OCR."""
    s = (text or "").strip()
    if len(s) < MIN_PDF_TEXT_CHARS:
        return False
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) < MIN_PDF_TEXT_LINES:
        return False
    money = len(re.findall(r"\$?\d{1,3}(?:,\d{3})*\.\d{2}", s))
    dates = len(re.findall(r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2})\b", s))
    return money >= 3 and dates >= 3


def year_from_drop_name(name: str, fallback: int | None = None) -> int | None:
    n = (name or "").replace("\\", "/").split("/")[-1]
    m = re.search(r"(20\d{2})", n)
    if m:
        return int(m.group(1))
    return fallback


def is_statement_amount(cell: str) -> bool:
    s = str(cell or "").strip()
    if not s or parse_money(s) is None:
        return False
    if not re.search(r"\d", s):
        return False
    if "$" in s or "," in s or re.search(r"\.\d{2}\b", s):
        return True
    return False


def clean_statement_payee(value: str) -> str:
    s = re.sub(r"\s+", " ", str(value or "").strip())
    s = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9.)%/-]+$", "", s)
    return s.strip(" -")


def payee_is_usable(value: str) -> bool:
    s = clean_statement_payee(value)
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(s) < MIN_STATEMENT_PAYEE or len(letters) < MIN_STATEMENT_PAYEE:
        return False
    low = s.lower()
    if low in {
        "total", "amount", "date", "description", "balance", "payment",
        "purchases", "fees", "interest", "page", "continued",
    }:
        return False
    return True


def stitch_statement_lines(lines: list[str]) -> list[str]:
    """Join wrapped OCR rows so date and amount land on one line."""
    out: list[str] = []
    buf = ""
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if buf:
            buf = f"{buf} {line}"
            if _line_has_amount(buf):
                out.append(buf)
                buf = ""
            continue
        if _line_has_date(line) and not _line_has_amount(line):
            buf = line
            continue
        out.append(line)
    if buf:
        out.append(buf)
    return out


def _line_has_date(line: str) -> bool:
    cells = coalesce_month_day(split_paste_cells(line))
    if cells and parse_loose_date(cells[0]) is not None:
        return True
    return bool(re.search(
        r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2})\b",
        line or "",
    ))


def _line_has_amount(line: str) -> bool:
    return bool(re.search(r"\$?\d{1,3}(?:,\d{3})*\.\d{2}", line or ""))


def parse_pasted_lines(
    text: str,
    default_year: int | None = None,
    max_lines: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Parse copied statement rows. Never log payees or amounts."""
    errors: list[str] = []
    rows: list[dict] = []
    if not isinstance(text, str) or not text.strip():
        return [], ["Paste is empty"]
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.strip() for ln in raw_lines if ln.strip()]
    cap = MAX_PASTE_LINES if max_lines is None else int(max_lines)
    if len(lines) > cap:
        return [], [f"Paste is over {cap} lines"]
    year = default_year
    lines = stitch_statement_lines(lines)
    if len(lines) > cap:
        lines = lines[:cap]
    for i, line in enumerate(lines, start=1):
        cells = coalesce_month_day(split_paste_cells(line))
        if not cells:
            continue
        if looks_like_header(cells):
            continue
        money_idx = None
        for idx in range(len(cells) - 1, 0, -1):
            if is_statement_amount(cells[idx]):
                money_idx = idx
                break
        if money_idx is None:
            blob = " ".join(cells[1:]) if len(cells) > 1 else line
            m = re.search(
                r"(-?\$?\(?[0-9][0-9,]*(?:\.[0-9]{2})\)?-?)$",
                blob.strip(),
            )
            if not m:
                continue
            amount_s = m.group(1)
            payee_s = clean_statement_payee(blob[: m.start()])
            dt = parse_loose_date(cells[0], year)
            if dt is None and len(cells) >= 2:
                dt = parse_loose_date(cells[1], year)
                if dt is not None and payee_s.lower().startswith(str(cells[1]).lower()):
                    payee_s = clean_statement_payee(payee_s[len(cells[1]):])
            if dt is None or not payee_is_usable(payee_s):
                continue
            amt = parse_money(amount_s)
            if amt is None:
                continue
            rows.append({"date": dt.strftime("%Y-%m-%d"), "payee": payee_s, "amount": amt})
            continue
        amount_s = cells[money_idx]
        dt = parse_loose_date(cells[0], year)
        start = 1
        if len(cells) > 3:
            post = parse_loose_date(cells[1], year)
            if post is not None:
                if dt is None:
                    dt = post
                start = 2
        if dt is None:
            continue
        payee_s = clean_statement_payee(" ".join(c for c in cells[start:money_idx] if c))
        amt = parse_money(amount_s)
        if amt is None or not payee_is_usable(payee_s):
            continue
        rows.append({"date": dt.strftime("%Y-%m-%d"), "payee": payee_s, "amount": amt})
    if not rows:
        errors.append("No rows found")
    return rows, errors

def account_labels_from_names(names: list[str], exclude_path: str = "") -> list[str]:
    """Other Organize ledgers as transfer-account labels (filename stem)."""
    out: list[str] = []
    seen: set[str] = set()
    excl = (exclude_path or "").replace("\\", "/").strip().lower()
    excl_name = excl.split("/")[-1]
    for name in names or []:
        path = ledger_path_from_name(name) or ""
        if not path or is_manual_ledger(path):
            continue
        low = path.lower()
        if excl and (low == excl or low.split("/")[-1] == excl_name):
            continue
        label = account_label({}, path)
        key = label.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def ledger_choices(names: list[str], labels: dict[str, str] | None = None) -> list[dict]:
    """All Transactions first, then each Organize mapped file as its own account."""
    out = [{"path": ALL_TRANSACTIONS, "label": "All Transactions"}]
    mapped = sorted(n for n in names if is_mapped_name(n))
    if MANUAL_LEDGER_NAME in mapped:
        mapped.remove(MANUAL_LEDGER_NAME)
        mapped.insert(0, MANUAL_LEDGER_NAME)
    for name in mapped:
        path = ledger_path_from_name(name)
        if not path:
            continue
        label = ""
        if labels and path in labels:
            label = str(labels[path] or "").strip()
        if not label:
            label = account_label({}, path)
        out.append({"path": path, "label": label})
    return out


def account_label(payload: dict, path: str = "") -> str:
    if isinstance(payload, dict):
        raw = payload.get("account") or payload.get("source_account")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    name = (path or "").replace("\\", "/").split("/")[-1]
    if name.endswith(LEDGER_SUFFIX):
        name = name[: -len(LEDGER_SUFFIX)]
    return name.replace("-", " ").replace("_", " ").strip() or "Statement"


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


def signed_amount_for_kind(amount: float, kind: str) -> float:
    """Expenses are outflows; income is inflow. Transfers keep the stored sign."""
    if kind == EXPENSE_KIND:
        return -abs(amount)
    if kind == INCOME_KIND:
        return abs(amount)
    return float(amount)


def write_row_amount(row: dict, amount: float) -> None:
    fields = row.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        row["fields"] = fields
    fields["Debit/Credit"] = amount
    if "Amount" in fields:
        fields["Amount"] = amount
    row["amount"] = amount


def write_row_payee(row: dict, payee: str) -> None:
    fields = row.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        row["fields"] = fields
    fields["Name"] = payee
    row["payee"] = payee


def write_row_date(row: dict, date: str) -> str | None:
    dt = parse_row_date({"Date": date})
    if dt is None:
        return "Date is required"
    stamp = dt.strftime("%Y-%m-%d")
    fields = row.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        row["fields"] = fields
    fields["Date"] = stamp
    row["date"] = stamp
    return None


def coerce_row_amount(row: dict, kind: str) -> None:
    amt = row_signed_amount(row)
    if amt is None:
        return
    coerced = signed_amount_for_kind(amt, kind)
    if coerced != amt:
        write_row_amount(row, coerced)


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
            hit = datetime.strptime(s, fmt)
            if "%Y" not in fmt:
                return None
            return hit
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


def norm_phrase(value: Any) -> str:
    t = str(value or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b\d{2,}\b", " ", t)
    return " ".join(t.split())


def row_match_text(row: dict) -> str:
    row = flatten_row(row)
    parts: list[str] = []
    for key in ("Name", "Description", "description", "Payee", "Memo", "Additional information"):
        val = row.get(key)
        if val not in (None, ""):
            parts.append(str(val))
    return norm_phrase(" ".join(parts))


def category_label(row: dict) -> str:
    lab = row.get("category_label")
    if isinstance(lab, str) and lab.strip():
        return lab.strip()
    return UNCATEGORIZED


def chart_kind(label: str, item: dict | None = None) -> str:
    """Income, expense, or transfer (balance-sheet) for grouping. Explicit kind wins."""
    if isinstance(item, dict):
        if str(item.get("layer") or "").strip().lower() == "account":
            return TRANSFER_KIND
        raw = str(item.get("kind") or item.get("side") or "").strip().lower()
        if raw in ("balance", "liability", "asset", "equity"):
            return TRANSFER_KIND
        if raw in (INCOME_KIND, EXPENSE_KIND, TRANSFER_KIND):
            return raw
    lab = (label or "").strip().lower()
    if not lab:
        return EXPENSE_KIND
    if any(needle in lab for needle in TRANSFER_LABEL_NEEDLES):
        return TRANSFER_KIND
    if any(needle in lab for needle in INCOME_LABEL_NEEDLES):
        return INCOME_KIND
    if "income" in lab:
        return INCOME_KIND
    return EXPENSE_KIND


def owner_loan_chart_item() -> dict:
    return builtin_transfer_item(OWNER_LOAN_LABEL)


def builtin_transfer_item(label: str) -> dict:
    return {
        "label": str(label or "").strip(),
        "code": None,
        "layer": "builtin",
        "kind": TRANSFER_KIND,
    }


def ensure_builtin_chart(
    items: list[dict],
    exclude_labels: list[str] | None = None,
) -> list[dict]:
    out = list(items or [])
    seen = {str(c.get("label") or "").strip().lower() for c in out}
    skip = {str(s or "").strip().lower() for s in (exclude_labels or []) if str(s or "").strip()}
    for label in BUILTIN_TRANSFER_LABELS:
        key = label.lower()
        if key in seen or key in skip:
            continue
        out.append(builtin_transfer_item(label))
        seen.add(key)
    return out


def transfer_account_item(label: str) -> dict:
    return {
        "label": str(label or "").strip(),
        "code": None,
        "layer": "account",
        "kind": TRANSFER_KIND,
    }


def merge_chart_with_accounts(
    chart: list[dict],
    account_labels: list[str],
    exclude_labels: list[str] | None = None,
) -> list[dict]:
    """Other statement accounts are transfer targets, not income or expense."""
    out = list(chart or [])
    by_key: dict[str, dict] = {}
    for item in out:
        key = str(item.get("label") or "").strip().lower()
        if key:
            by_key[key] = item
    skip = {str(s or "").strip().lower() for s in (exclude_labels or []) if str(s or "").strip()}
    for raw in account_labels or []:
        label = str(raw or "").strip()
        if not label:
            continue
        key = label.lower()
        if key in skip:
            continue
        if key in by_key:
            by_key[key]["kind"] = TRANSFER_KIND
            by_key[key]["layer"] = "account"
            continue
        rec = transfer_account_item(label)
        out.append(rec)
        by_key[key] = rec
    return ensure_builtin_chart(out, exclude_labels=list(skip))


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
            rec["kind"] = chart_kind(label, item if isinstance(item, dict) else rec)
            out.append(rec)
    return ensure_builtin_chart(out)


def chart_lookup(chart: list[dict], label: str) -> dict | None:
    want = (label or "").strip().lower()
    if not want:
        return None
    for c in chart:
        if str(c.get("label") or "").strip().lower() == want:
            return c
    return None


def add_write_in_expense(payload: dict, label: str) -> tuple[dict | None, dict | None, str | None]:
    """Add a Presence-local expense bucket. Does not change the product chart."""
    if not isinstance(payload, dict):
        return None, None, "Ledger JSON must be an object"
    wanted = " ".join(str(label or "").split())
    if not wanted or wanted == WRITE_IN_EXPENSE:
        return None, None, "Expense name is required"
    if len(wanted) > MAX_CATEGORY_LABEL:
        return None, None, f"Expense name is over {MAX_CATEGORY_LABEL} characters"
    key = wanted.lower()
    reserved = {UNCATEGORIZED.lower(), ALL_BOOKS, "all", "expenses", "income", "transfers"}
    reserved.update(s.lower() for s in _RESERVED_STEMS)
    if key in reserved:
        return None, None, "That name is reserved"
    stored = working_chart_from_payload(payload)
    hit = chart_lookup(stored, wanted)
    if hit is not None:
        return payload, hit, None
    rec = {
        "label": wanted,
        "code": None,
        "layer": "write-in",
        "kind": EXPENSE_KIND,
    }
    stored.append(rec)
    payload["working_chart"] = stored
    return payload, rec, None


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


def row_is_disabled(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    return bool(row.get("disabled") or row.get("excluded"))


def row_is_manual(row: dict, source_path: str = "") -> bool:
    if is_manual_ledger(source_path):
        return True
    if not isinstance(row, dict):
        return False
    origin = str(row.get("origin") or "").strip().lower()
    return origin in ("manual", "typed")


def active_rows(rows: list) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict) and not row_is_disabled(row):
            out.append(row)
    return out


def line_preview(row: dict, source_path: str, index: int, account: str = "") -> dict:
    amt = row_signed_amount(row)
    dt = parse_row_date(row)
    acct = str(flatten_row(row).get("account") or "").strip() or account or account_label({}, source_path)
    cat = category_label(row)
    return {
        "id": f"{source_path}#{index}",
        "source_path": source_path,
        "index": index,
        "date": dt.strftime("%Y-%m-%d") if dt else "",
        "payee": row_payee(row),
        "amount": amt,
        "category": cat,
        "needs_review": bool(row.get("needs_review")),
        "account": acct,
        "disabled": row_is_disabled(row),
        "manual": row_is_manual(row, source_path),
        "filing_book": row_filing_book(row),
        "kind": chart_kind(cat),
    }


def merge_working_charts(payloads: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in working_chart_from_payload(payload):
            if str(item.get("layer") or "") == "builtin":
                continue
            key = str(item.get("label") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return ensure_builtin_chart(merged)


def merge_vendor_maps(payloads: list[dict]) -> list[dict]:
    by_phrase: dict[str, dict] = {}
    for payload in payloads:
        for rule in vendor_map_from_payload(payload):
            phrase = rule["phrase"]
            prior = by_phrase.get(phrase)
            if prior is None:
                by_phrase[phrase] = dict(rule)
                continue
            if rule.get("source") == "operator" and prior.get("source") != "operator":
                by_phrase[phrase] = dict(rule)
    return sorted(by_phrase.values(), key=lambda x: x["phrase"])


def collect_lines(
    rows: list,
    source_path: str,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    account: str = "",
    limit: int = MAX_COMBINED_LINES,
    filing_book: str = ALL_BOOKS,
    payload: dict | None = None,
) -> list[dict]:
    want = (category or "").strip()
    all_cats = not want or want == ALL_LINES
    disabled_only = want == DISABLED_LINES
    book = normalize_filing_book(filing_book, default=ALL_BOOKS)
    out: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if book != ALL_BOOKS and row_filing_book(row, payload) != book:
            continue
        if not in_period(row, year, month):
            continue
        if disabled_only:
            if not row_is_disabled(row):
                continue
        elif row_is_disabled(row) and not all_cats:
            continue
        if not all_cats and not disabled_only and category_label(row) != want:
            continue
        out.append(line_preview(row, source_path, i, account=account))
        if len(out) >= limit:
            break
    return out


def pnl_from_rows(
    rows: list[dict],
    year: int | None = None,
    month: int | None = None,
    filing_book: str = ALL_BOOKS,
    payload: dict | None = None,
    chart: list[dict] | None = None,
) -> dict:
    """Category rollup. Totals are floats for the UI; callers must not log them."""
    income: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    expense: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    transfer: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    uncat = {"total": 0.0, "count": 0}
    skipped = 0
    used = 0
    book = normalize_filing_book(filing_book, default=ALL_BOOKS)
    chart_items = chart if isinstance(chart, list) else working_chart_from_payload(payload or {})
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        if row_is_disabled(row):
            skipped += 1
            continue
        if book != ALL_BOOKS and row_filing_book(row, payload) != book:
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
        hit = chart_lookup(chart_items, label)
        kind = chart_kind(label, hit)
        if kind == INCOME_KIND:
            bucket = income
            bucket[label]["total"] += abs(amt)
        elif kind == TRANSFER_KIND:
            bucket = transfer
            bucket[label]["total"] += amt
        else:
            bucket = expense
            bucket[label]["total"] += abs(amt)
        bucket[label]["count"] += 1

    def _lines(store: dict[str, dict], *, expenses: bool) -> list[dict]:
        items = []
        for label, rec in store.items():
            total = rec["total"]
            items.append({
                "label": label,
                "count": rec["count"],
                "total": abs(total) if expenses else total,
            })
        items.sort(key=lambda x: (-abs(x["total"]), x["label"].lower()))
        return items

    income_lines = _lines(income, expenses=False)
    expense_lines = _lines(expense, expenses=True)
    transfer_lines = _lines(transfer, expenses=False)
    income_total = sum(x["total"] for x in income_lines)
    expense_total = sum(x["total"] for x in expense_lines)
    transfer_total = sum(x["total"] for x in transfer_lines)
    owner_loan_total = sum(
        x["total"] for x in transfer_lines if str(x["label"]).strip().lower() == OWNER_LOAN_LABEL.lower()
    )
    disabled_count = 0
    for row in rows:
        if not isinstance(row, dict) or not row_is_disabled(row):
            continue
        if book != ALL_BOOKS and row_filing_book(row, payload) != book:
            continue
        if not in_period(row, year, month):
            continue
        disabled_count += 1
    return {
        "income": income_lines,
        "expenses": expense_lines,
        "transfers": transfer_lines,
        "uncategorized": {
            "label": UNCATEGORIZED,
            "count": uncat["count"],
            "total": uncat["total"],
        },
        "income_total": income_total,
        "expense_total": expense_total,
        "transfer_total": transfer_total,
        "owner_loan_total": owner_loan_total,
        "disabled_count": disabled_count,
        "net": income_total - expense_total + uncat["total"],
        "row_count": used,
        "skipped": skipped,
    }


def apply_category(
    payload: dict,
    index: int,
    label: str,
    chart: list[dict] | None = None,
) -> tuple[dict | None, str | None]:
    return apply_categories(payload, [index], label, chart=chart)


def apply_categories(
    payload: dict,
    indexes: list[int],
    label: str,
    chart: list[dict] | None = None,
) -> tuple[dict | None, str | None]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if not indexes:
        return None, "No transactions selected"
    chart_items = chart if isinstance(chart, list) else working_chart_from_payload(payload)
    hit = chart_lookup(chart_items, label)
    if hit is None:
        payload, hit, werr = add_write_in_expense(payload, label)
        if werr or hit is None:
            return None, werr or "Category is not on the working chart"
        chart_items = working_chart_from_payload(payload)
    stored = working_chart_from_payload(payload)
    if chart_lookup(stored, hit["label"]) is None:
        stored.append({
            "label": hit["label"],
            "code": hit.get("code"),
            "layer": hit.get("layer") or "account",
            "kind": chart_kind(hit["label"], hit),
        })
    payload["working_chart"] = stored
    seen: set[int] = set()
    for index in indexes:
        if not isinstance(index, int) or index < 0 or index >= len(rows):
            return None, "Transaction not found"
        if index in seen:
            continue
        row = rows[index]
        if not isinstance(row, dict):
            return None, "Transaction not found"
        row["category_label"] = hit["label"]
        row["category_code"] = hit.get("code")
        row["category_layer"] = hit.get("layer")
        coerce_row_amount(row, chart_kind(hit["label"], hit))
        row["needs_review"] = False
        row["match_rule"] = "operator"
        seen.add(index)
    payload["rows"] = rows
    teach_from_indexes(payload, list(seen), hit["label"])
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review")
    )
    return payload, None


def vendor_map_from_payload(payload: dict) -> list[dict]:
    raw = payload.get("vendor_map") if isinstance(payload, dict) else None
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        phrase = norm_phrase(item.get("phrase") or item.get("vendor") or "")
        label = str(item.get("label") or item.get("category") or "").strip()
        if len(phrase) < MIN_MAP_PHRASE or not label:
            continue
        if phrase in seen:
            continue
        seen.add(phrase)
        out.append({
            "phrase": phrase,
            "label": label,
            "source": item.get("source") or "operator",
        })
    out.sort(key=lambda x: x["phrase"])
    return out


def teach_vendor_rule(payload: dict, phrase: Any, label: str) -> bool:
    """Upsert one operator phrase → category. Returns True if the map changed."""
    if not isinstance(payload, dict):
        return False
    cleaned = norm_phrase(phrase)
    if len(cleaned) < MIN_MAP_PHRASE:
        return False
    chart = working_chart_from_payload(payload)
    hit = chart_lookup(chart, label)
    if hit is None:
        return False
    rules = vendor_map_from_payload(payload)
    for rule in rules:
        if rule["phrase"] == cleaned:
            if rule["label"] == hit["label"] and rule.get("source") == "operator":
                return False
            rule["label"] = hit["label"]
            rule["source"] = "operator"
            payload["vendor_map"] = sorted(rules, key=lambda x: x["phrase"])
            return True
    rules.append({"phrase": cleaned, "label": hit["label"], "source": "operator"})
    payload["vendor_map"] = sorted(rules, key=lambda x: x["phrase"])
    return True


def teach_from_indexes(payload: dict, indexes: list[int], label: str) -> int:
    """Teach one phrase per distinct payee on the selected rows."""
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return 0
    taught = 0
    seen: set[str] = set()
    for index in indexes:
        if not isinstance(index, int) or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, dict):
            continue
        phrase = norm_phrase(row_payee(row) or row_match_text(row))
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)
        if teach_vendor_rule(payload, phrase, label):
            taught += 1
    return taught


def set_vendor_map(payload: dict, rules: list[dict]) -> tuple[dict | None, str | None]:
    if not isinstance(payload, dict):
        return None, "Ledger JSON must be an object"
    chart = working_chart_from_payload(payload)
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in rules:
        if not isinstance(item, dict):
            continue
        phrase = norm_phrase(item.get("phrase") or item.get("vendor") or "")
        label = str(item.get("label") or item.get("category") or "").strip()
        if len(phrase) < MIN_MAP_PHRASE:
            return None, f"Phrase must be at least {MIN_MAP_PHRASE} characters"
        hit = chart_lookup(chart, label)
        if hit is None:
            return None, "Category is not on the working chart"
        if phrase in seen:
            return None, "Duplicate phrase in map"
        seen.add(phrase)
        cleaned.append({
            "phrase": phrase,
            "label": hit["label"],
            "source": item.get("source") or "operator",
        })
    cleaned.sort(key=lambda x: x["phrase"])
    payload["vendor_map"] = cleaned
    return payload, None


def seed_vendor_map_from_placed(payload: dict) -> tuple[dict | None, dict | None, str | None]:
    """Fill vendor_map from already-placed payees. Operator rules win on a clash."""
    if not isinstance(payload, dict):
        return None, None, "Ledger JSON must be an object"
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None, None, "Ledger has no rows"
    chart = working_chart_from_payload(payload)
    existing = vendor_map_from_payload(payload)
    by_phrase = {r["phrase"]: dict(r) for r in existing}
    added = 0
    skipped_conflict = 0
    seen_phrase: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = category_label(row)
        if label == UNCATEGORIZED or row.get("needs_review"):
            continue
        hit = chart_lookup(chart, label)
        if hit is None:
            continue
        phrase = norm_phrase(row_payee(row) or row_match_text(row))
        if len(phrase) < MIN_MAP_PHRASE or phrase in seen_phrase:
            continue
        seen_phrase.add(phrase)
        prior = by_phrase.get(phrase)
        if prior:
            if prior["label"] != hit["label"] and prior.get("source") == "operator":
                skipped_conflict += 1
                continue
            if prior["label"] == hit["label"]:
                continue
        by_phrase[phrase] = {
            "phrase": phrase,
            "label": hit["label"],
            "source": "placed",
        }
        added += 1
    cleaned = sorted(by_phrase.values(), key=lambda x: x["phrase"])
    payload["vendor_map"] = cleaned
    return payload, {"added": added, "kept": len(existing), "skipped_conflict": skipped_conflict}, None


def apply_vendor_map(payload: dict) -> tuple[dict | None, dict | None, str | None]:
    """Re-tag uncategorized / review rows from the operator map. Leaves placed rows alone."""
    if not isinstance(payload, dict):
        return None, None, "Ledger JSON must be an object"
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None, None, "Ledger has no rows"
    chart = working_chart_from_payload(payload)
    rules = vendor_map_from_payload(payload)
    applied = 0
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = category_label(row)
        if row_is_disabled(row):
            skipped += 1
            continue
        if label != UNCATEGORIZED and not row.get("needs_review"):
            skipped += 1
            continue
        text = row_match_text(row)
        if not text:
            continue
        hits: list[dict] = []
        for rule in rules:
            phrase = rule["phrase"]
            if phrase and phrase in text:
                hits.append(rule)
        if not hits:
            continue
        labels = {h["label"] for h in hits}
        if len(labels) > 1:
            continue
        hit = chart_lookup(chart, hits[0]["label"])
        if hit is None:
            continue
        row["category_label"] = hit["label"]
        row["category_code"] = hit.get("code")
        row["category_layer"] = hit.get("layer")
        row["needs_review"] = False
        row["match_rule"] = "vendor-map"
        applied += 1
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review")
    )
    return payload, {"applied": applied, "skipped_placed": skipped}, None


def empty_manual_payload(source: dict | None = None) -> dict:
    src = source if isinstance(source, dict) else {}
    return {
        "entity": src.get("entity") or "",
        "account": "Manual",
        "origin": "manual",
        "filing_book": infer_filing_book(src),
        "working_chart": working_chart_from_payload(src),
        "vendor_map": vendor_map_from_payload(src) if src else [],
        "rows": [],
        "needs_review_count": 0,
    }


def build_manual_row(date: str, payee: str, amount: Any, category: str = "", chart: list[dict] | None = None, filing_book: str = "chords") -> tuple[dict | None, str | None]:
    payee_s = str(payee or "").strip()
    if not payee_s:
        return None, "Payee is required"
    if len(payee_s) > 200:
        return None, "Payee is too long"
    dt = parse_row_date({"Date": date})
    if dt is None:
        return None, "Date is required"
    amt = parse_money(amount)
    if amt is None:
        return None, "Amount is required"
    bid = normalize_filing_book(filing_book, default="chords")
    if bid not in _FILING_IDS:
        bid = "chords"
    label = str(category or "").strip()
    hit = None
    if label and label != UNCATEGORIZED:
        hit = chart_lookup(chart or [], label)
        if hit is None:
            return None, "Category is not on the working chart"
        kind = chart_kind(hit["label"], hit)
        amt = signed_amount_for_kind(amt, kind)
    row = {
        "origin": "manual",
        "disabled": False,
        "needs_review": False,
        "match_rule": "operator",
        "filing_book": bid,
        "fields": {
            "Date": dt.strftime("%Y-%m-%d"),
            "Name": payee_s,
            "Debit/Credit": amt,
        },
    }
    if hit is not None:
        row["category_label"] = hit["label"]
        row["category_code"] = hit.get("code")
        row["category_layer"] = hit.get("layer")
    return row, None


def add_manual_row(payload: dict, date: str, payee: str, amount: Any, category: str = "", filing_book: str = "") -> tuple[dict | None, dict | None, str | None]:
    if not isinstance(payload, dict):
        return None, None, "Ledger JSON must be an object"
    rows = payload.get("rows")
    if rows is None:
        rows = []
        payload["rows"] = rows
    if not isinstance(rows, list):
        return None, None, "Ledger has no rows"
    if len(rows) >= MAX_COMBINED_LINES:
        return None, None, "Ledger is full"
    chart = working_chart_from_payload(payload)
    book = filing_book or infer_filing_book(payload)
    label = str(category or "").strip()
    if label and label != UNCATEGORIZED and chart_lookup(chart, label) is None:
        payload, _hit, werr = add_write_in_expense(payload, label)
        if werr:
            return None, None, werr
        chart = working_chart_from_payload(payload)
    row, err = build_manual_row(date, payee, amount, category, chart, filing_book=book)
    if err or row is None:
        return None, None, err
    rows.append(row)
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review") and not row_is_disabled(r)
    )
    return payload, line_preview(row, MANUAL_LEDGER_PATH, len(rows) - 1, account="Manual"), None


def update_row_details(
    payload: dict,
    index: int,
    *,
    date: str | None = None,
    payee: str | None = None,
    amount: Any = None,
    category: str | None = None,
    filing_book: str | None = None,
    chart: list[dict] | None = None,
) -> tuple[dict | None, str | None]:
    """Edit any field on a ledger line. Dropdown values must be on the chart."""
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if not isinstance(index, int) or index < 0 or index >= len(rows):
        return None, "Transaction not found"
    row = rows[index]
    if not isinstance(row, dict):
        return None, "Transaction not found"
    chart_items = chart if isinstance(chart, list) else working_chart_from_payload(payload)
    hit = None
    label = None if category is None else str(category).strip()
    if label:
        if label == UNCATEGORIZED:
            row.pop("category_label", None)
            row.pop("category_code", None)
            row.pop("category_layer", None)
            row["needs_review"] = True
        else:
            hit = chart_lookup(chart_items, label)
            if hit is None:
                payload, hit, werr = add_write_in_expense(payload, label)
                if werr or hit is None:
                    return None, werr or "Category is not on the working chart"
                chart_items = working_chart_from_payload(payload)
            stored = working_chart_from_payload(payload)
            if chart_lookup(stored, hit["label"]) is None:
                stored.append({
                    "label": hit["label"],
                    "code": hit.get("code"),
                    "layer": hit.get("layer") or "account",
                    "kind": chart_kind(hit["label"], hit),
                })
            payload["working_chart"] = stored
            row["category_label"] = hit["label"]
            row["category_code"] = hit.get("code")
            row["category_layer"] = hit.get("layer")
            row["needs_review"] = False
            row["match_rule"] = "operator"
    if date is not None:
        derr = write_row_date(row, date)
        if derr:
            return None, derr
    if payee is not None:
        payee_s = str(payee).strip()
        if not payee_s:
            return None, "Payee is required"
        if len(payee_s) > 200:
            return None, "Payee is too long"
        write_row_payee(row, payee_s)
    if amount is not None and amount != "":
        amt = parse_money(amount)
        if amt is None:
            return None, "Amount is required"
        kind = chart_kind(category_label(row), hit or chart_lookup(chart_items, category_label(row)))
        if kind in (EXPENSE_KIND, INCOME_KIND):
            amt = signed_amount_for_kind(amt, kind)
        write_row_amount(row, amt)
    elif hit is not None:
        coerce_row_amount(row, chart_kind(hit["label"], hit))
    if filing_book is not None and str(filing_book).strip():
        bid = normalize_filing_book(filing_book, default="")
        if bid not in _FILING_IDS:
            return None, "Unknown filing book"
        row["filing_book"] = bid
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review") and not row_is_disabled(r)
    )
    return payload, None


def set_row_disabled(payload: dict, index: int, disabled: bool) -> tuple[dict | None, str | None]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if not isinstance(index, int) or index < 0 or index >= len(rows):
        return None, "Transaction not found"
    row = rows[index]
    if not isinstance(row, dict):
        return None, "Transaction not found"
    row["disabled"] = bool(disabled)
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review") and not row_is_disabled(r)
    )
    return payload, None


def remove_manual_row(payload: dict, index: int) -> tuple[dict | None, str | None]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "Ledger has no rows"
    if not isinstance(index, int) or index < 0 or index >= len(rows):
        return None, "Transaction not found"
    row = rows[index]
    if not isinstance(row, dict):
        return None, "Transaction not found"
    if not row_is_manual(row):
        return None, "Only typed lines can be removed"
    del rows[index]
    payload["rows"] = rows
    payload["needs_review_count"] = sum(
        1 for r in rows if isinstance(r, dict) and r.get("needs_review") and not row_is_disabled(r)
    )
    return payload, None


def add_manual_rows(payload: dict, items: list[dict], filing_book: str = "", default_category: str = "") -> tuple[dict | None, int, str | None]:
    if not isinstance(payload, dict):
        return None, 0, "Ledger JSON must be an object"
    if not isinstance(items, list) or not items:
        return None, 0, "No rows to add"
    if len(items) > MAX_PASTE_LINES:
        return None, 0, f"Paste is over {MAX_PASTE_LINES} lines"
    added = 0
    last_err = None
    for item in items:
        if not isinstance(item, dict):
            last_err = "Row must be an object"
            continue
        updated, _line, err = add_manual_row(
            payload,
            date=str(item.get("date") or ""),
            payee=item.get("payee") or item.get("name") or "",
            amount=item.get("amount"),
            category=str(item.get("category") or default_category or ""),
            filing_book=str(item.get("filing_book") or filing_book or ""),
        )
        if err or updated is None:
            last_err = err
            continue
        payload = updated
        added += 1
    if added == 0:
        return None, 0, last_err or "No rows added"
    return payload, added, None

