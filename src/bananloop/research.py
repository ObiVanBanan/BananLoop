from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from .contracts import CampaignSpec, Hypothesis, MechanismEvidence, ResearchState


def planning_mode(non_improve_streak: int, plateau_experiments: int) -> str:
    """Choose a deterministic research strategy from durable campaign evidence."""
    if non_improve_streak <= 0:
        return "exploit"
    if non_improve_streak >= max(1, plateau_experiments - 1):
        return "reframe"
    return "explore"


def agent_evaluation_view(
    spec: CampaignSpec,
    payload_json: str | None,
) -> dict[str, Any] | None:
    """Return only DEV evidence explicitly allowed into adaptive agent context."""
    if not payload_json:
        return None
    payload = json.loads(payload_json)
    payload["artifacts"] = []

    visible_metrics = set(spec.agent_visible_metrics)
    if visible_metrics:
        payload["metrics"] = {
            name: value
            for name, value in (payload.get("metrics") or {}).items()
            if name in visible_metrics
        }

    visible_diagnostics = set(spec.agent_visible_diagnostics)
    diagnostics = payload.get("diagnostics") or {}
    payload["diagnostics"] = {
        name: value
        for name, value in diagnostics.items()
        if name in visible_diagnostics and isinstance(value, (int, float)) and math.isfinite(float(value))
    }
    return payload


def _dev_decision_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("dev_decision_json"):
        return json.loads(row["dev_decision_json"])
    if not row.get("decision_json"):
        return None

    # Compatibility for campaigns created before dev_decision_json existed.
    # VALIDATE is entered only after a DEV acceptance, so a combined decision
    # with a validation payload can be safely reconstructed at the DEV boundary
    # without exposing validation evidence.
    decision = json.loads(row["decision_json"])
    had_validation = "validation" in decision
    decision.pop("validation", None)
    predicates = dict(decision.get("predicates") or {})
    predicates.pop("validation", None)
    decision["predicates"] = predicates
    decision["reasons"] = [
        reason
        for reason in (decision.get("reasons") or [])
        if reason != "VALIDATION_REJECTED"
        and reason != "INVALID_VALIDATION"
        and not reason.startswith("VALIDATION:")
    ]
    if had_validation:
        decision["accepted"] = True
        if "ACCEPTED" not in decision["reasons"]:
            decision["reasons"].append("ACCEPTED")
    return decision


def agent_decision_view(
    spec: CampaignSpec,
    decision: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if decision is None:
        return None

    visible = set(spec.agent_visible_metrics)
    if not visible:
        return decision

    primary_visible = spec.primary_metric.name in visible
    view: dict[str, Any] = {"accepted": bool(decision.get("accepted"))}
    if primary_visible:
        for name in ("primary_value", "champion_value", "delta"):
            if name in decision:
                view[name] = decision[name]

    predicates: dict[str, bool] = {}
    for key, value in (decision.get("predicates") or {}).items():
        if key == "minimum_improvement":
            if primary_visible:
                predicates[key] = bool(value)
        elif key.startswith("check:"):
            predicates[key] = bool(value)
        elif key.startswith("guard:") and any(
            key.startswith(f"guard:{metric}") for metric in visible
        ):
            predicates[key] = bool(value)
        elif key.startswith("non_regression:") and any(
            key.startswith(f"non_regression:{metric}:") for metric in visible
        ):
            predicates[key] = bool(value)
    view["predicates"] = predicates

    reasons: list[str] = []
    for reason in decision.get("reasons") or []:
        if reason == "ACCEPTED" or reason.startswith("CHECK_FAILED:"):
            reasons.append(reason)
        elif reason == "PRIMARY_NOT_IMPROVED" and primary_visible:
            reasons.append(reason)
        elif reason.startswith("GUARDRAIL_FAILED:"):
            metric = reason.split(":", 1)[1]
            if metric in visible:
                reasons.append(reason)
        elif reason.startswith("REGRESSION:"):
            metric = reason.split(":", 1)[1]
            if metric in visible:
                reasons.append(reason)
    view["reasons"] = reasons
    return view


def classify_dev_outcome(
    spec: CampaignSpec,
    row: dict[str, Any],
) -> str:
    decision = agent_decision_view(spec, _dev_decision_from_row(row))
    if row.get("evaluation_json") and decision is not None:
        if decision.get("accepted"):
            return "DEV_IMPROVEMENT"
        reasons = decision.get("reasons") or []
        if "PRIMARY_NOT_IMPROVED" in reasons:
            return "DEV_NO_EFFECT"
        if any(
            reason.startswith(("GUARDRAIL_FAILED:", "REGRESSION:", "CHECK_FAILED:"))
            for reason in reasons
        ):
            return "DEV_GUARD_REJECT"
        return "DEV_REJECTED"

    failure = row.get("failure_category")
    if failure in {"SCOPE_VIOLATION", "EVALUATOR_CHANGED", "LEAKAGE_GUARD"}:
        return "PRE_EVAL_REJECT"
    if failure == "REDUNDANT_HYPOTHESIS":
        return "REDUNDANT_HYPOTHESIS"
    if failure == "NO_HYPOTHESIS":
        return "NO_HYPOTHESIS"
    if failure:
        return "OPERATIONAL_FAILURE"
    return "UNCLASSIFIED"


def _lesson_for(outcome: str, mechanism: str) -> str:
    messages = {
        "DEV_IMPROVEMENT": "DEV evidence showed a measurable improvement signal.",
        "DEV_NO_EFFECT": "DEV evidence did not meet the required minimum improvement.",
        "DEV_GUARD_REJECT": "DEV evidence violated an approved guardrail or mandatory check.",
        "DEV_REJECTED": "DEV evidence rejected the candidate without a visible causal category.",
        "PRE_EVAL_REJECT": "The candidate was rejected before DEV measurement by deterministic policy.",
        "REDUNDANT_HYPOTHESIS": "The mechanism duplicated prior work without an explicit genealogy relation.",
        "OPERATIONAL_FAILURE": "The attempt failed operationally and is not scientific evidence against the mechanism.",
        "NO_HYPOTHESIS": "The proposer reported no useful bounded hypothesis.",
        "UNCLASSIFIED": "No classified DEV evidence is available.",
    }
    return f"{mechanism}: {messages[outcome]}"


def build_history(
    spec: CampaignSpec,
    experiments: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for row in experiments[-limit:]:
        if row.get("status") == "ACTIVE":
            continue
        hypothesis = json.loads(row["hypothesis_json"]) if row.get("hypothesis_json") else None
        decision = agent_decision_view(spec, _dev_decision_from_row(row))
        outcome = classify_dev_outcome(spec, row)
        history.append(
            {
                "ordinal": row["ordinal"],
                "hypothesis": hypothesis,
                "dev_outcome": outcome,
                "dev_evaluation": agent_evaluation_view(spec, row.get("evaluation_json")),
                "dev_decision": decision,
                "lesson": _lesson_for(
                    outcome,
                    (hypothesis or {}).get("mechanism_key", "unknown"),
                ),
            }
        )
    return history


def build_research_state(
    spec: CampaignSpec,
    campaign: dict[str, Any],
    experiments: list[dict[str, Any]],
) -> ResearchState:
    mechanism_rows: dict[str, dict[str, Any]] = {}
    failure_counts: Counter[str] = Counter()
    lessons: list[str] = []

    for row in experiments:
        if row.get("status") == "ACTIVE" or not row.get("hypothesis_json"):
            continue
        hypothesis = json.loads(row["hypothesis_json"])
        key = hypothesis["mechanism_key"]
        outcome = classify_dev_outcome(spec, row)
        failure_counts[outcome] += 1
        item = mechanism_rows.setdefault(
            key,
            {
                "mechanism_key": key,
                "experiment_ordinals": [],
                "dev_outcomes": {},
                "last_relation": None,
            },
        )
        item["experiment_ordinals"].append(int(row["ordinal"]))
        item["dev_outcomes"][outcome] = item["dev_outcomes"].get(outcome, 0) + 1
        item["last_relation"] = hypothesis.get("relation", "new")
        lessons.append(_lesson_for(outcome, key))

    remaining = {
        "experiments": max(0, spec.budget.max_experiments - int(campaign["experiment_count"])),
        "attempts": max(0, spec.budget.max_attempts - int(campaign["attempt_count"])),
        "agent_calls": max(0, spec.budget.max_agent_calls - int(campaign["agent_calls"])),
        "evaluator_calls": max(0, spec.budget.max_evaluator_calls - int(campaign["evaluator_calls"])),
        "experiments_until_plateau": max(
            0,
            spec.budget.plateau_experiments - int(campaign["non_improve_streak"]),
        ),
    }
    return ResearchState(
        planning_mode=planning_mode(
            int(campaign["non_improve_streak"]),
            spec.budget.plateau_experiments,
        ),
        non_improve_streak=int(campaign["non_improve_streak"]),
        failure_counts=dict(sorted(failure_counts.items())),
        mechanisms=[
            MechanismEvidence.model_validate(value)
            for _, value in sorted(mechanism_rows.items())
        ],
        recent_lessons=lessons[-8:],
        remaining_budget=remaining,
    )


def redundancy_reason(
    hypothesis: Hypothesis,
    experiments: list[dict[str, Any]],
) -> str | None:
    same_mechanism: list[int] = []
    target = hypothesis.mechanism_key.casefold().strip()
    for row in experiments:
        if not row.get("hypothesis_json"):
            continue
        previous = json.loads(row["hypothesis_json"])
        if previous.get("mechanism_key", "").casefold().strip() == target:
            same_mechanism.append(int(row["ordinal"]))

    if not same_mechanism:
        return None
    if (
        hypothesis.relation in {"refine", "retry", "supersede"}
        and set(hypothesis.prior_experiment_refs).intersection(same_mechanism)
    ):
        return None
    return (
        f"mechanism_key {hypothesis.mechanism_key!r} already appeared in experiments "
        f"{same_mechanism}; use relation=refine|retry|supersede and reference a prior ordinal"
    )
