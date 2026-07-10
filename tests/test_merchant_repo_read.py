"""#D17 — the merchant (release role) gets READ-ONLY repo access, never push/PR.

Mercer's channel bound no repo tools, so he couldn't reach the code (it lives at /sites,
via git_*; NC is the brain). The fix binds a safe read subset universally for merchant
channels, mirroring the steward's universal queue/delegation modules — and must NOT leak
any write/dangerous dev tool."""
from unittest.mock import patch

import src.graphs.channels as ch
from src.tools import dev_read_tools

DANGEROUS = {"git_push", "git_force_push", "create_github_pr", "git_commit", "git_add",
             "git_delete_branch", "db_execute", "git_create_branch", "git_revert_file"}


def _tool_names(tools):
    return {getattr(t, "name", str(t)) for t in tools}


def test_read_module_has_only_safe_read_tools():
    names = _tool_names(dev_read_tools.TOOLS)
    assert names == {"git_status", "git_diff", "git_log",
                     "git_current_branch", "git_diff_branch"}
    assert not (names & DANGEROUS)


def _modules_for(mtype, cfg_tools):
    cfg = {"name": "mercer" if mtype == "merchant" else "stuart", "tools": cfg_tools}
    with patch.object(ch, "_is_manager_channel", return_value=True), \
         patch.object(ch, "_get_manager_config", return_value=(cfg, mtype)):
        return ch._channel_tool_modules("mercer-day" if mtype == "merchant" else "stuart-day")


def test_merchant_channel_gets_read_tools_universally():
    # even a cove.yaml that predates the fix (no dev_read in its list) gets it appended
    mods = _modules_for("merchant", ["tools.finance_tools"])
    assert "tools.dev_read_tools" in mods
    # and NOT the full dev set (no push/PR path for the merchant)
    assert "tools.dev_tools" not in mods


def test_merchant_no_duplicate_when_already_listed():
    mods = _modules_for("merchant", ["tools.finance_tools", "tools.dev_read_tools"])
    assert mods.count("tools.dev_read_tools") == 1


def test_steward_channel_does_not_get_merchant_read_module():
    # the steward already carries full dev_tools; the merchant read module is merchant-only
    mods = _modules_for("steward", ["tools.dev_tools"])
    assert "tools.dev_read_tools" not in mods
    # steward still gets its universal queue/delegation modules
    assert "tools.steward_queue_tools" in mods
    assert "tools.delegation_tools" in mods
