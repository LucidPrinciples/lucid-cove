"""Pre-approve public hygiene: no Attention card for leaky ship text."""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.tools.approval as ap  # noqa: E402
import src.tools.dev_tools as dt  # noqa: E402


@pytest.mark.asyncio
async def test_precheck_ship_refuses_leaky_title():
    msg = await ap.precheck_approve_args(
        "ship_branch",
        {"project": "lucid-cove", "title": "Founders video polish", "body": "clean"},
    )
    assert msg and msg.startswith("REFUSED: public-repo hygiene")
    assert "instance-label" in msg


@pytest.mark.asyncio
async def test_precheck_ship_allows_clean_product_title(monkeypatch):
    async def _no_unpushed(*a, **k):
        return None
    monkeypatch.setattr(dt, "_refuse_unpushed_commit_leaks", _no_unpushed)
    msg = await ap.precheck_approve_args(
        "ship_branch",
        {
            "project": "lucid-cove",
            "title": "Persist moments plan Save for shared Process and Crop doors",
            "body": "Product change only.",
        },
    )
    assert msg is None


@pytest.mark.asyncio
async def test_precheck_create_pr_refuses_leaky_body():
    msg = await ap.precheck_approve_args(
        "create_github_pr",
        {"project": "x", "title": "Fix sites", "body": "Restart Clearfield after merge"},
    )
    assert msg and "instance-label" in msg


@pytest.mark.asyncio
async def test_block_for_approval_no_card_on_hygiene(monkeypatch):
    """Leaky ship must not persist an approval request."""
    saved = []

    async def _save(req, channel=""):
        saved.append(req)

    async def _pending():
        return []

    monkeypatch.setattr(ap, "_save_approval_to_db", _save)
    monkeypatch.setattr(ap, "get_pending_approvals", _pending)

    with pytest.raises(RuntimeError) as ei:
        await ap.block_for_approval(
            "ship_branch",
            {"project": "lucid-cove", "title": "Atlas Founders deploy", "body": ""},
            channel="day",
        )
    assert str(ei.value).startswith("REFUSED: public-repo hygiene")
    assert saved == []


@pytest.mark.asyncio
async def test_precheck_ignores_non_ship_tools():
    msg = await ap.precheck_approve_args("docker_stop", {"container": "Clearfield"})
    assert msg is None
