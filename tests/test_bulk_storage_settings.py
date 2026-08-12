"""Bulk storage settings surface (second-disk self-host path)."""

from src.dashboard.routes import settings as settings_routes
from src.env import REGISTRY


def test_env_registry_has_bulk_keys():
    names = {v.name for v in REGISTRY}
    assert "COVE_BULK_ROOT" in names
    assert "NEXTCLOUD_HOST_PATH" in names


def test_bulk_storage_route_on_settings_router():
    paths = {getattr(r, "path", None) for r in settings_routes.router.routes}
    assert "/api/settings/bulk-storage" in paths
