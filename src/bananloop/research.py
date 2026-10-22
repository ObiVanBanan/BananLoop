from __future__ import annotations

import json
import math
import fnmatch
import hashlib
import re
from collections import Counter
from typing import Any, Callable
from pydantic import ValidationError

from .contracts import (
    CampaignSpec, CandidateDecision, DevExperimentEvidence, DevFailureSummary,
    DevResearchLesson, FailureCapsule, Hypothesis, HypothesisCandidate,
    HypothesisPortfolio, MechanismEvidence, PortfolioRecord, ResearchState,
)


_FAILURE_NAME = re.compile(
    r"miss|fail|error|wrong|drop|reject|timeout|no_result|unmatched|violation|selection",
    re.IGNORECASE,
)
_AREA_LOCATOR = re.compile(r"(?:[A-Za-z_]\w*(?:\.\w+)*|\d+(?:-\d+)?)\Z")


def _area_matches_scope(area: str, pattern: str) -> bool:
    def matches(path: str) -> bool:
        return (
            pattern == "**"
            or fnmatch.fnmatchcase(path, pattern)
            or (pattern.endswith("/**") and path == pattern[:-3])
        )

    if matches(area):
        return True
    # Agents sometimes qualify a repository path with a function or line
    # locator (for example ``src/model.py:build_model``). Treat that suffix as
    # context only when the actual path itself matches the approved scope.
    path, separator, locator = area.rpartition(":")
    return bool(
        separator
        and path
        and _AREA_LOCATOR.fullmatch(locator)
        and matches(path)
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


def capsule_evidence_ref(capsule: FailureCapsule) -> str:
    """Return a content-addressed ref that survives sliding-window reordering."""
    payload = json.dumps(capsule.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "capsule:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalize_portfolio(payload: dict[str, Any]) -> HypothesisPortfolio:
    """Normalize a v2 portfolio or a legacy single-Hypothesis response."""
    if isinstance(payload.get("candidates"), list):
        raw_candidates = payload["candidates"]
        if any(value is not None for key, value in payload.items() if key != "candidates"):
            raise ValueError("portfolio response cannot mix legacy hypothesis fields")
        if not isinstance(raw_candidates, list):
            raise ValueError("portfolio candidates must be a JSON list")
        valid: list[HypothesisCandidate] = []
        malformed: list[dict[str, str]] = []
        for index, raw in enumerate(raw_candidates[:4], start=1):
            candidate_id = raw.get("candidate_id") if isinstance(raw, dict) else None
            safe_id = (candidate_id if isinstance(candidate_id, str)
                       and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", candidate_id)
                       else f"invalid-{index}")
            try:
                parsed = HypothesisCandidate.model_validate(raw)
                safe_refs = []
                for ref in parsed.evidence_refs:
                    if ref == "INSUFFICIENT_EVIDENCE" or re.fullmatch(r"exp:[1-9][0-9]{0,8}", ref) \
                            or re.fullmatch(r"signal:[A-Za-z0-9_.:-]{1,120}", ref) \
                            or re.fullmatch(r"capsule:(?:[1-9][0-9]{0,2}|[a-f0-9]{16})", ref):
                        safe_refs.append(ref)
                    else:
                        safe_refs.append("<invalid>")
                valid.append(parsed.model_copy(update={"evidence_refs": safe_refs}))
            except ValidationError as exc:
                malformed.append({"candidate_id": safe_id, "rejection_reason": "MALFORMED_CANDIDATE_SCHEMA"})
        if len(raw_candidates) > 4:
            malformed.append({
                "candidate_id": "overflow",
                "rejection_reason": f"PORTFOLIO_EXCEEDS_MAX_4:{len(raw_candidates) - 4} additional candidate(s) rejected",
            })
        return HypothesisPortfolio(candidates=valid, malformed_candidates=malformed[:4])
    # Codex strict-output fallback includes a null candidates slot and null
    # values for unused fields. Remove those wire-only nulls before validation.
    hypothesis = Hypothesis.model_validate({key: value for key, value in payload.items()
                                            if key != "candidates" and value is not None})
    digest = hashlib.sha256(
        json.dumps(hypothesis.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()[:16]
    return HypothesisPortfolio(candidates=[HypothesisCandidate(
        candidate_id=f"legacy-{digest}", hypothesis=hypothesis,
        evidence_refs=["INSUFFICIENT_EVIDENCE"],
        predicted_observable=hypothesis.expected_effect,
        discriminating_test=hypothesis.falsification_condition,
        scope_eligibility=True, rationale=hypothesis.rationale,
        uncertainty="Legacy single-hypothesis adapter did not report uncertainty.",
    )])


def candidate_rejection_reasons(
    candidate: HypothesisCandidate, *, spec: CampaignSpec, state: ResearchState,
    experiments: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    hypothesis = candidate.hypothesis
    # Only frozen DEV evidence is used to blacklist a stagnant mechanism;
    # validation and holdout results must never guide adaptive proposals.
    if hypothesis.mechanism_key.casefold().strip() in state.stagnant_mechanisms:
        reasons.append("STAGNANT_DEV_MECHANISM_CHANGE_FAMILY")
    family = (hypothesis.mechanism_family or hypothesis.mechanism_key).casefold().strip()
    if state.planning_mode == "reframe" and family in state.failed_dev_families:
        reasons.append("REFRAME_REUSES_FAILED_DEV_FAMILY")
    if not candidate.scope_eligibility:
        reasons.append("AGENT_SCOPE_INELIGIBLE")
    scopes = spec.editable_scope or ["**"]
    for area in hypothesis.files_or_area:
        normalized = area.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            reasons.append("UNSAFE_OR_UNGROUNDED_AREA")
            break
        if not any(_area_matches_scope(normalized, pattern) for pattern in scopes):
            reasons.append("OUT_OF_SCOPE")
            break
    if not hypothesis.files_or_area:
        reasons.append("UNTESTABLE_NO_AFFECTED_AREA")
    if not candidate.predicted_observable.strip() or not candidate.discriminating_test.strip():
        reasons.append("UNTESTABLE_NO_OBSERVABLE_OR_DISCRIMINATOR")

    evidence_ids = {f"exp:{item.ordinal}" for item in state.experiment_evidence}
    evidence_ids.update(f"signal:{item.signal_key}" for item in state.failure_signals)
    evidence_ids.update(capsule_evidence_ref(item) for item in state.recent_failure_capsules)
    # Accept numeric refs in already persisted proposal actions during recovery.
    evidence_ids.update(f"capsule:{i}" for i, _ in enumerate(state.recent_failure_capsules, start=1))
    for ref in candidate.evidence_refs:
        if ref != "INSUFFICIENT_EVIDENCE" and ref not in evidence_ids:
            reasons.append("UNKNOWN_EVIDENCE_REFERENCE")
            break
    if not candidate.evidence_refs:
        reasons.append("MISSING_EVIDENCE_OR_INSUFFICIENT_EVIDENCE_MARKER")
    if redundancy_reason(hypothesis, experiments):
        reasons.append("REDUNDANT_MECHANISM")
    # One implementation call and the configured staged evaluator calls are
    # unavoidable regardless of an adviser's optimistic cost estimate.
    min_agent_calls = 1
    min_evaluator_calls = 1 + int(bool(spec.validation_command)) + int(bool(spec.holdout_command))
    if candidate.estimated_agent_calls == 0:
        reasons.append("ESTIMATED_AGENT_COST_BELOW_MINIMUM")
    if candidate.estimated_evaluator_calls == 0:
        reasons.append("ESTIMATED_EVALUATOR_COST_BELOW_MINIMUM")
    required_agent_calls = max(candidate.estimated_agent_calls, min_agent_calls)
    required_evaluator_calls = max(
        candidate.estimated_evaluator_calls,
        1 + int(bool(spec.validation_command)),
    ) + int(bool(spec.holdout_command))
    if required_agent_calls > state.remaining_budget.get("agent_calls", 0):
        reasons.append("INSUFFICIENT_AGENT_BUDGET")
    if required_evaluator_calls > state.remaining_budget.get("evaluator_calls", 0):
        reasons.append("INSUFFICIENT_EVALUATOR_BUDGET")
    remaining_units = state.remaining_budget.get("work_units")
    required_units = max(
        candidate.estimated_work_units or 0,
        min_evaluator_calls * (spec.budget.evaluation_units_per_call or 1),
    )
    if remaining_units is not None and required_units > remaining_units:
        reasons.append("INSUFFICIENT_EVALUATION_WORK_UNITS")
    if state.remaining_budget.get("experiments", 0) < 1 or state.remaining_budget.get("attempts", 0) < 1:
        reasons.append("INSUFFICIENT_EXPERIMENT_BUDGET")

    target = hypothesis.mechanism_key.casefold().strip()
    repeated = [row for row in experiments if row.get("hypothesis_json") and
                json.loads(row["hypothesis_json"]).get("mechanism_key", "").casefold().strip() == target]
    if repeated:
        fresh = set(candidate.evidence_refs) - {"INSUFFICIENT_EVIDENCE"}
        previously_cited: set[str] = set()
        for row in repeated:
            previous_hypothesis = json.loads(row["hypothesis_json"])
            previously_cited.update(f"exp:{ordinal}" for ordinal in previous_hypothesis.get("prior_experiment_refs", []))
            if previous_hypothesis.get("target_failure_signal"):
                previously_cited.add(f"signal:{previous_hypothesis['target_failure_signal']}")
        if not fresh or not (fresh - previously_cited):
            reasons.append("REPEAT_WITHOUT_NEW_DISCRIMINATING_EVIDENCE")
    return list(dict.fromkeys(reasons))


def select_candidate(
    portfolio: HypothesisPortfolio, *, spec: CampaignSpec, state: ResearchState,
    experiments: list[dict[str, Any]], experiment_ordinal: int, action_id: str,
    task: dict[str, Any] | None = None,
    grounding_check: Callable[[Hypothesis, dict[str, Any]], str | None] | None = None,
) -> PortfolioRecord:
    """Apply deterministic, explainable core gates and select one candidate."""
    ids = [item.candidate_id for item in portfolio.candidates]
    duplicate_ids = {item for item in ids if ids.count(item) > 1}
    known = {item.mechanism_key for item in state.mechanisms}
    decisions: list[CandidateDecision] = []
    invalid_decisions = [CandidateDecision(
        candidate_id=item["candidate_id"], admissible=False,
        rejection_reasons=[item["rejection_reason"]],
        selection_reason=item["rejection_reason"],
    ) for item in portfolio.malformed_candidates]
    eligible: list[tuple[tuple[int, int, int, int, int, str], HypothesisCandidate]] = []
    for candidate in portfolio.candidates:
        reasons = candidate_rejection_reasons(candidate, spec=spec, state=state, experiments=experiments)
        if grounding_check and task is not None:
            grounding_error = grounding_check(candidate.hypothesis, task)
            if grounding_error:
                reasons.append("UNGROUNDED_EVIDENCE: " + grounding_error)
        if candidate.candidate_id in duplicate_ids:
            reasons.append("DUPLICATE_CANDIDATE_ID")
        relevant_refs = set(candidate.evidence_refs).intersection(
            {f"exp:{ordinal}" for ordinal in candidate.hypothesis.prior_experiment_refs}
            | ({f"signal:{candidate.hypothesis.target_failure_signal}"}
               if candidate.hypothesis.target_failure_signal else set())
        )
        evidence_score = min(3, len(relevant_refs))
        novelty = int(candidate.hypothesis.mechanism_key not in known)
        strategy_priority = -novelty if state.planning_mode == "exploit" else novelty
        scores = {"dev_evidence": evidence_score, "mechanism_novelty": int(novelty),
                  "falsifiability": int(bool(candidate.discriminating_test.strip())),
                  "budget_feasibility": int(not reasons),
                  "lower_estimated_cost": -(candidate.estimated_agent_calls + candidate.estimated_evaluator_calls)}
        if reasons:
            decisions.append(CandidateDecision(candidate_id=candidate.candidate_id, admissible=False,
                rejection_reasons=list(dict.fromkeys(reasons)), criterion_scores=scores,
                selection_reason="; ".join(dict.fromkeys(reasons))))
        else:
            eligible.append(((strategy_priority, evidence_score, scores["falsifiability"], 1,
                              scores["lower_estimated_cost"], candidate.candidate_id), candidate))
            decisions.append(CandidateDecision(candidate_id=candidate.candidate_id, admissible=True,
                                                criterion_scores=scores))
    trigger = [f"DEV non-improve streak={state.dev_non_improve_streak}"]
    if state.experiment_evidence:
        latest = state.experiment_evidence[-1]
        trigger.append(f"DEV experiment {latest.ordinal}: {latest.dev_outcome}")
    selected = None
    stop_reason = ""
    if eligible:
        _, winner = max(eligible, key=lambda item: item[0])
        selected = winner.candidate_id
        for decision in decisions:
            if decision.candidate_id == selected:
                decision.selection_reason = (
                    "Selected by deterministic ordering: strategy-aware mechanism novelty, DEV evidence, "
                    "falsifiability, budget feasibility, estimated cost, stable candidate ID."
                )
            elif decision.admissible:
                decision.selection_reason = "Ranked below selected candidate by the recorded deterministic criteria."
    else:
        stop_reason = "No candidate passed the core evidence, scope, falsifiability, redundancy, and remaining-budget gates."
    return PortfolioRecord(experiment_ordinal=experiment_ordinal, action_id=action_id,
        strategy=state.planning_mode, trigger_evidence=trigger, candidates=portfolio.candidates,
        decisions=decisions, invalid_candidates=invalid_decisions,
        selected_candidate_id=selected, stop_reason=stop_reason)


def derive_dev_lesson(row: dict[str, Any], spec: CampaignSpec) -> DevResearchLesson | None:
    """Summarize one scientific DEV measurement, excluding operational failures."""
    if attempt_bucket(row) != "scientific_trials":
        return None
    hypothesis = json.loads(row.get("hypothesis_json") or "{}")
    outcome = classify_dev_outcome(spec, row)
    interpretation = ("supported" if outcome in {"DEV_IMPROVEMENT", "DEV_PARTIAL_IMPROVEMENT"}
                      else "refuted" if outcome in {"DEV_REGRESSION", "DEV_GUARD_REJECT"} else "unknown")
    decision = _dev_decision_from_row(row) or {}
    delta = decision.get("delta")
    capsules = _failure_capsules_from_row(row)
    # These are proposals from an LLM analyst, not established causal facts.
    causal_inference = "; ".join(c.likely_mechanism for c in capsules[:2] if c.likely_mechanism)
    discriminators = [c.suggested_regression for c in capsules[:2] if c.suggested_regression]
    next_discriminator = "; ".join(discriminators) or hypothesis.get("falsification_condition", "")
    return DevResearchLesson(
        experiment_ordinal=int(row["ordinal"]), mechanism_key=hypothesis.get("mechanism_key", "unknown"),
        observation=f"DEV outcome {outcome}; primary delta {delta if delta is not None else 'unknown'}.",
        interpretation=interpretation,
        inference=(
            ("DEV analyst's unverified causal proposal: " + causal_inference)
            if causal_inference else
            "DEV score alone cannot establish the cause of the observed change."
        ),
        next_discriminator=next_discriminator,
        evidence_refs=[f"exp:{row['ordinal']}"],
    )


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
        # A/A readiness calibrates noise below min_improvement. Tiny positive
        # AND negative DEV deltas are inconclusive, not scientific progress or
        # evidence that the underlying mechanism regressed.
        delta = decision.get("delta")
        threshold = float(spec.primary_metric.min_improvement)
        reasons = decision.get("reasons") or []
        if isinstance(delta, (int, float)) and math.isfinite(delta):
            if delta >= threshold:
                # A meaningful primary gain can still violate another guard.
                return "DEV_PARTIAL_IMPROVEMENT"
            if delta <= -threshold:
                return "DEV_REGRESSION"
            if any(
                reason.startswith(("GUARDRAIL_FAILED:", "REGRESSION:", "CHECK_FAILED:"))
                for reason in reasons
            ):
                return "DEV_GUARD_REJECT"
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
        "DEV_NO_EFFECT": "The DEV primary change is below the configured minimum meaningful change, in either direction; treat it as inconclusive noise.",
        "DEV_REGRESSION": "DEV primary quality regressed by at least the configured minimum meaningful change.",
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
            # An aggregate-only capsule records that the cause is unknown; it
            # is useful planning context, but it is not an actionable DEV
            # failure signal that a hypothesis can claim to target.
            if capsule.failure_class == "insufficient_evidence":
                continue
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


def stagnant_dev_mechanisms(
    spec: CampaignSpec,
    experiments: list[dict[str, Any]],
    *,
    tolerance: float = 1e-6,
) -> list[str]:
    """Find mechanisms whose last two scientific DEV trials are indistinguishable.

    The primary metric uses the *configured minimum meaningful improvement* as
    a conservative noise band (readiness requires A/A variation below it).
    Other allowlisted metric/diagnostic values require near-exact equality:
    unlike the primary metric, they have no configured noise calibration.
    Do not inspect validation/holdout outcomes, status rejection reasons, or cases.
    """
    observations: dict[str, list[tuple[str, float, dict[str, float]]]] = {}
    for row in experiments:
        if (attempt_bucket(row) != "scientific_trials"
                or not row.get("hypothesis_json") or not row.get("evaluation_json")):
            continue
        mechanism = str(json.loads(row["hypothesis_json"]).get("mechanism_key") or "").strip().casefold()
        decision = agent_decision_view(spec, _dev_decision_from_row(row)) or {}
        delta = decision.get("delta")
        if not mechanism or not isinstance(delta, (int, float)) or not math.isfinite(delta):
            continue
        observations.setdefault(mechanism, []).append((
            classify_dev_outcome(spec, row),
            float(delta),
            _visible_numbers(spec, row["evaluation_json"]),
        ))

    stagnant: list[str] = []
    for mechanism, trials in observations.items():
        if len(trials) < 2:
            continue
        before, after = trials[-2:]
        minimum_effect = float(spec.primary_metric.min_improvement)
        both_inconclusive = all(abs(trial[1]) < minimum_effect for trial in (before, after))
        nearly_equal = math.isclose(
            before[1], after[1], rel_tol=0.0, abs_tol=max(tolerance, minimum_effect),
        )
        if before[0] != after[0] or not (both_inconclusive or nearly_equal):
            continue
        primary_key = f"metric:{spec.primary_metric.name}"
        prior_metrics = {k: v for k, v in before[2].items() if k != primary_key}
        current_metrics = {k: v for k, v in after[2].items() if k != primary_key}
        if prior_metrics.keys() != current_metrics.keys():
            continue
        if all(math.isclose(prior_metrics[key], current_metrics[key],
                            rel_tol=0.0, abs_tol=tolerance) for key in prior_metrics):
            stagnant.append(mechanism)
    return sorted(stagnant)[:16]


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
    events: list[dict[str, Any]] | None = None,
    remaining_work_units: int | None = None,
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
        "agent_calls": max(0, spec.budget.max_agent_calls - int(campaign["agent_calls"])),
        "evaluator_calls": max(0, spec.budget.max_evaluator_calls - int(campaign["evaluator_calls"])),
        "dev_experiments_until_reframe": max(
            0,
            spec.budget.plateau_experiments - 1 - dev_non_improve_streak,
        ),
    }
    if remaining_work_units is not None:
        remaining["work_units"] = max(0, remaining_work_units)
    failure_signals, experiment_evidence = summarize_dev_failures(
        spec, campaign, experiments,
    )
    portfolios: list[PortfolioRecord] = []
    blocked_stop = ""
    for event in events or []:
        if event.get("event_type") != "PORTFOLIO_DECISION":
            if event.get("event_type") == "PLATEAU_REFRAME_BLOCKED":
                try:
                    blocked_stop = str(json.loads(event["payload_json"]).get("reason", ""))
                except (KeyError, ValueError, TypeError):
                    pass
            continue
        try:
            portfolios.append(PortfolioRecord.model_validate(json.loads(event["payload_json"])))
        except (KeyError, ValueError, TypeError):
            continue
    dev_lessons = [lesson for row in experiments if (lesson := derive_dev_lesson(row, spec)) is not None]
    stagnant = stagnant_dev_mechanisms(spec, experiments)
    # The last unsuccessful DEV trials constrain the next reframe. Do not use
    # validation rejection status as evidence that a DEV mechanism failed.
    failed_families: list[str] = []
    for row in experiments[-8:]:
        if attempt_bucket(row) != "scientific_trials" or not row.get("hypothesis_json"):
            continue
        if classify_dev_outcome(spec, row) not in {"DEV_NO_EFFECT", "DEV_REGRESSION", "DEV_GUARD_REJECT"}:
            continue
        hypothesis = json.loads(row["hypothesis_json"])
        family = str(hypothesis.get("mechanism_family") or hypothesis.get("mechanism_key") or "").strip().casefold()
        if family and family not in failed_families:
            failed_families.append(family)
    latest_mechanism = next((
        str(json.loads(row["hypothesis_json"]).get("mechanism_key") or "").strip().casefold()
        for row in reversed(experiments)
        if attempt_bucket(row) == "scientific_trials" and row.get("hypothesis_json")
    ), "")
    return ResearchState(
        planning_mode=("reframe" if latest_mechanism in stagnant else planning_mode(
            dev_non_improve_streak,
            spec.budget.plateau_experiments,
        )),
        stagnant_mechanisms=stagnant,
        failed_dev_families=failed_families[-16:],
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
        portfolios=portfolios[-16:],
        dev_lessons=dev_lessons[-16:],
        stop_explanation=(blocked_stop or (portfolios[-1].stop_reason if portfolios and portfolios[-1].stop_reason else "")),
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
