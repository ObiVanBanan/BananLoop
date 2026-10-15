from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from .contracts import BudgetSpec, CampaignSpec, FailureCapsule, Hypothesis, MechanismEvidence, ResearchState
from .workspace import WorkspaceManager


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
    budget: BudgetSpec | None = None,
    evaluation_units_reserved: int = 0,
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
        if outcome == "DEV_IMPROVEMENT":
            dev_non_improve_streak = 0
        else:
            dev_non_improve_streak += 1

    active_budget = budget or spec.budget
    remaining = {
        "experiments": max(0, active_budget.max_experiments - int(campaign["experiment_count"])),
        "attempts": max(0, active_budget.max_attempts - int(campaign["attempt_count"])),
        "agent_calls": max(0, active_budget.max_agent_calls - int(campaign["agent_calls"])),
        "evaluator_calls": max(0, active_budget.max_evaluator_calls - int(campaign["evaluator_calls"])),
        "dev_experiments_until_reframe": max(
            0,
            active_budget.plateau_experiments - 1 - dev_non_improve_streak,
        ),
    }
    if active_budget.max_evaluation_units is not None:
        remaining["evaluation_units"] = max(
            0, active_budget.max_evaluation_units - evaluation_units_reserved
        )
    diagnosis = _repeated_retrieval_diagnosis(spec, experiments)
    return ResearchState(
        planning_mode=planning_mode(
            dev_non_improve_streak,
            active_budget.plateau_experiments,
        ),
        dev_non_improve_streak=dev_non_improve_streak,
        failure_counts=dict(sorted(failure_counts.items())),
        mechanisms=[
            MechanismEvidence.model_validate(value)
            for _, value in sorted(mechanism_rows.items())
        ],
        recent_lessons=lessons[-8:],
        recent_failure_capsules=failure_capsules[-16:],
        remaining_budget=remaining,
        repeated_failure_diagnosis=diagnosis,
    )


def _repeated_retrieval_diagnosis(
    spec: CampaignSpec,
    experiments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Summarize repeated retrieval-stage failure using only visible DEV metrics."""
    visible = set(spec.agent_visible_metrics)
    if visible and not {"RETRIEVAL_MISS", "initial_recall"}.issubset(visible):
        return None
    eligible: list[dict[str, Any]] = []
    for row in experiments:
        if row.get("status") == "ACTIVE" or attempt_bucket(row) != "scientific_trials":
            continue
        if classify_dev_outcome(spec, row) == "DEV_IMPROVEMENT":
            eligible = []
            continue
        evaluation = agent_evaluation_view(spec, row.get("evaluation_json")) or {}
        metrics = evaluation.get("metrics") or {}
        miss = metrics.get("RETRIEVAL_MISS")
        recall = metrics.get("initial_recall")
        if not isinstance(miss, dict) or not isinstance(recall, dict):
            eligible = []
            continue
        miss_value, recall_value = miss.get("value"), recall.get("value")
        if not isinstance(miss_value, (int, float)) or not isinstance(recall_value, (int, float)):
            eligible = []
            continue
        # A success at the retrieval target or zero misses breaks the repeated
        # failure run; unrelated missing measurements cannot be treated as evidence.
        if miss_value <= 0:
            eligible = []
            continue
        eligible.append({
            "ordinal": int(row["ordinal"]),
            "mechanism_key": (json.loads(row["hypothesis_json"] or "{}").get("mechanism_key", "unknown")),
            "retrieval_miss": miss_value,
            "initial_recall": recall_value,
            "rerank_recall": (metrics.get("rerank_recall") or {}).get("value"),
        })
    recent = eligible[-3:]
    if len(recent) < 2 or any(item["retrieval_miss"] <= 0 for item in recent):
        return None
    # Catalog, pre-truncation, initial, and rerank evidence below are all
    # evaluator-owned DEV diagnostics. Aggregate misses alone stay unclassified.
    catalog_path = "data/competitor_catalogs/README.md"
    catalogs_editable = any(
        WorkspaceManager._matches(catalog_path, [pattern])
        for pattern in spec.editable_scope
    ) and not any(
        WorkspaceManager._matches(catalog_path, [pattern])
        for pattern in spec.protected_paths
    )
    case_evidence = _retrieval_case_evidence(experiments, recent)
    cause_counts = case_evidence["cause_counts"]
    if cause_counts.get("CATALOG_COVERAGE_GAP", 0):
        finding = (
            f"{cause_counts['CATALOG_COVERAGE_GAP']} failed DEV cases have no acceptable target in the loaded catalog. "
            "Catalog coverage is directly implicated by per-case evidence."
        )
    elif cause_counts.get("QDRANT_OR_MODALITY_POOL_MISS", 0):
        finding = (
            f"{cause_counts['QDRANT_OR_MODALITY_POOL_MISS']} failed DEV cases have acceptable catalog targets "
            "that never enter the pre-truncation candidate pool; investigate Qdrant/index and modality retrieval."
        )
    elif case_evidence["failed_case_count"] and not case_evidence["unclassified_case_count"]:
        finding = "Per-case traces localize the remaining failures after candidate retrieval; see cause_counts."
    else:
        finding = (
            "Relevant candidates are still lost before reranking in multiple failed DEV experiments. "
            "Per-case stage traces are needed to identify the cause."
        )
    if cause_counts.get("CATALOG_COVERAGE_GAP", 0) and catalogs_editable:
        next_step = "Propose a bounded catalog coverage change and verify the missing DEV targets enter the catalog."
    elif cause_counts.get("CATALOG_COVERAGE_GAP", 0):
        next_step = "Request editable_scope for the relevant catalog before proposing a catalog coverage change."
    elif cause_counts.get("QDRANT_OR_MODALITY_POOL_MISS", 0):
        next_step = "Inspect Qdrant/index and modality candidate generation for the listed hashed DEV cases."
    elif cause_counts.get("RRF_TRUNCATION", 0):
        next_step = "Inspect the pre-truncation ranks and bounded candidate truncation for the listed DEV cases."
    elif cause_counts.get("FILTER_DROP", 0):
        next_step = "Inspect the recorded hard-filter fields for the listed DEV cases before changing ranking."
    elif cause_counts.get("RERANK_SELECTION", 0):
        next_step = "Investigate reranker selection for candidates already present in its candidate pool."
    else:
        next_step = (
            "Do not claim a root cause from aggregate metrics. Supply per-case catalog-presence and "
            "candidate-stage traces before another ranking-only experiment."
        )
    return {
        "kind": "repeated_retrieval_miss",
        "evidence": recent,
        "case_evidence": case_evidence,
        "scope_constraints": {
            "catalogs_editable": catalogs_editable,
            "editable_scope": list(spec.editable_scope),
            "operator_action_if_data_change_is_supported": (
                None if catalogs_editable else "expand editable_scope to the relevant catalog path"
            ),
        },
        "finding": finding,
        "unknowns": (
            [] if case_evidence["failed_case_count"] and not case_evidence["unclassified_case_count"]
            else [
                "case-level retrieval traces are missing or incomplete",
                "catalog coverage versus candidate-generation loss is not yet distinguished",
            ]
        ),
        "required_next_step": next_step,
    }


def _retrieval_case_evidence(
    experiments: list[dict[str, Any]], recent: list[dict[str, Any]]
) -> dict[str, Any]:
    """Classify hashed DEV traces by the first retrieval stage that lost a target."""
    by_ordinal = {int(row["ordinal"]): row for row in experiments}
    latest = by_ordinal.get(int(recent[-1]["ordinal"]), {}) if recent else {}
    try:
        result = json.loads(latest.get("evaluation_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        result = {}
    failed = []
    for trace in result.get("retrieval_case_traces") or []:
        if trace.get("failure_stage") == "SUCCESS":
            continue
        catalog = trace.get("acceptable_present_in_catalog")
        pretruncate = trace.get("acceptable_in_pretruncate_pool")
        initial = trace.get("acceptable_in_initial_retrieval")
        rerank = trace.get("acceptable_in_rerank_candidates")
        selected = trace.get("acceptable_selected")
        if catalog is None:
            cause = "UNCLASSIFIED"
        elif not catalog:
            cause = "CATALOG_COVERAGE_GAP"
        elif pretruncate is None:
            cause = "UNCLASSIFIED"
        elif not pretruncate:
            cause = "QDRANT_OR_MODALITY_POOL_MISS"
        elif initial is None:
            cause = "UNCLASSIFIED"
        elif not initial:
            cause = "RRF_TRUNCATION"
        elif rerank is None:
            cause = "UNCLASSIFIED"
        elif not rerank:
            cause = "FILTER_DROP"
        elif selected is None:
            cause = "UNCLASSIFIED"
        elif not selected:
            cause = "RERANK_SELECTION"
        else:
            cause = "UNRESOLVED"
        failed.append({
            "case_id": trace.get("case_id"),
            "repetition": trace.get("repetition", 0),
            "input_sha256": trace.get("input_sha256"),
            "failure_stage": trace.get("failure_stage"),
            "failure_substage": trace.get("failure_substage"),
            "cause": cause,
            "acceptable_present_in_catalog": trace.get("acceptable_present_in_catalog"),
            "acceptable_in_pretruncate_pool": trace.get("acceptable_in_pretruncate_pool"),
            "acceptable_in_initial_retrieval": trace.get("acceptable_in_initial_retrieval"),
            "acceptable_in_rerank_candidates": trace.get("acceptable_in_rerank_candidates"),
            "acceptable_selected": trace.get("acceptable_selected"),
            "best_acceptable_pretruncate_rank": trace.get("best_acceptable_pretruncate_rank"),
        })
    trace_count = len(result.get("retrieval_case_traces") or [])
    case_counts: dict[str, int] = {}
    for item in failed:
        case_counts[item["cause"]] = case_counts.get(item["cause"], 0) + 1
    return {
        "experiment_ordinal": int(recent[-1]["ordinal"]) if recent else None,
        "failed_case_count": len(failed),
        "failed_cases": failed,
        "trace_example_count": trace_count,
        "unclassified_case_count": max(
            0,
            len(failed) - trace_count,
        ) + case_counts.get("UNCLASSIFIED", 0),
        "cause_counts": dict(sorted(case_counts.items())),
    }


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

