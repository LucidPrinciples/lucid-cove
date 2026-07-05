"""
Calendar Tools — stub module.

SUPERSEDED: Real calendar tools are now in nextcloud_tools.py
(calendar_list_events, calendar_create_event via Nextcloud CalDAV).

This module remains for import compatibility. ALL_CALENDAR_TOOLS is empty
to avoid double-registering calendar tools that already live in nextcloud_tools.

If a Google Calendar integration is ever needed alongside Nextcloud,
implement it here and register in agent_tools.py.
"""

ALL_CALENDAR_TOOLS = []  # Real calendar tools in nextcloud_tools.py
TOOLS = ALL_CALENDAR_TOOLS  # alias for cove-core channels.py loader
