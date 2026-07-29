"""Qualification agent (LangGraph) — the hybrid-execution centerpiece.

Flow: **hard filters** (deterministic code) -> if they fail, disqualify
immediately without ever calling the LLM. Only leads that clear the objective
ICP bar reach **conversational fit** (LLM-judged, with an offline heuristic
fallback). The final **score** and qualify/not decision are computed in code.

Why this split matters: company size and industry are binary-correct facts — you
never want an LLM "deciding" whether a 3-person company clears a 50-employee
floor. The model only judges the soft, linguistic signal (does the message sound
like a real, in-market buyer?).
"""
from __future__ import annotations

import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.llm.client import LLMClient
from app.schemas import QualificationResult

# Weighting: the combined score blends the deterministic ICP pass with the
# LLM-judged conversational fit.
_HARD_PASS_BASE = 40  # points awarded for clearing the objective ICP bar
_FIT_WEIGHT = 0.6     # remaining 60 points come from conversational fit

_FIT_SYSTEM = (
    "You score how well an inbound sales lead's message signals a real, in-market "
    "buyer, from 0 to 100. Reply with ONLY an integer. Higher = stronger intent, "
    "specific need, budget/authority signals. Vague or spammy = low."
)


class QualState(TypedDict, total=False):
    company_size: int
    industry: str
    message: str
    icp_min_company_size: int
    icp_industries: list[str]
    threshold: int
    # filled by nodes:
    hard_filter_passed: bool
    hard_filter_reason: str
    conversational_fit: int
    decided_by: str


def _hard_filter_node(state: QualState) -> QualState:
    """Deterministic ICP gate. No LLM."""
    size = state.get("company_size") or 0
    industry = (state.get("industry") or "").strip().lower()
    allowed = [i.strip().lower() for i in state.get("icp_industries", []) if i.strip()]

    if size < state["icp_min_company_size"]:
        floor = state["icp_min_company_size"]
        return {**state, "hard_filter_passed": False,
                "hard_filter_reason": f"company_size {size} < ICP min {floor}"}
    if allowed and industry not in allowed:
        return {**state, "hard_filter_passed": False,
                "hard_filter_reason": f"industry '{industry or 'unknown'}' not in ICP {allowed}"}
    return {**state, "hard_filter_passed": True, "hard_filter_reason": "ICP hard filters passed"}


def _route_after_hard_filter(state: QualState) -> str:
    return "fit" if state.get("hard_filter_passed") else END


def _fit_node(state: QualState, llm: LLMClient | None) -> QualState:
    message = (state.get("message") or "").strip()
    if llm is None:
        # Offline heuristic: this is deterministic code, so the audit trail must
        # say "code" here, not "llm" — the compliance record has to be honest.
        return {**state, "conversational_fit": _heuristic_fit(message), "decided_by": "code"}
    resp = llm.complete(system=_FIT_SYSTEM, prompt=message or "(no message provided)", max_tokens=8)
    return {**state, "conversational_fit": _parse_score(resp.text), "decided_by": "llm"}


def build_qualification_graph(llm: LLMClient | None):
    graph = StateGraph(QualState)
    graph.add_node("hard_filter", _hard_filter_node)
    graph.add_node("fit", lambda s: _fit_node(s, llm))
    graph.set_entry_point("hard_filter")
    graph.add_conditional_edges("hard_filter", _route_after_hard_filter, {"fit": "fit", END: END})
    graph.add_edge("fit", END)
    return graph.compile()


def score_from_state(state: QualState, threshold: int) -> QualificationResult:
    """Combine the deterministic pass + conversational fit into a final score.
    Pure code — the qualify/not decision is never delegated to the LLM."""
    if not state.get("hard_filter_passed"):
        return QualificationResult(
            score=0, qualified=False, hard_filter_passed=False, conversational_fit=0,
            notes=state.get("hard_filter_reason", "hard filter failed"), decided_by="code",
        )
    fit = int(state.get("conversational_fit", 0))
    score = round(_HARD_PASS_BASE + _FIT_WEIGHT * fit)
    score = max(0, min(100, score))
    qualified = score >= threshold
    # Attribute to whoever actually judged fit (LLM online, code offline).
    return QualificationResult(
        score=score, qualified=qualified, hard_filter_passed=True, conversational_fit=fit,
        notes=f"ICP passed; conversational fit {fit}/100; score {score} vs threshold {threshold}",
        decided_by=state.get("decided_by", "code"),
    )


def _parse_score(text: str) -> int:
    # First integer in the reply (so "45 out of 100" -> 45, not 45100).
    match = re.search(r"\d+", text)
    if not match:
        return 0
    return max(0, min(100, int(match.group())))


def _heuristic_fit(message: str) -> int:
    """Offline fallback for conversational fit: reward buying-intent signals."""
    if not message:
        return 20
    text = message.lower()
    signals = ["pricing", "price", "demo", "buy", "budget", "trial", "evaluate",
               "compare", "team of", "seats", "onboard", "contract", "quote", "roi"]
    hits = sum(1 for s in signals if s in text)
    length_bonus = min(20, len(text) // 40)
    return min(100, 25 + hits * 15 + length_bonus)
