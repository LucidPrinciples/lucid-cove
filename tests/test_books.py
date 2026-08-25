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
