"""Tests for xAI Grok OAuth2 provider (#D53).

Tests cover:
  - Token caching (load/save/delete)
  - OAuth config validation
  - Message conversion to xAI format
  - Provider dispatch integration
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# Import after env setup
os.environ["XAI_CLIENT_ID"] = "test-client-id"

from src.models import xai_oauth
from src.models.xai_oauth import (
    _load_cached_tokens,
    _save_cached_tokens,
    _delete_cached_tokens,
    _get_oauth_config,
    ChatXAI,
)


class TestTokenCache:
    """Test token caching functionality."""

    def test_load_cached_tokens_missing(self):
        """Loading from non-existent file returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            xai_oauth.TOKEN_CACHE_FILE = Path(tmpdir) / "tokens.json"
            result = _load_cached_tokens()
            assert result is None

    def test_save_and_load_cached_tokens(self):
        """Saved tokens can be loaded back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            xai_oauth.TOKEN_CACHE_FILE = Path(tmpdir) / "tokens.json"
            xai_oauth.TOKEN_CACHE_DIR = Path(tmpdir)

            tokens = {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_at": 1234567890,
            }
            _save_cached_tokens(tokens)

            loaded = _load_cached_tokens()
            assert loaded == tokens

    def test_delete_cached_tokens(self):
        """Deleted tokens return None on load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            xai_oauth.TOKEN_CACHE_FILE = Path(tmpdir) / "tokens.json"
            xai_oauth.TOKEN_CACHE_DIR = Path(tmpdir)

            tokens = {"access_token": "test"}
            _save_cached_tokens(tokens)
            assert _load_cached_tokens() is not None

            _delete_cached_tokens()
            assert _load_cached_tokens() is None


class TestOAuthConfig:
    """Test OAuth configuration."""

    def test_get_oauth_config_success(self):
        """Valid config returns expected dict."""
        config = _get_oauth_config()
        assert config["client_id"] == "test-client-id"
        # No client_secret for public OAuth flow

    def test_get_oauth_config_missing_client_id(self, monkeypatch):
        """Missing client_id raises ValueError with helpful message."""
        monkeypatch.delenv("XAI_CLIENT_ID", raising=False)
        with pytest.raises(ValueError) as exc:
            _get_oauth_config()
        assert "XAI_CLIENT_ID not configured" in str(exc.value)
        assert "x.ai/api" in str(exc.value)


class TestMessageConversion:
    """Test LangChain message conversion."""

    def test_convert_system_message(self):
        """SystemMessage converts to system role."""
        chat = ChatXAI()
        messages = [SystemMessage(content="You are helpful")]
        result = chat._convert_messages(messages)
        assert result == [{"role": "system", "content": "You are helpful"}]

    def test_convert_human_message(self):
        """HumanMessage converts to user role."""
        chat = ChatXAI()
        messages = [HumanMessage(content="Hello")]
        result = chat._convert_messages(messages)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_convert_ai_message(self):
        """AIMessage converts to assistant role."""
        chat = ChatXAI()
        messages = [AIMessage(content="Hi there")]
        result = chat._convert_messages(messages)
        assert result == [{"role": "assistant", "content": "Hi there"}]

    def test_convert_mixed_messages(self):
        """Mixed message types convert correctly."""
        chat = ChatXAI()
        messages = [
            SystemMessage(content="System"),
            HumanMessage(content="User"),
            AIMessage(content="Assistant"),
        ]
        result = chat._convert_messages(messages)
        assert result == [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User"},
            {"role": "assistant", "content": "Assistant"},
        ]


class TestProviderIntegration:
    """Test integration with provider.py dispatch."""

    def test_xai_oauth_in_provider_dispatch(self):
        """xai-oauth provider is registered in provider.py."""
        from src.models.provider import _client_for

        # Should not raise for xai-oauth
        with patch("src.models.xai_oauth.get_valid_access_token", new_callable=AsyncMock):
            client = _client_for("xai-oauth", "grok-build-0.1", 0.7)
            assert client is not None

    def test_unknown_provider_raises(self):
        """Unknown provider raises RuntimeError."""
        from src.models.provider import _client_for

        with pytest.raises(RuntimeError) as exc:
            _client_for("unknown-provider", "model", 0.7)
        assert "Unknown provider" in str(exc.value)


class TestChatXAIProperties:
    """Test ChatXAI model properties."""

    def test_llm_type(self):
        """_llm_type returns expected value."""
        chat = ChatXAI()
        assert chat._llm_type == "xai-grok"

    def test_identifying_params(self):
        """_identifying_params includes model and temperature."""
        chat = ChatXAI(model="grok-4.3", temperature=0.5)
        params = chat._identifying_params
        assert params["model"] == "grok-4.3"
        assert params["temperature"] == 0.5

    def test_default_model(self):
        """Default model is grok-build-0.1."""
        chat = ChatXAI()
        assert chat.model == "grok-build-0.1"

    def test_default_temperature(self):
        """Default temperature is 0.7."""
        chat = ChatXAI()
        assert chat.temperature == 0.7
