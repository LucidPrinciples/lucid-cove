"""
Communication Tools — email, GitHub, notifications.

STUBS — interfaces defined, implementation added when accounts are ready.

Tier assignments:
  AUTO    — read_email, list_github_issues, get_github_pr, read_notifications
  NOTIFY  — draft_email, create_github_issue, comment_github_issue, notify_operator
  APPROVE — send_email, close_github_issue, merge_github_pr
"""

from typing import Optional

from langchain_core.tools import tool

from src.tools.approval import auto, notify, approve


# =============================================================================
# Email (stub — implement with Gmail API or similar when ready)
# =============================================================================

@auto
@tool
async def read_email(folder: str = "inbox", limit: int = 20) -> str:
    """Read recent emails. [NOT YET IMPLEMENTED]

    Args:
        folder: Email folder (inbox, sent, etc.)
        limit: Number of emails to return
    """
    return "Email tools not yet configured. Set up Gmail API credentials to enable."


@notify
@tool
async def draft_email(to: str, subject: str, body: str) -> str:
    """Draft an email (does not send). [NOT YET IMPLEMENTED]

    Args:
        to: Recipient email
        subject: Email subject
        body: Email body
    """
    return (
        f"[DRAFT — not sent]\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"Body: {body}\n\n"
        f"Email sending not yet configured."
    )


@approve
@tool
async def send_email(to: str, subject: str, body: str) -> str:
    """Send an email. Requires approval. [NOT YET IMPLEMENTED]

    Args:
        to: Recipient email
        subject: Email subject
        body: Email body
    """
    return "Email sending not yet configured. Set up Gmail API credentials to enable."


# =============================================================================
# GitHub (implement with gh CLI — likely already available on the host)
# =============================================================================

@auto
@tool
async def list_github_issues(repo: str, state: str = "open", limit: int = 20) -> str:
    """List GitHub issues for a repo. [NOT YET IMPLEMENTED]

    Args:
        repo: Repo in owner/name format
        state: open, closed, all
        limit: Max issues to return
    """
    return "GitHub tools not yet configured. Install gh CLI and authenticate to enable."


@auto
@tool
async def get_github_pr(repo: str, pr_number: int) -> str:
    """Get details of a GitHub pull request. [NOT YET IMPLEMENTED]

    Args:
        repo: Repo in owner/name format
        pr_number: PR number
    """
    return "GitHub tools not yet configured."


@notify
@tool
async def create_github_issue(repo: str, title: str, body: str = "",
                               labels: str = "") -> str:
    """Create a GitHub issue. [NOT YET IMPLEMENTED]

    Args:
        repo: Repo in owner/name format
        title: Issue title
        body: Issue body
        labels: Comma-separated labels
    """
    return "GitHub tools not yet configured."


@notify
@tool
async def comment_github_issue(repo: str, issue_number: int, comment: str) -> str:
    """Comment on a GitHub issue. [NOT YET IMPLEMENTED]

    Args:
        repo: Repo in owner/name format
        issue_number: Issue number
        comment: Comment text
    """
    return "GitHub tools not yet configured."


@approve
@tool
async def close_github_issue(repo: str, issue_number: int, reason: str = "") -> str:
    """Close a GitHub issue. Requires approval. [NOT YET IMPLEMENTED]

    Args:
        repo: Repo in owner/name format
        issue_number: Issue number
        reason: Why closing
    """
    return "GitHub tools not yet configured."


# =============================================================================
# Notifications to the operator
# =============================================================================

@notify
@tool
async def notify_operator(message: str, urgency: str = "normal") -> str:
    """Send a notification to the operator via Mission Control.

    Args:
        message: Notification message
        urgency: low, normal, high (affects display in MC)
    """
    from src.tools.approval import _notification_queue
    from datetime import datetime, timezone

    _notification_queue.append({
        "tier": "direct_notification",
        "message": message,
        "urgency": urgency,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return f"Notification queued: {message}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_COMMS_TOOLS = [
    # Email
    read_email, draft_email, send_email,
    # GitHub
    list_github_issues, get_github_pr, create_github_issue,
    comment_github_issue, close_github_issue,
    # Notifications
    notify_operator,
]
TOOLS = ALL_COMMS_TOOLS  # alias for cove-core channels.py loader
