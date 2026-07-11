"""Tests for #D52: Gated executions must verify their EFFECT.

This module tests that git_push and create_github_pr verify their actual
results on the remote, not just report success from the git command.
"""

import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock


class TestGitPushVerification:
    """Test that git_push verifies the remote ref exists after push."""

    @pytest.mark.asyncio
    async def test_git_push_verifies_remote_ref_exists(self):
        """git_push must verify branch exists on origin after push."""
        from src.tools.dev_tools import git_push

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            
            # Mock branch detection
            mock_run_git.side_effect = [
                "stuart/test-branch",  # branch --show-current
                "fatal: Needed a single revision",  # rev-parse for upstream (new branch)
                "3",  # rev-list --count (3 commits on branch)
                "remote: Create PR at https://github.com...\n * [new branch]",  # push output
                "abc123 refs/heads/stuart/test-branch",  # ls-remote shows branch exists
            ]
            
            result = await git_push("lucid-cove")
            
            # Should succeed and mention the push
            assert "new branch" in result or "[new branch]" in result
            # Should have called ls-remote to verify
            assert any("ls-remote" in str(call) for call in mock_run_git.call_args_list)

    @pytest.mark.asyncio
    async def test_git_push_fails_when_remote_ref_missing(self):
        """git_push must fail if branch not found on origin after 'success'."""
        from src.tools.dev_tools import git_push

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            
            # Simulate: push reports success but ref doesn't exist (credential failure)
            mock_run_git.side_effect = [
                "stuart/test-branch",  # branch --show-current
                "fatal: Needed a single revision",  # no upstream
                "3",  # 3 commits
                "Everything up-to-date",  # push "succeeded"
                "(no output)",  # ls-remote shows NO branch on origin
            ]
            
            result = await git_push("lucid-cove")
            
            # Should report failure, not success
            assert "FAILED" in result
            assert "not found on origin" in result
            assert "no push credentials" in result.lower() or "credentials" in result.lower()


class TestCreateGitHubPrVerification:
    """Test that create_github_pr verifies the PR exists after creation."""

    @pytest.mark.asyncio
    async def test_create_github_pr_verifies_pr_exists(self):
        """create_github_pr must verify PR is reachable via API after creation."""
        from src.tools.dev_tools import create_github_pr

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git, \
             patch('src.tools.dev_tools._github_repo_and_token') as mock_creds, \
             patch('httpx.AsyncClient') as mock_client_class:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            mock_run_git.return_value = "stuart/test-branch"
            mock_creds.return_value = ("LucidPrinciples/lucid-cove", "fake-token")
            
            # Mock httpx client
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
            
            # Mock POST response (PR created)
            post_response = MagicMock()
            post_response.status_code = 201
            post_response.json.return_value = {
                "number": 123,
                "html_url": "https://github.com/LucidPrinciples/lucid-cove/pull/123"
            }
            
            # Mock verification GET
            verify_response = MagicMock()
            verify_response.status_code = 200
            
            # Mock compare API
            compare_response = MagicMock()
            compare_response.status_code = 200
            compare_response.json.return_value = {"files": []}
            
            mock_client.post.return_value = post_response
            mock_client.get.side_effect = [verify_response, compare_response]
            
            result = await create_github_pr("lucid-cove", "Test PR")
            
            # Parse JSON result
            data = json.loads(result)
            assert data["status"] == "created"
            assert data["pr_number"] == 123
            assert data["pr_url"] == "https://github.com/LucidPrinciples/lucid-cove/pull/123"
            
            # Should have called GET to verify PR exists
            assert mock_client.get.called

    @pytest.mark.asyncio
    async def test_create_github_pr_fails_when_pr_not_verifiable(self):
        """create_github_pr must fail if PR cannot be verified after 'creation'."""
        from src.tools.dev_tools import create_github_pr

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git, \
             patch('src.tools.dev_tools._github_repo_and_token') as mock_creds, \
             patch('httpx.AsyncClient') as mock_client_class:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            mock_run_git.return_value = "stuart/test-branch"
            mock_creds.return_value = ("LucidPrinciples/lucid-cove", "fake-token")
            
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
            
            # POST succeeds
            post_response = MagicMock()
            post_response.status_code = 201
            post_response.json.return_value = {
                "number": 123,
                "html_url": "https://github.com/LucidPrinciples/lucid-cove/pull/123"
            }
            
            # But verification GET fails (404 = not found)
            verify_response = MagicMock()
            verify_response.status_code = 404
            
            mock_client.post.return_value = post_response
            mock_client.get.return_value = verify_response
            
            result = await create_github_pr("lucid-cove", "Test PR")
            
            # Should report failure, not JSON
            assert "FAILED" in result
            assert "verification failed" in result.lower()
            assert "PR URL" in result  # Should include URL for debugging

    @pytest.mark.asyncio
    async def test_create_github_pr_fails_when_pr_data_incomplete(self):
        """create_github_pr must fail if API returns 201 but with missing data."""
        from src.tools.dev_tools import create_github_pr

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git, \
             patch('src.tools.dev_tools._github_repo_and_token') as mock_creds, \
             patch('httpx.AsyncClient') as mock_client_class:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            mock_run_git.return_value = "stuart/test-branch"
            mock_creds.return_value = ("LucidPrinciples/lucid-cove", "fake-token")
            
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
            
            # POST returns 201 but with incomplete data (no pr_number)
            post_response = MagicMock()
            post_response.status_code = 201
            post_response.json.return_value = {
                "html_url": "https://github.com/.../pull/123"
                # Missing "number" key
            }
            
            mock_client.post.return_value = post_response
            
            result = await create_github_pr("lucid-cove", "Test PR")
            
            # Should report failure
            assert "FAILED" in result
            assert "incomplete" in result.lower() or "PR data" in result


class TestApprovalCardsShowResultText:
    """Test that approval cards render actual tool result text, not just executed-true."""
    
    # Note: This tests the contract that tools return descriptive text.
    # The actual card rendering happens in the approval executor/UI layer.
    
    @pytest.mark.asyncio
    async def test_git_push_returns_descriptive_result(self):
        """git_push must return descriptive text about what happened."""
        from src.tools.dev_tools import git_push

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            mock_run_git.side_effect = [
                "stuart/test-branch",
                "fatal: Needed a single revision",  # no upstream
                "3",
                "remote: Create PR...\n * [new branch]",
                "abc123 refs/heads/stuart/test-branch",
            ]
            
            result = await git_push("lucid-cove")
            
            # Should include git output, not just "executed"
            assert len(result) > 20  # More than just "OK"
            assert "branch" in result.lower() or "remote" in result.lower()

    @pytest.mark.asyncio
    async def test_create_github_pr_returns_structured_result(self):
        """create_github_pr must return structured result with PR URL."""
        from src.tools.dev_tools import create_github_pr

        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_git') as mock_run_git, \
             patch('src.tools.dev_tools._github_repo_and_token') as mock_creds, \
             patch('httpx.AsyncClient') as mock_client_class:
            
            mock_resolve.return_value = "/app/data/projects/lucid-cove"
            mock_run_git.return_value = "stuart/test-branch"
            mock_creds.return_value = ("LucidPrinciples/lucid-cove", "fake-token")
            
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
            
            post_response = MagicMock()
            post_response.status_code = 201
            post_response.json.return_value = {
                "number": 42,
                "html_url": "https://github.com/LucidPrinciples/lucid-cove/pull/42"
            }
            
            verify_response = MagicMock()
            verify_response.status_code = 200
            
            compare_response = MagicMock()
            compare_response.status_code = 200
            compare_response.json.return_value = {"files": []}
            
            mock_client.post.return_value = post_response
            mock_client.get.side_effect = [verify_response, compare_response]
            
            result = await create_github_pr("lucid-cove", "Test PR")
            
            # Parse JSON
            data = json.loads(result)
            
            # Must include PR URL for the card to link to
            assert "pr_url" in data
            assert "pr_number" in data
            assert "https://github.com" in data["pr_url"]
            assert "#42" in data["message"] or "42" in str(data["pr_number"])
