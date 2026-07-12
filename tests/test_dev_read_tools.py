"""Tests for #D40: scoped read_file tool for release-role agents.

Security requirements:
- Absolute paths rejected
- Path traversal (..) rejected
- Path must stay within resolved repo root
- Nonexistent files handled gracefully
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.tools.dev_read_tools import read_file


class TestReadFileSecurity:
    """Security boundary tests."""

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self):
        """Absolute paths are rejected — only project-relative allowed."""
        result = await read_file.ainvoke({"path": "/etc/passwd"})
        assert "Absolute paths not allowed" in result

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        """.. traversal attempts are blocked."""
        result = await read_file.ainvoke({"path": "lucid-cove/../../etc/passwd"})
        assert "Path traversal" in result

    @pytest.mark.asyncio
    async def test_rejects_empty_path(self):
        """Empty path is rejected."""
        result = await read_file.ainvoke({"path": ""})
        assert "Empty path" in result


class TestReadFileResolution:
    """Path resolution and file access tests."""

    @pytest.mark.asyncio
    @patch("src.tools.dev_read_tools._resolve_repo")
    async def test_reads_file_successfully(self, mock_resolve):
        """Valid project-relative path reads file."""
        mock_resolve.return_value = "/app/data/projects/lucid-cove"
        
        # Create a temp file to read
        test_content = "hello world"
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=True):
                with patch("pathlib.Path.read_text", return_value=test_content):
                    result = await read_file.ainvoke({"path": "lucid-cove/src/test.py"})
        
        assert result == test_content

    @pytest.mark.asyncio
    @patch("src.tools.dev_read_tools._resolve_repo")
    async def test_file_not_found(self, mock_resolve):
        """Nonexistent file returns error."""
        mock_resolve.return_value = "/app/data/projects/lucid-cove"
        
        with patch("pathlib.Path.exists", return_value=False):
            result = await read_file.ainvoke({"path": "lucid-cove/nonexistent.py"})
        
        assert "File not found" in result

    @pytest.mark.asyncio
    @patch("src.tools.dev_read_tools._resolve_repo")
    async def test_path_is_directory(self, mock_resolve):
        """Directory path returns error."""
        mock_resolve.return_value = "/app/data/projects/lucid-cove"
        
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=False):
                result = await read_file.ainvoke({"path": "lucid-cove/src"})
        
        assert "not a file" in result


class TestReadFileTruncation:
    """Size limit and truncation tests."""

    @pytest.mark.asyncio
    @patch("src.tools.dev_read_tools._resolve_repo")
    async def test_truncates_large_files(self, mock_resolve):
        """Files larger than max_chars are truncated."""
        mock_resolve.return_value = "/app/data/projects/lucid-cove"
        large_content = "x" * 1000
        
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=True):
                with patch("pathlib.Path.read_text", return_value=large_content):
                    result = await read_file.ainvoke(
                        {"path": "lucid-cove/big.txt", "max_chars": 100}
                    )
        
        assert len(result) < 200  # truncated + marker
        assert "truncated" in result
        assert "1000 chars" in result

    @pytest.mark.asyncio
    @patch("src.tools.dev_read_tools._resolve_repo")
    async def test_small_files_not_truncated(self, mock_resolve):
        """Files under limit returned complete."""
        mock_resolve.return_value = "/app/data/projects/lucid-cove"
        content = "small content"
        
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=True):
                with patch("pathlib.Path.read_text", return_value=content):
                    result = await read_file.ainvoke({"path": "lucid-cove/small.txt"})
        
        assert result == content
        assert "truncated" not in result
