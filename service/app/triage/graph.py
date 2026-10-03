"""Reply triage as a LangGraph pipeline: classify -> decide.

The graph is the reasoning: it reads the reply and returns a classification
and a plan. Carrying the plan out (booking, emails, database) is the worker's
job (app.workers.triage), so the graph can be run on test replies, e.g. by the
accuracy check, without touching anything.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.triage.classify import TriageResult, classify_reply
from app.triage.plan import Plan, decide


class TriageState(TypedDict, total=False):
    subject: str
    body: str
    event_type: str
    lead_timezone: str
    today: date
    last_sent: Optional[dict]
    offered: list[str]
    forced_category: Optional[str]
    has_meeting: bool
    min_confidence: float
    result: TriageResult
    plan: Plan


async def classify_node(state: TriageState) -> dict[str, Any]:
    result = await classify_reply(
        state.get("subject", ""),
        state.get("body", ""),
        lead_timezone=state.get("lead_timezone") or "UTC",
        today=state.get("today") or date.today(),
        last_sent=state.get("last_sent"),
        offered=state.get("offered") or None,
        forced_category=state.get("forced_category"),
    )
    return {"result": result}


async def decide_node(state: TriageState) -> dict[str, Any]:
    r = state["result"]
    plan = decide(
        r.category, r.confidence, r.extracted,
        min_confidence=state.get("min_confidence", 0.7),
        event_type=state.get("event_type", "reply"),
        offered_count=len(state.get("offered") or []),
        has_meeting=bool(state.get("has_meeting")),
    )
    # decide() may settle the category (e.g. header-detected auto-replies).
    if plan.category != r.category:
        r.category = plan.category
    return {"plan": plan}


@lru_cache(maxsize=1)
def build_triage_graph():
    g = StateGraph(TriageState)
    g.add_node("classify", classify_node)
    g.add_node("decide", decide_node)
    g.add_edge(START, "classify")
    g.add_edge("classify", "decide")
    g.add_edge("decide", END)
    return g.compile()


async def triage(state: TriageState) -> TriageState:
    return await build_triage_graph().ainvoke(state)
