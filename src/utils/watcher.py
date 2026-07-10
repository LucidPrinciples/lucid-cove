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
  proxy-drift       the Caddy proxy config changed since the last sweep (#D33 —
                    unexpected reconfigure of the box's TLS/routing)
  cert-expiry       a Caddy-served TLS cert is within CERT_EXPIRY_DAYS of expiry
                    (#D33 — both filesystem checks no-op if the path isn't mounted)

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
    "tuning-missing", "push-no-pr", "steward-queue",
    "proxy-drift", "cert-expiry",
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


async def _check_steward_queue(conn) -> list[dict]:
    """Assigned queue tickets nobody has touched in days — the steward (or the
    operator) gets a nudge instead of the ticket quietly rotting (spec Pillar 3
    ties into Pillar 1). in_review staleness is the PR-hygiene checks' domain."""
    r = await conn.execute(
        "SELECT id, title, assignee, updated_at FROM steward_queue "
        "WHERE status = 'assigned' "
        "AND updated_at < NOW() - INTERVAL '3 days'")
    return [{
        "alert_key": f"steward-queue-stale-{row['id']}",
        "category": "steward-queue",
        "title": f"Queue ticket [{row['id']}] assigned but untouched 3+ days",
        "detail": _clip(f"{row['title']} — assignee {row['assignee'] or 'unset'}"),
        "urgency": "normal",
    } for row in await r.fetchall()]


# ── Infra-drift checks (#D33) ───────────────────────────────────────────────
# These read the FILESYSTEM (Caddy config + cert store), not the DB. The watcher
# runs inside the Cove app container, where those paths may not be mounted — every
# helper here MUST no-op cleanly (return nothing) rather than raise/spam when a path
# is absent or unreadable. The pure decision logic is factored out and unit-tested.

CERT_EXPIRY_DAYS = 14         # served-name TLS cert with ≤ this many days left → alert


def _caddy_config_paths() -> list:
    """Candidate Caddy config artifacts to watch for drift. Env-overridable
    (LP_WATCHER_CADDY_PATHS, colon-separated). Defaults cover the paths the app
    container actually has in the shared-Caddy (conf.d) and bundled-Caddy layouts;
    non-existent ones are simply ignored by max_mtime()."""
    import os
    override = (os.getenv("LP_WATCHER_CADDY_PATHS", "") or "").strip()
    if override:
        return [p for p in override.split(":") if p]
    confd = os.getenv("COVE_SHARED_CONFD", "/app/shared-caddy-confd")
    docker_dir = os.getenv("COVE_DOCKER_DIR", "/app/cove-docker")
    return [confd, os.path.join(docker_dir, "Caddyfile"),
            "/data/caddy/autosave.json"]


def max_mtime(paths: list) -> "float | None":
    """Newest mtime across the given files/dirs (dirs scanned one level deep so a
    new/changed conf.d snippet is seen). Pure over the filesystem; returns None when
    nothing exists — the drift check reads that as 'not reachable here, no-op'."""
    import os
    newest = None
    for p in paths or []:
        try:
            if not os.path.exists(p):
                continue
            newest = max(newest or 0.0, os.path.getmtime(p))
            if os.path.isdir(p):
                for name in os.listdir(p):
                    fp = os.path.join(p, name)
                    try:
                        newest = max(newest, os.path.getmtime(fp))
                    except OSError:
                        continue
        except OSError:
            continue
    return newest


async def _watcher_state_get(conn, key: str):
    try:
        r = await conn.execute("SELECT value FROM watcher_state WHERE key = %s", (key,))
        row = await r.fetchone()
        return row["value"] if row else None
    except Exception:
        return None   # table missing / unreadable → treat as no prior state


async def _watcher_state_set(conn, key: str, value: str) -> None:
    try:
        await conn.execute(
            "INSERT INTO watcher_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (key, value))
    except Exception:
        pass   # never let a state-write failure spam / kill the check


async def _check_proxy_drift(conn) -> list[dict]:
    """Caddy proxy config changed since the last sweep → a heads-up card. The first
    observation just establishes a baseline (no card); a later change fires one card
    (single alert_key) that auto-resolves once the config is stable again. If no Caddy
    config path is reachable in this container, no-op."""
    cur = max_mtime(_caddy_config_paths())
    if cur is None:
        return []
    last = await _watcher_state_get(conn, "proxy_config_mtime")
    await _watcher_state_set(conn, "proxy_config_mtime", repr(cur))
    if last is None:
        return []   # baseline only
    try:
        last_f = float(last)
    except (TypeError, ValueError):
        return []
    if cur <= last_f + 1.0:   # 1s guard for filesystem mtime granularity
        return []
    from datetime import datetime, timezone
    when = datetime.fromtimestamp(cur, tz=timezone.utc).isoformat()
    return [{
        "alert_key": "proxy-drift",
        "category": "proxy-drift",
        "title": "Proxy (Caddy) config changed",
        "detail": _clip(f"Caddy config artifacts changed at {when}. Expected after a "
                        f"Set-Address or deploy; otherwise check who reloaded the proxy."),
        "urgency": "normal",
    }]


def _caddy_cert_dir() -> str:
    import os
    return (os.getenv("LP_WATCHER_CADDY_CERT_DIR", "") or "").strip() or \
        "/data/caddy/certificates"


def _read_caddy_certs(cert_dir: str = "") -> list:
    """[(served_name, not_after_datetime)] from Caddy's cert store. Best-effort:
    returns [] when the dir isn't mounted here or cryptography is unavailable — the
    watcher runs in-container where /data/caddy may not exist, and must never spam."""
    import os
    cert_dir = cert_dir or _caddy_cert_dir()
    if not cert_dir or not os.path.isdir(cert_dir):
        return []
    try:
        from cryptography import x509
    except Exception:
        return []
    out = []
    for root, _dirs, files in os.walk(cert_dir):
        for fn in files:
            if not fn.endswith(".crt"):
                continue
            path = os.path.join(root, fn)
            try:
                cert = x509.load_pem_x509_certificate(open(path, "rb").read())
                try:
                    not_after = cert.not_valid_after_utc
                except AttributeError:   # older cryptography → naive UTC
                    from datetime import timezone
                    not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
                out.append((fn[:-4], not_after))
            except Exception:
                continue   # unparseable cert file — skip, don't spam
    return out


def certs_expiring_within(certs: list, now, days: int) -> list:
    """Pure: [(name, days_left)] for certs whose not_after is within `days` (already
    expired = negative days_left). Sorted soonest-first. Unit-testable without any I/O."""
    out = []
    for name, not_after in certs or []:
        if not_after is None:
            continue
        days_left = (not_after - now).total_seconds() / 86400.0
        if days_left <= days:
            out.append((name, int(days_left) if days_left >= 0 else -((-int(days_left)) or 1)))
    return sorted(out, key=lambda t: t[1])


async def _check_cert_expiry(conn) -> list[dict]:
    """Served-name TLS cert expiring within CERT_EXPIRY_DAYS. No-op when the cert
    store isn't reachable in this container (the common case) — never error-spam."""
    certs = _read_caddy_certs()
    if not certs:
        return []
    from datetime import datetime, timezone
    alerts = []
    for name, days_left in certs_expiring_within(certs, datetime.now(timezone.utc),
                                                 CERT_EXPIRY_DAYS):
        expired = days_left < 0
        alerts.append({
            "alert_key": f"cert-expiry-{name}",
            "category": "cert-expiry",
            "title": (f"TLS cert for {name} EXPIRED" if expired
                      else f"TLS cert for {name} expires in {days_left}d"),
            "detail": _clip(f"Caddy-served name {name}: renew before HTTPS breaks."
                            + (" Cert is already past its expiry." if expired else "")),
            "urgency": "high" if (expired or days_left <= 3) else "normal",
        })
    return alerts


# ── The run ─────────────────────────────────────────────────────────────────

_CHECKS = (
    _check_approved_failed,
    _check_approval_stale,
    _check_queue_stuck,
    _check_tuning_missing,
    _check_push_no_pr,
    _check_steward_queue,
    _check_proxy_drift,
    _check_cert_expiry,
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
