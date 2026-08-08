"""#VP-ATLAS1 — presence video pipeline agent tools."""

import json
from types import SimpleNamespace

import pytest

from src.tools import video_pipeline_tools as vpt


def test_has_processed_marker_and_shorts():
    shorts = {
        "IMG_7168-moments-processed.json",
        "IMG_7168-m0-story-Full-video.mp4",
        "IMG_7168-m0-preview.mp4",
        "OTHER-clip.mp4",
    }
    assert vpt._has_processed("IMG_7168", shorts) is True
    assert vpt._has_processed("IMG_9999", shorts) is False
    assert vpt._has_processed("OTHER", {"OTHER-preview.mp4"}) is False
    assert vpt._has_processed("OTHER", {"OTHER-clip.mp4"}) is True


def test_active_list_rule_matches_ui():
    # masters always listed
    assert vpt._on_active_pipeline_list(
        in_inbox=True, in_processing=False, has_transcript=True, has_processed=True
    )
    assert vpt._on_active_pipeline_list(
        in_inbox=False, in_processing=True, has_transcript=True, has_processed=True
    )
    # transcript-only + shorts + incomplete plan → still listed (work left)
    assert vpt._on_active_pipeline_list(
        in_inbox=False,
        in_processing=False,
        has_transcript=True,
        has_processed=True,
        has_moments=True,
        moments_complete=False,
    )
    # transcript-only + shorts + complete plan → hide
    assert not vpt._on_active_pipeline_list(
        in_inbox=False,
        in_processing=False,
        has_transcript=True,
        has_processed=True,
        has_moments=True,
        moments_complete=True,
    )
    # transcript-only + shorts + missing plan → keep (needs Analyze)
    assert vpt._on_active_pipeline_list(
        in_inbox=False,
        in_processing=False,
        has_transcript=True,
        has_processed=True,
        has_moments=False,
        moments_complete=False,
    )
    # transcript-only not processed → still listed
    assert vpt._on_active_pipeline_list(
        in_inbox=False, in_processing=False, has_transcript=True, has_processed=False
    )


def test_shorts_belong_to_stem_rejects_clean_dual():
    assert vpt._shorts_belong_to_stem("IMG_7171", "IMG_7171-m12-thought.mp4")
    assert not vpt._shorts_belong_to_stem("IMG_7171", "IMG_7171-clean-m11.mp4")
    assert not vpt._shorts_belong_to_stem("IMG_7171", "OTHER-clip.mp4")


def test_plan_progress_counts_left():
    data = {
        "moments": [
            {
                "id": 1,
                "clips": [
                    {"processed": True},
                    {"skipped": True},
                    {"processed": False},
                ],
            }
        ]
    }
    p = vpt._plan_progress(data)
    assert p["clip_count"] == 3
    assert p["clips_left"] == 1
    assert p["moments_complete"] is False
    data["moments"][0]["clips"][2]["processed"] = True
    p2 = vpt._plan_progress(data)
    assert p2["clips_left"] == 0
    assert p2["moments_complete"] is True


def test_nc_list_parses_nextcloud_list_format():
    sample = """Contents of AgentSkills/Content/video/inbox:
📄 IMG_7159.MOV (1,234,567 bytes)
📁 Archive
📄 note.txt (12 bytes)"""

    async def _fake_list(path: str = "/"):
        return sample

    # patch through module path used inside _nc_list
    import src.tools.nextcloud_tools as nc

    original = nc.nextcloud_list
    nc.nextcloud_list = SimpleNamespace(coroutine=_fake_list)
    try:
        import asyncio
        names = asyncio.get_event_loop().run_until_complete(
            vpt._nc_list("AgentSkills/Content/video/inbox")
        )
    finally:
        nc.nextcloud_list = original
    assert "IMG_7159.MOV" in names
    assert "Archive" in names
    assert "note.txt" in names
    assert not any("bytes" in n for n in names)


@pytest.mark.asyncio
async def test_queue_status_requires_presence():
    # unbound
    out = await vpt.video_queue_status.coroutine()
    data = json.loads(out)
    assert data.get("error") == "no_presence_scope"


@pytest.mark.asyncio
async def test_pipeline_status_builds_matrix(monkeypatch):
    async def fake_list(subdir: str):
        data = {
            "inbox": [],
            "processing": ["IMG_7159.MOV"],
            "raw": [],
            "transcripts": [
                "IMG_7159-transcript.json",
                "IMG_7168-transcript.json",
                "IMG_7168-moments.json",
            ],
            "shorts": [
                "IMG_7168-moments-processed.json",
                "IMG_7168-m0-story.mp4",
            ],
            "moments": [],
            "to-delete": [],
        }
        return data.get(subdir, []), None

    async def fake_read(rel):
        # large-plan shape; also proves full read path is used
        if rel.endswith("IMG_7168-moments.json"):
            return {
                "moments": [
                    {"id": 1, "clips": [{"processed": True}, {"processed": False}]}
                ]
            }
        return None

    monkeypatch.setattr(vpt, "_nc_list_or_err", fake_list)
    monkeypatch.setattr(vpt, "_nc_read_json", fake_read)
    out = await vpt.video_pipeline_status.coroutine(include_hidden="true")
    data = json.loads(out)
    by = {r["stem"]: r for r in data["stems"]}
    assert by["IMG_7159"]["on_active_pipeline_list"] is True
    assert by["IMG_7159"]["folder"] == "processing"
    # 7168 has shorts + moments with 1 left → stays on active list
    assert by["IMG_7168"]["on_active_pipeline_list"] is True
    assert by["IMG_7168"]["has_moments_json"] is True
    assert by["IMG_7168"]["clips_left"] == 1
    assert by["IMG_7168"]["moments_complete"] is False


def test_tools_exported():
    names = {getattr(t, "name", None) for t in vpt.TOOLS}
    assert names == {
        "video_pipeline_status",
        "video_moments_map",
        "video_queue_status",
        "video_jobs_recent",
    }


def test_presence_default_module_lists_video_tools():
    from src.config import _PRESENCE_DEFAULT_MODULES

    assert "tools.video_pipeline_tools" in _PRESENCE_DEFAULT_MODULES
