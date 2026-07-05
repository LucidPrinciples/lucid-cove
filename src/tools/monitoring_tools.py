"""
Monitoring Tools — Soren's accountability and system health toolkit.

Soren (The Lens) uses these tools to:
- Track whether operator requests get completed
- Monitor JouleWork metrics for system health (diagnostic, not evaluative)
- Check Vera pass/fail rates as team quality signal
- Verify tuning echo health across agents
- Escalate dropped items to Stuart, then to operator if unresolved

All read operations are AUTO tier. Escalations are NOTIFY tier.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import tool

from src.tools.approval import auto, notify
from src.memory.database import get_db

logger = logging.getLogger("cove.monitoring")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Task Accountability
# =============================================================================

@auto
@tool
async def check_task_completion(hours_threshold: int = 4, status_filter: str = "") -> str:
    """Check for tasks that are past their expected completion window.

    Returns tasks that are assigned or in_progress and either:
    - past their expected_by timestamp, OR
    - older than hours_threshold without expected_by set

    Args:
        hours_threshold: Hours before a task without explicit deadline is flagged (default 4)
        status_filter: Optional status to check (default: assigned, in_progress, audit, audit_failed)
    """
    try:
        statuses = ["assigned", "in_progress", "audit", "audit_failed"]
        if status_filter:
            statuses = [s.strip() for s in status_filter.split(",")]

        placeholders = ", ".join(["%s"] * len(statuses))

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT t.id, t.title, t.status, t.assignee, t.source,
                           t.expected_by, t.escalation_count, t.created_at,
                           t.workflow_state, t.notes,
                           p.name as project_name,
                           EXTRACT(EPOCH FROM (NOW() - t.created_at)) / 3600 as hours_old
                    FROM tasks t
                    LEFT JOIN projects p ON t.project_id = p.id
                    WHERE t.status IN ({placeholders})
                      AND (
                        (t.expected_by IS NOT NULL AND t.expected_by < NOW())
                        OR
                        (t.expected_by IS NULL AND t.created_at < NOW() - INTERVAL '%s hours')
                      )
                    ORDER BY
                      CASE WHEN t.source = 'operator' THEN 0 ELSE 1 END,
                      t.created_at ASC""",
                (*statuses, hours_threshold),
            )
            rows = await result.fetchall()

        if not rows:
            return f"No overdue tasks found (checked statuses: {', '.join(statuses)}, threshold: {hours_threshold}h)"

        items = []
        for r in rows:
            age = f"{r['hours_old']:.1f}h old"
            deadline = f", expected by {r['expected_by']}" if r["expected_by"] else ""
            escalation = f", escalated {r['escalation_count']}x" if r["escalation_count"] else ""
            source_tag = " [OPERATOR REQUEST]" if r["source"] == "operator" else ""
            items.append(
                f"- Task #{r['id']}: {r['title']} (status={r['status']}, "
                f"assignee={r['assignee']}, {age}{deadline}{escalation}){source_tag}"
            )

        return f"Found {len(rows)} overdue/stale tasks:\n" + "\n".join(items)

    except Exception as e:
        logger.error(f"check_task_completion failed: {e}")
        return f"Error checking task completion: {e}"


@auto
@tool
async def check_operator_requests(hours_lookback: int = 24) -> str:
    """Check recent Day channel activity for untracked operator requests.

    Cross-references recent chat threads on the Day channel (where operator
    requests arrive) against tasks with source='operator' to find gaps.

    Args:
        hours_lookback: How many hours back to check (default 24)
    """
    try:
        async with get_db() as conn:
            # Get recent Day channel threads with activity
            thread_result = await conn.execute(
                """SELECT ct.id, ct.thread_id, ct.title, ct.summary,
                          ct.message_count, ct.last_message_at, ct.agent_id
                   FROM chat_threads ct
                   WHERE ct.channel = 'day'
                     AND ct.last_message_at > NOW() - INTERVAL '%s hours'
                   ORDER BY ct.last_message_at DESC
                   LIMIT 30""",
                (hours_lookback,),
            )
            threads = await thread_result.fetchall()

            # Get recent operator-sourced tasks
            task_result = await conn.execute(
                """SELECT id, title, status, created_at
                   FROM tasks
                   WHERE source = 'operator'
                     AND created_at > NOW() - INTERVAL '%s hours'
                   ORDER BY created_at DESC""",
                (hours_lookback,),
            )
            tasks = await task_result.fetchall()

        thread_count = len(threads)
        task_count = len(tasks)

        if thread_count == 0:
            return f"No Day channel activity in the last {hours_lookback}h."

        # Build summary
        lines = [
            f"Day channel threads in last {hours_lookback}h: {thread_count}",
            f"Operator-sourced tasks in same period: {task_count}",
            "",
            "Recent Day channel threads (newest first):",
        ]
        for t in threads[:20]:
            title = (t["title"] or "(untitled)")[:80]
            ts = t["last_message_at"].strftime("%H:%M") if t["last_message_at"] else "?"
            msgs = t["message_count"] or 0
            lines.append(f"  [{ts}] {title} ({msgs} msgs, agent={t['agent_id']})")

        if tasks:
            lines.append("")
            lines.append("Tracked operator tasks:")
            for t in tasks[:15]:
                lines.append(f"  - #{t['id']}: {t['title']} ({t['status']})")

        if thread_count > 0 and task_count == 0:
            lines.append("")
            lines.append(
                "WARNING: Day channel has activity but no operator-sourced tasks "
                "were created. Review whether any threads contained action requests."
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"check_operator_requests failed: {e}")
        return f"Error checking operator requests: {e}"


# =============================================================================
# JouleWork Metrics (Diagnostic — System Health)
# =============================================================================

@auto
@tool
async def get_jw_summary(days: int = 7) -> str:
    """Aggregate JouleWork metrics for a date range.

    Returns per-agent token totals, average duration, failure rates,
    and model distribution. Diagnostic only — system health, not ranking.

    Args:
        days: Number of days to look back (default 7)
    """
    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT
                     agent_id,
                     COUNT(*) as total_calls,
                     SUM(tokens_total) as total_tokens,
                     AVG(tokens_total) as avg_tokens,
                     AVG(duration_ms) as avg_duration_ms,
                     SUM(CASE WHEN NOT succeeded THEN 1 ELSE 0 END) as failures,
                     COUNT(DISTINCT model_used) as models_used,
                     array_agg(DISTINCT model_used) as model_list
                   FROM jw_metrics
                   WHERE recorded_at > NOW() - INTERVAL '%s days'
                   GROUP BY agent_id
                   ORDER BY total_tokens DESC NULLS LAST""",
                (days,),
            )
            rows = await result.fetchall()

        if not rows:
            return f"No JW metrics found in the last {days} days."

        # System totals
        total_calls = sum(r["total_calls"] for r in rows)
        total_tokens = sum(r["total_tokens"] or 0 for r in rows)
        total_failures = sum(r["failures"] for r in rows)

        lines = [
            f"JouleWork Summary — last {days} days",
            f"System total: {total_calls} calls, {total_tokens:,} tokens, "
            f"{total_failures} failures ({total_failures/max(total_calls,1)*100:.1f}%)",
            "",
            "Per-agent breakdown:",
        ]

        for r in rows:
            fail_pct = (r["failures"] / max(r["total_calls"], 1)) * 100
            models = ", ".join(r["model_list"]) if r["model_list"] else "none"
            lines.append(
                f"  {r['agent_id']}: {r['total_calls']} calls, "
                f"{r['total_tokens'] or 0:,} tokens (avg {r['avg_tokens'] or 0:.0f}), "
                f"avg {r['avg_duration_ms'] or 0:.0f}ms, "
                f"{r['failures']} failures ({fail_pct:.1f}%), "
                f"models: {models}"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_jw_summary failed: {e}")
        return f"Error getting JW summary: {e}"


@auto
@tool
async def get_jw_anomalies(days: int = 7, spike_threshold: float = 2.0) -> str:
    """Compare recent JW metrics against rolling averages to find anomalies.

    Flags agents with token consumption or failure rates significantly
    above their own baseline.

    Args:
        days: Recent window to check (default 7)
        spike_threshold: Multiplier above average to flag (default 2.0x)
    """
    try:
        async with get_db() as conn:
            # Get recent period stats
            recent = await conn.execute(
                """SELECT agent_id,
                     AVG(tokens_total) as recent_avg_tokens,
                     AVG(duration_ms) as recent_avg_duration,
                     SUM(CASE WHEN NOT succeeded THEN 1 ELSE 0 END)::float
                       / GREATEST(COUNT(*), 1) as recent_fail_rate,
                     COUNT(*) as recent_calls
                   FROM jw_metrics
                   WHERE recorded_at > NOW() - INTERVAL '%s days'
                   GROUP BY agent_id""",
                (days,),
            )
            recent_rows = {r["agent_id"]: r for r in await recent.fetchall()}

            # Get baseline (previous period of same length)
            baseline = await conn.execute(
                """SELECT agent_id,
                     AVG(tokens_total) as baseline_avg_tokens,
                     AVG(duration_ms) as baseline_avg_duration,
                     SUM(CASE WHEN NOT succeeded THEN 1 ELSE 0 END)::float
                       / GREATEST(COUNT(*), 1) as baseline_fail_rate,
                     COUNT(*) as baseline_calls
                   FROM jw_metrics
                   WHERE recorded_at BETWEEN
                     NOW() - INTERVAL '%s days' AND NOW() - INTERVAL '%s days'
                   GROUP BY agent_id""",
                (days * 2, days),
            )
            baseline_rows = {r["agent_id"]: r for r in await baseline.fetchall()}

        anomalies = []
        for agent_id, recent_data in recent_rows.items():
            base = baseline_rows.get(agent_id)
            if not base or base["baseline_calls"] < 3:
                continue  # Not enough baseline data

            issues = []
            if (base["baseline_avg_tokens"] or 0) > 0:
                token_ratio = (recent_data["recent_avg_tokens"] or 0) / base["baseline_avg_tokens"]
                if token_ratio >= spike_threshold:
                    issues.append(f"tokens {token_ratio:.1f}x baseline")

            if recent_data["recent_fail_rate"] > 0.3 and base["baseline_fail_rate"] < 0.1:
                issues.append(
                    f"failure rate {recent_data['recent_fail_rate']*100:.0f}% "
                    f"(baseline {base['baseline_fail_rate']*100:.0f}%)"
                )

            if (base["baseline_avg_duration"] or 0) > 0:
                duration_ratio = (recent_data["recent_avg_duration"] or 0) / base["baseline_avg_duration"]
                if duration_ratio >= spike_threshold:
                    issues.append(f"duration {duration_ratio:.1f}x baseline")

            if issues:
                anomalies.append(f"  {agent_id}: {', '.join(issues)}")

        if not anomalies:
            return f"No JW anomalies detected in the last {days} days (threshold: {spike_threshold}x)."

        return (
            f"JW Anomalies — last {days} days vs previous {days} days "
            f"(threshold: {spike_threshold}x):\n" + "\n".join(anomalies)
        )

    except Exception as e:
        logger.error(f"get_jw_anomalies failed: {e}")
        return f"Error checking JW anomalies: {e}"


# =============================================================================
# Quality Signal
# =============================================================================

@auto
@tool
async def get_vera_pass_rate(days: int = 7) -> str:
    """Query task history for audit verdict changes over a date range.

    Returns pass/fail/rework counts as a team-level quality signal.
    Not an individual scorecard — a system health indicator.

    Args:
        days: Number of days to look back (default 7)
    """
    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT
                     t.assignee,
                     COUNT(*) FILTER (WHERE th.new_value = 'pass') as passes,
                     COUNT(*) FILTER (WHERE th.new_value = 'fail') as fails,
                     COUNT(*) FILTER (WHERE th.new_value LIKE 'rework%%') as reworks,
                     COUNT(*) as total_verdicts
                   FROM task_history th
                   JOIN tasks t ON th.task_id = t.id
                   WHERE th.field_changed = 'audit_verdict'
                     AND th.changed_at > NOW() - INTERVAL '%s days'
                   GROUP BY t.assignee
                   ORDER BY total_verdicts DESC""",
                (days,),
            )
            rows = await result.fetchall()

        if not rows:
            return f"No audit verdicts recorded in the last {days} days."

        total_pass = sum(r["passes"] for r in rows)
        total_fail = sum(r["fails"] for r in rows)
        total_all = sum(r["total_verdicts"] for r in rows)
        team_rate = (total_pass / max(total_all, 1)) * 100

        lines = [
            f"Vera Audit Summary — last {days} days",
            f"Team pass rate: {team_rate:.0f}% ({total_pass} pass, {total_fail} fail, {total_all} total)",
            "",
            "By agent (work producer, not Vera herself):",
        ]

        for r in rows:
            rate = (r["passes"] / max(r["total_verdicts"], 1)) * 100
            lines.append(
                f"  {r['assignee']}: {rate:.0f}% pass rate "
                f"({r['passes']}P / {r['fails']}F / {r['reworks']}R)"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_vera_pass_rate failed: {e}")
        return f"Error getting Vera pass rate: {e}"


# =============================================================================
# Echo / Tuning Health
# =============================================================================

@auto
@tool
async def get_echo_health(days: int = 7) -> str:
    """Check tuning echo completeness across all agents for a date range.

    Looks for missing or failed tunings — days where an agent should have
    tuned but didn't. System health indicator for the LTP pipeline.

    Args:
        days: Number of days to check (default 7)
    """
    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT
                     agent_id,
                     COUNT(*) as echo_count,
                     COUNT(DISTINCT DATE(tuned_at)) as days_with_echoes,
                     MAX(tuned_at) as last_echo,
                     array_agg(DISTINCT frequency ORDER BY frequency) as frequencies
                   FROM echoes
                   WHERE tuned_at > NOW() - INTERVAL '%s days'
                   GROUP BY agent_id
                   ORDER BY agent_id""",
                (days,),
            )
            rows = await result.fetchall()

            # Get list of all agents that should be tuning
            agent_result = await conn.execute(
                "SELECT DISTINCT agent_id FROM agent_state WHERE status = 'active'"
            )
            active_agents = {r["agent_id"] for r in await agent_result.fetchall()}

        tuning_agents = {r["agent_id"] for r in rows}
        missing = active_agents - tuning_agents

        lines = [f"Echo Health — last {days} days", ""]

        for r in rows:
            freqs = ", ".join(r["frequencies"]) if r["frequencies"] else "none"
            last = r["last_echo"].strftime("%Y-%m-%d %H:%M") if r["last_echo"] else "never"
            coverage = f"{r['days_with_echoes']}/{days} days"
            lines.append(
                f"  {r['agent_id']}: {r['echo_count']} echoes ({coverage}), "
                f"last: {last}, frequencies: {freqs}"
            )

        if missing:
            lines.append("")
            lines.append(f"  MISSING (active but no echoes): {', '.join(sorted(missing))}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"get_echo_health failed: {e}")
        return f"Error checking echo health: {e}"


# =============================================================================
# Escalation
# =============================================================================

@notify
@tool
async def escalate_to_stuart(issue_type: str, detail: str, task_id: Optional[int] = None) -> str:
    """Flag an issue for Stuart's attention. First-level escalation.

    Used when Soren finds a dropped task, overdue item, or anomaly
    that Stuart should address before it reaches the operator.

    Args:
        issue_type: Type of issue: overdue, dropped, untracked, anomaly, quality
        detail: What specifically is wrong
        task_id: The task ID to flag (optional — not all issues have a task yet)
    """
    try:
        if task_id:
            async with get_db() as conn:
                # Update escalation count on the task
                await conn.execute(
                    """UPDATE tasks
                       SET escalation_count = COALESCE(escalation_count, 0) + 1,
                           notes = COALESCE(notes, '') || E'\n[ESCALATION ' || NOW()::text || '] ' || %s
                       WHERE id = %s""",
                    (f"{issue_type}: {detail}", task_id),
                )

        tag = f"Task #{task_id}" if task_id else "General"
        logger.warning(f"[ESCALATION→STUART] {tag} ({issue_type}): {detail}")
        return (
            f"Escalated to Stuart: {tag} ({issue_type}). "
            f"Detail: {detail}. Stuart should review and resolve."
        )

    except Exception as e:
        logger.error(f"escalate_to_stuart failed: {e}")
        return f"Error escalating to Stuart: {e}"


@notify
@tool
async def escalate_to_operator(issue_type: str, detail: str, task_id: Optional[int] = None) -> str:
    """Send a notification to the operator about an unresolved issue.

    Second-level escalation. Only fires when Stuart-level escalation
    didn't resolve the issue within the monitoring window.

    Args:
        issue_type: Type of issue: overdue, dropped, anomaly, quality
        detail: What specifically is wrong and what was tried
        task_id: The task ID to flag (optional — not all issues have a task)
    """
    try:
        if task_id:
            async with get_db() as conn:
                # Update escalation count
                await conn.execute(
                    """UPDATE tasks
                       SET escalation_count = COALESCE(escalation_count, 0) + 1,
                           notes = COALESCE(notes, '') || E'\n[OPERATOR ESCALATION ' || NOW()::text || '] ' || %s
                       WHERE id = %s""",
                    (f"{issue_type}: {detail}", task_id),
                )

        # Push notification via calendar event (same pattern as approval notifications)
        try:
            from src.tools.calendar_notify import push_approval_to_calendar
            import asyncio
            asyncio.ensure_future(push_approval_to_calendar(
                request_id=f"escalation-{task_id}",
                tool_name="accountability_escalation",
                description=f"Task #{task_id} needs attention: {issue_type} — {detail}",
            ))
        except Exception as cal_err:
            logger.debug(f"Calendar push for escalation failed (non-fatal): {cal_err}")

        logger.warning(f"[ESCALATION→OPERATOR] Task #{task_id} ({issue_type}): {detail}")
        return (
            f"Escalated to operator: Task #{task_id} ({issue_type}). "
            f"Notification sent. Detail: {detail}"
        )

    except Exception as e:
        logger.error(f"escalate_to_operator failed: {e}")
        return f"Error escalating to operator: {e}"


# =============================================================================
# Accountability Audit Trail
# =============================================================================

@notify
@tool
async def log_accountability_check(
    tasks_checked: int,
    issues_found: int,
    escalations: str = "[]",
    notes: str = "",
) -> str:
    """Record that a monitoring sweep was performed.

    Creates an audit trail of Soren's accountability checks.

    Args:
        tasks_checked: Number of tasks reviewed
        issues_found: Number of issues found
        escalations: JSON array of escalation details (default empty)
        notes: Any additional observations
    """
    try:
        esc_data = json.loads(escalations) if escalations else []

        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO accountability_log
                   (tasks_checked, issues_found, escalations, notes)
                   VALUES (%s, %s, %s, %s)""",
                (tasks_checked, issues_found, json.dumps(esc_data), notes),
            )

        logger.info(
            f"[ACCOUNTABILITY] Sweep logged: {tasks_checked} checked, "
            f"{issues_found} issues, {len(esc_data)} escalations"
        )
        return (
            f"Accountability check logged: {tasks_checked} tasks checked, "
            f"{issues_found} issues found, {len(esc_data)} escalations."
        )

    except Exception as e:
        logger.error(f"log_accountability_check failed: {e}")
        return f"Error logging accountability check: {e}"


# =============================================================================
# Tool Collection
# =============================================================================

ALL_MONITORING_TOOLS = [
    check_task_completion,
    check_operator_requests,
    get_jw_summary,
    get_jw_anomalies,
    get_vera_pass_rate,
    get_echo_health,
    escalate_to_stuart,
    escalate_to_operator,
    log_accountability_check,
]
