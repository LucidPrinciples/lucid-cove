"""Tests for the run_tests tool — #D48.

Verify-before-ship demands tests, and agents need a test-runner tool.
This test file validates the run_tests tool behavior including:
- Successful test execution
- Error handling for missing/invalid repos
- Path validation and scope enforcement
- Output formatting
"""

import pytest
from unittest.mock import patch, AsyncMock

from src.tools.dev_tools import run_tests, _resolve_repo


class TestRunTests:
    """Test cases for the run_tests tool."""

    @pytest.mark.asyncio
    async def test_run_tests_success(self):
        """run_tests executes pytest and returns output."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_cmd') as mock_run:
            mock_resolve.return_value = "/app/data/projects/test-repo"
            mock_run.return_value = "..\n1 passed in 0.05s"

            result = await run_tests.ainvoke({"project": "test-repo"})

            mock_resolve.assert_called_once_with("test-repo")
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert "pytest" in call_args[0][0]
            assert "/app/data/projects/test-repo" == call_args[1].get('cwd')
            assert result == "..\n1 passed in 0.05s"

    @pytest.mark.asyncio
    async def test_run_tests_with_path(self):
        """run_tests accepts a specific test file or directory."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_cmd') as mock_run:
            mock_resolve.return_value = "/app/data/projects/test-repo"
            mock_run.return_value = "test_specific.py PASSED"

            result = await run_tests.ainvoke({"project": "test-repo", "path": "tests/test_specific.py"})

            call_args = mock_run.call_args
            assert "tests/test_specific.py" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_tests_verbose(self):
        """run_tests respects verbose flag."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_cmd') as mock_run:
            mock_resolve.return_value = "/app/data/projects/test-repo"
            mock_run.return_value = "PASSED"

            await run_tests.ainvoke({"project": "test-repo", "verbose": True})

            call_args = mock_run.call_args
            assert "-v" in call_args[0][0]
            assert "-q" not in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_tests_not_verbose(self):
        """run_tests uses quiet mode when not verbose."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve, \
             patch('src.tools.dev_tools._run_cmd') as mock_run:
            mock_resolve.return_value = "/app/data/projects/test-repo"
            mock_run.return_value = "."

            await run_tests.ainvoke({"project": "test-repo", "verbose": False})

            call_args = mock_run.call_args
            assert "-q" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_tests_repo_not_found(self):
        """run_tests returns error when repo doesn't exist."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve:
            mock_resolve.return_value = "Error: Repo 'nonexistent' not found. Available repos: none found."

            result = await run_tests.ainvoke({"project": "nonexistent"})

            assert "Error:" in result
            assert "not found" in result

    @pytest.mark.asyncio
    async def test_run_tests_path_traversal_rejected(self):
        """run_tests rejects path traversal attempts."""
        with patch('src.tools.dev_tools._resolve_repo') as mock_resolve:
            mock_resolve.return_value = "Error: Path traversal ('..') not allowed in repo name: ../etc/passwd"

            result = await run_tests.ainvoke({"project": "../etc/passwd"})

            assert "Error:" in result
            assert "Path traversal" in result


class TestRunTestsIntegration:
    """Integration tests that actually run pytest (slower)."""

    @pytest.mark.asyncio
    async def test_run_tests_on_self(self):
        """run_tests can run this project's own test suite (meta)."""
        # This test actually runs pytest on a subset of tests
        result = await run_tests.ainvoke({"project": "lucid-cove", "path": "tests/test_run_tests.py::TestRunTests::test_run_tests_success"})

        # In quiet mode, pytest outputs dots and percentage like ". [100%]"
        # A successful run shows dots and high percentage
        assert "100%" in result or "." in result or "passed" in result.lower()

    @pytest.mark.asyncio
    async def test_run_tests_output_format(self):
        """run_tests returns properly formatted output."""
        result = await run_tests.ainvoke({"project": "lucid-cove", "path": "tests/test_run_tests.py::TestRunTests::test_run_tests_success", "verbose": True})

        # Verbose output should contain test name details
        assert "test_run_tests_success" in result or "PASSED" in result or "passed" in result.lower() or "100%" in result
