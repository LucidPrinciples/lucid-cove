"""#VMETA-POLISH1 — final meta polish gate + polish model setting."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_polish_model_flag_and_api_surface():
    src = (ROOT / "src/dashboard/routes/pipeline_keys.py").read_text()
    assert "POLISH_MODEL_FLAG" in src
    assert "def get_polish_model" in src
    assert "polish_model_allowed" in src
    assert "/api/pipeline/polish-model" in src
    assert "out[\"polish_model\"]" in src or "out['polish_model']" in src


def test_ui_surfaces_polish_model():
    ui = (ROOT / "src/dashboard/static/action-board/full-video-pipeline.html").read_text()
    assert "pk-polish-model" in ui
    assert "savePolishModel" in ui
    assert "Polish model" in ui
    # Mine label clarifies primary for moments
    assert "moments (mine)" in ui or "use primary" in ui.lower()


def test_build_polish_prompt_includes_skeleton():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        build_polish_system_prompt,
        DEFAULT_DESCRIPTION_SKELETON,
    )
    p = build_polish_system_prompt(empty_video_meta())
    assert "FINAL POLISH" in p or "final editor" in p.lower()
    assert "LEAD-IN" in p or "lead-in" in p.lower()
    assert "DE-DUPE" in p or "de-dupe" in p.lower()
    m = empty_video_meta()
    m["description_skeleton"] = "CUSTOM SKEL ONLY"
    m["brand_name"] = "Ridge"
    p2 = build_polish_system_prompt(m)
    assert "CUSTOM SKEL ONLY" in p2
    assert "Ridge" in p2


def test_parse_polish_response_items():
    from src.dashboard.routes.video_meta import _parse_polish_response
    raw = """{
      "items": [
        {"id": "a", "title": "T1", "description": "D1", "hashtags": "#x", "tags": ["t"]},
        {"id": "b", "title": "T2", "description": "D2", "hashtags": "", "tags": []}
      ]
    }"""
    items = _parse_polish_response(raw)
    assert items and len(items) == 2
    assert items[0]["id"] == "a" and items[0]["title"] == "T1"
    # fences
    fenced = "```json\n" + raw + "\n```"
    assert _parse_polish_response(fenced)[0]["id"] == "a"
    assert _parse_polish_response("") is None
    assert _parse_polish_response("not json") is None


def test_strip_hashtags_from_title():
    from src.dashboard.routes.video_meta import (
        strip_hashtags_from_title,
        ensure_title_differs_from_opening,
    )
    assert strip_hashtags_from_title("Freedom to choose #Shorts") == "Freedom to choose"
    assert strip_hashtags_from_title("#shorts Daily practice") == "Daily practice"
    assert strip_hashtags_from_title("Stay with it #lucid #dreams") == "Stay with it"
    assert strip_hashtags_from_title("No tags here") == "No tags here"
    cleaned = ensure_title_differs_from_opening({
        "title": "A real question #Shorts",
        "description": "Something else entirely.",
        "hashtags": "#practice",
    })
    assert cleaned["title"] == "A real question"
    assert cleaned["hashtags"] == "#practice"


def test_merge_polished_item():
    from src.dashboard.routes.video_meta import _merge_polished_item
    orig = {"id": "1", "title": "Old", "description": "Old d", "hashtags": "", "tags": [], "platform": "youtube"}
    pol = {"id": "1", "title": "New", "description": "New d", "hashtags": "#a", "tags": ["a"]}
    m = _merge_polished_item(orig, pol)
    assert m["title"] == "New" and m["description"] == "New d"
    assert m["platform"] == "youtube"
    # empty polish fields do not wipe
    m2 = _merge_polished_item(orig, {"id": "1", "title": "", "description": None})
    assert m2["title"] == "Old"


def test_polish_batch_skips_single_without_model():
    from src.dashboard.routes.video_meta import polish_metadata_batch

    drafts = [{"id": "only", "platform": "x", "title": "Hi", "description": "d", "hashtags": "", "tags": []}]

    async def run():
        with patch("src.dashboard.routes.pipeline_keys.get_polish_model", return_value=""):
            out = await polish_metadata_batch(drafts, video_meta={})
            assert out is drafts or out == drafts
            assert out[0]["title"] == "Hi"

    asyncio.get_event_loop().run_until_complete(run())


def test_polish_batch_applies_parsed_items():
    from src.dashboard.routes.video_meta import polish_metadata_batch

    drafts = [
        {"id": "1", "platform": "youtube", "clip_type": "quote", "clip_label": "A",
         "title": "Same Title", "description": "→ topic only", "hashtags": "", "tags": []},
        {"id": "2", "platform": "youtube", "clip_type": "thought", "clip_label": "B",
         "title": "Same Title", "description": "→ topic only", "hashtags": "", "tags": []},
    ]
    fake = {
        "items": [
            {"id": "1", "title": "Title A", "description": "Lead in.\n\n→ topic", "hashtags": "#a", "tags": ["a"]},
            {"id": "2", "title": "Title B", "description": "Other lead.\n\n→ topic", "hashtags": "#b", "tags": ["b"]},
        ]
    }
    import json

    async def fake_invoke(sys_p, hum_p):
        return json.dumps(fake), "test-model"

    async def run():
        with patch("src.dashboard.routes.pipeline_keys.get_polish_model", return_value=""):
            with patch("src.dashboard.routes.video_meta._invoke_polish_model", side_effect=fake_invoke):
                out = await polish_metadata_batch(drafts, video_meta={})
        assert out[0]["title"] == "Title A"
        assert out[1]["title"] == "Title B"
        assert "Lead in" in out[0]["description"]

    asyncio.get_event_loop().run_until_complete(run())


def test_process_moments_calls_polish_batch():
    src = (ROOT / "src/dashboard/routes/video_processing.py").read_text()
    assert "polish_metadata_batch" in src
    assert "pending_drafts" in src
    assert "meta_polished" in src


def test_no_lucid_hardcode_in_polish():
    meta = (ROOT / "src/dashboard/routes/video_meta.py").read_text().lower()
    # polish section should not introduce brand URLs
    assert "lucidtuner.com" not in meta
    assert "lucidprinciples.com/vision" not in meta
