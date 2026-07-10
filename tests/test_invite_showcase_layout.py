# #D6 — invite showcase layout. Center the "joining now" / "already in the Cove"
# boxes, and pair operator + agent SIDE BY SIDE as one Presence (icon + operator
# name, agent next to them) instead of a flat one-card-per-person grid.
import pathlib

HTML = (pathlib.Path(__file__).resolve().parents[1]
        / "src" / "dashboard" / "static" / "action-board" / "new-agent-setup.html").read_text()


def test_presence_pair_helper_exists():
    assert "function _presencePairCard(" in HTML
    # renders operator + agent as two cells joined by a connector
    assert "cell(personName" in HTML
    assert "cell(agentName" in HTML


def test_centered_wrap_helper_exists():
    assert "function _showcaseCenter(" in HTML
    assert "justify-content:center" in HTML


def test_joining_now_uses_centered_pair():
    seg = HTML.split("_showcaseHead('joining now')")[1][:200]
    assert "_showcaseCenter(" in seg
    assert "_presencePairCard(" in seg


def test_already_in_cove_uses_centered_pair():
    # the members block builds pair cards, and its head is wrapped in the centered wrap
    block = HTML.split("if (members.length)")[1][:900]
    assert "_presencePairCard(" in block
    assert 'who\'s already in the Cove") + _showcaseCenter(' in HTML


def test_flat_grid_no_longer_used_for_presence_sections():
    # the joining/already sections must not fall back to the old left-aligned grid
    assert "_showcaseHead('joining now') + _showcaseGrid(" not in HTML
    assert 'who\'s already in the Cove") + _showcaseGrid(' not in HTML
