# #D20 — the backlog board must escape item-derived text before innerHTML injection.
# A ticket title/desc/source/tag containing a literal '<' otherwise swallowed the rest
# of the lane (proven live) and is XSS-adjacent. There is no JS test harness in-repo, so
# this guards the source: every item-derived interpolation into innerHTML goes through
# ESC(), and the → Team button keeps its JSON-attribute escaping.
import pathlib
import re

BACKLOG = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static" / "backlog.html"


def _src():
    return BACKLOG.read_text()


def test_esc_helper_defined():
    s = _src()
    assert "const ESC =" in s
    # neutralizes the three structural chars at minimum
    assert "&lt;" in s and "&gt;" in s and "&amp;" in s


def test_item_fields_are_escaped():
    s = _src()
    # the card render must not interpolate raw item fields into innerHTML
    for raw in ("'>' + item.title +", "'>' + item.desc +", "'>' + item.source +",
                "'>' + t + '</span>'"):
        assert raw not in s, f"unescaped interpolation still present: {raw}"
    # and must use ESC() for them
    for esc in ("ESC(item.title)", "ESC(item.desc)", "ESC(item.source)", "ESC(t)"):
        assert esc in s, f"expected {esc}"


def test_team_queue_fields_are_escaped():
    s = _src()
    assert "ESC(q.title)" in s
    assert "ESC(q.source)" in s
    assert "ESC(q.assignee)" in s


def test_flow_to_team_button_keeps_json_attr_escaping():
    # The → Team onclick still double-escapes JSON into the attribute (quotes → &quot;),
    # which is how flowToTeam receives titles containing quotes/apostrophes/angle brackets.
    s = _src()
    assert re.search(r"JSON\.stringify\(item\.title\)\.replace\(/\"/g, '&quot;'\)", s)
