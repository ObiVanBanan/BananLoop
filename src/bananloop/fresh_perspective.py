"""Opt-in fresh-context proposal lane inspired by ledger-based orchestration.

Only a proposal is generated here. The supervisor owns selection, budget and
evaluation; the second agent sees the same code scope but no historical DEV
lessons that could anchor it to a failing strategy.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import HypothesisPortfolio


_FRESH_KEYS = (
    "schema_version", "role", "goal", "project_context",
    "primary_metric", "guardrails", "non_regression", "editable_scope",
    "protected_paths", "agent_visible_training_paths",
    "baseline", "champion", "champion_snapshot", "portfolio_contract",
    "experiment_id",
)


def should_request_fresh(
    task: dict[str, Any], *, enabled: bool,
    remaining_agent_calls: int, action_already_reserved: bool = False,
) -> bool:
    """Reserve a new worker only if the implementer still has one call left."""
    return (
        enabled and task.get("role") == "propose"
        and task.get("planning_mode") == "reframe"
        and (action_already_reserved or remaining_agent_calls >= 2)
    )


def build_fresh_proposal_task(task: dict[str, Any]) -> dict[str, Any]:
    """Construct a strict allowlist view; never forward the normal research ledger."""
    if task.get("role") != "propose":
        raise ValueError("fresh perspective only supports propose")
    fresh = {key: deepcopy(task[key]) for key in _FRESH_KEYS if key in task}
    fresh["fresh_perspective"] = True
    fresh["planning_mode"] = "reframe"
    fresh["history"] = []
    fresh["research_state"] = {
        "planning_mode": "reframe",
        "experiment_evidence": [],
        "failure_signals": [],
        "recent_failure_capsules": [],
        "mechanisms": [],
    }
    fresh["planning_requirements"] = {
        "reframe": "Find an alternative mechanism from repository evidence, not previous proposals.",
        "evidence": "Use INSUFFICIENT_EVIDENCE when no DEV evidence is supplied.",
    }
    return fresh



def grounding_task_for_candidate(
    task: dict[str, Any], *, mechanism_key: str,
    independent_mechanism_key: str | None,
) -> dict[str, Any]:
    """Exempt only the ledger-isolated candidate from mandatory old DEV signals.

    Other gates remain enforced. This is source-bound: the core identifies the
    exact candidate supplied by its separate fresh-proposal action, not by a
    model-controlled flag in the hypothesis.
    """
    if mechanism_key != independent_mechanism_key or independent_mechanism_key is None:
        return task
    independent_task = deepcopy(task)
    state = dict(independent_task.get("research_state") or {})
    state["failure_signals"] = []
    independent_task["research_state"] = state
    return independent_task


def merge_proposal_portfolios(
    primary: HypothesisPortfolio, fresh: HypothesisPortfolio,
) -> HypothesisPortfolio:
    """Retain up to three main candidates plus one independent mechanism.

    When the fresh pass offers no new mechanism or candidate ID, keep the
    original portfolio entirely. All candidates still go through core gates.
    """
    original = list(primary.candidates)
    original_ids = {candidate.candidate_id for candidate in original}
    original_mechanisms = {candidate.hypothesis.mechanism_key for candidate in original}
    independent = next(
        (candidate for candidate in fresh.candidates
         if candidate.candidate_id not in original_ids
         and candidate.hypothesis.mechanism_key not in original_mechanisms),
        None,
    )
    combined = original[:3] + [independent] if independent is not None else original
    return HypothesisPortfolio(
        candidates=combined,
        malformed_candidates=[
            *primary.malformed_candidates,
            *fresh.malformed_candidates,
        ][:4],
    )
