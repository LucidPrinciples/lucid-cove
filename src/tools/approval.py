"""
Approval Tier System — safety backbone for all agent tool operations.

Three tiers control what agents can do autonomously vs. what needs operator sign-off:

  AUTO    — Read-only, monitoring, search. Agents run these silently.
  NOTIFY  — File writes, git commits, container restarts. Agents run and log.
  APPROVE — Destructive ops, sends, payments, system changes. Agents propose and WAIT.

Usage:
    from src.tools.approval import auto, notify, approve, ApprovalRequired

    @auto
    @tool
    async def read_file(path: str) -> str: ...

    @notify
    @tool
    async def write_file(path: str, content: str) -> str: ...

    @approve
    @tool
    async def delete_file(path: str) -> str: ...

When an @approve tool is called, it raises ApprovalRequired with a description
of what it wants to do. The LangGraph tool node catches this, sends the request
to Mission Control, and pauses until the operator confirms or denies.

Approval requests are persisted to PostgreSQL so they survive container restarts.
Notifications remain in-memory (they're ephemeral status updates, not actions).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("approval")


# =============================================================================
# Tier definitions
# =============================================================================

class Tier(str, Enum):
    AUTO = "auto"
    NOTIFY = "notify"
    APPROVE = "approve"


@dataclass
class ApprovalRequest:
    """Represents a pending approval request from an agent to the operator."""
    tool_name: str
    description: str
    args: dict
    tier: Tier
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str = ""
    status: str = "pending"  # pending | approved | denied | expired

    def __post_init__(self):
        if not self.request_id:
            import uuid
            self.request_id = str(uuid.uuid4())[:8]


class ApprovalRequired(Exception):
    """Raised when a tool needs operator approval before executing."""
    def __init__(self, request: ApprovalRequest):
        self.request = request
        super().__init__(
            f"[APPROVAL REQUIRED] {request.tool_name}: {request.description} "
            f"(request_id: {request.request_id})"
        )


# =============================================================================
# Notification queue (in-memory — ephemeral status updates, not actions)
# =============================================================================

_notification_queue: list[dict] = []


def get_notifications(clear: bool = True) -> list[dict]:
    """Get pending notifications for Mission Control."""
    global _notification_queue
    items = list(_notification_queue)
    if clear:
        _notification_queue.clear()
    return items


# =============================================================================
# DB-backed approval functions (survive container restarts)
# =============================================================================

async def get_pending_approvals() -> list[ApprovalRequest]:
    """Get all pending approval requests from the database."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at DESC"
            )
            rows = await result.fetchall()
        return [
            ApprovalRequest(
                request_id=r["request_id"],
                tool_name=r["tool_name"],
                description=r["description"],
                args=r["args"] if isinstance(r["args"], dict) else json.loads(r["args"] or "{}"),
                tier=Tier(r["tier"]),
                timestamp=r["created_at"].isoformat() if r["created_at"] else "",
                status=r["status"],
            )
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Failed to load pending approvals from DB: {e}")
        return []


async def respond_to_approval(request_id: str, approved: bool) -> bool:
    """Operator responds to an approval request via Mission Control.
    Updates the DB record. Returns True if found and updated."""
    try:
        from src.memory.database import get_db
        status = "approved" if approved else "denied"
        async with get_db() as conn:
            result = await conn.execute(
                """UPDATE approval_requests
                   SET status = %s, resolved_at = NOW(), resolved_by = 'operator'
                   WHERE request_id = %s AND status = 'pending'
                   RETURNING request_id""",
                (status, request_id),
            )
            row = await result.fetchone()
        if row:
            logger.info(f"Approval {request_id}: {'APPROVED' if approved else 'DENIED'}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to respond to approval {request_id}: {e}")
        return False


async def check_approval_status(request_id: str) -> Optional[str]:
    """Check if an approval has been responded to."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT status FROM approval_requests WHERE request_id = %s",
                (request_id,),
            )
            row = await result.fetchone()
        return row["status"] if row else None
    except Exception as e:
        logger.error(f"Failed to check approval status {request_id}: {e}")
        return None


async def _save_approval_to_db(request: ApprovalRequest, channel: str = "") -> None:
    """Persist an approval request to the database."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO approval_requests (request_id, tool_name, description, args, tier, status, channel)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (request_id) DO NOTHING""",
                (request.request_id, request.tool_name, request.description,
                 json.dumps(request.args), request.tier.value, request.status, channel),
            )
        logger.info(f"Approval {request.request_id} saved to DB (channel={channel})")
    except Exception as e:
        logger.error(f"Failed to save approval to DB (request still raised): {e}")


# =============================================================================
# Decorators
# =============================================================================

def auto(func: Callable) -> Callable:
    """Mark a tool as AUTO tier — runs silently, no logging overhead."""
    func._approval_tier = Tier.AUTO
    return func


def notify(func: Callable) -> Callable:
    """Mark a tool as NOTIFY tier — runs and logs to Mission Control.

    Just tags the tier. Notification logging happens in the tool_node
    at execution time, keeping the StructuredTool intact for bind_tools().
    """
    func._approval_tier = Tier.NOTIFY
    return func


def approve(func: Callable) -> Callable:
    """Mark a tool as APPROVE tier — needs operator sign-off.

    Just tags the tier. Approval blocking happens in the tool_node
    at execution time, keeping the StructuredTool intact for bind_tools().
    """
    func._approval_tier = Tier.APPROVE
    return func


def log_notify(tool_name: str, kwargs: dict) -> None:
    """Log a NOTIFY tier tool execution to the notification queue.

    Called by the tool_node when executing a NOTIFY-tier tool.
    """
    desc = f"{tool_name}({', '.join(f'{k}={repr(v)[:80]}' for k, v in kwargs.items())})"
    logger.info(f"[NOTIFY] {desc}")
    _notification_queue.append({
        "tier": "notify",
        "tool": tool_name,
        "args": {k: repr(v)[:200] for k, v in kwargs.items()},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "description": desc,
    })


async def block_for_approval(tool_name: str, kwargs: dict, channel: str = "") -> None:
    """Block an APPROVE tier tool and raise ApprovalRequired.

    Called by the tool_node when executing an APPROVE-tier tool.
    Persists the request to DB so it survives container restarts.
    Also pushes a calendar event with alarm for phone notification.
    """
    arg_desc = ', '.join(f'{k}={repr(v)[:100]}' for k, v in kwargs.items())
    description = f"{tool_name}({arg_desc})"

    # Dedupe: agents retry a blocked call (the "APPROVAL REQUIRED" result reads like a
    # failure to them), which would otherwise stack identical requests in the operator's
    # queue. If an identical one is already pending, reuse it.
    try:
        for r in await get_pending_approvals():
            if r.tool_name == tool_name and r.args == kwargs:
                logger.info(f"[APPROVE] reusing pending {r.request_id} for duplicate {description}")
                raise ApprovalRequired(r)
    except ApprovalRequired:
        raise
    except Exception as e:
        logger.debug(f"[approval] dedupe check skipped (non-fatal): {e}")

    request = ApprovalRequest(
        tool_name=tool_name,
        description=description,
        args=kwargs,
        tier=Tier.APPROVE,
    )

    # Save to DB first
    await _save_approval_to_db(request, channel=channel)

    logger.warning(f"[APPROVE REQUIRED] {description} — request_id: {request.request_id}")

    # Push to calendar for phone notification (fire-and-forget)
    try:
        from src.tools.calendar_notify import push_approval_to_calendar
        asyncio.ensure_future(push_approval_to_calendar(
            request_id=request.request_id,
            tool_name=tool_name,
            description=description,
        ))
    except Exception as e:
        logger.debug(f"[approval] Calendar push failed (non-fatal): {e}")

    raise ApprovalRequired(request)


# =============================================================================
# Execute approved tool
# =============================================================================

def _result_is_success(result_str: str) -> bool:
    """#D52: a gated tool that RETURNS (no exception) can still have failed.
    git_push returns 'FAILED: ... not found on origin', create_github_pr returns
    'Error: ...', and both can return 'REFUSED: ...'. The old executor marked every
    non-throwing call success=True, so the approval card rendered a green checkmark on
    a failed push -- the exact false-success gap #D52 closes. Reuse the tested #D13
    classifier (looks_like_error) so success reflects the real outcome."""
    from src.utils.watcher import looks_like_error
    if not result_str or not result_str.strip():
        return True
    if result_str.lstrip().upper().startswith("REFUSED"):
        return False
    return not looks_like_error(result_str)


async def execute_approved_tool(request_id: str) -> dict:
    """Execute a tool that has been approved by the operator.

    Looks up the tool by name, runs it with stored args, saves result to DB.
    Returns {"success": True, "result": "..."} or {"success": False, "error": "..."}.
    """
    try:
        from src.memory.database import get_db

        # Load the approval record
        async with get_db() as conn:
            row = await (await conn.execute(
                "SELECT * FROM approval_requests WHERE request_id = %s AND status = 'approved'",
                (request_id,),
            )).fetchone()

        if not row:
            return {"success": False, "error": f"No approved request with id '{request_id}'"}

        tool_name = row["tool_name"]
        tool_args = row["args"] if isinstance(row["args"], dict) else json.loads(row["args"] or "{}")

        # Find the tool. Build a superset map from every tool pool so ANY approvable
        # tool resolves regardless of which agent raised it (git_push et al. live in
        # ALL_DEV_TOOLS). NOTE: get_agent_tools(agent_id) requires an arg — calling it
        # bare used to throw here and silently strand every approved action.
        tool_func = None
        try:
            from src.tools import agent_tools as _at
            pools = []
            for _name in dir(_at):
                if _name.startswith("ALL_") and _name.endswith("_TOOLS"):
                    pools += list(getattr(_at, _name, []) or [])
            tool_map = {getattr(t, "name", None): t for t in pools}
            tool_func = tool_map.get(tool_name)
            if tool_func is None:
                # Fallback: a concrete agent's set (default getter → Stuart, includes dev tools).
                tool_func = {t.name: t for t in _at.get_agent_tools("stuart")}.get(tool_name)
        except Exception as e:
            logger.error(f"Tool lookup for approved '{tool_name}' failed: {e}")

        if not tool_func:
            return {"success": False, "error": f"Tool '{tool_name}' not found"}

        # Execute it
        logger.info(f"Executing approved tool: {tool_name}({tool_args}) [request_id={request_id}]")
        result = await tool_func.ainvoke(tool_args)
        result_str = str(result)

        # Save result to DB
        async with get_db() as conn:
            await conn.execute(
                "UPDATE approval_requests SET result = %s WHERE request_id = %s",
                (result_str[:10000], request_id),  # cap at 10k chars
            )

        # #D52: map a FAILED/Error/REFUSED result (tool returned without throwing) to
        # executed=False so the approval card can't show a green checkmark on a failed push.
        real_success = _result_is_success(result_str)
        logger.info(
            f"Approved tool executed: {tool_name} -> success={real_success} :: {result_str[:200]}"
        )
        return {"success": real_success, "result": result_str}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Failed to execute approved tool {request_id}: {error_msg}")
        # Save error to DB too
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE approval_requests SET result = %s WHERE request_id = %s",
                    (f"ERROR: {error_msg}", request_id),
                )
        except Exception:
            pass
        return {"success": False, "error": error_msg}


# =============================================================================
# Utility: get tier for a tool
# =============================================================================

def get_tier(tool_func: Callable) -> Tier:
    """Get the approval tier of a tool function."""
    return getattr(tool_func, '_approval_tier', Tier.AUTO)


def tier_summary(tools: list) -> dict[str, list[str]]:
    """Summarize tools by tier — useful for Mission Control display."""
    summary: dict[str, list[str]] = {"auto": [], "notify": [], "approve": []}
    for t in tools:
        tier = get_tier(t)
        name = getattr(t, 'name', t.__name__)
        summary[tier.value].append(name)
    return summary
