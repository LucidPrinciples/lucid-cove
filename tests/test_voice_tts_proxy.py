"""Same-origin TTS proxy — browser speak without reaching voice.{domain}."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.dashboard.routes.voice_proxy import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


class _FakeResp:
    def __init__(self, status_code=200, content=b"RIFF....WAV", headers=None, json_body=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "audio/wav"}
        self._json = json_body

    def json(self):
        if self._json is not None:
            return self._json
        raise ValueError("no json")


def test_proxy_tts_forwards_wav():
    app = _app()
    fake = _FakeResp(content=b"WAVDATA")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.dashboard.routes.voice_proxy._voice_internal", return_value="http://voice:8300"), \
         patch("httpx.AsyncClient", return_value=mock_client):
        client = TestClient(app)
        r = client.post("/api/voice/tts", json={"text": "Hello there.", "agent": "cyrus"})

    assert r.status_code == 200
    assert r.content == b"WAVDATA"
    assert "audio" in r.headers.get("content-type", "")
    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.await_args
    assert args[0] == "http://voice:8300/api/tts"
    assert kwargs["json"]["text"] == "Hello there."
    assert kwargs["json"]["agent"] == "cyrus"


def test_proxy_tts_rejects_empty():
    app = _app()
    with patch("src.dashboard.routes.voice_proxy._voice_internal", return_value="http://voice:8300"):
        client = TestClient(app)
        r = client.post("/api/voice/tts", json={"text": "   "})
    assert r.status_code == 400


def test_proxy_tts_disabled_when_no_internal():
    app = _app()
    with patch("src.dashboard.routes.voice_proxy._voice_internal", return_value=""):
        client = TestClient(app)
        r = client.post("/api/voice/tts", json={"text": "hi"})
    assert r.status_code == 503


def test_proxy_tts_timeout_maps_504():
    import httpx

    app = _app()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.dashboard.routes.voice_proxy._voice_internal", return_value="http://voice:8300"), \
         patch("httpx.AsyncClient", return_value=mock_client):
        client = TestClient(app)
        r = client.post("/api/voice/tts", json={"text": "Hello"})

    assert r.status_code == 504


def test_proxy_voices_lists():
    app = _app()
    fake = _FakeResp(
        content=b'{"config":{"default":"en_US-lessac-medium"},"loaded":{}}',
        headers={"content-type": "application/json"},
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=fake)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.dashboard.routes.voice_proxy._voice_internal", return_value="http://voice:8300"), \
         patch("httpx.AsyncClient", return_value=mock_client):
        client = TestClient(app)
        r = client.get("/api/voice/tts/voices")

    assert r.status_code == 200
    assert b"lessac" in r.content
