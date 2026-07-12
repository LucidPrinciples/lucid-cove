"""#D18 — PR review card: lock create_github_pr JSON return shape and /api/pr/diff route.

Tests the new JSON return format from create_github_pr (status, pr_number, pr_url,
additions, deletions, etc.) and the /api/pr/diff endpoint that powers the review card.
"""
import json
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.tools.dev_tools as dt  # noqa: E402


# =============================================================================
# Mocks for httpx.AsyncClient
# =============================================================================

class _MockResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _MockClient:
    """Base mock that returns configured responses."""
    def __init__(self, responses):
        # responses: dict of url pattern -> (status_code, json_data, text)
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


class _FailingClient:
    """Simulates network-level failures."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        raise RuntimeError("connection refused")

    async def get(self, *a, **k):
        raise RuntimeError("connection refused")


# =============================================================================
# create_github_pr return shape tests
# =============================================================================

@pytest.mark.asyncio
async def test_create_github_pr_returns_json_on_success(monkeypatch, tmp_path):
    """Successful PR creation returns structured JSON with all card fields."""
    # Set up a fake git repo structure
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    # Mock git commands
    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"  # Branch exists on origin
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)

    # Mock _github_repo_and_token to return test credentials
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))

    # Mock httpx.AsyncClient
    def _make_client(*a, **k):
        return _MockClient({
            "/pulls/42": (200, {"number": 42, "state": "open"}, ""),   # verify GET (AUDIT-F2)
            "/pulls": (201, {"number": 42, "html_url": "https://github.com/test-owner/test-repo/pull/42"}, ""),
            "/compare/": (200, {
                "files": [
                    {"filename": "foo.py", "additions": 10, "deletions": 5, "status": "modified", "patch": "@@ -1 +1 @@"},
                    {"filename": "bar.py", "additions": 3, "deletions": 0, "status": "added"},
                ]
            }, ""),
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    # Parse the JSON result
    data = json.loads(result)
    assert data["status"] == "created"
    assert data["pr_number"] == 42
    assert data["pr_url"] == "https://github.com/test-owner/test-repo/pull/42"
    assert data["title"] == "Test PR"
    assert data["branch"] == "stuart/test-branch"
    assert data["base"] == "main"
    assert data["repo"] == "test-owner/test-repo"
    assert data["additions"] == 13  # 10 + 3
    assert data["deletions"] == 5   # 5 + 0
    assert "PR CREATED: #42" in data["message"]


@pytest.mark.asyncio
async def test_create_github_pr_returns_string_on_422_already_exists(monkeypatch, tmp_path):
    """422 'already exists' returns plain string (not JSON) for backward compatibility."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))

    def _make_client(*a, **k):
        return _MockClient({
            "/pulls": (422, {}, 'A pull request already exists for test-owner:test-branch'),
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    # Should be a plain string error, not JSON
    assert "already exists" in result
    assert result.startswith("A PR for")


@pytest.mark.asyncio
async def test_create_github_pr_returns_string_on_api_error(monkeypatch, tmp_path):
    """Other API errors return plain string with status code."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))

    def _make_client(*a, **k):
        return _MockClient({
            "/pulls": (401, {}, 'Unauthorized'),
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    # Should be a plain string error
    assert "Error: GitHub API returned 401" in result


@pytest.mark.asyncio
async def test_create_github_pr_returns_string_on_network_error(monkeypatch, tmp_path):
    """Network failures return plain string error."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))
    monkeypatch.setattr("httpx.AsyncClient", _FailingClient)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    # Should be a plain string error
    assert "Error: PR request failed" in result


@pytest.mark.asyncio
async def test_create_github_pr_gracefully_handles_compare_failure(monkeypatch, tmp_path):
    """If compare API fails, PR still created but stats are zero."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))

    def _make_client(*a, **k):
        return _MockClient({
            "/pulls/42": (200, {"number": 42, "state": "open"}, ""),   # verify GET (AUDIT-F2)
            "/pulls": (201, {"number": 42, "html_url": "https://github.com/test-owner/test-repo/pull/42"}, ""),
            "/compare/": (404, {}, "Not found"),  # Compare fails
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    # Should still succeed with JSON
    data = json.loads(result)
    assert data["status"] == "created"
    assert data["pr_number"] == 42
    # Stats should be zero when compare fails
    assert data["additions"] == 0
    assert data["deletions"] == 0


# =============================================================================
# /api/pr/diff route tests - test the route function directly
# =============================================================================

@pytest.mark.asyncio
async def test_pr_diff_endpoint_success(monkeypatch):
    """Valid repo/head returns compare data."""
    import src.dashboard.routes.home as home

    async def _mock_compare(repo, base, head, pat):
        assert repo == "owner/repo"
        assert base == "main"
        assert head == "feature-branch"
        assert pat == "test-pat"
        return {
            "status": "ahead",
            "ahead_by": 3,
            "behind_by": 0,
            "total_commits": 3,
            "files": [
                {"filename": "test.py", "status": "modified", "additions": 5, "deletions": 2, "patch": "@@ -1,5 +1,8 @@"},
            ],
        }

    monkeypatch.setattr("src.config.get_feature_flags", lambda: {"github_pat": "test-pat"})
    monkeypatch.setattr("src.utils.github.github_get_compare", _mock_compare)

    # Test the route function directly using a mock request
    class MockRequest:
        pass

    result = await home.get_pr_diff(repo="owner/repo", base="main", head="feature-branch")
    
    assert result["repo"] == "owner/repo"
    assert result["base"] == "main"
    assert result["head"] == "feature-branch"
    assert result["status"] == "ahead"
    assert result["ahead_by"] == 3
    assert len(result["files"]) == 1
    assert result["files"][0]["filename"] == "test.py"


@pytest.mark.asyncio
async def test_pr_diff_endpoint_missing_params():
    """Missing repo or head returns 400."""
    import src.dashboard.routes.home as home
    from fastapi.responses import JSONResponse

    # Missing head
    result = await home.get_pr_diff(repo="owner/repo", base="main", head="")
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    assert "head" in result.body.decode().lower() or "required" in result.body.decode().lower()

    # Missing repo
    result = await home.get_pr_diff(repo="", base="main", head="feature")
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    assert "repo" in result.body.decode().lower() or "required" in result.body.decode().lower()


@pytest.mark.asyncio
async def test_pr_diff_endpoint_no_pat_configured(monkeypatch):
    """PAT not configured returns 400."""
    import src.dashboard.routes.home as home
    from fastapi.responses import JSONResponse

    monkeypatch.setattr("src.config.get_feature_flags", lambda: {"github_pat": ""})

    result = await home.get_pr_diff(repo="owner/repo", base="main", head="feature")
    
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    body = result.body.decode()
    assert "pat" in body.lower() or "not configured" in body.lower()


@pytest.mark.asyncio
async def test_pr_diff_endpoint_github_error(monkeypatch):
    """GitHub API errors return 500 with error message."""
    import src.dashboard.routes.home as home
    from fastapi.responses import JSONResponse

    async def _mock_compare_error(repo, base, head, pat):
        raise Exception("GitHub API: 404 Not Found")

    monkeypatch.setattr("src.config.get_feature_flags", lambda: {"github_pat": "test-pat"})
    monkeypatch.setattr("src.utils.github.github_get_compare", _mock_compare_error)

    result = await home.get_pr_diff(repo="owner/repo", base="main", head="feature")
    
    assert isinstance(result, JSONResponse)
    assert result.status_code == 500
    body = result.body.decode()
    assert "github api" in body.lower() or "not found" in body.lower()


@pytest.mark.asyncio
async def test_pr_diff_endpoint_empty_files(monkeypatch):
    """No differences returns empty files array."""
    import src.dashboard.routes.home as home

    async def _mock_compare_empty(repo, base, head, pat):
        return {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0, "files": []}

    monkeypatch.setattr("src.config.get_feature_flags", lambda: {"github_pat": "test-pat"})
    monkeypatch.setattr("src.utils.github.github_get_compare", _mock_compare_empty)

    result = await home.get_pr_diff(repo="owner/repo", base="main", head="feature")
    
    assert result["files"] == []
    assert result["status"] == "identical"


# =============================================================================
# AUDIT-F2 regression: verify/compare must use the LIVE client
# =============================================================================

class _ClosingClient:
    """Emulates real httpx.AsyncClient lifecycle: once the `async with` block
    exits, the client is closed and any further request raises — exactly how
    httpx behaves. The old code issued the verify/compare GETs AFTER the
    context manager closed, so a genuine 201 was reported as FAILED.
    """
    def __init__(self, responses):
        self.responses = responses
        self._closed = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self._closed = True
        return False

    def _lookup(self, url):
        for pattern, (status, data, text) in self.responses.items():
            if pattern in url:
                return _MockResponse(status, data, text)
        return _MockResponse(500, {}, "unexpected url")

    async def post(self, url, **kwargs):
        if self._closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.calls.append(("post", url))
        return self._lookup(url)

    async def get(self, url, **kwargs):
        if self._closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.calls.append(("get", url))
        return self._lookup(url)


@pytest.mark.asyncio
async def test_create_github_pr_success_survives_client_lifecycle(monkeypatch, tmp_path):
    """AUDIT-F2: a real 201 must return created-JSON even though the verify and
    compare GETs run after the POST — they must reuse the still-open client, not
    a closed one. Regression for 'FAILED: PR created but verification error'."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    async def _mock_git(cmd, repo_dir_arg, timeout=30):
        if "branch --show-current" in cmd:
            return "stuart/test-branch"
        if "ls-remote" in cmd:
            return "abc123 refs/heads/stuart/test-branch"
        return ""

    monkeypatch.setattr(dt, "_run_git", _mock_git)
    monkeypatch.setattr(dt, "_github_repo_and_token", lambda r: ("test-owner/test-repo", "fake-token"))

    def _make_client(*a, **k):
        return _ClosingClient({
            "/pulls/42": (200, {"number": 42, "state": "open"}, ""),   # verify GET
            "/pulls": (201, {"number": 42, "html_url": "https://github.com/test-owner/test-repo/pull/42"}, ""),
            "/compare/": (200, {"files": [{"additions": 4, "deletions": 1}]}, ""),
        })

    monkeypatch.setattr("httpx.AsyncClient", _make_client)

    result = await dt.create_github_pr.coroutine(str(repo_dir), "Test PR", "Body text")

    data = json.loads(result)   # must be created-JSON, not a FAILED string
    assert data["status"] == "created"
    assert data["pr_number"] == 42
    assert "FAILED" not in result
    assert data["additions"] == 4 and data["deletions"] == 1
