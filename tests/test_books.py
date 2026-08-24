"""BOOKLED1 — P&L helper tests. Dummy amounts only."""

from src.dashboard import books as bk


def _row(date, amount, category=None, name="Vendor", review=False):
    rec = {
        "fields": {"Date": date, "Debit/Credit": amount, "Name": name},
        "needs_review": review,
    }
    if category is not None:
        rec["category_label"] = category
    return rec


def test_clean_books_path():
    ok, err = bk.clean_books_path("Bookkeeping/Organize/foo.mapped.json")
    assert ok == "Bookkeeping/Organize/foo.mapped.json" and not err
    assert bk.clean_books_path("../secret.mapped.json")[0] is None
    assert bk.clean_books_path("Bookkeeping/Drop/foo.mapped.json")[0] is None
    assert bk.clean_books_path("Bookkeeping/Organize/foo.json")[0] is None
    assert bk.clean_books_path("Tables/foo.mapped.json")[0] is None


def test_parse_money_and_signed_amount():
    assert bk.parse_money("(12.50)") == -12.5
    assert bk.parse_money("$1,200.00") == 1200.0
    row = {"fields": {"Debit/Credit": "-40.00", "Name": "Bank"}}
    assert bk.row_signed_amount(row) == -40.0
    row = {"fields": {"Credit": "10.00"}}
    assert bk.row_signed_amount(row) == 10.0


def test_pnl_rollup_and_period():
    rows = [
        _row("2025-01-15", "-10.00", "BANK SERVICE CHARGES 210"),
        _row("2025-01-20", "-20.00", "COMPUTER - HOSTING 731"),
        _row("2025-02-01", "100.00", "Other income"),
        _row("2025-02-02", "-5.00"),
        _row("2024-12-01", "-1.00", "BANK SERVICE CHARGES 210"),
    ]
    all_time = bk.pnl_from_rows(rows)
    assert all_time["row_count"] == 5
    assert all_time["uncategorized"]["count"] == 1
    hosting = next(x for x in all_time["expenses"] if "HOSTING" in x["label"])
    assert hosting["count"] == 1
    assert hosting["total"] == 20.0
    assert all_time["income_total"] == 100.0

    jan = bk.pnl_from_rows(rows, year=2025, month=1)
    assert jan["row_count"] == 2
    assert jan["income_total"] == 0.0
    assert jan["expense_total"] == 30.0

    periods = bk.available_periods(rows)
    assert 2025 in periods["years"]
    assert {"year": 2025, "month": 1} in periods["months"]


def test_apply_category_uses_working_chart_only():
    payload = {
        "working_chart": [
            {"label": "BANK SERVICE CHARGES 210", "code": "210", "layer": "write-in"},
            {"label": "Advertising", "code": None, "layer": "official"},
        ],
        "rows": [
            _row("2025-01-15", "-10.00"),
        ],
    }
    updated, err = bk.apply_category(payload, 0, "Advertising")
    assert err is None and updated is not None
    assert updated["rows"][0]["category_label"] == "Advertising"
    assert updated["rows"][0]["needs_review"] is False
    assert updated["rows"][0]["match_rule"] == "operator"

    bad, err = bk.apply_category(payload, 0, "Invented account")
    assert bad is None and err
    missing, err = bk.apply_category(payload, 9, "Advertising")
    assert missing is None and err
