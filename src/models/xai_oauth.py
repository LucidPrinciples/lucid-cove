"""
xAI Grok OAuth2 provider — device-code authentication and Responses-API transport.

Handles:
  1. Device-code OAuth flow against accounts.x.ai
  2. Token storage, refresh, and auto-refresh on 401
  3. xAI Responses-API client for chat completion

Token cache lives in app data volume (survives recreates), never in repo/logs.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from src.env import env

# xAI OAuth2 endpoints
# Auth API server (device-code + token endpoints)
# From Hermes docs: auth.x.ai is the API server, accounts.x.ai is the user-facing verification URL
XAI_AUTH_URL = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_TOKEN_VERIFY_URL = "https://auth.x.ai/oauth2/introspect"
# User verification URL (where they enter the code)
XAI_VERIFY_URL = "https://accounts.x.ai"

# xAI Responses-API endpoint
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"

# Token storage path (app data volume, survives recreates)
TOKEN_CACHE_DIR = Path("/app/data/.xai_tokens")
TOKEN_CACHE_FILE = TOKEN_CACHE_DIR / "tokens.json"

# OAuth scopes for Grok access
XAI_SCOPES = ["openid", "profile", "email", "offline_access", "grok-cli:access", "api:access"]


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
    
    xAI device-code OAuth requires a registered OAuth app client_id.
    No client_secret is needed (public client), but you must register
    an app at https://x.ai/api to get a client_id.
    """
    # Shared "Grok Build" OAuth client (the same one Hermes/OpenClaw use). The
    # operator authenticates with their OWN SuperGrok / X-Premium subscription
    # via the device-code flow; this id only identifies the app to xAI.
    client_id = env("XAI_CLIENT_ID", default="b1a00492-073a-47ea-816f-4c329264a828")
    
    if not client_id:
        raise ValueError(
            "XAI_CLIENT_ID not configured. "
            "Register an OAuth app at https://x.ai/api to get a client_id, "
            "then set XAI_CLIENT_ID in your environment."
        )
    
    return {
        "client_id": client_id,
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
            await asyncio.sleep(5)  # Back off — AUDIT-F4: never block the event loop
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
        try:
            data = resp.json()
        except Exception:
            data = {}
        error = data.get("error") if isinstance(data, dict) else None
        if error == "invalid_grant":
            # AUDIT-F1: before wiping the cache, check whether another refresher (e.g.
            # a second process sharing the token volume) rotated the token underneath
            # us. If the on-disk refresh_token differs from the one we just presented,
            # a valid newer token exists — reuse it instead of deleting and forcing a
            # needless re-auth. Only a genuine expiry/revoke (token unchanged) clears
            # the cache. The in-process single-flight lock (below) already prevents our
            # own coroutines from racing here.
            current = _load_cached_tokens() or {}
            disk_refresh = current.get("refresh_token")
            if disk_refresh and disk_refresh != refresh_token:
                return current
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


# AUDIT-F1: serialize refreshes. Without this, two concurrent callers both see the
# token inside the expiry buffer, both POST the SAME refresh_token, and a rotating
# server rejects the second as invalid_grant — which used to wipe the cache and wedge
# the (primary) provider until a manual device-code re-auth. The lock makes refresh
# single-flight; waiters reuse the fresh token instead of presenting a consumed one.
_refresh_lock = asyncio.Lock()


async def _refresh_single_flight(failed_token: str | None = None) -> str:
    """Return a valid access token, refreshing at most once across concurrent callers.

    failed_token is None  -> proactive path (token near expiry): inside the lock,
                             reuse the cached token if it is genuinely still valid.
    failed_token set      -> reactive path (a request just got 401): inside the lock,
                             reuse the cached token if it DIFFERS from the one that
                             failed (someone already refreshed), else refresh now.
    """
    async with _refresh_lock:
        tokens = _load_cached_tokens() or {}
        cached_access = tokens.get("access_token")

        if failed_token is not None:
            if cached_access and cached_access != failed_token:
                return cached_access
        else:
            expires_at = tokens.get("expires_at")
            if cached_access and expires_at and time.time() < (expires_at - 300):
                return cached_access

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise ValueError("Access token expired and no refresh token available. Re-authentication required.")

        new_tokens = await refresh_access_token(refresh_token)
        return new_tokens["access_token"]


async def get_valid_access_token() -> str:
    """Get valid access token, refreshing if needed.

    Returns access token string. Raises ValueError if re-auth needed.
    """
    tokens = _load_cached_tokens()
    if not tokens:
        raise ValueError("No xAI tokens cached. Run device-code OAuth flow first.")

    access_token = tokens.get("access_token")
    expires_at = tokens.get("expires_at")

    # Fast path: still valid (with 5-minute buffer), no lock needed.
    if expires_at and time.time() < (expires_at - 300):
        return access_token

    # Needs refresh — go through the single-flight lock so concurrent callers coalesce.
    return await _refresh_single_flight()


async def revoke_tokens():
    """Revoke/clear stored tokens."""
    _delete_cached_tokens()




# =============================================================================
# xAI Responses-API Chat Model
# =============================================================================

class ChatXAI(BaseChatModel):
    """LangChain-compatible chat model for xAI Grok via Responses-API.

    Uses OAuth2 tokens (auto-refreshed) for authentication.
    Supports non-streaming completions, fake streaming, and TOOL CALLING.

    Tool calling (added 2026-07-13, grok-tool-calling-spec Part A):
      The base BaseChatModel.bind_tools raises NotImplementedError, so every
      agent turn (channels.py binds tools on every call) died and fell back to
      local qwen. ChatXAI now implements the LangChain tool contract on top of
      the xAI Responses API (OpenAI-Responses-compatible, FLATTENED tool shape).
    """

    model: str = "grok-build-0.1"  # Default model
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 120.0
    reasoning_effort: str = "medium"  # xAI reasoning depth: low | medium | high

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

    # ------------------------------------------------------------------
    # Tool calling — LangChain contract
    # ------------------------------------------------------------------
    @staticmethod
    def _format_tool_for_xai(tool: Any) -> dict:
        """Convert one LangChain tool to the xAI Responses tool shape.

        `convert_to_openai_tool` yields the Chat-Completions shape
        `{"type":"function","function":{name,description,parameters}}`.
        The Responses API is FLATTENED — name/description/parameters live at
        the tool top level, NOT nested under a "function" key.
        """
        oai = convert_to_openai_tool(tool)
        fn = oai.get("function", oai) if isinstance(oai, dict) else {}
        return {
            "type": "function",
            "name": fn.get("name"),
            "description": fn.get("description", "") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }

    @staticmethod
    def _resolve_tool_choice(tool_choice: Any) -> Any:
        """Map LangChain tool_choice conventions to the xAI Responses shape.

        Responses accepts: "auto" | "required" | "none" | {"type":"function","name":...}.
        LangChain callers may pass None / a bool / "any" / a bare tool name.
        """
        if tool_choice is None:
            return None
        if isinstance(tool_choice, dict):
            return tool_choice
        if isinstance(tool_choice, bool):
            return "required" if tool_choice else "none"
        if isinstance(tool_choice, str):
            if tool_choice in ("auto", "required", "none"):
                return tool_choice
            if tool_choice == "any":
                return "required"
            # A specific tool name.
            return {"type": "function", "name": tool_choice}
        return None

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """Bind tools for the xAI Responses API.

        Returns a Runnable (self.bind(...)) so the formatted tools + resolved
        tool_choice arrive in `_agenerate`/`_astream` **kwargs on every call.
        This is the standard custom-model pattern — self is not mutated.
        """
        formatted = [self._format_tool_for_xai(t) for t in tools]
        bind_kwargs: dict[str, Any] = {"tools": formatted}
        resolved = self._resolve_tool_choice(tool_choice)
        if resolved is not None:
            bind_kwargs["tool_choice"] = resolved
        return self.bind(**bind_kwargs, **kwargs)

    # ------------------------------------------------------------------
    # Message conversion (single converter — request build AND history replay)
    # ------------------------------------------------------------------
    def _build_responses_input(self, messages: list[BaseMessage]) -> tuple[str | None, list[dict]]:
        """Convert LangChain messages to (instructions, input_items) for Responses.

        System -> `instructions` (top-level). Human/plain-AI -> role items.
        The tool-loop return leg:
          - AIMessage WITH .tool_calls  -> optional assistant text item, then one
            `{"type":"function_call", name, arguments, call_id}` per call.
          - ToolMessage                 -> `{"type":"function_call_output", call_id, output}`.
        Ordering is preserved by iterating messages in order, so every
        function_call precedes its matching function_call_output (Responses
        rejects an output whose call has not been seen yet).
        """
        instructions_parts: list[str] = []
        input_items: list[dict] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                instructions_parts.append(
                    m.content if isinstance(m.content, str) else str(m.content)
                )
            elif isinstance(m, ToolMessage):
                content = m.content
                if not isinstance(content, str):
                    content = str(content)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": m.tool_call_id,
                    "output": content,
                })
            elif isinstance(m, AIMessage):
                tool_calls = getattr(m, "tool_calls", None) or []
                text = m.content
                if isinstance(text, list):
                    text = " ".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in text
                    )
                if text and isinstance(text, str) and text.strip():
                    input_items.append({"role": "assistant", "content": text})
                for tc in tool_calls:
                    input_items.append({
                        "type": "function_call",
                        "name": tc.get("name"),
                        "arguments": json.dumps(tc.get("args") or {}),
                        "call_id": tc.get("id") or f"call_{uuid.uuid4().hex}",
                    })
            elif isinstance(m, HumanMessage):
                input_items.append({"role": "user", "content": m.content})
            else:
                input_items.append({"role": "user", "content": str(m.content)})
        instructions = "\n\n".join(p for p in instructions_parts if p) or None
        return instructions, input_items

    def _convert_messages(self, messages: list[BaseMessage]) -> list[dict]:
        """LEGACY flat converter (System->system role item). NOT used by the live
        path — `_agenerate` uses `_build_responses_input`, which splits System out
        to `instructions` and understands tool items. Retained unchanged because
        existing unit tests pin this shape; kept for any external caller."""
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

    # ------------------------------------------------------------------
    # Response parsing (Responses output -> AIMessage with tool_calls)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_response(data: dict) -> AIMessage:
        """Parse an xAI Responses payload into an AIMessage.

        Accumulates message text and collects `function_call` items into
        LangChain tool_calls (`{name, args, id, type}`). Falls back to the
        chat-completions shape if the API ever returns `choices`.
        """
        content = ""
        tool_calls: list[dict] = []

        if "output" in data:
            for item in data.get("output") or []:
                itype = item.get("type")
                if itype == "message":
                    for ci in item.get("content", []):
                        if ci.get("type") in ("output_text", "text"):
                            content += ci.get("text", "")
                elif itype == "function_call":
                    raw_args = item.get("arguments")
                    try:
                        if isinstance(raw_args, str) and raw_args.strip():
                            args = json.loads(raw_args)
                        elif isinstance(raw_args, dict):
                            args = raw_args
                        else:
                            args = {}
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"
                    tool_calls.append({
                        "name": item.get("name") or "",
                        "args": args,
                        "id": call_id,
                        "type": "tool_call",
                    })
        elif "choices" in data:
            # Fallback: chat-completions shape.
            for choice in data["choices"]:
                msg = choice.get("message", {}) or {}
                content += msg.get("content") or ""
                for tc in msg.get("tool_calls", []) or []:
                    fn = tc.get("function", {}) or {}
                    raw_args = fn.get("arguments")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tool_calls.append({
                        "name": fn.get("name") or "",
                        "args": args,
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex}",
                        "type": "tool_call",
                    })

        if tool_calls:
            return AIMessage(content=content, tool_calls=tool_calls)
        return AIMessage(content=content)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync generation — bridges to the async path safely.

        AUDIT-F5: the old body called `asyncio.run(...)` unconditionally, which raises
        `RuntimeError: asyncio.run() cannot be called from a running event loop` whenever
        a sync LangChain path (.invoke) is reached from inside this async app. Detect a
        running loop and, if present, run the coroutine on its own loop in a worker
        thread instead of blowing up.
        """
        coro_factory = lambda: self._agenerate(messages, stop, run_manager, **kwargs)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro_factory())
        # Already inside a running loop — offload to a thread with its own loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro_factory())).result()

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async call to xAI Responses-API. AUDIT-F5: this is the canonical async method
        LangChain invokes for .ainvoke (was `_acall`, which LangChain never called).

        Tool calling: `tools`/`tool_choice` arrive via kwargs from bind_tools().
        """
        access_token = await get_valid_access_token()

        # xAI Responses API: the conversation goes in `input`; system prompts map
        # to the top-level `instructions` field (OpenAI-Responses-compatible shape).
        # One converter handles System/Human/AI AND the tool return leg
        # (ToolMessage -> function_call_output, AI.tool_calls -> function_call).
        instructions, input_items = self._build_responses_input(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "temperature": self.temperature,
        }
        # Reasoning depth applies to the grok-4.x reasoning models (not grok-build).
        # grok-4.x supports reasoning + tools together; keep both. If a future tier
        # ever 422s on the combination, drop `reasoning` when tools are present.
        if self.reasoning_effort and self.model.startswith("grok-4"):
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if instructions:
            payload["instructions"] = instructions
        if self.max_tokens:
            payload["max_output_tokens"] = self.max_tokens

        # Tools (from bind_tools -> Runnable.bind -> per-call kwargs).
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = tools
            tool_choice = kwargs.get("tool_choice")
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                XAI_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        # Handle 401 — try refresh once (single-flight; concurrent 401s coalesce onto
        # one refresh instead of each presenting the same soon-consumed token). AUDIT-F1
        if resp.status_code == 401:
            tokens = _load_cached_tokens()
            if tokens and tokens.get("refresh_token"):
                try:
                    access_token = await _refresh_single_flight(failed_token=access_token)
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
            # Standard tier error — surface honestly. AUDIT-F3: a subscription/gateway
            # 403 often returns HTML or an empty body, and the token endpoints return
            # `error` as a STRING, not a dict — so parse defensively and never let a
            # JSONDecodeError/AttributeError mask the honest 403 message.
            try:
                data = resp.json()
            except Exception:
                data = {}
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                error_msg = err.get("message") or "Access denied"
            else:
                error_msg = err or resp.text or "Access denied"
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

        # Parse Responses output -> AIMessage (text + tool_calls). When there are
        # tool_calls, content may be empty — that is valid; the graph checks
        # response.tool_calls (channels.py) and routes to the tools.
        message = self._parse_response(data)
        # Stamp provenance so channels.py (actual_model = meta["model_name"]) and
        # the chat UI badge show the real model. Without this, ChatXAI returned no
        # response_metadata, so a grok turn was mislabeled "local Ollama" and the
        # badge went blank. self.model is the API id (e.g. "grok-4.5").
        rmeta: dict[str, Any] = {"model_name": self.model, "model": self.model}
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            rmeta["token_usage"] = usage
        message.response_metadata = rmeta
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatGenerationChunk, None]:
        """Async streaming not implemented — xAI Responses-API may not support it.
        Fall back to non-streaming and yield a single chunk. AUDIT-F5: BaseChatModel
        requires ChatGenerationChunk here, not ChatGeneration. The chunk carries
        tool_call_chunks so a streaming caller still sees tool calls (the agent path
        uses .ainvoke, so _agenerate is the hot path — this is for completeness)."""
        result = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        for gen in result.generations:
            msg = gen.message
            rmeta = getattr(msg, "response_metadata", {}) or {}
            tcs = getattr(msg, "tool_calls", None) or []
            if tcs:
                tool_call_chunks = [
                    {
                        "name": tc.get("name"),
                        "args": json.dumps(tc.get("args") or {}),
                        "id": tc.get("id"),
                        "index": i,
                    }
                    for i, tc in enumerate(tcs)
                ]
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=msg.content,
                        tool_call_chunks=tool_call_chunks,
                        response_metadata=rmeta,
                    )
                )
            else:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=msg.content, response_metadata=rmeta)
                )




# Convenience factory matching provider.py pattern
def _xai_oauth_client(model: str, temperature: float) -> ChatXAI:
    """Factory for xAI Grok client with OAuth2 authentication."""
    return ChatXAI(model=model, temperature=temperature)
