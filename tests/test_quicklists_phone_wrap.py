"""Home Quick Lists wrap at phone width; desktop strip is unchanged."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = (ROOT / "src/dashboard/static/css/dashboard.css").read_text(encoding="utf-8")
AB = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")


def test_desktop_quicklists_stay_a_horizontal_strip():
    cards = DASH.split(".ql-cards {")[1].split("}")[0]
    assert "display: flex" in cards
    assert "overflow-x: auto" in cards
    assert "flex-wrap" not in cards
    card = DASH.split(".ql-card {")[1].split("}")[0]
    assert "min-height: 44px" not in card


def test_phone_quicklists_wrap_without_overlap():
    assert "@media (max-width: 768px)" in DASH
    phone = None
    parts = DASH.split("@media (max-width: 768px)")
    for part in parts[1:]:
        body = part.split("{", 1)[1] if "{" in part else part
        if ".ql-cards" in body and "flex-wrap" in body:
            phone = body
            break
    assert phone is not None
    assert "flex-wrap: wrap" in phone
    assert "overflow-x: visible" in phone
    assert "min-height: 44px" in phone
    assert "min-width: 0" in phone
    assert "overflow: hidden" in phone
    assert "flex: 1 1 calc(50% - 8px)" in phone
    add = phone.split(".ql-card-add")[1].split("}")[0]
    assert "min-width: 44px" in add
    assert "min-height: 44px" in add
    # Do not keep the old single-row min-width that packed chips on top of each other.
    assert ".ql-card { min-width: 80px" not in AB
