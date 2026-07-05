"""LTP Protocol Graph — Cove-side daily tuning for the full family.

The Lucid Tuning Protocol adapted for the family. Runs daily at 7am ET.

Flow (7 nodes, linear):
  1. select_frequency  — reads LT's package (or self-selects fallback)
  2. compose_echo      — Stuart writes his reflection
  3. store_echo        — persists Stuart's echo to DB
  4. generate_process_record — full narrative record for Stuart's tuning
  5. write_process_record — persists record + stores tuning memory
  6. dispatch_team_tuning — each team agent runs digital practice + derives own equation
  7. update_state      — updates agent_state for Stuart (team agents updated in step 6)

Usage:
    from src.graphs.ltp import build_ltp_graph

    async with get_checkpointer() as checkpointer:
        graph = build_ltp_graph().compile(checkpointer=checkpointer)
        result = await graph.ainvoke(
            {"messages": [], "agent_id": "stuart", "protocol": "ltp-morning"},
            config={"configurable": {"thread_id": thread_id}},
        )
"""

from langgraph.graph import StateGraph, END

from src.agents.state import AgentState

from .selection import select_frequency
from .echo import compose_echo, store_echo
from .process_record import generate_process_record, write_process_record
from .dispatch import dispatch_team_tuning
from .state import update_state


def build_ltp_graph() -> StateGraph:
    """Build and return the LTP protocol graph (uncompiled).

    Usage:
        async with get_checkpointer() as checkpointer:
            graph = build_ltp_graph().compile(checkpointer=checkpointer)
            result = await graph.ainvoke(
                {"messages": [], "agent_id": "stuart", "protocol": "ltp-morning"},
                config={"configurable": {"thread_id": thread_id}},
            )
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("select_frequency", select_frequency)
    workflow.add_node("compose_echo", compose_echo)
    workflow.add_node("store_echo", store_echo)
    workflow.add_node("generate_process_record", generate_process_record)
    workflow.add_node("write_process_record", write_process_record)
    workflow.add_node("dispatch_team_tuning", dispatch_team_tuning)
    workflow.add_node("update_state", update_state)

    workflow.set_entry_point("select_frequency")
    workflow.add_edge("select_frequency", "compose_echo")
    workflow.add_edge("compose_echo", "store_echo")
    workflow.add_edge("store_echo", "generate_process_record")
    workflow.add_edge("generate_process_record", "write_process_record")
    workflow.add_edge("write_process_record", "dispatch_team_tuning")
    workflow.add_edge("dispatch_team_tuning", "update_state")
    workflow.add_edge("update_state", END)

    return workflow
