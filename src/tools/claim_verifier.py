"""Claim Verifier — #D50: detect fabricated completions in agent messages.

When an agent says "I committed X" or "tests pass", this module checks
whether the corresponding tool call actually happened and succeeded.
It runs AFTER the agent's response is generated but BEFORE it reaches
the operator, so fabrications get flagged immediately.

Pairs with:
  - src.tools.verification (Soren layer — verifies tool results at execution)
  - src.dashboard.routes.ops_visibility (UI face — reconciles board/queue/GitHub)

Design: regex-based claim extraction + tool-call log lookup.
No NLP — deterministic, fast, auditable.
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional


# =============================================================================
# Claim patterns — what agents say vs what tools they should have called
# =============================================================================

_CLAIM_PATTERNS = [
    # (regex, tool_name_must_exist, friendly_name)
    (
        re.compile(r"(?:committed|commit|git commit|committed changes|pushed commit)", re.IGNORECASE),
        "git_commit",
        "git commit",
    ),
    (
        re.compile(r"(?:pushed|git push|push(?:ed)? (?:branch|to origin|the branch))", re.IGNORECASE),
        "git_push",
        "git push",
    ),
    (
        re.compile(r"(?:created (?:a )?(?:pull request|pr)|opened (?:a )?(?:pr|pull request)|pr (?:created|opened)|pull request #?\d+)", re.IGNORECASE),
        "create_github_pr",
        "GitHub PR creation",
    ),
    (
        re.compile(r"(?:tests? (?:pass|passed|green|running)|pytest|running tests?|all tests? pass)", re.IGNORECASE),
        "run_tests",
        "test run",
    ),
    (
        re.compile(r"(?:deployed|deployment|live|merged to main|merged pr)", re.IGNORECASE),
        None,  # No single tool — requires cross-checking ops_visibility
        "deploy/merge",
    ),
]


# =============================================================================
# Claim extraction
# =============================================================================

def extract_claims(text: str) -> list[dict]:
    """Scan agent message for completion claims.

    Returns list of dicts:
      {claim_type, matched_text, tool_expected, confidence}
    confidence = 'high' (explicit tool mention) or 'medium' (action implied)
    """
    claims = []
    for pattern, tool_name, friendly in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            # Check if this is a negation ("tests did NOT pass")
            window_start = max(0, match.start() - 30)
            window = text[window_start:match.end()]
            if re.search(r"\b(not|no|never|didn'?t|failed|failing)\b", window, re.IGNORECASE):
                continue  # Negated claim = not a positive completion claim

            claims.append({
                "claim_type": friendly,
                "tool_expected": tool_name,
                "matched_text": match.group(0),
                "confidence": "high" if tool_name else "medium",
            })
    return claims


# =============================================================================
# Tool-call log lookup
# =============================================================================

async def _recent_tool_calls(
    agent_id: str,
    tool_name: str,
    minutes: int = 30,
    limit: int = 10,
) -> list[dict]:
    """Fetch recent tool calls for an agent from the database.

    Returns rows from tool_execution_log or similar table.
    """
    try:
        from src.memory.database import get_db
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        async with get_db() as conn:
            result = await conn.execute(
                """
                SELECT tool_name, arguments, result_preview, created_at, success
                FROM tool_execution_log
                WHERE agent_id = :agent_id
                  AND tool_name = :tool_name
                  AND created_at > :cutoff
                ORDER BY created_at DESC
                LIMIT :limit
                """,
                {
                    "agent_id": agent_id,
                    "tool_name": tool_name,
                    "cutoff": cutoff.isoformat(),
                    "limit": limit,
                },
            )
            rows = await result.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        # If table doesn't exist or DB is unavailable, return empty
        # (graceful degradation — don't block the agent on infra issues)
        return []


# =============================================================================
# Verification
# =============================================================================

async def verify_claims(
    text: str,
    agent_id: str,
    channel: str = "",
) -> list[dict]:
    """Extract claims from text and verify against tool-call log.

    Returns list of:
      {
        claim_type: str,
        matched_text: str,
        verified: bool | None,  # True=found, False=not found, None=can't check
        detail: str,           # human-readable explanation
        tool_calls: list,      # matching tool calls found
      }
    """
    claims = extract_claims(text)
    if not claims:
        return []

    results = []
    for claim in claims:
        tool_name = claim["tool_expected"]
        if not tool_name:
            # No specific tool to check (e.g., "deployed")
            results.append({
                **claim,
                "verified": None,
                "detail": "Claim type has no direct tool verification; check ops_visibility.",
                "tool_calls": [],
            })
            continue

        calls = await _recent_tool_calls(agent_id, tool_name)
        if calls:
            # Found matching tool calls — verify they succeeded
            success_calls = [c for c in calls if c.get("success")]
            if success_calls:
                results.append({
                    **claim,
                    "verified": True,
                    "detail": f"Found {len(success_calls)} successful {tool_name} call(s) in last 30 min.",
                    "tool_calls": calls[:3],
                })
            else:
                results.append({
                    **claim,
                    "verified": False,
                    "detail": f"Found {len(calls)} {tool_name} call(s) but none succeeded.",
                    "tool_calls": calls[:3],
                })
        else:
            results.append({
                **claim,
                "verified": False,
                "detail": f"No {tool_name} tool calls found in last 30 min for {agent_id}.",
                "tool_calls": [],
            })

    return results


# =============================================================================
# Attention card generation
# =============================================================================

async def check_and_flag(
    text: str,
    agent_id: str,
    channel: str = "",
) -> Optional[dict]:
    """Check agent message for unverified claims and return Attention card data.

    Returns None if all claims verified or no claims found.
    Returns dict with card data if fabrication detected.
    """
    results = await verify_claims(text, agent_id, channel)
    unverified = [r for r in results if r["verified"] is False]

    if not unverified:
        return None

    detail_lines = []
    for r in unverified:
        detail_lines.append(
            f"• Claimed: '{r['matched_text']}' ({r['claim_type']})\n"
            f"  → {r['detail']}"
        )

    return {
        "severity": "warning",
        "title": f"Unverified completion claim by {agent_id}",
        "detail": "\n".join(detail_lines),
        "agent_id": agent_id,
        "channel": channel,
        "claims": unverified,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
