"""
Watcher — background monitor so nothing fails silently again.

Steward-unit spec Pillar 3 (built first, 2026-07-09 decision). Everything that
hurt in the post-flip week was a silent failure: the PR tool failing for days
behind green cards, tunings deadlocked with a contradictory counter, queue rows
stuck forever. The watcher is a cheap scheduled sweep of DB FACTS — it never
calls a model and never mutates anything except its own alerts table.

Checks (each isolated — one broken check never kills the run):
  approved-failed   approval_requests approved but the stored result reads as an
                    error (the #D13 ghost: cards look green, result says failed)
  approval-stale    approvals pending longer than STALE_APPROVAL_HOURS
  queue-stuck       youtube_queue / social_queue rows failed, or queued with an
                    upload_date long past
  tuning-missing    after the morning window, team agents still missing today's
                    echo (the 07-08 team_missing deadlock, surfaced not logged)
  push-no-pr        an approved git_push with no create_github_pr requested
                    afterwards (the #D9 gap: a pushed branch is NOT done)

Delivery: alerts upsert into watcher_alerts (migration 030) keyed on a stable
alert_key, so a persisting condition is ONE card, not one per run. Open alerts
render on the operator's Attention home (home.js) with a Dismiss. When a
condition clears, its alert auto-resolves on the next run. A steward-channel
post rides the #D14 continuation mechanism once that ships — the operator card
is the v1 surface, per spec: never just a log line.
"""

import json
import re

from src.memory.database import get_db
from src.utils.time_utils import now_app, today_app, ts_log

# ── Tunables ────────────────────────────────────────────────────────────────
STALE_APPROVAL_HOURS = 12     # pending approval → reminder
LOOKBACK_HOURS = 72           # how far back approved-failed / push-no-pr scan
QUEUE_STUCK_HOURS = 2         # queued with upload_date this far past → stuck
PUSH_NO_PR_HOURS = 4          # approved push with no PR request after this → alert
TUNING_ALERT_HOUR = 8         # local hour after which missing tunings alert
                              # (self-tune 06:30-06:55 + sweep cycles get to settle)

# The check categories this module owns — auto-resolve only touches these.
CATEGORIES = (
    "approved-failed", "approval-stale", "queue-stuck",
    "tuning-missing", "push-no-pr",
)

_ERROR_PATTERNS = (
    re.compile(r"\[exit:\s*\d+\]"),
    re.compile(r"(?im)^\s*error\b"),
    re.compile(r"(?i)\berror:"),
    re.compile(r"(?i)\btraceback\b"),
    re.compile(r"(?im)^\s*fatal\b"),
    re.compile(r"(?i)\bnot found\b"),
    re.compile(r"(?i)\bHTTP (4\d\d|5\d\d)\b"),
    re.compile(r"(?i)\bfailed\b"),
)


def looks_like_error(result: str) -> bool:
    """Does a stored tool result read as a failure? Pure — unit-testable.

    Conservative on purpose: an empty/whitespace result is NOT an error (many
    tools return nothing on success); we only flag text that positively smells
    like a failure. This is the #D13 classifier — the same shapes that sat in
    approval_requests.result for days while the cards looked green."""
    if not result or not result.strip():
        return False
    return any(p.search(result) for p in _ERROR_PATTERNS)


def _args_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _clip(text: str, n: int = 220) -> str:
    text = (text or "").strip().replace("\n", " · ")
    return text[: n - 1] + "…" if len(text) > n else text


# ── Checks — each returns a list of alert dicts ─────────────────────────────

async def _check_approved_failed(conn) -> list[dict]:
    r = await conn.execute(
        "SELECT request_id, tool_name, result, resolved_at FROM approval_requests "
        "WHERE status = 'approved' AND result IS NOT NULL "
        "AND created_at > NOW() - (%s || ' hours')::interval",
        (str(LOOKBACK_HOURS),))
    alerts = []
    for row in await r.fetchall():
        if looks_like_error(row["result"]):
            alerts.append({
                "alert_key": f"approved-failed-{row['request_id']}",
                "category": "approved-failed",
                "title": f"Approved {row['tool_name']} FAILED after approval",
                "detail": _clip(row["result"]),
                "urgency": "high",
            })
    return alerts


async def _check_approval_stale(conn) -> list[dict]:
    r = await conn.execute(
        "SELECT request_id, tool_name, description, created_at FROM approval_requests "
        "WHERE status = 'pending' "
        "AND created_at < NOW() - (%s || ' hours')::interval",
        (str(STALE_APPROVAL_HOURS),))
    return [{
        "alert_key": f"approval-stale-{row['request_id']}",
        "category": "approval-stale",
        "title": f"Approval waiting >{STALE_APPROVAL_HOURS}h: {row['tool_name']}",
        "detail": _clip(row["description"] or ""),
        "urgency": "normal",
    } for row in await r.fetchall()]


async def _check_queue_stuck(conn) -> list[dict]:
    alerts = []
    for table in ("youtube_queue", "social_queue"):
        r = await conn.execute(
            f"SELECT id, title, status, error_message FROM {table} "
            "WHERE status = 'failed' "
            "AND updated_at > NOW() - (%s || ' hours')::interval",
            (str(LOOKBACK_HOURS),))
        for row in await r.fetchall():
            alerts.append({
                "alert_key": f"queue-failed-{table}-{row['id']}",
                "category": "queue-stuck",
                "title": f"{table.replace('_', ' ')} #{row['id']} FAILED: {_clip(row['title'], 60)}",
                "detail": _clip(row["error_message"] or "no stored error"),
                "urgency": "high",
            })
        r = await conn.execute(
            f"SELECT id, title, upload_date FROM {table} "
            "WHERE status IN ('queued', 'uploading') AND upload_date IS NOT NULL "
            "AND upload_date < NOW() - (%s || ' hours')::interval",
            (str(QUEUE_STUCK_HOURS),))
        for row in await r.fetchall():
            alerts.append({
                "alert_key": f"queue-stuck-{table}-{row['id']}",
                "category": "queue-stuck",
                "title": f"{table.replace('_', ' ')} #{row['id']} stuck past its upload time",
                "detail": _clip(f"{row['title']} — upload_date {row['upload_date']}"),
                "urgency": "high",
            })
    return alerts


async def _check_tuning_missing(conn) -> list[dict]:
    """After the morning window, is any team agent still missing today's echo?
    Read-only: reuses the sweep's own expected-team + the ONE dedup definition
    (date-only key here — the sweep handles Drop-key precision; the watcher only
    asks 'did anyone get left behind today')."""
    if now_app().hour < TUNING_ALERT_HOUR:
        return []
    from src.tuning.sweep import _expected_team
    from src.tuning.dedup import tuned_today
    expected = _expected_team()
    if not expected:
        return []
    tuned = await tuned_today(today_app())
    missing = sorted(expected - tuned)
    if not missing:
        return []
    return [{
        "alert_key": f"tuning-missing-{today_app()}",
        "category": "tuning-missing",
        "title": f"{len(missing)} team agent(s) missing today's tuning after {TUNING_ALERT_HOUR}:00",
        "detail": _clip(", ".join(missing)),
        "urgency": "high",
    }]


def pushes_without_pr(pushes: list[dict], pr_requests: list[dict]) -> list[dict]:
    """Which approved pushes have no create_github_pr requested AFTER them?
    Pure — unit-testable. A PR request in ANY status counts (pending means the
    agent did ask; approved-failed is the approved-failed check's job)."""
    orphans = []
    for push in pushes:
        branch = (_args_dict(push.get("args")).get("branch") or "").strip()
        covered = any(
            pr["created_at"] >= push["created_at"] and (
                not branch
                or not (_args_dict(pr.get("args")).get("branch") or "").strip()
                or (_args_dict(pr.get("args")).get("branch") or "").strip() == branch
            )
            for pr in pr_requests
        )
        if not covered:
            orphans.append(push)
    return orphans


async def _check_push_no_pr(conn) -> list[dict]:
    r = await conn.execute(
        "SELECT request_id, args, created_at FROM approval_requests "
        "WHERE status = 'approved' AND tool_name = 'git_push' "
        "AND created_at > NOW() - (%s || ' hours')::interval "
        "AND created_at < NOW() - (%s || ' hours')::interval",
        (str(LOOKBACK_HOURS), str(PUSH_NO_PR_HOURS)))
    pushes = [dict(row) for row in await r.fetchall()]
    if not pushes:
        return []
    r = await conn.execute(
        "SELECT args, created_at FROM approval_requests "
        "WHERE tool_name = 'create_github_pr' "
        "AND created_at > NOW() - (%s || ' hours')::interval",
        (str(LOOKBACK_HOURS),))
    prs = [dict(row) for row in await r.fetchall()]
    alerts = []
    for push in pushes_without_pr(pushes, prs):
        branch = _args_dict(push.get("args")).get("branch") or "(current branch)"
        alerts.append({
            "alert_key": f"push-no-pr-{push['request_id']}",
            "category": "push-no-pr",
            "title": f"Branch pushed, no PR after {PUSH_NO_PR_HOURS}h: {_clip(branch, 60)}",
            "detail": "An approved git_push has no create_github_pr requested after it. "
                      "A pushed branch is NOT done — nudge the agent to open the PR.",
            "urgency": "normal",
        })
    return alerts


# ── The run ─────────────────────────────────────────────────────────────────

_CHECKS = (
    _check_approved_failed,
    _check_approval_stale,
    _check_queue_stuck,
    _check_tuning_missing,
    _check_push_no_pr,
)


async def run_watcher() -> dict:
    """Run every check, upsert findings, auto-resolve cleared conditions.

    Returns {"open": n, "new": n, "resolved": n, "check_errors": [...]}."""
    alerts: list[dict] = []
    checked: set[str] = set()
    check_errors: list[str] = []

    async with get_db() as conn:
        for check in _CHECKS:
            # Category owned by this check — derive from name for auto-resolve scoping.
            category = check.__name__.replace("_check_", "").replace("_", "-")
            try:
                alerts.extend(await check(conn))
                checked.add(category)
            except Exception as e:  # a broken check must not kill the watcher
                check_errors.append(f"{category}: {e}")
                print(f"{ts_log()} [watcher] check {category} errored: {e}")

        new_count = 0
        for a in alerts:
            r = await conn.execute(
                """INSERT INTO watcher_alerts (alert_key, category, title, detail, urgency)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (alert_key) DO UPDATE SET
                       last_seen = NOW(),
                       title = EXCLUDED.title,
                       detail = EXCLUDED.detail,
                       status = CASE WHEN watcher_alerts.status = 'dismissed'
                                     THEN 'dismissed' ELSE 'open' END,
                       resolved_at = CASE WHEN watcher_alerts.status = 'dismissed'
                                          THEN watcher_alerts.resolved_at ELSE NULL END
                   RETURNING (xmax = 0) AS inserted""",
                (a["alert_key"], a["category"], a["title"], a["detail"], a["urgency"]))
            row = await r.fetchone()
            if row and row["inserted"]:
                new_count += 1

        # Auto-resolve: open alerts in categories we successfully checked whose
        # condition no longer holds. Categories that errored are left alone.
        resolved = 0
        if checked:
            current_keys = [a["alert_key"] for a in alerts] or [""]
            r = await conn.execute(
                "UPDATE watcher_alerts SET status = 'resolved', resolved_at = NOW() "
                "WHERE status = 'open' AND category = ANY(%s) "
                "AND NOT (alert_key = ANY(%s)) RETURNING alert_key",
                (list(checked), current_keys))
            resolved = len(await r.fetchall())

        r = await conn.execute(
            "SELECT COUNT(*) AS n FROM watcher_alerts WHERE status = 'open'")
        open_count = (await r.fetchone())["n"]

    if alerts or resolved or check_errors:
        print(f"{ts_log()} [watcher] open={open_count} new={new_count} "
              f"resolved={resolved}"
              + (f" check_errors={check_errors}" if check_errors else ""))
    return {"open": open_count, "new": new_count, "resolved": resolved,
            "check_errors": check_errors}
