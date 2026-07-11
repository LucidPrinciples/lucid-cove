"""Tests for #D51: Canonical checkout only — no ad-hoc clones; durable workspace.

Verifies:
1. _resolve_repo rejects absolute paths outside canonical roots
2. _resolve_repo rejects path traversal (..)
3. _resolve_repo error messages name allowed roots
4. Sites mount is part of canonical compose generation
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools import dev_tools as dt


class TestResolveRepoBoundaries:
    """HARD BOUNDARY: dev tools only operate within canonical mounted workspaces."""

    def test_rejects_absolute_path_outside_roots(self, tmp_path):
        """Absolute paths outside /app/data/projects or /sites are rejected."""
        bad_path = "/tmp/evil-repo"
        result = dt._resolve_repo(bad_path)
        assert result.startswith("Error:")
        assert "outside allowed directories" in result
        assert "/app/data/projects" in result
        assert "/sites" in result

    def test_rejects_path_traversal(self):
        """Paths containing '..' are rejected."""
        result = dt._resolve_repo("../../../etc/passwd")
        assert result.startswith("Error:")
        assert "traversal" in result.lower()

    def test_accepts_projects_subpath(self, tmp_path, monkeypatch):
        """Paths under PROJECTS_DIR are accepted if they exist."""
        fake_projects = tmp_path / "projects"
        fake_projects.mkdir()
        fake_repo = fake_projects / "test-repo"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        monkeypatch.setattr(dt, "PROJECTS_DIR", fake_projects)
        result = dt._resolve_repo("test-repo")
        assert result == str(fake_repo)

    def test_accepts_sites_subpath(self, tmp_path, monkeypatch):
        """Paths under SITES_DIR are accepted if they exist."""
        fake_sites = tmp_path / "sites"
        fake_sites.mkdir()
        fake_site = fake_sites / "example.com"
        fake_site.mkdir()
        (fake_site / ".git").mkdir()

        monkeypatch.setattr(dt, "SITES_DIR", fake_sites)
        result = dt._resolve_repo("example.com")
        assert result == str(fake_site)

    def test_error_names_allowed_roots(self, tmp_path, monkeypatch):
        """Miss errors explicitly name the allowed directories."""
        fake_projects = tmp_path / "projects"
        fake_projects.mkdir()
        fake_sites = tmp_path / "sites"
        fake_sites.mkdir()

        monkeypatch.setattr(dt, "PROJECTS_DIR", fake_projects)
        monkeypatch.setattr(dt, "SITES_DIR", fake_sites)

        result = dt._resolve_repo("nonexistent-repo")
        assert "Allowed directories:" in result
        assert str(fake_projects) in result
        assert str(fake_sites) in result
        assert "do not improvise a clone" in result.lower()


class TestComposeGeneration:
    """Sites mount is part of canonical compose output."""

    def test_sites_mount_in_compose_template(self):
        """The compose template includes the sites bind mount."""
        from provision.centralized import build_compose

        # Minimal config for build_compose
        cove = {
            "id": "test",
            "name": "Test Cove",
            "_app_port": 8200,
        }
        deploy = {
            "target": "standalone",
            "lucid_cove_path": "/path/to/lucid-cove",
            "app_port": 8200,
            "nextcloud_port": 8080,
        }

        compose = build_compose(cove, {"name": "Test", "handle": "test"}, [], {}, {}, deploy)

        # Should include /sites mount
        assert "/sites" in compose
        assert "sites_mount" in compose or "/sites:/sites" in compose or "sites:/sites" in compose


class TestDevWorkflowBlock:
    """Identity injection includes hard boundary rule."""

    def test_hard_boundary_in_prompt(self):
        """_dev_workflow_block includes the canonical workspace rule."""
        from src.agents.identity import _dev_workflow_block

        agent = {"id": "test", "archetype": "builder", "role": "dev"}
        block = _dev_workflow_block(agent)

        assert "HARD BOUNDARY" in block
        assert "/app/data/projects/" in block
        assert "/sites" in block
        assert "REPORT it" in block
        assert "never improvise" in block.lower()
        assert "A capability you don't have is a reportable fact" in block
