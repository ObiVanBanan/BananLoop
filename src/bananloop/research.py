from __future__ import annotations

import json
import math
import fnmatch
import re
from collections import Counter
from typing import Any

from .budget import deadline_reached
from .contracts import CampaignSpec, FailureCapsule, Hypothesis, MechanismEvidence, ResearchState


def attempt_bucket(row: dict[str, Any]) -> str:
    """Classify current outcome, including legacy REJECTED operational rows."""
    status = row.get("status")
    failure = row.get("failure_category")
    decision = json.loads(row.get("decision_json") or row.get("dev_decision_json") or "{}")
    if (
        status == "ACCEPTED"
        and row.get("evaluation_json")
        and decision.get("accepted") is True
    ):
        # A completed acceptance is authoritative. Legacy versions could leave
        # a stale operational failure category on the experiment row.
        return "scientific_trials"
    categories = {
        "NO_CHANGES": "no_changes",
        "IMPLEMENTATION_BLOCKED": "implementation_blocked",
        "UNGROUNDED_HYPOTHESIS": "ungrounded",
        "UNGROUNDED": "ungrounded",
        "REDUNDANT_HYPOTHESIS": "redundant",
        "REDUNDANT": "redundant",
        "NO_HYPOTHESIS": "no_hypothesis",
        "SCOPE_VIOLATION": "pre_evaluation_rejected",
        "LEAKAGE_GUARD": "pre_evaluation_rejected",
        "PRE_EVALUATION_REJECTED": "pre_evaluation_rejected",
        "INVALID_EVALUATION": "infra_failures",
        "INVALID_VALIDATION": "infra_failures",
        "EVALUATOR_CHANGED": "infra_failures",
        "AGENT_PROTOCOL_ERROR": "infra_failures",
        "CANDIDATE_NOT_MATERIALIZED": "infra_failures",
        "PAIRED_INPUT_MISMATCH": "infra_failures",
    }
    if failure in categories:
        return categories[failure]
    if status == "ACTIVE":
        return "active"
    if status in categories:
        return categories[status]
    if any(
        reason in {
            "INVALID_VALIDATION",
            "PAIRED_INPUT_MISMATCH",
            "VALIDATION:PAIRED_INPUT_MISMATCH",
        }
        for reason in decision.get("reasons", [])
    ):
        return "infra_failures"
    if status == "REJECTED" and row.get("evaluation_json") and decision:
        return "scientific_trials"
    if status == "CANCELLED":
        return "cancelled"
    return "other"


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
    raw = json.loads(payload_json)
    # Positive allowlist: future evaluator extensions are private by default.
    payload = {key: value for key, value in raw.items() if key in {
        "schema_version", "status", "metrics", "checks", "diagnostics"
    }}
    payload["artifacts"] = []
    # Raw per-failure DEV evidence is privileged input for the dedicated
    # analyzer role. Proposer/implementer context only receives generalized
    # capsules persisted after analysis.
    payload["failure_signals"] = []
    payload["failure_examples"] = []

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
    if attempt_bucket(row) == "infra_failures":
        return "OPERATIONAL_FAILURE"
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


def _failure_capsules_from_row(row: dict[str, Any]) -> list[FailureCapsule]:
    payload = row.get("failure_analysis_json")
    if not payload:
        return []
    try:
        data = json.loads(payload)
        return [
            FailureCapsule.model_validate(item)
            for item in (data.get("capsules") or [])
        ]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


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
                "failure_capsules": [
                    capsule.model_dump(mode="json")
                    for capsule in _failure_capsules_from_row(row)
                ],
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
    *,
    evaluation_units_reserved: float = 0.0,
) -> ResearchState:
    mechanism_rows: dict[str, dict[str, Any]] = {}
    failure_counts: Counter[str] = Counter()
    lessons: list[str] = []
    failure_capsules: list[FailureCapsule] = []
    cause_observations: list[tuple[int, FailureCapsule]] = []

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
                "canonical_areas": [],
            },
        )
        item["experiment_ordinals"].append(int(row["ordinal"]))
        item["dev_outcomes"][outcome] = item["dev_outcomes"].get(outcome, 0) + 1
        item["last_relation"] = hypothesis.get("relation", "new")
        for area in hypothesis.get("files_or_area", []):
            normalized = canonical_area(area)
            if normalized and normalized not in item["canonical_areas"]:
                item["canonical_areas"].append(normalized)
        lessons.append(_lesson_for(outcome, key))
        row_capsules = _failure_capsules_from_row(row)
        if attempt_bucket(row) == "scientific_trials":
            failure_capsules.extend(row_capsules)
            cause_observations.extend((int(row["ordinal"]), capsule) for capsule in row_capsules)

    dev_non_improve_streak = 0
    for row in experiments:
        if attempt_bucket(row) != "scientific_trials":
            continue
        outcome = classify_dev_outcome(spec, row)
        if outcome == "DEV_IMPROVEMENT":
            dev_non_improve_streak = 0
        else:
            dev_non_improve_streak += 1

    remaining = {
        "experiments": max(0, spec.budget.max_experiments - int(campaign["experiment_count"])),
        "attempts": max(0, spec.budget.max_attempts - int(campaign["attempt_count"])),
        "agent_calls": max(0, spec.budget.max_agent_calls - int(campaign["agent_calls"])),
        "evaluator_calls": max(0, spec.budget.max_evaluator_calls - int(campaign["evaluator_calls"])),
        "dev_experiments_until_reframe": max(
            0,
            spec.budget.plateau_experiments - 1 - dev_non_improve_streak,
        ),
    }
    scientific_rows = [
        row for row in experiments
        if row.get("status") != "ACTIVE" and attempt_bucket(row) == "scientific_trials"
    ]
    latest_outcome = classify_dev_outcome(spec, scientific_rows[-1]) if scientific_rows else None
    mode = planning_mode(dev_non_improve_streak, spec.budget.plateau_experiments)
    rationale = (
        f"{mode} selected from {dev_non_improve_streak} consecutive DEV non-improvements."
        if dev_non_improve_streak else "exploit selected because there is no consecutive DEV non-improvement."
    )
    if latest_outcome == "DEV_GUARD_REJECT" and mode != "reframe":
        mode = "explore"
        rationale = "Explore a different mechanism because the latest DEV candidate violated a guard/check."
    latest_cause_by_experiment: dict[int, str] = {}
    for ordinal, item in cause_observations:
        if item.confidence >= 0.6 and item.likely_mechanism.strip():
            latest_cause_by_experiment[ordinal] = item.likely_mechanism.strip().casefold()
    recent_causes = sorted(latest_cause_by_experiment.items())[-2:]
    if len(recent_causes) == 2 and recent_causes[-1][1] == recent_causes[-2][1]:
        mode = "reframe"
        rationale = "Reframe because separate DEV experiments repeated the same suspected mechanism; test a different causal explanation."
    evidence_strength = spec.evaluation_evidence.trust_level if spec.evaluation_evidence else "UNVERIFIED"
    if evidence_strength != "INDEPENDENTLY_VALIDATED":
        rationale += f" Evaluator evidence remains {evidence_strength}; treat metric conclusions as provisional."
    minimum_probe_cost = {
        "agent_calls": 2,
        "dev_evaluator_calls": 1,
        "additional_reserved_evaluator_calls": int(bool(spec.validation_command)) + int(bool(spec.holdout_command)),
        "evaluation_units": 0,
    }
    required_evaluator_calls = minimum_probe_cost["dev_evaluator_calls"] + minimum_probe_cost["additional_reserved_evaluator_calls"]
    if spec.budget.evaluation_units_per_call is not None:
        minimum_probe_cost["evaluation_units"] = required_evaluator_calls * spec.budget.evaluation_units_per_call
    deadline_available = not deadline_reached(campaign["created_at"], spec.budget)
    reframe_available = (
        remaining["experiments"] > 0
        and remaining["attempts"] > 0
        and remaining["agent_calls"] >= minimum_probe_cost["agent_calls"]
        and remaining["evaluator_calls"] >= required_evaluator_calls
        and deadline_available
        and (
            spec.budget.max_evaluation_units is None
            or evaluation_units_reserved + minimum_probe_cost["evaluation_units"] <= spec.budget.max_evaluation_units
        )
    )
    if mode == "reframe" and not reframe_available:
        rationale += " Reframe selected, but no minimum experiment budget remains for its verification."
        if not deadline_available:
            rationale += " The campaign deadline has elapsed."
    return ResearchState(
        planning_mode=mode,
        planning_rationale=rationale,
        last_scientific_outcome=latest_outcome,
        evidence_strength=evidence_strength,
        reframe_available=reframe_available,
        expected_next_test_cost=minimum_probe_cost,
        dev_non_improve_streak=dev_non_improve_streak,
        failure_counts=dict(sorted(failure_counts.items())),
        mechanisms=[
            MechanismEvidence.model_validate(value)
            for _, value in sorted(mechanism_rows.items())
        ],
        recent_lessons=lessons[-8:],
        recent_failure_capsules=failure_capsules[-16:],
        remaining_budget=remaining,
    )


def redundancy_reason(
    hypothesis: Hypothesis,
    experiments: list[dict[str, Any]],
) -> str | None:
    same_mechanism: list[tuple[int, dict[str, Any]]] = []
    target = hypothesis.mechanism_key.casefold().strip()
    for row in experiments:
        if not row.get("hypothesis_json"):
            continue
        previous = json.loads(row["hypothesis_json"])
        if previous.get("mechanism_key", "").casefold().strip() == target:
            same_mechanism.append((int(row["ordinal"]), previous))

    if not same_mechanism:
        return None
    if (
        hypothesis.relation in {"refine", "retry", "supersede"}
        and set(hypothesis.prior_experiment_refs).intersection(ordinal for ordinal, _ in same_mechanism)
    ):
        return None
    new_areas = [canonical_area(item) for item in hypothesis.files_or_area]
    new_effect = _normalize_semantics(hypothesis.expected_effect)
    duplicates = [
        ordinal
        for ordinal, previous in same_mechanism
        if new_effect
        and new_effect == _normalize_semantics(previous.get("expected_effect", ""))
        and _areas_overlap(new_areas, [canonical_area(item) for item in previous.get("files_or_area", [])])
    ]
    if duplicates:
        return (
            f"duplicate test: mechanism {hypothesis.mechanism_key!r}, same expected effect, "
            f"and overlapping canonical area as experiments {duplicates}; declare a referenced "
            "refine/retry/supersede only when the verification itself changes"
        )
    return None


def canonical_area(value: str) -> str:
    value = str(value).strip().replace("\\", "/")
    value = re.sub(r"^\./+", "", value)
    value = re.sub(r"/+", "/", value)
    return value.strip("/")


def _normalize_semantics(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _areas_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    return any(
        a == b
        or a.startswith(b.rstrip("/") + "/")
        or b.startswith(a.rstrip("/") + "/")
        or fnmatch.fnmatchcase(a, b)
        or fnmatch.fnmatchcase(b, a)
        for a in left if a
        for b in right if b
    )

