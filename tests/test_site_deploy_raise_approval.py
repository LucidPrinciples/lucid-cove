"""site_deploy stays gated by default; callers may skip the Attention card."""
import inspect

from src.dashboard.routes.sites import _deploy_site_core


def test_deploy_core_accepts_no_card_flag():
    params = inspect.signature(_deploy_site_core).parameters
    assert "raise_approval" in params
    assert params["raise_approval"].default is True
