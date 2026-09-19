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
