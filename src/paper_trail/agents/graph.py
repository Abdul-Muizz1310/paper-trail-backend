"""LangGraph assembly — debate StateGraph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from paper_trail.agents.nodes import judge as judge_mod
from paper_trail.agents.nodes import plan as plan_mod
from paper_trail.agents.nodes import proponent as proponent_mod
from paper_trail.agents.nodes import render as render_mod
from paper_trail.agents.nodes import skeptic as skeptic_mod
from paper_trail.agents.state import DebateState


async def _plan_node(state: DebateState) -> dict[str, Any]:
    return await plan_mod.plan(state)


async def _proponent_node(state: DebateState) -> dict[str, Any]:
    return await proponent_mod.proponent(state)


async def _skeptic_node(state: DebateState) -> dict[str, Any]:
    return await skeptic_mod.skeptic(state)


async def _judge_node(state: DebateState) -> dict[str, Any]:
    return await judge_mod.judge(state)


async def _render_node(state: DebateState) -> dict[str, Any]:
    return await render_mod.render(state)


def _route_after_plan(state: DebateState) -> list[Send]:
    """Fan out to proponent and skeptic in parallel via Send."""
    return [Send("proponent", state), Send("skeptic", state)]


def _route_after_judge(state: DebateState) -> list[Send] | str:
    """Loop back to proponent+skeptic or proceed to render."""
    from paper_trail.agents.state import is_converged

    if is_converged(state) or not state.get("need_more"):
        return "render"
    return [Send("proponent", state), Send("skeptic", state)]


def build_graph() -> Any:
    """Build and compile the debate StateGraph.

    Topology:
        START -> plan -> (proponent || skeptic) -> judge
              -> (need_more ? back to proponent+skeptic : render) -> END
    """
    g: StateGraph[DebateState, Any, DebateState, DebateState] = StateGraph(DebateState)
    g.add_node("plan", _plan_node)
    g.add_node("proponent", _proponent_node)
    g.add_node("skeptic", _skeptic_node)
    g.add_node("judge", _judge_node)
    g.add_node("render", _render_node)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _route_after_plan, ["proponent", "skeptic"])
    g.add_edge("proponent", "judge")
    g.add_edge("skeptic", "judge")
    g.add_conditional_edges(
        "judge",
        _route_after_judge,
        ["proponent", "skeptic", "render"],
    )
    g.add_edge("render", END)
    return g.compile()
