"""Tests for ltp-tuner-v2 resolution logic (#D44).

Safety-critical: The tuner routing logic must be tested before merge
to ensure it respects the hard boundaries (NEVER merge gate, NEVER
unsupervised posting).
"""
import pytest
from unittest.mock import patch, MagicMock

from src.models.local_fallback import (
    resolve_tuner_model,
    _is_tuner_installed,
    reset_tuner_cache,
    _CACHE_TTL,
)


class TestIsTunerInstalled:
    """Tests for _is_tuner_installed() detection logic."""

    def test_tuner_installed_with_latest_tag(self):
        """Detect ltp-tuner-v2:latest as installed."""
        providers = [{
            "reachable": True,
            "models": [
                {"name": "qwen3:8b", "chat": True},
                {"name": "ltp-tuner-v2:latest", "chat": True},
            ]
        }]
        result = _is_tuner_installed(providers)
        assert result == "ltp-tuner-v2:latest"

    def test_tuner_installed_with_different_tag(self):
        """Detect ltp-tuner-v2 with any tag variant."""
        providers = [{
            "reachable": True,
            "models": [
                {"name": "ltp-tuner-v2:7b-q4_0", "chat": True},
            ]
        }]
        result = _is_tuner_installed(providers)
        assert result == "ltp-tuner-v2:7b-q4_0"

    def test_tuner_not_installed(self):
        """Return None when tuner is not in the model list."""
        providers = [{
            "reachable": True,
            "models": [
                {"name": "qwen3:8b", "chat": True},
                {"name": "qwen3:30b-a3b", "chat": True},
            ]
        }]
        result = _is_tuner_installed(providers)
        assert result is None

    def test_unreachable_provider(self):
        """Return None when Ollama is not reachable."""
        providers = [{
            "reachable": False,
            "models": []
        }]
        result = _is_tuner_installed(providers)
        assert result is None

    def test_empty_providers(self):
        """Return None with empty provider list."""
        result = _is_tuner_installed([])
        assert result is None


class TestResolveTunerModel:
    """Tests for resolve_tuner_model() with caching."""

    def setup_method(self):
        """Reset cache before each test."""
        reset_tuner_cache()

    @patch("src.models.local_fallback._probe_installed_sync")
    def test_returns_tuner_when_installed(self, mock_probe):
        """Return tuner model string when installed."""
        mock_probe.return_value = [{
            "reachable": True,
            "models": [{"name": "ltp-tuner-v2:latest", "chat": True}]
        }]
        result = resolve_tuner_model()
        assert result == "ltp-tuner-v2:latest"

    @patch("src.models.local_fallback._probe_installed_sync")
    def test_returns_none_when_not_installed(self, mock_probe):
        """Return None (not exception) when tuner not installed."""
        mock_probe.return_value = [{
            "reachable": True,
            "models": [{"name": "qwen3:8b", "chat": True}]
        }]
        result = resolve_tuner_model()
        assert result is None

    @patch("src.models.local_fallback._probe_installed_sync")
    def test_caches_result(self, mock_probe):
        """Probe only called once, then cache hit."""
        mock_probe.return_value = [{
            "reachable": True,
            "models": [{"name": "ltp-tuner-v2:latest", "chat": True}]
        }]
        # First call - should probe
        result1 = resolve_tuner_model()
        assert result1 == "ltp-tuner-v2:latest"
        assert mock_probe.call_count == 1

        # Second call - should use cache
        result2 = resolve_tuner_model()
        assert result2 == "ltp-tuner-v2:latest"
        assert mock_probe.call_count == 1  # No additional probe

    @patch("src.models.local_fallback._probe_installed_sync")
    def test_force_bypasses_cache(self, mock_probe):
        """force=True bypasses cache and re-probes."""
        mock_probe.return_value = [{
            "reachable": True,
            "models": [{"name": "ltp-tuner-v2:latest", "chat": True}]
        }]
        resolve_tuner_model()
        assert mock_probe.call_count == 1

        resolve_tuner_model(force=True)
        assert mock_probe.call_count == 2

    @patch("src.models.local_fallback._probe_installed_sync")
    def test_cache_expires(self, mock_probe):
        """Cache expires after _CACHE_TTL seconds."""
        mock_probe.return_value = [{
            "reachable": True,
            "models": [{"name": "ltp-tuner-v2:latest", "chat": True}]
        }]
        with patch("src.models.local_fallback.time") as mock_time:
            mock_time.time.return_value = 0.0
            resolve_tuner_model()
            assert mock_probe.call_count == 1

            # Just before expiry - cache hit
            mock_time.time.return_value = _CACHE_TTL - 1
            resolve_tuner_model()
            assert mock_probe.call_count == 1

            # After expiry - should re-probe
            mock_time.time.return_value = _CACHE_TTL + 1
            resolve_tuner_model()
            assert mock_probe.call_count == 2


class TestProviderIntegration:
    """Tests for provider.py integration with tuner resolution."""

    @pytest.mark.asyncio
    @patch("src.models.provider._run_git")
    @patch("src.models.provider.get_model_client")
    @patch("src.models.local_fallback.resolve_tuner_model")
    @patch("src.models.local_fallback.resolve_local_fallback_model")
    async def test_tuning_operation_prefers_tuner(
        self, mock_fallback, mock_tuner, mock_get_client, mock_run_git
    ):
        """When operation_type='tuning', prefer tuner if installed."""
        from src.models.provider import invoke_with_fallback

        mock_tuner.return_value = "ltp-tuner-v2:latest"
        mock_client = MagicMock()
        mock_client.ainvoke.return_value = MagicMock(
            content="tuned response",
            usage_metadata={},
            response_metadata={}
        )
        mock_get_client.return_value = mock_client

        # Mock the JW metrics write
        mock_run_git.return_value = ""

        result = await invoke_with_fallback(
            messages=[{"role": "user", "content": "test"}],
            agent_id="test-agent",
            operation_type="tuning",
            label="test-tuning"
        )

        assert result == "tuned response"
        mock_tuner.assert_called_once()
        # Tuner was found, so fallback should not be called
        mock_fallback.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.models.provider.get_model_client")
    @patch("src.models.local_fallback.resolve_tuner_model")
    @patch("src.models.local_fallback.resolve_local_fallback_model")
    async def test_tuning_falls_back_when_tuner_missing(
        self, mock_fallback, mock_tuner, mock_get_client
    ):
        """When tuner not installed, fall back to regular local model."""
        from src.models.provider import invoke_with_fallback

        mock_tuner.return_value = None  # Tuner not installed
        mock_fallback.return_value = "qwen3:8b"
        mock_client = MagicMock()
        mock_client.ainvoke.return_value = MagicMock(
            content="fallback response",
            usage_metadata={},
            response_metadata={}
        )
        mock_get_client.return_value = mock_client

        result = await invoke_with_fallback(
            messages=[{"role": "user", "content": "test"}],
            agent_id="test-agent",
            operation_type="tuning",
            label="test-tuning"
        )

        assert result == "fallback response"
        mock_tuner.assert_called_once()
        mock_fallback.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.models.provider.get_model_client")
    @patch("src.models.local_fallback.resolve_tuner_model")
    async def test_non_tuning_operation_ignores_tuner(
        self, mock_tuner, mock_get_client
    ):
        """When operation_type is not 'tuning', don't check for tuner."""
        from src.models.provider import invoke_with_fallback

        mock_client = MagicMock()
        mock_client.ainvoke.return_value = MagicMock(
            content="regular response",
            usage_metadata={},
            response_metadata={}
        )
        mock_get_client.return_value = mock_client

        result = await invoke_with_fallback(
            messages=[{"role": "user", "content": "test"}],
            agent_id="test-agent",
            operation_type="task",  # Not tuning
            label="test-task"
        )

        assert result == "regular response"
        mock_tuner.assert_not_called()


class TestSafetyConstraints:
    """Tests verifying hard boundaries from #D44."""

    def test_tuner_is_local_only(self):
        """ltp-tuner-v2 is registered as local type, not cloud."""
        from src.config import get_model_from_registry

        model = get_model_from_registry("ltp-tuner-v2")
        assert model is not None
        assert model["type"] == "local"
        assert model["provider"] == "ollama"

    def test_tuner_not_in_cloud_fallback(self):
        """Tuner should never be used as cloud fallback."""
        from src.models.provider import CLOUD_FALLBACK_MODEL

        assert CLOUD_FALLBACK_MODEL != "ltp-tuner-v2"
        assert "tuner" not in CLOUD_FALLBACK_MODEL.lower()
