"""batch8 #5 / C3 — platform selection drives the rendered formats."""
from src.dashboard.routes.video_processing import _formats_for_platforms


def test_tiktok_only_is_vertical():
    assert _formats_for_platforms(["tiktok"]) == ["vertical"]


def test_x_only_is_horizontal():
    # x-only selection renders horizontal only (was: always both).
    assert _formats_for_platforms(["x"]) == ["horizontal"]


def test_mixed_dedupes_and_is_order_stable():
    # youtube+tiktok both vertical, x horizontal → one of each, in first-seen order.
    assert _formats_for_platforms(["youtube", "tiktok", "x"]) == ["vertical", "horizontal"]
    assert _formats_for_platforms(["x", "youtube"]) == ["horizontal", "vertical"]


def test_empty_selection_defaults_vertical():
    assert _formats_for_platforms([]) == ["vertical"]
    assert _formats_for_platforms(None) == ["vertical"]


def test_unknown_platform_ignored_then_default():
    assert _formats_for_platforms(["myspace"]) == ["vertical"]
    assert _formats_for_platforms(["myspace", "x"]) == ["horizontal"]


def test_case_insensitive():
    assert _formats_for_platforms(["TikTok", "X"]) == ["vertical", "horizontal"]


def test_long_clip_x_selection_stays_horizontal_only():
    # A >180s moment used to be forced horizontal-only regardless; now the platform
    # governs. x-only → horizontal (no duration coupling). Verified via the format
    # derivation that feeds the render; the voice-side 180s rule was removed.
    assert _formats_for_platforms(["x"]) == ["horizontal"]
    assert _formats_for_platforms(["tiktok"]) == ["vertical"]  # >180s tiktok still vertical
