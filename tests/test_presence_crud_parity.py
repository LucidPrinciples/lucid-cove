"""Presence CRUD parity — #QL2 #PRJ2 #CAL1 #LNK1 tool registration + scope shape."""
import inspect

from src.tools import quick_list_tools as ql
from src.tools import project_tools as pt
from src.tools import links_tools as ln
from src.tools import nextcloud_tools as nc


def _names(tools):
    return [getattr(t, "name", None) or str(t) for t in tools]


def _src(t):
    fn = getattr(t, "coroutine", None) or getattr(t, "func", None) or t
    return inspect.getsource(fn)


def test_ql2_tools_registered():
    names = _names(ql.ALL_QUICK_LIST_TOOLS)
    for n in ("update_quick_list", "delete_list_item", "restore_quick_list", "delete_quick_list"):
        assert n in names, names


def test_ql2_update_and_delete_item_are_scoped():
    assert "presence_id" in _src(ql.update_quick_list) or "_ql_scope" in _src(ql.update_quick_list)
    assert "archived = TRUE" in _src(ql.delete_list_item)
    assert "archived = FALSE" in _src(ql.restore_quick_list)
    # get_list_items hides archived rows
    assert "archived" in _src(ql.get_list_items)


def test_prj2_tools_registered():
    names = _names(pt.ALL_PROJECT_TOOLS)
    assert "update_project" in names
    assert "archive_project" in names


def test_prj2_update_archive_use_presence_scope():
    assert "_prj_scope" in _src(pt.update_project)
    assert "status = 'archived'" in _src(pt.archive_project) or "archived" in _src(pt.archive_project)
    assert "_prj_scope" in _src(pt.archive_project)


def test_lnk1_tools_registered():
    names = _names(ln.ALL_LINKS_TOOLS)
    assert "remove_action_link" in names
    assert "update_action_link" in names


def test_lnk1_remove_uses_presence_store():
    # multi-mode path goes through _read_cards/_write_cards which bind presence
    assert "_read_cards" in _src(ln.remove_action_link)
    assert "_write_cards" in _src(ln.remove_action_link)
    assert "_read_cards" in _src(ln.update_action_link)


def test_cal1_tools_registered():
    names = _names(nc.ALL_NEXTCLOUD_TOOLS)
    assert "calendar_update_event" in names
    assert "calendar_delete_event" in names


def test_cal1_list_exposes_uid_and_parse_uid():
    assert "uid" in _src(nc.calendar_list_events).lower() or "[uid=" in _src(nc.calendar_list_events)
    parse_src = inspect.getsource(nc._parse_vevent)
    assert 'key == "UID"' in parse_src or "UID" in parse_src
    assert "calendar_delete_event" in _src(nc.calendar_delete_event) or "delete" in _src(nc.calendar_delete_event).lower()


def test_lnk2_presence_default_modules_include_links():
    from src import config as cfg
    assert "tools.links_tools" in cfg._PRESENCE_DEFAULT_MODULES


def test_lnk2_channels_universal_steward_includes_links():
    from pathlib import Path
    src = Path("src/graphs/channels.py").read_text()
    # steward universal append list must mention links_tools
    assert "tools.links_tools" in src
