"""Weekly founder backup is restic-on-host; skip the in-app runner quietly."""

from src.utils.scheduler import weekly_backup_disabled


def test_weekly_backup_disabled_with_flag(monkeypatch):
    monkeypatch.setenv("DISABLE_WEEKLY_BACKUP", "1")
    monkeypatch.setenv("BACKUP_REPO_DIR", "/tmp/does-not-exist-bookled13")
    assert weekly_backup_disabled() is True


def test_weekly_backup_disabled_without_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("DISABLE_WEEKLY_BACKUP", raising=False)
    monkeypatch.setenv("BACKUP_REPO_DIR", str(tmp_path / "no-git"))
    assert weekly_backup_disabled() is True


def test_weekly_backup_enabled_when_repo_present(monkeypatch, tmp_path):
    repo = tmp_path / "agent"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("DISABLE_WEEKLY_BACKUP", raising=False)
    monkeypatch.setenv("BACKUP_REPO_DIR", str(repo))
    assert weekly_backup_disabled() is False


def test_weekly_backup_skip_log_line():
    from pathlib import Path

    text = Path("src/utils/scheduler.py").read_text()
    assert "weekly-backup skipped: restic on host" in text
    assert "if protocol == \"weekly-backup\" and skip_weekly:" in text
    html = Path("src/dashboard/static/books.html").read_text()
    assert "overdue" not in html.lower()
