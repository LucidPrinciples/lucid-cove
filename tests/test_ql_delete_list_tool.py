"""delete_quick_list is part of presence quick-list tools."""
import inspect

from src.tools import quick_list_tools as ql


def _tool_name(t) -> str:
    return getattr(t, "name", None) or str(t)


def _tool_source(t) -> str:
    fn = getattr(t, "coroutine", None) or getattr(t, "func", None) or t
    return inspect.getsource(fn)


def test_delete_quick_list_registered():
    names = [_tool_name(t) for t in ql.ALL_QUICK_LIST_TOOLS]
    assert "delete_quick_list" in names, names


def test_get_quick_lists_filters_archived_sql_shape():
    # Source contract: list queries must ignore archived shells
    src = _tool_source(ql.get_quick_lists)
    assert "ql.archived = FALSE" in src
    src_resolve = inspect.getsource(ql._resolve_list)
    assert "archived = FALSE" in src_resolve
    src_del = _tool_source(ql.delete_quick_list)
    assert "archived = TRUE" in src_del
