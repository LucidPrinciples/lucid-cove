"""#VMETA-REGEN1 — title≠opening safety + draft meta regen surface."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ensure_title_differs_from_opening():
    from src.dashboard.routes.video_meta import ensure_title_differs_from_opening

    same = ensure_title_differs_from_opening({
        "title": "Attention within attention is how you co-create reality.",
        "description": (
            "Attention within attention is how you co-create reality.\n\n"
            "Your experience is not accidental.\n\n→ topic"
        ),
    })
    assert same["description"].startswith("Your experience")
    assert "Attention within attention" not in same["description"].split("\n\n")[0]

    ok = ensure_title_differs_from_opening({
        "title": "A short title",
        "description": "A different opening.\n\nBody",
    })
    assert ok["description"].startswith("A different")

    # cannot empty description
    only = ensure_title_differs_from_opening({"title": "Only", "description": "Only"})
    assert only["description"] == "Only"


def test_prompts_require_title_vs_opening():
    from src.dashboard.routes.video_meta import (
        build_platform_system_prompt,
        build_full_video_system_prompt,
        build_polish_system_prompt,
        empty_video_meta,
    )
    m = empty_video_meta()
    yt = build_platform_system_prompt("youtube", m, "thought", "30s")
    assert "TITLE VS OPENING LINE" in yt
    full = build_full_video_system_prompt(m)
    assert "TITLE VS OPENING LINE" in full
    pol = build_polish_system_prompt(m)
    assert "TITLE VS OPENING" in pol or "opening line" in pol.lower()


def test_regen_endpoint_surface():
    ab = (ROOT / "src/dashboard/routes/action_board.py").read_text()
    assert "/api/action-board/regen-draft-meta" in ab
    assert "status = 'draft'" in ab
    assert "polish_metadata_batch" in ab
    js = (ROOT / "src/dashboard/static/js/action-board.js").read_text()
    assert "regenDraftMeta" in js
    assert "regen-draft-meta" in js
