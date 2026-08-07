"""Public-repo hygiene: refuse ops/personal detail on GitHub PR/commit surfaces."""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.tools.dev_tools as dt  # noqa: E402


def test_find_leaks_instance_and_operator():
    hits = dt.find_public_surface_leaks(
        "Deploy on Clearfield after merge",
        "Founders first; ask JAG; @jasonbroadcast",
    )
    assert "instance-label" in hits
    assert "personal-name" in hits
    assert "operator-handle" in hits


def test_find_leaks_personal_agents_and_home_lab():
    hits = dt.find_public_surface_leaks(
        "Wire Ben and Atlas on P620 at /home/lphomebase/ClearfieldCove",
        "check 192.168.1.15 then lucidcove-6f6f-app",
    )
    assert "personal-name" in hits
    assert "personal-agent" in hits
    assert "home-lab" in hits or "home-path" in hits
    assert "instance-id" in hits or "container-name" in hits
    assert "lan-ip" in hits


def test_find_leaks_clean_product_text():
    hits = dt.find_public_surface_leaks(
        "fix(x): refuse silent 280-cap truncate on queue publish",
        "App-only restart after merge. Enable Premium long posts on the posting presence.",
    )
    assert hits == []


def test_find_leaks_allows_product_role_names():
    """Steward template names are public product vocabulary, not household leaks."""
    hits = dt.find_public_surface_leaks(
        "Stuart coordinates Jules capture; Mercer owns commerce tools",
    )
    assert hits == []


def test_dynamic_family_name_blocked(monkeypatch):
    monkeypatch.setattr(
        dt, "get_setting_sync",
        lambda key, default=None: {
            "family_name": "Quietgrove",
            "public_hygiene_extra_terms": "River,Mabel",
        }.get(key, default),
    )
    hits = dt.find_public_surface_leaks("Ship Quietgrove calendar fix for River")
    assert "instance-label" in hits
    assert "extra-denylist" in hits


def test_public_commit_identity_ignores_family_label(monkeypatch):
    monkeypatch.setattr(
        dt, "get_setting_sync",
        lambda key, default=None: {
            "admin_agent_display_name": "Clearfield",
            "admin_agent_id": "stuart",
            "family_name": "Clearfield",
        }.get(key, default),
    )
    name, email = dt._public_commit_identity()
    assert name == "Stuart Cove"
    assert email == "stuart@lucidtuner.ai"
    assert "Clearfield" not in name


def test_public_commit_identity_normal_stuart(monkeypatch):
    monkeypatch.setattr(
        dt, "get_setting_sync",
        lambda key, default=None: {
            "admin_agent_display_name": "Stuart",
            "admin_agent_id": "stuart",
        }.get(key, default),
    )
    name, email = dt._public_commit_identity()
    assert name == "Stuart Cove"
    assert email == "stuart@lucidtuner.ai"


@pytest.mark.asyncio
async def test_create_github_pr_refuses_leaky_body(monkeypatch, tmp_path):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/hygiene"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    # Should refuse before remote/token work
    result = await dt.create_github_pr.coroutine(
        str(repo_dir),
        "fix sites",
        "Restart Clearfield after merge",
        "main",
    )
    assert result.startswith("REFUSED: public-repo hygiene")
    assert "instance-label" in result


@pytest.mark.asyncio
async def test_ship_branch_refuses_leaky_title(monkeypatch, tmp_path):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/hygiene"
        return "should-not-push"

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    result = await dt.ship_branch.coroutine(
        str(repo_dir), "Founders video polish", "clean body", "main", ""
    )
    assert result.startswith("REFUSED: public-repo hygiene")


@pytest.mark.asyncio
async def test_git_commit_refuses_leaky_message_on_github(monkeypatch, tmp_path):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/hygiene"
        if "diff --cached" in cmd:
            return "diff --git a/x b/x\n+hi"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_is_github_origin", lambda r: True)

    result = await dt.git_commit.coroutine(
        str(repo_dir), "fix deploy path for lp-homebase mesh"
    )
    assert result.startswith("REFUSED: public-repo hygiene")


def test_ship_branch_docstring_has_no_instance_labels():
    assert "Clearfield" not in (dt.ship_branch.description or "")
    assert "Founders" not in (dt.ship_branch.description or "")


@pytest.mark.asyncio
async def test_git_push_refuses_leaky_unpushed_commit_message(monkeypatch, tmp_path):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/hygiene"
        if "rev-parse" in cmd:
            return "a" * 40
        if "rev-list --count" in cmd:
            return "1"
        if cmd.startswith("log "):
            return "fix deploy path for Clearfield app restart\n"
        if cmd.startswith("push "):
            return "SHOULD_NOT_PUSH"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_is_github_origin", lambda r: True)

    result = await dt.git_push.coroutine(str(repo_dir), "stuart/hygiene")
    assert result.startswith("REFUSED: public-repo hygiene")
    assert "unpushed commit" in result


@pytest.mark.asyncio
async def test_refuse_unpushed_clean_messages(monkeypatch, tmp_path):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if cmd.startswith("log "):
            return "fix(x): refuse silent truncate\n\nApp-only restart after merge.\n"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_is_github_origin", lambda r: True)
    assert await dt._refuse_unpushed_commit_leaks(str(repo_dir), "stuart/x", "main") is None
