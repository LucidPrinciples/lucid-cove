"""Video description profile — empty defaults, presence > Cove merge, no Tuner hardcodes."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_lucidtuner_hardcodes_in_generators():
    social = (ROOT / "src/dashboard/routes/social_templates.py").read_text()
    proc = (ROOT / "src/dashboard/routes/video_processing.py").read_text()
    meta = (ROOT / "src/dashboard/routes/video_meta.py").read_text()
    for blob, name in ((social, "social"), (proc, "processing"), (meta, "meta")):
        low = blob.lower()
        assert "lucidtuner.com" not in low, name
        assert "lucidprinciples.com/vision" not in low, name
        assert "#lucidtuner" not in low, name


def test_empty_default_and_merge():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        merge_video_meta,
        build_platform_system_prompt,
        build_full_video_system_prompt,
    )

    e = empty_video_meta()
    assert all(v == "" for v in e.values())
    assert "brand_name" in e and "short_cta_url" in e and "full_cta_url" in e

    p = empty_video_meta()
    p["brand_name"] = "Atlas"
    p["short_cta_url"] = "https://presence.example"
    c = empty_video_meta()
    c["brand_name"] = "CoveCo"
    c["full_cta_url"] = "https://cove.example"
    c["hashtag_seeds"] = "#cove"
    m = merge_video_meta(p, c)
    assert m["brand_name"] == "Atlas"
    assert m["short_cta_url"] == "https://presence.example"
    assert m["full_cta_url"] == "https://cove.example"
    assert m["hashtag_seeds"] == "#cove"


def test_empty_prompt_forbids_invented_links():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        build_platform_system_prompt,
        build_full_video_system_prompt,
    )

    empty = empty_video_meta()
    yt = build_platform_system_prompt("youtube", empty, "thought", "45s")
    assert "lucidtuner" not in yt.lower()
    assert "Do not add any URL" in yt or "no forced" in yt.lower() or "No invented" in yt

    full = build_full_video_system_prompt(empty)
    assert "lucidprinciples" not in full.lower()
    assert "Do not add any URL" in full or "No invented" in full


def test_filled_prompt_includes_cta():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        build_platform_system_prompt,
        build_full_video_system_prompt,
        _compose_closing_block,
    )

    m = empty_video_meta()
    m["brand_name"] = "Ridge Hardware"
    m["short_cta_url"] = "https://ridge.example"
    m["attribute_handle"] = "@ridge on X"
    m["full_cta_line"] = "Visit us: https://ridge.example/hours"
    yt = build_platform_system_prompt("youtube", m, "quote", "30s")
    assert "Ridge Hardware" in yt
    # URL is composed into a plain "More at …" closing, not forced "Creator is"
    assert "More at ridge.example" in yt
    assert "@ridge on X" in yt
    # Instruction forbids inventing "Creator is …" credit prose
    assert "Do not rewrite into 'Creator is" in yt
    full = build_full_video_system_prompt(m)
    assert "Visit us: https://ridge.example/hours" in full

    # Explicit multi-line block wins over URL composition
    block = _compose_closing_block(
        "More at lucidprinciples.com\n@jasonbroadcast on X",
        "https://ignored.example",
        "@ignored",
    )
    assert block == "More at lucidprinciples.com\n@jasonbroadcast on X"


def test_moment_context_and_all_platforms_in_prompt():
    from src.dashboard.routes.video_meta import empty_video_meta, build_platform_system_prompt
    from src.dashboard.routes.social_templates import PLATFORM_NAMES

    m = empty_video_meta()
    ctx = "theme_tag: Deep Work\nsibling sizes in this moment:\n- quote: hook (12s)\n- story: arc (75s)"
    for platform in PLATFORM_NAMES:
        out = build_platform_system_prompt(
            platform, m, "thought", "45s", moment_context=ctx,
        )
        assert out, platform
        assert "Deep Work" in out or "sibling sizes" in out
        # Easy response prompt guidance, not forced bait language as a requirement dump
        assert "response prompt" in out.lower() or platform == "x"


def test_attribute_handle_field_present():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        VIDEO_META_FIELDS,
        VIDEO_META_FIELD_META,
        merge_video_meta,
    )
    assert "attribute_handle" in VIDEO_META_FIELDS
    assert "attribute_handle" in VIDEO_META_FIELD_META
    e = empty_video_meta()
    assert e["attribute_handle"] == ""
    p = empty_video_meta()
    p["attribute_handle"] = "@me on X"
    c = empty_video_meta()
    c["attribute_handle"] = "@cove"
    assert merge_video_meta(p, c)["attribute_handle"] == "@me on X"
    assert merge_video_meta(empty_video_meta(), c)["attribute_handle"] == "@cove"


def test_api_and_ui_surface_exist():
    posting = (ROOT / "src/dashboard/routes/posting.py").read_text()
    ui = (ROOT / "src/dashboard/static/action-board/full-video-pipeline.html").read_text()
    assert "/api/posting/video-meta" in posting
    assert "video-meta/cove" in posting
    assert "Description profile" in ui
    assert "savePresenceVideoMeta" in ui
    assert "saveCoveVideoMeta" in ui


def test_braces_in_brand_do_not_crash_prompt():
    from src.dashboard.routes.video_meta import empty_video_meta, build_platform_system_prompt

    m = empty_video_meta()
    m["brand_name"] = "Foo {bar} Baz"
    out = build_platform_system_prompt("x", m, "story", "90s")
    assert "Foo {bar} Baz" in out


def test_theme_mix_field_and_meta():
    from src.dashboard.routes.video_meta import (
        empty_video_meta,
        VIDEO_META_FIELDS,
        VIDEO_META_FIELD_META,
        merge_video_meta,
    )
    assert "theme_mix" in VIDEO_META_FIELDS
    assert "theme_mix" in VIDEO_META_FIELD_META
    assert VIDEO_META_FIELD_META["theme_mix"]["label"]
    e = empty_video_meta()
    assert e["theme_mix"] == ""
    p = empty_video_meta()
    p["theme_mix"] = "story; howto"
    c = empty_video_meta()
    c["theme_mix"] = "cove default"
    m = merge_video_meta(p, c)
    assert m["theme_mix"] == "story; howto"
    m2 = merge_video_meta(empty_video_meta(), c)
    assert m2["theme_mix"] == "cove default"


def test_identify_moments_prompt_includes_diversity():
    """Static check: analyzer prompt builder mentions theme diversity."""
    src = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text()
    assert "THEME DIVERSITY" in src or "theme_mix" in src
    assert "diversity_guidance" in src
    assert "theme_tag" in src
    assert "video_meta=_vm" in src or "video_meta=_vm" in src.replace(" ", "")


def test_short_tier_offers_three_sizes_when_supported():
    """<5 min talks still get nested quote/thought/story guidance (story optional)."""
    src = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text()
    # Short tier block should mention story and nested sizes
    assert "duration_mins < 5" in src
    assert "nested clip lengths" in src or "nested sizes" in src
    assert 'type": "story"' in src or '"type": "story"' in src


def test_ig_fb_default_selected_in_crop_ui():
    ui = (ROOT / "src/dashboard/static/action-board/video-crop-position.html").read_text()
    assert "{ id: 'instagram'" in ui
    assert "{ id: 'facebook'" in ui
    # Both default selected true so process passes draft the full set
    assert "id: 'instagram', label: 'Instagram'" in ui
    assert "selected: true, format: '9:16' },\n    { id: 'facebook'" in ui or (
        "instagram" in ui and "selected: true" in ui
    )
    # crude but stable: facebook line has selected: true
    for line in ui.splitlines():
        if "id: 'facebook'" in line:
            assert "selected: true" in line
        if "id: 'instagram'" in line:
            assert "selected: true" in line


def test_process_moments_passes_moment_context():
    src = (ROOT / "src/dashboard/routes/video_processing.py").read_text()
    assert "moment_context" in src
    assert "_moment_context_for" in src
    social = (ROOT / "src/dashboard/routes/social_templates.py").read_text()
    assert "moment_context" in social
