"""F3: test_observer flag on accounts.preferences — stats skip, anonymous plays stay."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCT = (ROOT / "src/dashboard/routes/account.py").read_text(encoding="utf-8")


def test_left_join_keeps_anonymous():
    assert "a.id IS NULL" in ACCT
    assert "LEFT JOIN accounts a ON te.presence_id = a.id" in ACCT
    assert "LEFT JOIN accounts a ON ts.presence_id = a.id" in ACCT
    assert "_SQL_NOT_TEST_OBSERVER" in ACCT


def test_flag_is_preferences_jsonb_not_column():
    assert "preferences->>'test_observer'" in ACCT
    assert "is_test" not in ACCT
    assert "chief_scout_test" not in ACCT
    assert "lucidtuner@gmail.com" not in ACCT


def test_stats_and_activity_use_the_filter():
    assert "/api/admin/stats" in ACCT
    assert "/api/admin/activity" in ACCT
    assert "_SQL_ACCOUNT_NOT_TEST_OBSERVER" in ACCT
    assert "_SQL_ACCOUNT_A_NOT_TEST_OBSERVER" in ACCT
    assert "today_listen_minutes" in ACCT
