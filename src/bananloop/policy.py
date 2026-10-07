from __future__ import annotations

import math
import statistics

from .contracts import (
    CampaignSpec,
    Decision,
    Direction,
    EvaluationProtocol,
    EvaluationResult,
    GuardrailSpec,
)


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


def validate_evaluation_protocol(
    protocol: EvaluationProtocol | None,
    result: EvaluationResult,
) -> None:
    if protocol is None:
        return
    expected = {
        (item.case_id, item.repetition, item.input_sha256)
        for item in protocol.inputs
    }
    observed = {
        (item.case_id, item.repetition, item.input_sha256)
        for item in result.cases
    }
    if expected != observed:
        missing = len(expected - observed)
        unexpected = len(observed - expected)
        raise InvalidEvaluation(
            "evaluation protocol manifest mismatch: "
            f"expected={len(expected)}, observed={len(observed)}, "
            f"missing={missing}, unexpected={unexpected}"
        )


def validate_evaluation(spec: CampaignSpec, result: EvaluationResult) -> None:
    if spec.paired_comparison:
        paired = spec.paired_comparison
        groups = {}
        for case in result.cases:
            groups.setdefault(case.case_id, set()).add(case.repetition)
            for name in {paired.metric, *(g.metric for g in paired.guardrails), *(g.metric for g in paired.non_regression)}:
                if name not in case.metrics:
                    raise InvalidEvaluation(f"missing paired metric: {name}")
        if len(groups) != paired.case_count or any(v != set(range(paired.repetitions)) for v in groups.values()):
            raise InvalidEvaluation("incomplete fixed case/repetition set")
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
    if spec.paired_comparison:
        paired = spec.paired_comparison
        left = {(c.case_id, c.repetition): c for c in champion.cases}
        right = {(c.case_id, c.repetition): c for c in candidate.cases}
        aligned = (champion.protocol_sha256 == candidate.protocol_sha256
                   and left.keys() == right.keys()
                   and all(left[k].input_sha256 == right[k].input_sha256 for k in left))
        predicates["paired:aligned"] = aligned
        if not aligned:
            reasons.append("PAIRED_INPUT_MISMATCH")
        else:
            sign = 1 if paired.direction == Direction.MAXIMIZE else -1
            deltas = {}
            for key in left:
                a, b = left[key], right[key]
                deltas.setdefault(key[0], []).append(sign * (b.metrics[paired.metric] - a.metrics[paired.metric]))
            means = [statistics.fmean(v) for v in deltas.values()]
            predicates["paired:minimum_wins"] = sum(d > 0 for d in means) >= paired.min_wins
            # Worst repetition, not just the mean: a bad seed cannot be concealed.
            predicates["paired:worst_case"] = min(d for ds in deltas.values() for d in ds) >= -paired.worst_case_tolerance
            for guard in paired.guardrails:
                predicates[f"paired:guard:{guard.metric}:{guard.operator}:{guard.bound}"] = all(_compare(c.metrics[guard.metric], guard) for c in right.values())
            for guard in paired.non_regression:
                sign = 1 if guard.direction == Direction.MAXIMIZE else -1
                predicates[f"paired:non_regression:{guard.metric}:{guard.direction.value}:{guard.tolerance}"] = all(
                    sign * (right[k].metrics[guard.metric] - left[k].metrics[guard.metric]) >= -guard.tolerance for k in left)
            if not all(v for k, v in predicates.items() if k.startswith("paired:")):
                reasons.append("PAIRED_REGRESSION_OR_INSUFFICIENT_WINS")
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
    if spec.paired_comparison:
        for guard in spec.paired_comparison.guardrails:
            if not all(_compare(case.metrics[guard.metric], guard) for case in result.cases):
                return False
    return all(result.checks[name].passed for name in spec.mandatory_checks)
