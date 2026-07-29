"""Hybrid execution in qualification: deterministic hard filters short-circuit
before any LLM call; only ICP-passing leads get an LLM-judged fit score."""
from __future__ import annotations

from app.agents.qualification import (
    _hard_filter_node,
    build_qualification_graph,
    score_from_state,
)


def test_hard_filter_rejects_small_company_deterministically():
    state = _hard_filter_node({
        "company_size": 3, "industry": "software",
        "icp_min_company_size": 50, "icp_industries": [],
    })
    assert state["hard_filter_passed"] is False
    assert "company_size" in state["hard_filter_reason"]


def test_hard_filter_rejects_off_icp_industry():
    state = _hard_filter_node({
        "company_size": 500, "industry": "agriculture",
        "icp_min_company_size": 50, "icp_industries": ["software", "finance"],
    })
    assert state["hard_filter_passed"] is False
    assert "industry" in state["hard_filter_reason"]


def test_disqualified_lead_is_decided_by_code_not_llm():
    graph = build_qualification_graph(llm=None)
    final = graph.invoke({
        "company_size": 2, "industry": "software", "message": "hi",
        "icp_min_company_size": 50, "icp_industries": [], "threshold": 60,
    })
    result = score_from_state(final, threshold=60)
    # A hard-filtered lead never involves the model.
    assert result.qualified is False
    assert result.decided_by == "code"
    assert result.score == 0


def test_strong_lead_qualifies_offline():
    graph = build_qualification_graph(llm=None)  # heuristic fit
    final = graph.invoke({
        "company_size": 300, "industry": "software",
        "message": "We have budget and want a demo and pricing for a team of 40 this quarter.",
        "icp_min_company_size": 50, "icp_industries": ["software"], "threshold": 60,
    })
    result = score_from_state(final, threshold=60)
    assert result.hard_filter_passed is True
    assert result.qualified is True
    assert result.score >= 60
    # Offline (no LLM): the audit trail must attribute fit to code, not the model.
    assert result.decided_by == "code"
