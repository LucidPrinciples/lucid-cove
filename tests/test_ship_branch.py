"""#SHIP1 — ship_branch: one approval push + open PR, structured card JSON."""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.tools.dev_tools as dt  # noqa: E402


class _MockResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _MockClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        for pattern, (status, data, text) in self.responses.items():
            if pattern in url:
                return _MockResponse(status, data, text)
        return _MockResponse(500, {}, "unexpected url")

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        for pattern, (status, data, text) in self.responses.items():
            if pattern in url:
                return _MockResponse(status, data, text)
        return _MockResponse(500, {}, "unexpected url")


def _repo(tmp_path):
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    return repo_dir


@pytest.mark.asyncio
async def test_ship_branch_push_then_pr_json(monkeypatch, tmp_path):
    """Happy path: push succeeds, PR created, card JSON with pr_url."""
    repo_dir = _repo(tmp_path)
    pushed = {"n": 0}

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/ship-test"
        if "rev-parse --verify" in cmd:
            return "not-a-sha"  # no upstream yet
        if "rev-list --count stuart" in cmd or "rev-list --count" in cmd:
            return "2"
        if cmd.startswith("push origin") or "push origin" in cmd:
            pushed["n"] += 1
            return "ok pushed"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/ship-test"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("own/repo", "tok"))

    def _make_client(*a, **k):
        return _MockClient({
            "/pulls/7": (200, {"number": 7, "state": "open"}, ""),
            "/pulls": (201, {
                "number": 7,
                "html_url": "https://github.com/own/repo/pull/7",
            }, ""),
            "/compare/": (200, {"files": [
                {"additions": 4, "deletions": 1},
            ]}, ""),
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.ship_branch.coroutine(
        str(repo_dir), "#SHIP1 One approval", "Body", "main", ""
    )
    data = json.loads(result)
    assert data["status"] == "created"
    assert data["pr_number"] == 7
    assert data["pr_url"] == "https://github.com/own/repo/pull/7"
    assert data["title"] == "#SHIP1 One approval"
    assert data["branch"] == "stuart/ship-test"
    assert data.get("pushed") is True
    assert "SHIPPED" in data["message"]
    assert pushed["n"] == 1


@pytest.mark.asyncio
async def test_ship_branch_refuses_main(monkeypatch, tmp_path):
    repo_dir = _repo(tmp_path)

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "main"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    result = await dt.ship_branch.coroutine(str(repo_dir), "Nope")
    assert result.startswith("REFUSED:")
    assert "main" in result


@pytest.mark.asyncio
async def test_ship_branch_push_failure_short_circuits(monkeypatch, tmp_path):
    repo_dir = _repo(tmp_path)

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/x"
        if "rev-parse --verify" in cmd:
            return "not-a-sha"
        if "rev-list --count" in cmd:
            return "1"
        if "push origin" in cmd:
            return "ok"  # lie
        if "ls-remote" in cmd:
            return "(no output)"  # post-check fail
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    result = await dt.ship_branch.coroutine(str(repo_dir), "Title")
    assert result.startswith("FAILED:")


@pytest.mark.asyncio
async def test_ship_branch_already_open_pr_returns_card_json(monkeypatch, tmp_path):
    """422 already exists → resolve open PR into card JSON (no link hunt)."""
    repo_dir = _repo(tmp_path)

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/x"
        if "rev-parse --verify" in cmd:
            return "a" * 40
        if "rev-list --count" in cmd:
            return "1"
        if "push origin" in cmd:
            return "pushed"
        if "ls-remote" in cmd:
            return "abc refs/heads/stuart/x"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("own/repo", "tok"))

    def _make_client(*a, **k):
        return _MockClient({
            "/pulls": (422, {}, "A pull request already exists for own:stuart/x"),
        })

    # First client for create PR POST 422; ship_branch then GETs pulls list —
    # our mock returns 422 for any /pulls match. Need smarter mock.
    class _Client2(_MockClient):
        async def post(self, url, **kwargs):
            self.calls.append(("post", url, kwargs))
            return _MockResponse(422, {}, "A pull request already exists for own:stuart/x")

        async def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            if "/pulls" in url:
                return _MockResponse(200, [{
                    "number": 9,
                    "html_url": "https://github.com/own/repo/pull/9",
                    "title": "Existing",
                }], "")
            return _MockResponse(404, {}, "no")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _Client2({}))

    result = await dt.ship_branch.coroutine(str(repo_dir), "Title")
    data = json.loads(result)
    assert data["status"] == "created"
    assert data["pr_number"] == 9
    assert data["pr_url"].endswith("/pull/9")
    assert data.get("already_existed") is True


def test_ship_branch_in_dev_tool_registry():
    names = {t.name for t in dt.ALL_DEV_TOOLS}
    assert "ship_branch" in names
    assert "git_push" in names
    assert "create_github_pr" in names


def test_ship_branch_is_approve_tier():
    """@approve tags the StructuredTool; tool_node reads get_tier(tool)."""
    from src.tools.approval import Tier, get_tier
    assert get_tier(dt.ship_branch) == Tier.APPROVE
    assert get_tier(dt.git_push) == Tier.APPROVE
    assert get_tier(dt.create_github_pr) == Tier.APPROVE
