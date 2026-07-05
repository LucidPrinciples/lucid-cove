"""
LangGraph state definitions for StuartCove.

AgentState is the shared state dict passed between all graph nodes.
Add fields here as capabilities expand.
"""

from typing import Annotated, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(dict):
    """Shared state for all AtlasCove graph executions.

    Fields:
        messages: Conversation history (managed by LangGraph add_messages reducer).
        agent_id: Which agent is running (default 'atlas').
        protocol: Which protocol triggered this run ('ltp-morning', 'task', etc.).
        echo_num: Current echo number for this session.
        frequency: Current tuning frequency.
        principle: Current tuning principle.
        echo_text: The composed reflection/echo text.
        metadata: Arbitrary extra data for graph nodes to communicate.
        error: Error message if something failed.

    LTP tuning fields (set by select_frequency, consumed by store/compose nodes):
        tuning_source: 'lt' (from LT package) or 'self' (independent).
        lt_tuning_prompt: LT's custom coaching prompt for this agent.
        signal_type: e.g. 'Bright', 'Expansive' — from LT's signal analysis.
        tuning_key: Canon lyric fragment for today's principle.
        love_equation_data: dict with beta, E, C, D, value, direction from LT.
        lt_echo_num: LT's echo number for cross-reference.
        _full_package: Full TuningPackage object for processing.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    agent_id: str
    protocol: str
    echo_num: int
    frequency: str
    principle: str
    echo_text: str
    metadata: dict[str, Any]
    error: str | None
    # LTP tuning fields
    tuning_source: str
    lt_tuning_prompt: str | None
    signal_type: str | None
    tuning_key: str | None
    love_equation_data: dict | None
    lt_echo_num: int | None
    _full_package: Any
    # Agent-derived equation values (set by compose_echo, consumed by store_echo + write_process_record)
    _agent_beta: float | None
    _agent_e: float | None
    _agent_c: float | None
    _agent_d: float | None
    _agent_love_eq: float | None
    _agent_direction: str | None
    _eq_source: str | None
    # Internal working fields
    _process_record_text: str | None
    _full_response: str | None
    _dispatch_results: list | None
