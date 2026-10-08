from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from .contracts import (
    CampaignSpec, DevExperimentEvidence, DevFailureSummary, FailureCapsule,
    Hypothesis, MechanismEvidence, ResearchState,
)


_FAILURE_NAME = re.compile(
    r"miss|fail|error|wrong|drop|reject|timeout|no_result|unmatched|violation|selection",
    re.IGNORECASE,
)


def attempt_bucket(row: dict[str, Any]) -> str:
    """Classify current outcome, including legacy REJECTED operational rows."""
    status = row.get("status")
    failure = row.get("failure_category")
    decision = json.loads(row.get("decision_json") or "{}")
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
        # A promotion threshold or guard can reject a candidate that still
        # yielded a useful DEV improvement. Keep that signal for planning.
        delta = decision.get("delta")
        if isinstance(delta, (int, float)) and math.isfinite(delta) and delta > 0:
            return "DEV_PARTIAL_IMPROVEMENT"
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
        "DEV_PARTIAL_IMPROVEMENT": "DEV primary quality improved, but promotion thresholds or guards were not met.",
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


def _visible_numbers(spec: CampaignSpec, payload_json: str | None) -> dict[str, float]:
    """Read only aggregate DEV fields already authorized for agent context."""
    view = agent_evaluation_view(spec, payload_json) or {}
    numbers: dict[str, float] = {}
    for name, item in (view.get("metrics") or {}).items():
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, (int, float)) and math.isfinite(value):
            numbers[f"metric:{name}"] = float(value)
    for name, value in (view.get("diagnostics") or {}).items():
        if isinstance(value, (int, float)) and math.isfinite(value):
            numbers[f"diagnostic:{name}"] = float(value)
    return numbers


def summarize_dev_failures(
    spec: CampaignSpec,
    campaign: dict[str, Any],
    experiments: list[dict[str, Any]],
) -> tuple[list[DevFailureSummary], list[DevExperimentEvidence]]:
    """Prioritize allowed aggregate failures and preserve each trial's effect.

    No case rows, raw failure examples, validation, or holdout measurements
    enter this summary. Accepted DEV evaluations advance the reference used
    for later experiment deltas.
    """
    baseline = _visible_numbers(spec, campaign.get("baseline_json"))
    champion = _visible_numbers(spec, campaign.get("champion_json"))
    rows: list[tuple[dict[str, Any], dict[str, float], dict[str, float]]] = []
    reference = baseline
    for row in experiments:
        if row.get("status") == "ACTIVE" or not row.get("evaluation_json"):
            continue
        candidate = _visible_numbers(spec, row["evaluation_json"])
        rows.append((row, candidate, reference))
        if row.get("status") == "ACCEPTED":
            reference = candidate

    keys = {
        key for values in [baseline, champion, *(item[1] for item in rows)]
        for key, value in values.items()
        if _FAILURE_NAME.search(key) and value >= 0
    }
    numeric: list[DevFailureSummary] = []
    for key in keys:
        observed = [(int(row["ordinal"]), candidate[key]) for row, candidate, _ in rows if key in candidate]
        values = [v for v in (baseline.get(key), champion.get(key), *(v for _, v in observed)) if v is not None]
        if not values or max(values) == 0:
            continue
        best_ordinal, best_value = min(observed, key=lambda item: item[1]) if observed else (None, None)
        reference_values = [
            value for value in (baseline.get(key), champion.get(key)) if value is not None
        ]
        if best_value is not None and reference_values and best_value >= min(reference_values):
            best_ordinal = None
        changed = [
            int(row["ordinal"])
            for row, candidate, before in rows
            if key in candidate and key in before and not math.isclose(candidate[key], before[key])
        ]
        numeric.append(DevFailureSummary(
            signal_key=key,
            source=key.split(":", 1)[0],
            baseline_value=baseline.get(key),
            current_value=champion.get(key),
            last_candidate_value=observed[-1][1] if observed else None,
            best_candidate_value=best_value,
            best_experiment_ordinal=best_ordinal,
            changed_experiment_ordinals=changed,
            occurrences=len(observed),
        ))

    capsule_rows: dict[str, DevFailureSummary] = {}
    for row in experiments:
        if row.get("status") == "ACTIVE":
            continue
        for capsule in _failure_capsules_from_row(row):
            # Free-form case identifiers cannot become planning signal keys.
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_-]{0,79}", capsule.failure_class):
                continue
            key = f"capsule:{capsule.failure_class}"
            summary = capsule_rows.setdefault(key, DevFailureSummary(
                signal_key=key, source="capsule", likely_mechanism=capsule.likely_mechanism,
            ))
            summary.occurrences += 1
            ordinal = int(row["ordinal"])
            if ordinal not in summary.changed_experiment_ordinals:
                summary.changed_experiment_ordinals.append(ordinal)

    summaries = sorted(
        [*numeric, *capsule_rows.values()],
        key=lambda item: (
            -max(item.baseline_value or 0, item.current_value or 0, item.occurrences),
            item.signal_key,
        ),
    )[:8]
    selected = {item.signal_key for item in summaries}
    evidence: list[DevExperimentEvidence] = []
    for row, candidate, before in rows[-8:]:
        hypothesis = json.loads(row.get("hypothesis_json") or "{}")
        decision = agent_decision_view(spec, _dev_decision_from_row(row)) or {}
        delta = decision.get("delta")
        evidence.append(DevExperimentEvidence(
            ordinal=int(row["ordinal"]),
            mechanism_key=hypothesis.get("mechanism_key", "unknown"),
            dev_outcome=classify_dev_outcome(spec, row),
            primary_delta=float(delta) if isinstance(delta, (int, float)) and math.isfinite(delta) else None,
            failure_signal_deltas={
                key: candidate[key] - before[key]
                for key in selected & candidate.keys() & before.keys()
                if not math.isclose(candidate[key], before[key])
            },
            rejection_reasons=list(decision.get("reasons") or [])[:8],
        ))
    return summaries, evidence


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
) -> ResearchState:
    mechanism_rows: dict[str, dict[str, Any]] = {}
    failure_counts: Counter[str] = Counter()
    lessons: list[str] = []
    failure_capsules: list[FailureCapsule] = []

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
        failure_capsules.extend(_failure_capsules_from_row(row))

    dev_non_improve_streak = 0
    for row in experiments:
        if attempt_bucket(row) != "scientific_trials":
            continue
        outcome = classify_dev_outcome(spec, row)
        if outcome in {"DEV_IMPROVEMENT", "DEV_PARTIAL_IMPROVEMENT"}:
            dev_non_improve_streak = 0
        else:
            dev_non_improve_streak += 1

    remaining = {
        "experiments": max(0, spec.budget.max_experiments - int(campaign["experiment_count"])),
        "attempts": max(0, spec.budget.max_attempts - int(campaign["attempt_count"])),
        "dev_experiments_until_reframe": max(
            0,
            spec.budget.plateau_experiments - 1 - dev_non_improve_streak,
        ),
    }
    failure_signals, experiment_evidence = summarize_dev_failures(
        spec, campaign, experiments,
    )
    return ResearchState(
        planning_mode=planning_mode(
            dev_non_improve_streak,
            spec.budget.plateau_experiments,
        ),
        dev_non_improve_streak=dev_non_improve_streak,
        failure_counts=dict(sorted(failure_counts.items())),
        mechanisms=[
            MechanismEvidence.model_validate(value)
            for _, value in sorted(mechanism_rows.items())
        ],
        recent_lessons=lessons[-8:],
        recent_failure_capsules=failure_capsules[-16:],
        failure_signals=failure_signals,
        experiment_evidence=experiment_evidence,
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
