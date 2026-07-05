"""
Task Graph — multi-agent delegation engine for workflow execution.

Flow-agnostic: handles all workflow patterns (build, report, research, comms,
knowledge, commerce) through a unified state machine. Stuart initiates,
agents execute their steps, Vera audits where required, Stuart reviews.

The graph routes work through agents based on workflow_pattern and workflow_state,
loading each agent's persona, tools, and accumulated chain data at each step.

Usage:
    from src.graphs.task_graph import run_workflow

    result = await run_workflow(task_id=42)
"""

import json
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from src.config import get_primary_agent_id
from src.agents.identity import build_system_prompt
from src.models.provider import invoke_with_fallback, _write_jw_metric
from src.memory.database import get_db
from src.tools.agent_tools import get_agent_tools
from src.utils.time_utils import ts_log


# =============================================================================
# Workflow Definitions — state machines per pattern
# =============================================================================

WORKFLOW_STEPS = {
    "report": {
        "steps": ["collect", "analyze", "write", "audit", "file", "deliver"],
        "agents": {
            "collect": "soren",
            "analyze": "arthur",
            "write": "julian",
            "audit": "vera",
            "file": "ezra",
            "deliver": "stuart",
        },
        "audit_step": "audit",
        "pre_audit_step": "write",  # Where to loop back on audit fail
    },
    "build": {
        "steps": ["implement", "audit", "review", "approve"],
        "agents": {
            "implement": "archimedes",
            "audit": "vera",
            "review": "stuart",
            "approve": "stuart",
        },
        "audit_step": "audit",
        "pre_audit_step": "implement",
    },
    "research": {
        "steps": ["gather", "analyze"],
        "agents": {
            "gather": "gabe",
            "analyze": "arthur",
        },
        "audit_step": None,  # No audit gate for research
        "pre_audit_step": None,
    },
    "comms": {
        "steps": ["draft", "audit", "review", "approve"],
        "agents": {
            "draft": "julian",  # Or iris — Stuart picks at assignment
            "audit": "vera",
            "review": "stuart",
            "approve": "stuart",
        },
        "audit_step": "audit",
        "pre_audit_step": "draft",
    },
    "knowledge": {
        "steps": ["organize", "review"],
        "agents": {
            "organize": "ezra",
            "review": "stuart",
        },
        "audit_step": None,
        "pre_audit_step": None,
    },
    "commerce": {
        "steps": ["execute", "audit", "review"],
        "agents": {
            "execute": "mercer",
            "audit": "vera",
            "review": "stuart",
        },
        "audit_step": "audit",
        "pre_audit_step": "execute",
    },
}


# =============================================================================
# Task DB helpers
# =============================================================================

async def _get_task(task_id: int) -> Optional[dict]:
    """Load a task from the DB."""
    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT * FROM tasks WHERE id = %s", (task_id,)
            )
            row = await result.fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"{ts_log()} [task_graph] Error loading task {task_id}: {e}")
        return None


async def _update_task_state(task_id: int, workflow_state: str,
                              audit_verdict: str = None,
                              notes_append: str = None):
    """Update a task's workflow state and optionally audit verdict/notes."""
    try:
        async with get_db() as conn:
            updates = ["workflow_state = %s", "updated_at = NOW()"]
            values = [workflow_state]

            if audit_verdict is not None:
                updates.append("audit_verdict = %s")
                values.append(audit_verdict)

            if notes_append:
                updates.append("notes = COALESCE(notes, '') || %s")
                values.append(f"\n\n---\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] {notes_append}")

            values.append(task_id)
            sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = %s"
            await conn.execute(sql, tuple(values))
            await conn.commit()
    except Exception as e:
        print(f"{ts_log()} [task_graph] Error updating task {task_id}: {e}")


async def _increment_audit_count(task_id: int):
    """Bump the audit attempt counter."""
    try:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE tasks SET audit_count = COALESCE(audit_count, 0) + 1 WHERE id = %s",
                (task_id,)
            )
            await conn.commit()
    except Exception as e:
        print(f"{ts_log()} [task_graph] Error incrementing audit count: {e}")


async def _log_work_entry(task_id: int, agent_id: str, step: str, content: str):
    """Record what an agent produced at a workflow step."""
    try:
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO task_history (task_id, field_changed, old_value, new_value, changed_by)
                   VALUES (%s, %s, %s, %s, %s)""",
                (task_id, f"workflow_step:{step}", None, content[:2000], agent_id)
            )
            await conn.commit()
    except Exception as e:
        print(f"{ts_log()} [task_graph] Error logging work entry: {e}")


# =============================================================================
# Agent Step Execution
# =============================================================================

def _build_step_prompt(agent_id: str, step: str, task: dict,
                        chain_data: dict, workflow_def: dict) -> list:
    """Build the messages for an agent's workflow step.

    Each agent gets:
    1. Their full system prompt (persona, boundaries, memory)
    2. A structured task briefing with what they need to do
    3. Any upstream data from previous steps in the chain
    """
    # Build the agent's system prompt
    system_prompt = build_system_prompt(agent_id)

    # Build the task briefing
    task_title = task.get("title", "Untitled")
    task_desc = task.get("description", "")
    task_notes = task.get("notes", "")
    pattern = task.get("workflow_pattern", "unknown")

    briefing_parts = [
        f"## Workflow Task Assignment",
        f"",
        f"**Task #{task['id']}:** {task_title}",
        f"**Workflow:** {pattern} → current step: **{step}**",
        f"**Your role in this step:** You are the assigned agent for the '{step}' phase.",
    ]

    if task_desc:
        briefing_parts.append(f"\n**Description:** {task_desc}")
    if task_notes:
        briefing_parts.append(f"\n**Task notes:** {task_notes}")

    # Add upstream chain data
    if chain_data:
        briefing_parts.append("\n## Upstream Data from Previous Steps\n")
        for prev_step, prev_output in chain_data.items():
            briefing_parts.append(f"### From {prev_step}:")
            # Truncate very long outputs but keep enough for context
            output_str = str(prev_output)
            if len(output_str) > 4000:
                output_str = output_str[:4000] + "\n\n[... truncated — full data available if needed]"
            briefing_parts.append(output_str)

    # Add step-specific instructions
    step_instructions = _get_step_instructions(step, pattern, task)
    if step_instructions:
        briefing_parts.append(f"\n## Your Instructions\n\n{step_instructions}")

    briefing = "\n".join(briefing_parts)

    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=briefing),
    ]


def _get_step_instructions(step: str, pattern: str, task: dict) -> str:
    """Get specific instructions for a workflow step."""
    instructions = {
        # Report flow
        ("report", "collect"): (
            "Query the database for the reporting period's data. Collect: echoes (counts, frequencies), "
            "tasks (completed, created, overdue), jw_metrics (per-agent totals), protocol_runs "
            "(success/fail), accountability_log entries. Return structured data — raw numbers, "
            "no interpretation. Output as clean JSON or structured text that Arthur can analyze."
        ),
        ("report", "analyze"): (
            "You receive Soren's raw data. Identify week-over-week deltas, trends, anomalies, "
            "and patterns. Flag anything unusual. Return structured analysis with confidence levels "
            "for each finding. Be precise with numbers — Vera will cross-check you."
        ),
        ("report", "write"): (
            "Write the report in plain language using Arthur's analysis AND Soren's raw data. "
            "Numbers must come from Soren's data, not Arthur's interpretation. "
            "Call out action items clearly. Use the standard report template. "
            "No padding, no filler. Vera will audit every number."
        ),
        ("report", "audit"): (
            "AUDIT this report. Cross-check every number in the report text against the raw data "
            "from the 'collect' step. Check: Are numbers accurate? Are trends fairly characterized? "
            "Any hallucinated metrics? Format correct?\n\n"
            "Return your verdict in this exact format:\n"
            "AUDIT: PASS or AUDIT: FAIL\n"
            "Issues found: [numbered list, or 'None']\n"
            "Notes: [observations that aren't blockers]\n"
            "Recommendation: [proceed / rework specific items]"
        ),
        ("report", "file"): (
            "Save the approved report. Write it to the vault at the appropriate location. "
            "Return confirmation with the file path."
        ),
        ("report", "deliver"): (
            "Post a summary of the report. Flag any action items that need operator attention. "
            "Mark the report task as completed."
        ),

        # Build flow
        ("build", "implement"): (
            "Implement what the task description specifies. Follow the spec exactly. "
            "When done, provide a clear summary of what was built, files created/modified, "
            "and how to verify it works."
        ),
        ("build", "audit"): (
            "AUDIT this implementation. Check:\n"
            "- Code syntax valid\n"
            "- Implementation matches the spec/description\n"
            "- No hardcoded values that should be config/env\n"
            "- Error handling present for external calls\n"
            "- No obvious security issues\n"
            "- All files mentioned in the spec are actually created/modified\n\n"
            "Return your verdict in this exact format:\n"
            "AUDIT: PASS or AUDIT: FAIL\n"
            "Issues found: [numbered list, or 'None']\n"
            "Notes: [observations]\n"
            "Recommendation: [proceed / rework specific items]"
        ),
        ("build", "review"): (
            "Review the overall result against project goals. Vera has already done the "
            "line-by-line review — your job is strategic: does this accomplish what we needed? "
            "Any concerns about approach or direction?"
        ),

        # Research flow
        ("research", "gather"): (
            "Research the question in the task description. Search, gather raw data, "
            "log findings with sources. Return structured raw findings — no interpretation yet."
        ),
        ("research", "analyze"): (
            "Analyze Gabe's raw findings. Produce structured analysis with confidence levels "
            "for each conclusion. Separate facts from inferences."
        ),

        # Comms flow
        ("comms", "draft"): (
            "Draft the communication described in the task. Match the specified audience and tone. "
            "Include all necessary content. This will go through Vera's audit before sending."
        ),
        ("comms", "audit"): (
            "AUDIT this communication. Check:\n"
            "- Factual accuracy — are claims verifiable?\n"
            "- Tone match — appropriate for the audience?\n"
            "- Completeness — does it address the stated purpose?\n"
            "- Framework alignment — LP terminology used correctly if present\n\n"
            "Return your verdict in this exact format:\n"
            "AUDIT: PASS or AUDIT: FAIL\n"
            "Issues found: [numbered list, or 'None']\n"
            "Notes: [observations]\n"
            "Recommendation: [proceed / rework specific items]"
        ),

        # Knowledge flow
        ("knowledge", "organize"): (
            "Perform the knowledge management work described in the task. "
            "Reorganize, update, index — whatever the task specifies. "
            "Return a summary of what was done."
        ),

        # Commerce flow
        ("commerce", "execute"): (
            "Execute the commerce task described. Handle listings, pricing, sourcing, "
            "or whatever is specified. Return results and any decisions made."
        ),
    }

    return instructions.get((pattern, step), "")


# =============================================================================
# Vera Audit Evaluation
# =============================================================================

def _parse_audit_verdict(response_text: str) -> tuple[str, str]:
    """Parse Vera's structured audit response.

    Returns: (verdict, details)
        verdict: 'pass' or 'fail'
        details: The full audit text
    """
    text = response_text.strip()

    # Look for AUDIT: PASS or AUDIT: FAIL
    text_upper = text.upper()
    if "AUDIT: PASS" in text_upper or "AUDIT:PASS" in text_upper:
        return "pass", text
    elif "AUDIT: FAIL" in text_upper or "AUDIT:FAIL" in text_upper:
        return "fail", text

    # Fallback: look for just PASS or FAIL at the start
    first_line = text_upper.split("\n")[0].strip()
    if "PASS" in first_line and "FAIL" not in first_line:
        return "pass", text
    elif "FAIL" in first_line:
        return "fail", text

    # Can't determine — treat as fail to be safe
    return "fail", f"[Could not parse audit verdict. Treating as FAIL.]\n\n{text}"


# =============================================================================
# Core Execution Engine
# =============================================================================

async def execute_step(task_id: int, step: str, workflow_def: dict,
                       chain_data: dict, max_audit_retries: int = 3) -> dict:
    """Execute a single workflow step with the assigned agent.

    Args:
        task_id: The task being executed
        step: Current step name (e.g., 'collect', 'audit', 'write')
        workflow_def: The workflow definition from WORKFLOW_STEPS
        chain_data: Accumulated outputs from previous steps
        max_audit_retries: Max times to loop through audit before giving up

    Returns:
        dict with keys: 'output', 'chain_data', 'next_step', 'status'
    """
    task = await _get_task(task_id)
    if not task:
        return {"output": None, "chain_data": chain_data, "next_step": None, "status": "error"}

    agent_id = workflow_def["agents"].get(step, "stuart")
    label = f"task#{task_id}/{step}/{agent_id}"

    print(f"{ts_log()} [task_graph] ─── Step: {step} → Agent: {agent_id} ───")

    # Update task state
    await _update_task_state(task_id, step)

    # Build the prompt for this agent
    messages = _build_step_prompt(agent_id, step, task, chain_data, workflow_def)

    # Invoke the agent's model chain
    t0 = time.monotonic()
    try:
        response = await invoke_with_fallback(
            messages,
            label=label,
            agent_id=agent_id,
            operation_type="workflow",
            timeout=180,  # Generous timeout for complex workflow steps
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        print(f"{ts_log()} [task_graph] {label} completed ({duration_ms}ms, {len(response)} chars)")

    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        tb = traceback.format_exc()
        print(f"{ts_log()} [task_graph] {label} FAILED: {e}")
        await _log_work_entry(task_id, agent_id, step, f"ERROR: {e}\n{tb}")
        await _update_task_state(task_id, step, notes_append=f"Step '{step}' failed: {e}")
        return {"output": None, "chain_data": chain_data, "next_step": None, "status": "error"}

    # Log the work entry
    await _log_work_entry(task_id, agent_id, step, response)

    # Store this step's output in chain data
    chain_data[step] = response

    # Determine next step
    steps = workflow_def["steps"]
    current_idx = steps.index(step)

    # Special handling for audit steps
    audit_step = workflow_def.get("audit_step")
    if step == audit_step:
        verdict, details = _parse_audit_verdict(response)
        await _increment_audit_count(task_id)

        if verdict == "pass":
            await _update_task_state(task_id, step, audit_verdict="pass",
                                     notes_append=f"Vera audit PASSED")
            print(f"{ts_log()} [task_graph] {label} — AUDIT PASS")
            # Advance to next step after audit
            next_step = steps[current_idx + 1] if current_idx + 1 < len(steps) else None
        else:
            # Check retry limit
            task_refreshed = await _get_task(task_id)
            audit_count = (task_refreshed or {}).get("audit_count", 0)

            if audit_count >= max_audit_retries:
                await _update_task_state(task_id, step, audit_verdict="fail",
                                         notes_append=f"Vera audit FAILED after {audit_count} attempts. Escalating to Stuart.")
                print(f"{ts_log()} [task_graph] {label} — AUDIT FAIL (max retries reached, escalating)")
                return {"output": response, "chain_data": chain_data,
                        "next_step": None, "status": "audit_failed_max_retries"}

            await _update_task_state(task_id, step, audit_verdict="fail",
                                     notes_append=f"Vera audit FAILED (attempt {audit_count}): {details[:500]}")
            print(f"{ts_log()} [task_graph] {label} — AUDIT FAIL → looping back to {workflow_def['pre_audit_step']}")

            # Add Vera's feedback to chain data so the worker gets it
            chain_data["audit_feedback"] = details

            # Loop back to the pre-audit step
            next_step = workflow_def["pre_audit_step"]
            return {"output": response, "chain_data": chain_data,
                    "next_step": next_step, "status": "audit_loop"}
    else:
        # Normal step — advance to next
        next_step = steps[current_idx + 1] if current_idx + 1 < len(steps) else None

    if next_step is None:
        return {"output": response, "chain_data": chain_data,
                "next_step": None, "status": "completed"}

    return {"output": response, "chain_data": chain_data,
            "next_step": next_step, "status": "advancing"}


# =============================================================================
# Main Entry Point
# =============================================================================

async def run_workflow(task_id: int, max_audit_retries: int = 3) -> dict:
    """Run a complete workflow for a task.

    Reads the task's workflow_pattern, resolves the step sequence,
    and executes each step in order — handling audit loops, errors,
    and chain data propagation.

    Args:
        task_id: The task to execute
        max_audit_retries: Max audit loop iterations before escalating

    Returns:
        dict with final status, chain_data, and any errors
    """
    task = await _get_task(task_id)
    if not task:
        return {"status": "error", "error": f"Task {task_id} not found"}

    pattern = task.get("workflow_pattern")
    if not pattern or pattern not in WORKFLOW_STEPS:
        return {"status": "error", "error": f"Unknown workflow pattern: {pattern}"}

    workflow_def = WORKFLOW_STEPS[pattern]
    label = f"task#{task_id}/{pattern}"

    print(f"\n{ts_log()} [task_graph] ════════════════════════════════════════")
    print(f"{ts_log()} [task_graph]  WORKFLOW START: Task #{task_id}")
    print(f"{ts_log()} [task_graph]  Pattern: {pattern}")
    print(f"{ts_log()} [task_graph]  Title: {task.get('title', 'Untitled')}")
    print(f"{ts_log()} [task_graph]  Steps: {' → '.join(workflow_def['steps'])}")
    print(f"{ts_log()} [task_graph] ════════════════════════════════════════\n")

    # Determine starting step — resume from current state if partially done
    current_state = task.get("workflow_state")
    if current_state and current_state in workflow_def["steps"]:
        # Resume from where we left off
        start_step = current_state
        print(f"{ts_log()} [task_graph] Resuming from step: {start_step}")
    else:
        start_step = workflow_def["steps"][0]

    # Update task status
    await _update_task_state(task_id, start_step,
                              notes_append=f"Workflow '{pattern}' started. Steps: {' → '.join(workflow_def['steps'])}")

    chain_data = {}
    current_step = start_step
    t_start = time.monotonic()

    while current_step:
        result = await execute_step(
            task_id, current_step, workflow_def, chain_data, max_audit_retries
        )

        chain_data = result["chain_data"]
        status = result["status"]

        if status == "error":
            total_ms = int((time.monotonic() - t_start) * 1000)
            print(f"{ts_log()} [task_graph] {label} — ERROR at step '{current_step}' ({total_ms}ms total)")
            await _update_task_state(task_id, current_step,
                                      notes_append=f"Workflow stopped due to error at step '{current_step}'")
            return {"status": "error", "step": current_step, "chain_data": chain_data,
                    "total_ms": total_ms}

        if status == "audit_failed_max_retries":
            total_ms = int((time.monotonic() - t_start) * 1000)
            print(f"{ts_log()} [task_graph] {label} — AUDIT FAILED (max retries) ({total_ms}ms total)")
            await _update_task_state(task_id, "review",
                                      notes_append=f"Audit failed after {max_audit_retries} attempts. Needs Stuart review.")
            return {"status": "audit_failed", "step": current_step, "chain_data": chain_data,
                    "total_ms": total_ms}

        if status == "completed":
            total_ms = int((time.monotonic() - t_start) * 1000)
            print(f"\n{ts_log()} [task_graph] ════════════════════════════════════════")
            print(f"{ts_log()} [task_graph]  WORKFLOW COMPLETE: Task #{task_id}")
            print(f"{ts_log()} [task_graph]  Pattern: {pattern}")
            print(f"{ts_log()} [task_graph]  Duration: {total_ms}ms")
            print(f"{ts_log()} [task_graph] ════════════════════════════════════════\n")
            await _update_task_state(task_id, "done",
                                      notes_append=f"Workflow '{pattern}' completed in {total_ms}ms")
            return {"status": "completed", "chain_data": chain_data,
                    "total_ms": total_ms}

        # status is 'advancing' or 'audit_loop' — continue to next step
        current_step = result["next_step"]

    # Should not reach here, but just in case
    total_ms = int((time.monotonic() - t_start) * 1000)
    return {"status": "completed", "chain_data": chain_data, "total_ms": total_ms}


# =============================================================================
# Convenience: Stuart can trigger workflows from chat
# =============================================================================

async def start_workflow(task_id: int, pattern: str = None) -> str:
    """Set up a task for workflow execution and run it.

    Called by Stuart when he assigns a task to a workflow pattern.
    If pattern isn't specified, reads it from the task's workflow_pattern field.

    Returns a status summary string.
    """
    task = await _get_task(task_id)
    if not task:
        return f"Task #{task_id} not found."

    if pattern:
        # Set the workflow pattern if provided
        try:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE tasks SET workflow_pattern = %s, workflow_state = %s WHERE id = %s",
                    (pattern, WORKFLOW_STEPS[pattern]["steps"][0], task_id)
                )
                await conn.commit()
        except Exception as e:
            return f"Error setting workflow pattern: {e}"
    elif not task.get("workflow_pattern"):
        return (f"Task #{task_id} has no workflow_pattern set. "
                f"Available patterns: {', '.join(WORKFLOW_STEPS.keys())}")

    actual_pattern = pattern or task["workflow_pattern"]
    if actual_pattern not in WORKFLOW_STEPS:
        return f"Unknown workflow pattern: {actual_pattern}. Available: {', '.join(WORKFLOW_STEPS.keys())}"

    steps = WORKFLOW_STEPS[actual_pattern]["steps"]

    # Run the workflow
    result = await run_workflow(task_id)

    status = result.get("status", "unknown")
    total_ms = result.get("total_ms", 0)

    if status == "completed":
        return (f"Workflow '{actual_pattern}' completed for task #{task_id} "
                f"in {total_ms}ms. Steps executed: {' → '.join(steps)}")
    elif status == "audit_failed":
        return (f"Workflow '{actual_pattern}' for task #{task_id} stopped at audit. "
                f"Vera failed the work after max retries. Needs manual review. ({total_ms}ms)")
    elif status == "error":
        step = result.get("step", "unknown")
        return (f"Workflow '{actual_pattern}' for task #{task_id} hit an error at step '{step}'. "
                f"Check task notes for details. ({total_ms}ms)")
    else:
        return f"Workflow finished with status: {status} ({total_ms}ms)"
