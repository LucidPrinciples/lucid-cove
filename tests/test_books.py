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
    assert any(r["phrase"] == "vendor" for r in bk.vendor_map_from_payload(updated))


def test_batch_recode_and_vendor_map():
    payload = {
        "working_chart": [
            {"label": "Advertising", "code": None, "layer": "official"},
            {"label": "BANK SERVICE CHARGES 210", "code": "210", "layer": "write-in"},
        ],
        "rows": [
            _row("2025-01-15", "-10.00", name="Acme Ads"),
            _row("2025-01-16", "-11.00", name="Acme Ads"),
            _row("2025-01-17", "-4.00", name="Other Co"),
        ],
    }
    updated, err = bk.apply_categories(payload, [0, 1], "Advertising")
    assert err is None and updated is not None
    assert updated["rows"][0]["category_label"] == "Advertising"
    assert updated["rows"][1]["category_label"] == "Advertising"
    assert updated["rows"][2].get("category_label") is None
    rules = bk.vendor_map_from_payload(updated)
    assert len(rules) == 1
    assert rules[0]["phrase"] == "acme ads"
    assert rules[0]["label"] == "Advertising"

    payload["vendor_map"] = [{"phrase": "other co", "label": "BANK SERVICE CHARGES 210"}]
    mapped, stats, err = bk.apply_vendor_map(payload)
    assert err is None and mapped is not None
    assert stats["applied"] == 1
    assert mapped["rows"][2]["category_label"] == "BANK SERVICE CHARGES 210"
    assert mapped["rows"][0]["category_label"] == "Advertising"

    cleaned, err = bk.set_vendor_map(payload, [{"phrase": "ok phrase", "label": "Advertising"}])
    assert err is None and cleaned is not None
    assert [r["phrase"] for r in cleaned["vendor_map"]] == ["ok phrase"]
    bad, err = bk.set_vendor_map(payload, [{"phrase": "x", "label": "Advertising"}])
    assert bad is None and err


def test_pick_default_ledger_and_account_label():
    assert bk.pick_default_ledger([]) is None
    assert bk.pick_default_ledger(["notes.txt", "a.mapped.json"]) == "Bookkeeping/Organize/a.mapped.json"
    assert bk.ledger_path_from_name("../x.mapped.json") == "Bookkeeping/Organize/x.mapped.json"
    assert bk.ledger_path_from_name("not-mapped.json") is None
    payload = {"account": "Checking"}
    assert bk.account_label(payload, "Bookkeeping/Organize/foo.mapped.json") == "Checking"
    assert "foo" in bk.account_label({}, "Bookkeeping/Organize/foo.mapped.json").lower()


def test_seed_vendor_map_from_placed_keeps_operator():
    payload = {
        "working_chart": [
            {"label": "Advertising", "code": None, "layer": "official"},
            {"label": "Office expense", "code": None, "layer": "official"},
        ],
        "vendor_map": [{"phrase": "acme ads", "label": "Advertising", "source": "operator"}],
        "rows": [
            _row("2025-01-15", "-10.00", "Advertising", name="Acme Ads"),
            _row("2025-01-16", "-8.00", "Office expense", name="Staples Store"),
            _row("2025-01-17", "-3.00", name="Unknown"),
            _row("2025-01-18", "-2.00", "Advertising", name="Acme Ads"),
        ],
    }
    updated, stats, err = bk.seed_vendor_map_from_placed(payload)
    assert err is None and updated is not None
    phrases = {r["phrase"]: r for r in updated["vendor_map"]}
    assert phrases["acme ads"]["source"] == "operator"
    assert phrases["acme ads"]["label"] == "Advertising"
    assert phrases["staples store"]["label"] == "Office expense"
    assert phrases["staples store"]["source"] == "placed"
    assert "unknown" not in phrases
    assert stats["added"] == 1


def test_ledger_choices_all_first():
    names = ["b.mapped.json", "a.mapped.json"]
    choices = bk.ledger_choices(names, {"Bookkeeping/Organize/a.mapped.json": "Checking"})
    assert choices[0]["path"] == bk.ALL_TRANSACTIONS
    assert choices[0]["label"] == "All Transactions"
    assert [c["path"] for c in choices[1:]] == [
        "Bookkeeping/Organize/a.mapped.json",
        "Bookkeeping/Organize/b.mapped.json",
    ]
    assert choices[1]["label"] == "Checking"
    assert bk.is_all_transactions("")
    assert bk.is_all_transactions("all")
    assert not bk.is_all_transactions("Bookkeeping/Organize/a.mapped.json")


def test_collect_all_lines_and_merge():
    rows_a = [
        _row("2025-01-15", "-10.00", "Advertising", name="Acme"),
        _row("2025-01-16", "-4.00", name="Loose"),
    ]
    rows_b = [
        _row("2025-02-01", "20.00", "Other income", name="Client"),
    ]
    all_lines = bk.collect_lines(rows_a, "Bookkeeping/Organize/a.mapped.json", category="*")
    assert len(all_lines) == 2
    one = bk.collect_lines(rows_a, "Bookkeeping/Organize/a.mapped.json", category="Advertising")
    assert len(one) == 1
    charts = bk.merge_working_charts([
        {"working_chart": [{"label": "Advertising"}]},
        {"working_chart": [{"label": "Advertising"}, {"label": "Other income"}]},
    ])
    assert [c["label"] for c in charts] == ["Advertising", "Other income"]
    maps = bk.merge_vendor_maps([
        {"vendor_map": [{"phrase": "acme", "label": "Advertising", "source": "placed"}]},
        {"vendor_map": [{"phrase": "acme", "label": "Office expense", "source": "operator"}]},
    ])
    assert maps[0]["source"] == "operator"
    assert maps[0]["label"] == "Office expense"


def test_manual_add_disable_remove():
    payload = {
        "account": "Manual",
        "working_chart": [{"label": "Advertising", "code": None, "layer": "official"}],
        "rows": [],
    }
    updated, line, err = bk.add_manual_row(payload, "2025-03-02", "Studio Rent", "-40.00", "Advertising")
    assert err is None and updated is not None and line is not None
    assert line["manual"] is True
    assert line["disabled"] is False
    assert updated["rows"][0]["origin"] == "manual"
    assert bk.row_signed_amount(updated["rows"][0]) == -40.0

    missing, _line, err = bk.add_manual_row(payload, "2025-03-02", "Studio Rent", "-5.00", "Invented")
    assert missing is None and err

    statement = {
        "working_chart": [{"label": "Advertising"}],
        "rows": [_row("2025-01-15", "-10.00", "Advertising", name="Bank Fee")],
    }
    disabled, err = bk.set_row_disabled(statement, 0, True)
    assert err is None and disabled is not None
    assert bk.row_is_disabled(disabled["rows"][0])
    pnl = bk.pnl_from_rows(disabled["rows"])
    assert pnl["row_count"] == 0
    all_lines = bk.collect_lines(disabled["rows"], "Bookkeeping/Organize/a.mapped.json", category="*")
    assert len(all_lines) == 1 and all_lines[0]["disabled"] is True
    cat_lines = bk.collect_lines(disabled["rows"], "Bookkeeping/Organize/a.mapped.json", category="Advertising")
    assert cat_lines == []

    restored, err = bk.set_row_disabled(disabled, 0, False)
    assert err is None
    assert not bk.row_is_disabled(restored["rows"][0])

    blocked, err = bk.remove_manual_row(statement, 0)
    assert blocked is None and err

    gone, err = bk.remove_manual_row(updated, 0)
    assert err is None and gone is not None
    assert gone["rows"] == []


def test_manual_ledger_choice_order():
    names = ["b.mapped.json", "manual.mapped.json", "a.mapped.json"]
    choices = bk.ledger_choices(names)
    assert [c["path"] for c in choices] == [
        "all",
        "Bookkeeping/Organize/manual.mapped.json",
        "Bookkeeping/Organize/a.mapped.json",
        "Bookkeeping/Organize/b.mapped.json",
    ]
    assert bk.is_manual_ledger("manual")
    assert bk.is_manual_ledger("Bookkeeping/Organize/manual.mapped.json")
    assert not bk.is_manual_ledger("Bookkeeping/Organize/a.mapped.json")


def test_filing_books_and_paste():
    assert bk.normalize_filing_book("Chords of Truth, LLC") == "chords"
    assert bk.normalize_filing_book("pickle") == "pickleball"
    assert bk.infer_filing_book({"entity": "Chords of Truth, LLC"}) == "chords"
    rows = [
        _row("2025-01-15", "-10.00", "Advertising", name="Acme"),
        dict(_row("2025-02-01", "50.00", "Other income", name="Client"), filing_book="pickleball"),
    ]
    chords = bk.pnl_from_rows(rows, filing_book="chords")
    pickle = bk.pnl_from_rows(rows, filing_book="pickleball")
    assert chords["row_count"] == 1
    assert pickle["row_count"] == 1
    assert pickle["income_total"] == 50.0

    payload = {
        "entity": "Chords of Truth, LLC",
        "working_chart": [
            {"label": "Advertising"},
            {"label": "Gross receipts or sales"},
        ],
        "rows": list(rows),
    }
    updated, err = bk.set_row_filing_book(payload, 0, "pickleball")
    assert err is None
    assert bk.row_filing_book(updated["rows"][0]) == "pickleball"

    parsed, errors = bk.parse_pasted_lines(
        "Trans Date Post Date Description Amount\n"
        "Jan 31 Feb 1 Sample Vendor Co $12.00\n"
        "Mar 2 Other Vendor 8.50",
        default_year=2025,
    )
    assert not errors
    assert len(parsed) == 2
    assert parsed[0]["date"] == "2025-01-31"
    assert parsed[0]["amount"] == 12.0
    assert "sample vendor" in parsed[0]["payee"].lower()

    added, n, aerr = bk.add_manual_rows(payload, parsed, filing_book="pickleball")
    assert aerr is None and n == 2
    assert added["rows"][-1]["filing_book"] == "pickleball"

    income_parsed, _err = bk.parse_pasted_lines("2025-06-01 Payer LLC 100.00", default_year=2025)
    labeled = [{**income_parsed[0], "category": "Gross receipts or sales", "amount": abs(income_parsed[0]["amount"])}]
    with_income, n, err = bk.add_manual_rows(payload, labeled, filing_book="pickleball")
    assert err is None and n == 1
    assert with_income["rows"][-1]["category_label"] == "Gross receipts or sales"
    assert bk.row_signed_amount(with_income["rows"][-1]) == 100.0


def test_chart_kind_splits_income_and_expense():
    chart = bk.working_chart_from_payload({
        "working_chart": [
            {"label": "Gross receipts or sales"},
            {"label": "Other income"},
            {"label": "Advertising"},
            {"label": "Office expense"},
            {"label": "Cost of goods sold", "kind": "expense"},
            {"label": "Returns and allowances"},
        ]
    })
    kinds = {c["label"]: c["kind"] for c in chart}
    assert kinds["Gross receipts or sales"] == "income"
    assert kinds["Other income"] == "income"
    assert kinds["Returns and allowances"] == "income"
    assert kinds["Advertising"] == "expense"
    assert kinds["Office expense"] == "expense"
    assert kinds["Cost of goods sold"] == "expense"
    assert bk.gross_receipts_label(chart) == "Gross receipts or sales"


def test_pnl_places_expense_labels_even_when_amount_is_positive():
    rows = [
        _row("2025-03-01", "40.00", "Supplies"),
        _row("2025-03-02", "200.00", "Computer hardware"),
        _row("2025-03-03", "100.00", "Gross receipts or sales"),
    ]
    chart = [
        {"label": "Supplies"},
        {"label": "Computer hardware"},
        {"label": "Gross receipts or sales"},
    ]
    pnl = bk.pnl_from_rows(rows, chart=chart)
    expense_labels = [x["label"] for x in pnl["expenses"]]
    income_labels = [x["label"] for x in pnl["income"]]
    assert "Supplies" in expense_labels
    assert "Computer hardware" in expense_labels
    assert "Gross receipts or sales" in income_labels
    assert "Supplies" not in income_labels
    assert pnl["expense_total"] == 240.0
    assert pnl["income_total"] == 100.0


def test_manual_expense_amount_is_stored_negative():
    payload = {
        "working_chart": [{"label": "Supplies"}, {"label": "Gross receipts or sales"}],
        "rows": [],
    }
    updated, line, err = bk.add_manual_row(payload, "2025-03-02", "Store", "40.00", "Supplies")
    assert err is None and line is not None
    assert bk.row_signed_amount(updated["rows"][0]) == -40.0
    income, line, err = bk.add_manual_row(updated, "2025-03-02", "Payer", "-50.00", "Gross receipts or sales")
    assert err is None
    assert bk.row_signed_amount(income["rows"][-1]) == 50.0
