"""CSV household tables — parse, serialize, embeds, tools export."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_parse_and_serialize_roundtrip():
    from src.dashboard import csv_tables as ct

    text = "item,who,notes\nchips,Alex,store brand\nsalsa,Sam,\n"
    parsed = ct.parse_csv_text(text)
    assert parsed["headers"] == ["item", "who", "notes"]
    assert parsed["row_count"] == 2
    assert parsed["rows"][0][0] == "chips"
    out = ct.serialize_csv(parsed["headers"], parsed["rows"])
    again = ct.parse_csv_text(out)
    assert again["headers"] == parsed["headers"]
    assert again["rows"] == parsed["rows"]


def test_parse_empty_and_bad_path():
    from src.dashboard import csv_tables as ct

    empty = ct.parse_csv_text("")
    assert empty["headers"] == [] and empty["rows"] == []
    clean, err = ct.normalize_table_path("../secret.csv")
    assert clean is None and err
    clean, err = ct.normalize_table_path("Tables/who.csv")
    assert clean == "Tables/who.csv" and not err
    clean, err = ct.normalize_table_path("Tables/who.txt")
    assert clean is None
    assert ct.default_tables_path("Who Brings What!") == "Tables/Who-Brings-What.csv"


def test_apply_row_update():
    from src.dashboard import csv_tables as ct

    headers = ["item", "who"]
    rows = [["chips", "Alex"]]
    h, r = ct.apply_row_update(headers, rows, values={"item": "salsa", "who": "Sam"}, append=True)
    assert len(r) == 2 and r[1] == ["salsa", "Sam"]
    h, r = ct.apply_row_update(headers, r, row_index=0, values={"who": "Jordan"})
    assert r[0][1] == "Jordan"


def test_expand_csv_refs():
    from src.dashboard import csv_tables as ct

    def resolve(path):
        if path.endswith("demo.csv"):
            return {"headers": ["a", "b"], "rows": [["1", "2"]]}
        return None

    md = "Hello\n\n```csv\nTables/demo.csv\n```\n\nAnd [[csv:Tables/missing.csv]]\n"
    out = ct.expand_csv_refs_in_markdown(md, resolve)
    assert "| a | b |" in out
    assert "Open table" in out
    assert "missing" in out.lower() or "Table" in out


def test_briefs_render_expands_vault_csv(tmp_path, monkeypatch):
    from src.dashboard.routes import briefs as br

    root = tmp_path / "briefs"
    monkeypatch.setattr(br, "BRIEFS_ROOT", root)
    monkeypatch.setattr(br, "BRIEFS_DOCS", root / "docs")
    monkeypatch.setattr(br, "BRIEFS_INDEX", root / "index.json")
    monkeypatch.setattr(br, "DATA_DIR", tmp_path)

    vault = tmp_path / "vault"
    tables = vault / "Tables"
    tables.mkdir(parents=True)
    (tables / "party.csv").write_text("item,who\ncups,Alex\n", encoding="utf-8")
    monkeypatch.setattr(br, "VAULT_ROOT", vault)

    html = br._render_html("# Plan\n\n```csv\nTables/party.csv\n```\n")
    assert "<table>" in html
    assert "cups" in html
    assert "/tables?path=" in html


def test_tools_export_and_wiring():
    from src.tools.table_tools import ALL_TABLE_TOOLS

    names = {t.name for t in ALL_TABLE_TOOLS}
    assert names == {
        "table_list",
        "table_read",
        "table_create",
        "table_add_row",
        "table_update_row",
    }
    agent = (ROOT / "src/tools/agent_tools.py").read_text()
    assert "ALL_TABLE_TOOLS" in agent
    cfg = (ROOT / "src/config.py").read_text()
    assert "tools.table_tools" in cfg
    app = (ROOT / "src/dashboard/app.py").read_text()
    assert "tables" in app
    viewer = (ROOT / "src/dashboard/static/tables/viewer.html").read_text()
    assert "/api/tables" in viewer
    channels = (ROOT / "src/graphs/channels.py").read_text()
    assert "tools.table_tools" in channels


def test_viewer_url():
    from src.dashboard import csv_tables as ct

    assert ct.viewer_url("Tables/x.csv").startswith("/tables?path=")
