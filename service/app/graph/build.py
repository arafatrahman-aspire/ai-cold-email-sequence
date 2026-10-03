"""Assemble the LangGraph pipeline: persona -> draft -> validate (-> retry)."""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph.draft import draft_node
from app.graph.persona import persona_node
from app.graph.state import SequenceState
from app.graph.validate import route_after_validation, validate_node


@lru_cache(maxsize=1)
def build_sequence_graph():
    graph = StateGraph(SequenceState)

    graph.add_node("persona", persona_node)
    graph.add_node("draft", draft_node)
    graph.add_node("validate", validate_node)

    graph.add_edge(START, "persona")
    graph.add_edge("persona", "draft")
    graph.add_edge("draft", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {"retry": "draft", "end": END},
    )

    return graph.compile()


async def generate_sequence(lead: dict, step_count: int = 4) -> SequenceState:
    """Run the full graph for one lead."""
    graph = build_sequence_graph()
    initial: SequenceState = {
        "lead": lead,
        "step_count": step_count,
        "draft_attempts": 0,
        "validation_errors": [],
        "emails": [],
    }
    result = await graph.ainvoke(initial)
    # A run that exits without an explicit status has failed structurally.
    if "status" not in result:
        result["status"] = "failed"
    return result
