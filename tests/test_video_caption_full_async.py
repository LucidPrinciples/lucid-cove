"""A14 extension — caption-full / process-moments as background jobs."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "src/dashboard/routes/video_jobs.py"
CROP = ROOT / "src/dashboard/static/action-board/video-crop-position.html"
PROC = ROOT / "src/dashboard/routes/video_processing.py"


def test_routes_registered():
    text = JOBS.read_text()
    assert '/caption-full/start' in text
    assert '/process-moments/start' in text
    assert '"caption_full"' in text or "'caption_full'" in text
    assert '"process_moments"' in text or "'process_moments'" in text
    assert '_KIND_START_PHASE' in text
    assert 'rendering' in text


def test_crop_page_uses_async_start_not_sync_post():
    html = CROP.read_text()
    assert '/api/video/caption-full/start' in html
    assert '/api/video/process-moments/start' in html
    assert 'pollVideoJob' in html
    # Sync held-open paths must not remain the primary fire path
    assert "fetch('/api/video/caption-full'" not in html
    assert "fetch('/api/video/process-moments'" not in html


def test_proxy_guards_empty_json_body():
    text = PROC.read_text()
    assert 'empty response' in text.lower() or 'Empty response' in text
    assert 'non-JSON' in text or 'non-json' in text.lower()


@pytest.mark.asyncio
async def test_start_caption_full_requires_stem():
    from src.dashboard.routes import video_jobs as vj

    req = MagicMock()
    req.json = AsyncMock(return_value={})
    resp = await vj.start_caption_full(req)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_start_caption_full_spawns_job(monkeypatch):
    from src.dashboard.routes import video_jobs as vj

    spawned = {}

    def fake_spawn(request, body, handler, kind):
        spawned["kind"] = kind
        spawned["body"] = body
        return {"job_id": "abc123", "state": "queued"}

    monkeypatch.setattr(vj, "_spawn", fake_spawn)
    # Avoid importing the real handler body
    import types
    import sys
    fake_mod = types.ModuleType("src.dashboard.routes.video_processing")
    async def caption_full_video(request):
        return {"ok": True}
    fake_mod.caption_full_video = caption_full_video
    monkeypatch.setitem(sys.modules, "src.dashboard.routes.video_processing", fake_mod)

    req = MagicMock()
    req.json = AsyncMock(return_value={"stem": "IMG_1", "caption": {}})
    out = await vj.start_caption_full(req)
    assert out["job_id"] == "abc123"
    assert spawned["kind"] == "caption_full"
    assert spawned["body"]["stem"] == "IMG_1"
