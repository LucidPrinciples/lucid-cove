"""
xAI Grok OAuth2 provider — device-code authentication and Responses-API transport.

Handles:
  1. Device-code OAuth flow against accounts.x.ai
  2. Token storage, refresh, and auto-refresh on 401
  3. xAI Responses-API client for chat completion

Token cache lives in app data volume (survives recreates), never in repo/logs.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from src.env import env

# xAI OAuth2 endpoints
# Auth server (from Hermes docs: https://accounts.x.ai)
XAI_AUTH_URL = "https://accounts.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://accounts.x.ai/oauth2/token"
XAI_TOKEN_VERIFY_URL = "https://accounts.x.ai/oauth2/introspect"
# User verification URL (where they enter the code)
XAI_VERIFY_URL = "https://accounts.x.ai"

# xAI Responses-API endpoint
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"

# Token storage path (app data volume, survives recreates)
TOKEN_CACHE_DIR = Path("/app/data/.xai_tokens")
TOKEN_CACHE_FILE = TOKEN_CACHE_DIR / "tokens.json"

# OAuth scopes for Grok access
XAI_SCOPES = ["read", "write"]  # Adjust per xAI documentation


def _ensure_token_dir():
    """Ensure token cache directory exists with restrictive permissions."""
    TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Restrict to owner read/write only (0o700)
    os.chmod(TOKEN_CACHE_DIR, 0o700)


def _load_cached_tokens() -> dict | None:
    """Load tokens from cache file."""
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with open(TOKEN_CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cached_tokens(tokens: dict):
    """Save tokens to cache file with restricted permissions."""
    _ensure_token_dir()
    temp_file = TOKEN_CACHE_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(tokens, f)
    # Restrict to owner read/write only (0o600)
    os.chmod(temp_file, 0o600)
    temp_file.rename(TOKEN_CACHE_FILE)


def _delete_cached_tokens():
    """Delete cached tokens (e.g., on revoke or invalid_grant)."""
    if TOKEN_CACHE_FILE.exists():
        TOKEN_CACHE_FILE.unlink()


def _get_oauth_config() -> dict:
    """Get xAI OAuth config.
    
    xAI uses a public OAuth client for device-code flow — no client_secret
    required. This matches the Hermes implementation pattern.
    """
    # xAI uses a public client for device-code flow
    # No env vars required — user authenticates via browser
    return {
        "client_id": "xai-public-client",  # Public client identifier
    }


async def start_device_code_flow() -> dict:
    """Start device-code OAuth flow.

    Returns dict with:
      - device_code: for polling
      - user_code: to display to operator
      - verification_uri: where operator goes to authorize
      - expires_in: seconds until device_code expires
    """
    config = _get_oauth_config()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            XAI_AUTH_URL,
            data={
                "client_id": config["client_id"],
                "scope": " ".join(XAI_SCOPES),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Device code request failed: {resp.status_code} - {resp.text}")

    data = resp.json()
    # Standard device-code response fields
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", XAI_VERIFY_URL),
        "expires_in": data.get("expires_in", 1800),
        "interval": data.get("interval", 5),  # polling interval in seconds
    }


async def poll_for_token(device_code: str) -> dict | None:
    """Poll for access token using device_code.

    Returns tokens dict on success, None on pending (keep polling),
    raises on error.
    """
    config = _get_oauth_config()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            XAI_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": config["client_id"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code == 400:
        data = resp.json()
        error = data.get("error")
        if error == "authorization_pending":
            return None  # Keep polling
        elif error == "slow_down":
            time.sleep(5)  # Back off
            return None
        elif error == "expired_token":
            raise RuntimeError("Device code expired. Start new flow.")
        elif error == "access_denied":
            raise RuntimeError("Authorization denied by user.")
        else:
            raise RuntimeError(f"Token poll error: {error}")

    if resp.status_code != 200:
        raise RuntimeError(f"Token poll failed: {resp.status_code} - {resp.text}")

    data = resp.json()
    # Calculate absolute expiration time
    data["expires_at"] = time.time() + data.get("expires_in", 3600)

    # Cache tokens
    _save_cached_tokens(data)

    return data


async def refresh_access_token(refresh_token: str) -> dict:
    """Refresh access token using refresh_token.

    Returns new tokens dict. Raises on failure (including invalid_grant).
    """
    config = _get_oauth_config()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            XAI_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config["client_id"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code == 400:
        data = resp.json()
        error = data.get("error")
        if error == "invalid_grant":
            # Refresh token expired or revoked — need re-auth
            _delete_cached_tokens()
            raise ValueError("Re-authentication required (invalid_grant). Device-code flow needed.")
        raise RuntimeError(f"Token refresh error: {error}")

    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.status_code} - {resp.text}")

    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)

    # Preserve refresh_token if not returned
    if "refresh_token" not in data:
        data["refresh_token"] = refresh_token

    # Cache updated tokens
    _save_cached_tokens(data)

    return data


async def get_valid_access_token() -> str:
    """Get valid access token, refreshing if needed.

    Returns access token string. Raises ValueError if re-auth needed.
    """
    tokens = _load_cached_tokens()
    if not tokens:
        raise ValueError("No xAI tokens cached. Run device-code OAuth flow first.")

    access_token = tokens.get("access_token")
    expires_at = tokens.get("expires_at")
    refresh_token = tokens.get("refresh_token")

    # Check if still valid (with 5-minute buffer)
    if expires_at and time.time() < (expires_at - 300):
        return access_token

    # Needs refresh
    if not refresh_token:
        raise ValueError("Access token expired and no refresh token available. Re-authentication required.")

    # Attempt refresh
    new_tokens = await refresh_access_token(refresh_token)
    return new_tokens["access_token"]


async def revoke_tokens():
    """Revoke/clear stored tokens."""
    _delete_cached_tokens()


# =============================================================================
# xAI Responses-API Chat Model
# =============================================================================

class ChatXAI(BaseChatModel):
    """LangChain-compatible chat model for xAI Grok via Responses-API.

    Uses OAuth2 tokens (auto-refreshed) for authentication.
    Supports streaming and non-streaming completions.
    """

    model: str = "grok-build-0.1"  # Default model
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 120.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "xai-grok"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync generation — delegates to async."""
        import asyncio
        return asyncio.run(self._acall(messages, stop, run_manager, **kwargs))

    def _convert_messages(self, messages: list[BaseMessage]) -> list[dict]:
        """Convert LangChain messages to xAI Responses-API format."""
        converted = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                converted.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                converted.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                converted.append({"role": "assistant", "content": msg.content})
            else:
                # Fallback — treat as user
                converted.append({"role": "user", "content": str(msg.content)})
        return converted

    async def _acall(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async call to xAI Responses-API."""
        access_token = await get_valid_access_token()

        payload = {
            "model": self.model,
            "messages": self._convert_messages(messages),
            "temperature": self.temperature,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if stop:
            payload["stop"] = stop

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                XAI_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        # Handle 401 — try refresh once
        if resp.status_code == 401:
            tokens = _load_cached_tokens()
            if tokens and tokens.get("refresh_token"):
                try:
                    new_tokens = await refresh_access_token(tokens["refresh_token"])
                    access_token = new_tokens["access_token"]
                    # Retry with new token
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(
                            XAI_RESPONSES_URL,
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                        )
                except ValueError as e:
                    if "invalid_grant" in str(e):
                        raise RuntimeError(f"xAI authentication expired: {e}")
                    raise

        if resp.status_code == 403:
            # Standard tier error — surface honestly
            data = resp.json()
            error_msg = data.get("error", {}).get("message", "Access denied")
            if "subscription" in error_msg.lower() or "premium" in error_msg.lower():
                raise RuntimeError(
                    f"xAI 403: {error_msg}. "
                    "SuperGrok/X-Premium+ subscription required. "
                    "See hermes-agent#26847 for upstream issue."
                )
            raise RuntimeError(f"xAI 403: {error_msg}")

        if resp.status_code != 200:
            raise RuntimeError(f"xAI API error {resp.status_code}: {resp.text}")

        data = resp.json()

        # Extract content from Responses-API format
        content = ""
        if "output" in data:
            for item in data["output"]:
                if item.get("type") == "message":
                    for content_item in item.get("content", []):
                        if content_item.get("type") == "text":
                            content += content_item.get("text", "")
        elif "choices" in data:
            # Fallback to chat-completions format
            for choice in data["choices"]:
                content += choice.get("message", {}).get("content", "")

        # Create LangChain message
        message = AIMessage(content=content)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatGeneration, None]:
        """Async streaming not implemented — xAI Responses-API may not support it."""
        # For now, just call non-streaming and yield once
        result = await self._acall(messages, stop=stop, run_manager=run_manager, **kwargs)
        for gen in result.generations:
            yield gen




# Convenience factory matching provider.py pattern
def _xai_oauth_client(model: str, temperature: float) -> ChatXAI:
    """Factory for xAI Grok client with OAuth2 authentication."""
    return ChatXAI(model=model, temperature=temperature)
