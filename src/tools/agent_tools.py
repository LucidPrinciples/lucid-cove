"""Agent Tools — master registry for family agent tool collections.

Each agent gets a specialized tool set based on their archetype and role.
Stuart (Steward) has the full suite. Specialists get focused subsets.

Usage:
    from src.tools.agent_tools import get_agent_tools, get_tool_summary

    # Get tools for any agent by ID
    tools = get_agent_tools("archimedes")

    # Or use direct getters
    builder_tools = get_builder_tools()

    # See what's available
    summary = get_tool_summary("archimedes")
"""

from src.tools.approval import tier_summary

# Import all tool registries
from src.tools.system_tools import ALL_SYSTEM_TOOLS
from src.tools.dev_tools import ALL_DEV_TOOLS
from src.tools.project_tools import ALL_PROJECT_TOOLS
from src.tools.research_tools import ALL_RESEARCH_TOOLS
from src.tools.comms_tools import ALL_COMMS_TOOLS
from src.tools.finance_tools import ALL_FINANCE_TOOLS
from src.tools.calendar_tools import ALL_CALENDAR_TOOLS
from src.tools.nextcloud_tools import ALL_NEXTCLOUD_TOOLS
from src.tools.memory_tools import ALL_MEMORY_TOOLS
from src.tools.monitoring_tools import ALL_MONITORING_TOOLS
from src.tools.links_tools import ALL_LINKS_TOOLS
from src.tools.steward_queue_tools import ALL_STEWARD_QUEUE_TOOLS

# Surgical dev subsets for agents that need specific dev tools without the full suite
from src.tools.dev_tools import db_query, check_syntax


def get_auditor_dev_subset() -> list:
    """Vera's dev tools — read-only DB query + syntax checking only.
    No git, no shell, no write operations."""
    return [db_query, check_syntax]


# =============================================================================
# Agent Registry — maps agent_id to tool getter
# =============================================================================

AGENT_TOOL_REGISTRY = {
    "stuart": "get_stuart_tools",
    "mercer": "get_mercer_tools",
    "archimedes": "get_builder_tools",
    "arthur": "get_analyst_tools",
    "gabe": "get_scout_tools",
    "ezra": "get_keeper_tools",
    "julian": "get_scribe_tools",
    "iris": "get_advocate_tools",
    "vera": "get_auditor_tools",
    "soren": "get_lens_tools",
}


def get_agent_tools(agent_id: str) -> list:
    """Get the appropriate tool set for any agent by ID.

    Args:
        agent_id: The agent's ID from agent.yaml (e.g., "archimedes")

    Returns:
        List of tools for that agent's role
    """
    getter_name = AGENT_TOOL_REGISTRY.get(agent_id, "get_stuart_tools")
    getter = globals().get(getter_name, get_stuart_tools)
    return getter()


# =============================================================================
# Stuart — Full Suite (The Steward)
# =============================================================================

def get_stuart_tools() -> list:
    """Full tool suite for Stuart — complete family infrastructure access."""
    return (
        ALL_SYSTEM_TOOLS
        + ALL_DEV_TOOLS
        + ALL_PROJECT_TOOLS
        + ALL_RESEARCH_TOOLS
        + ALL_COMMS_TOOLS
        + ALL_FINANCE_TOOLS
        + ALL_CALENDAR_TOOLS
        + ALL_NEXTCLOUD_TOOLS
        + ALL_MEMORY_TOOLS
        + ALL_LINKS_TOOLS
        + ALL_STEWARD_QUEUE_TOOLS
    )


# =============================================================================
# Mercer — The Merchant (eBay/surplus operations)
# =============================================================================

def get_mercer_tools() -> list:
    """Tools for Mercer: commerce ops — pricing research, marketplace comms,
    finance, file access, and memory for past transactions."""
    return (
        ALL_PROJECT_TOOLS
        + ALL_RESEARCH_TOOLS
        + ALL_COMMS_TOOLS
        + ALL_FINANCE_TOOLS
        + ALL_NEXTCLOUD_TOOLS
        + ALL_MEMORY_TOOLS
    )


# =============================================================================
# Archimedes — The Builder (technical implementation)
# =============================================================================

def get_builder_tools() -> list:
    """System + dev + memory tools for Archimedes — builds infrastructure,
    recalls past build decisions and specs."""
    return ALL_SYSTEM_TOOLS + ALL_DEV_TOOLS + ALL_PROJECT_TOOLS + ALL_MEMORY_TOOLS


# =============================================================================
# Arthur — The Analyst (data analysis, signal qualification)
# =============================================================================

def get_analyst_tools() -> list:
    """Research + memory + project tools for Arthur — analyzes and qualifies signals."""
    return ALL_RESEARCH_TOOLS + ALL_MEMORY_TOOLS + ALL_PROJECT_TOOLS


# =============================================================================
# Gabe — The Scout (research and information gathering)
# =============================================================================

def get_scout_tools() -> list:
    """Research + memory + project tools for Gabe — scouts raw information,
    logs findings against tasks."""
    return ALL_RESEARCH_TOOLS + ALL_MEMORY_TOOLS + ALL_PROJECT_TOOLS


# =============================================================================
# Ezra — The Keeper (knowledge management)
# =============================================================================

def get_keeper_tools() -> list:
    """Memory + Nextcloud + project tools for Ezra — maintains institutional knowledge."""
    return ALL_MEMORY_TOOLS + ALL_NEXTCLOUD_TOOLS + ALL_PROJECT_TOOLS + ALL_RESEARCH_TOOLS


# =============================================================================
# Julian — The Scribe (written content and documentation)
# =============================================================================

def get_scribe_tools() -> list:
    """Project + memory + research + comms + nextcloud tools for Julian —
    drafts documents, reads reference material, saves to vault."""
    return (
        ALL_PROJECT_TOOLS
        + ALL_MEMORY_TOOLS
        + ALL_RESEARCH_TOOLS
        + ALL_COMMS_TOOLS
        + ALL_NEXTCLOUD_TOOLS
    )


# =============================================================================
# Iris — The Advocate (external communications)
# =============================================================================

def get_advocate_tools() -> list:
    """Comms + nextcloud + project + memory tools for Iris — manages external
    relationships, recalls past correspondence and commitments."""
    return ALL_COMMS_TOOLS + ALL_NEXTCLOUD_TOOLS + ALL_PROJECT_TOOLS + ALL_MEMORY_TOOLS


# =============================================================================
# Vera — The Auditor (final review gate)
# =============================================================================

def get_auditor_tools() -> list:
    """Project + memory + research tools for Vera — reviews output before it leaves.
    Gets surgical dev subset (db_query + check_syntax) for independent verification."""
    return ALL_PROJECT_TOOLS + ALL_MEMORY_TOOLS + ALL_RESEARCH_TOOLS + get_auditor_dev_subset()


# =============================================================================
# Soren — The Lens (performance tracking and metrics)
# =============================================================================

def get_lens_tools() -> list:
    """Project + memory + monitoring tools for Soren — tracks patterns, metrics,
    and accountability. Gets db_query for direct DB reads, no other dev tools."""
    return ALL_PROJECT_TOOLS + ALL_MEMORY_TOOLS + ALL_MONITORING_TOOLS + [db_query]


# =============================================================================
# Utility Functions
# =============================================================================

def get_tool_summary(agent_id: str = "stuart") -> dict:
    """Get tools organized by approval tier for a specific agent.

    Args:
        agent_id: Which agent to summarize (default: stuart)

    Returns dict with keys 'auto', 'notify', 'block' —
    each containing a list of tool names.
    """
    tools = get_agent_tools(agent_id)
    return tier_summary(tools)


def get_tool_count(agent_id: str = "stuart") -> dict:
    """Quick count of tools available to a specific agent."""
    tools = get_agent_tools(agent_id)
    return {
        "agent_id": agent_id,
        "total": len(tools),
        "by_module": {
            "system": len([t for t in tools if t in ALL_SYSTEM_TOOLS]),
            "dev": len([t for t in tools if t in ALL_DEV_TOOLS]),
            "project": len([t for t in tools if t in ALL_PROJECT_TOOLS]),
            "research": len([t for t in tools if t in ALL_RESEARCH_TOOLS]),
            "comms": len([t for t in tools if t in ALL_COMMS_TOOLS]),
            "finance": len([t for t in tools if t in ALL_FINANCE_TOOLS]),
            "calendar": len([t for t in tools if t in ALL_CALENDAR_TOOLS]),
            "nextcloud": len([t for t in tools if t in ALL_NEXTCLOUD_TOOLS]),
            "memory": len([t for t in tools if t in ALL_MEMORY_TOOLS]),
            "monitoring": len([t for t in tools if t in ALL_MONITORING_TOOLS]),
        }
    }


def list_agent_tools(agent_id: str = "stuart") -> list:
    """List all tool names available to a specific agent."""
    tools = get_agent_tools(agent_id)
    return [getattr(t, 'name', str(t)) for t in tools]
