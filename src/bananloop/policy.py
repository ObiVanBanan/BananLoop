from __future__ import annotations

import math

from .contracts import CampaignSpec, Decision, Direction, EvaluationResult, GuardrailSpec


class InvalidEvaluation(ValueError):
    pass


def metric_value(result: EvaluationResult, name: str) -> float:
    if result.status != "ok":
        raise InvalidEvaluation(f"evaluation status is {result.status}")
    if name not in result.metrics:
        raise InvalidEvaluation(f"missing required metric: {name}")
    value = result.metrics[name].value
    if not math.isfinite(value):
        raise InvalidEvaluation(f"metric {name} is not finite")
    return value


def _case_metric_map(result: EvaluationResult, metric: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for case in result.cases:
        if case.case_key in values:
            raise InvalidEvaluation(f"duplicate case_key: {case.case_key}")
        if metric not in case.metrics:
            raise InvalidEvaluation(
                f"case {case.case_key!r} missing paired metric: {metric}"
            )
        value = case.metrics[metric].value
        if not math.isfinite(value):
            raise InvalidEvaluation(
                f"case {case.case_key!r} metric {metric} is not finite"
            )
        values[case.case_key] = value
    return values


def validate_evaluation(spec: CampaignSpec, result: EvaluationResult) -> None:
    value = metric_value(result, spec.primary_metric.name)
    metric = spec.primary_metric
    if metric.minimum is not None and value < metric.minimum:
        raise InvalidEvaluation(f"metric {metric.name} below valid range")
    if metric.maximum is not None and value > metric.maximum:
        raise InvalidEvaluation(f"metric {metric.name} above valid range")
    if metric.unit is not None:
        actual_unit = result.metrics[metric.name].unit
        if actual_unit not in (None, metric.unit):
            raise InvalidEvaluation(f"metric {metric.name} unit mismatch: {actual_unit!r}")
    for guard in spec.guardrails:
        metric_value(result, guard.metric)
    for guard in spec.non_regression:
        metric_value(result, guard.metric)
    for check in spec.mandatory_checks:
        if check not in result.checks:
            raise InvalidEvaluation(f"missing mandatory check: {check}")
    if spec.evaluation_protocol.paired is not None:
        paired = spec.evaluation_protocol.paired
        values = _case_metric_map(result, paired.metric)
        if not values:
            raise InvalidEvaluation(
                f"paired comparison requires per-case metric {paired.metric!r}"
            )
        sample = spec.evaluation_protocol.sample_plan
        if sample is not None and len(values) != sample.size:
            raise InvalidEvaluation(
                f"paired evaluation expected {sample.size} cases, got {len(values)}"
            )


def _compare(value: float, guard: GuardrailSpec) -> bool:
    return {
        "<=": value <= guard.bound,
        ">=": value >= guard.bound,
        "<": value < guard.bound,
        ">": value > guard.bound,
        "==": value == guard.bound,
    }[guard.operator]


def decide(spec: CampaignSpec, champion: EvaluationResult, candidate: EvaluationResult) -> Decision:
    validate_evaluation(spec, champion)
    validate_evaluation(spec, candidate)
    current = metric_value(champion, spec.primary_metric.name)
    proposed = metric_value(candidate, spec.primary_metric.name)
    if spec.primary_metric.direction == Direction.MAXIMIZE:
        delta = proposed - current
    else:
        delta = current - proposed
    predicates: dict[str, bool] = {
        "minimum_improvement": delta >= spec.primary_metric.min_improvement,
    }
    reasons: list[str] = []
    if not predicates["minimum_improvement"]:
        reasons.append("PRIMARY_NOT_IMPROVED")
    for guard in spec.guardrails:
        passed = _compare(metric_value(candidate, guard.metric), guard)
        predicates[f"guard:{guard.metric}{guard.operator}{guard.bound}"] = passed
        if not passed:
            reasons.append(f"GUARDRAIL_FAILED:{guard.metric}")
    for guard in spec.non_regression:
        champion_value = metric_value(champion, guard.metric)
        candidate_value = metric_value(candidate, guard.metric)
        if guard.direction == Direction.MAXIMIZE:
            passed = candidate_value >= champion_value - guard.tolerance
        else:
            passed = candidate_value <= champion_value + guard.tolerance
        predicates[
            f"non_regression:{guard.metric}:{guard.direction.value}:{guard.tolerance}"
        ] = passed
        if not passed:
            reasons.append(f"REGRESSION:{guard.metric}")
    for name in spec.mandatory_checks:
        passed = candidate.checks[name].passed
        predicates[f"check:{name}"] = passed
        if not passed:
            reasons.append(f"CHECK_FAILED:{name}")

    paired_summary: dict[str, float | int] = {}
    paired = spec.evaluation_protocol.paired
    if paired is not None:
        champion_cases = _case_metric_map(champion, paired.metric)
        candidate_cases = _case_metric_map(candidate, paired.metric)
        same_cases = set(champion_cases) == set(candidate_cases)
        if paired.require_same_cases:
            predicates["paired:same_cases"] = same_cases
            if not same_cases:
                reasons.append("PAIRED_CASE_SET_CHANGED")

        shared = sorted(set(champion_cases) & set(candidate_cases))
        if not shared:
            raise InvalidEvaluation("paired comparison has no shared cases")
        direction = paired.direction or spec.primary_metric.direction
        deltas = []
        for case_key in shared:
            baseline_value = champion_cases[case_key]
            candidate_value = candidate_cases[case_key]
            case_delta = (
                candidate_value - baseline_value
                if direction == Direction.MAXIMIZE
                else baseline_value - candidate_value
            )
            deltas.append(case_delta)

        improved = sum(1 for value in deltas if value > 0)
        improved_fraction = improved / len(deltas)
        worst_delta = min(deltas)
        paired_summary = {
            "n": len(deltas),
            "improved": improved,
            "improved_fraction": improved_fraction,
            "mean_delta": sum(deltas) / len(deltas),
            "worst_delta": worst_delta,
        }

        if paired.min_improved_fraction is not None:
            passed = improved_fraction >= paired.min_improved_fraction
            predicates[
                f"paired:min_improved_fraction:{paired.min_improved_fraction}"
            ] = passed
            if not passed:
                reasons.append("PAIRED_IMPROVED_FRACTION_FAILED")

        if paired.worst_case_tolerance is not None:
            passed = worst_delta >= -paired.worst_case_tolerance
            predicates[
                f"paired:worst_case_tolerance:{paired.worst_case_tolerance}"
            ] = passed
            if not passed:
                reasons.append("PAIRED_WORST_CASE_REGRESSION")

    accepted = all(predicates.values())
    if accepted:
        reasons.append("ACCEPTED")
    return Decision(
        accepted=accepted,
        primary_value=proposed,
        champion_value=current,
        delta=delta,
        predicates=predicates,
        reasons=reasons,
        paired_summary=paired_summary,
    )


def target_met(spec: CampaignSpec, result: EvaluationResult) -> bool:
    validate_evaluation(spec, result)
    value = metric_value(result, spec.primary_metric.name)
    metric_ok = value >= spec.primary_metric.target if spec.primary_metric.direction == Direction.MAXIMIZE else value <= spec.primary_metric.target
    if not metric_ok:
        return False
    for guard in spec.guardrails:
        if not _compare(metric_value(result, guard.metric), guard):
            return False
    return all(result.checks[name].passed for name in spec.mandatory_checks)
