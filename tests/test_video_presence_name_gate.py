"""Legacy video UI sent presence_name=operator; must not 403 the signed-in presence."""
from src.dashboard.routes.video_pipeline import _reject_cross_presence_name


def test_legacy_operator_default_is_not_cross_presence():
    assert _reject_cross_presence_name("operator", "Jason") is None
    assert _reject_cross_presence_name("Operator", "Atlas") is None
    assert _reject_cross_presence_name("", "Jason") is None
    assert _reject_cross_presence_name("default", "Jason") is None


def test_matching_name_ok():
    assert _reject_cross_presence_name("Jason", "Jason") is None
    assert _reject_cross_presence_name("jason", "Jason") is None


def test_real_cross_presence_still_rejected():
    err = _reject_cross_presence_name("Jules", "Jason")
    assert err and "must match" in err


def test_no_session_allows_body():
    assert _reject_cross_presence_name("Jason", "") is None
