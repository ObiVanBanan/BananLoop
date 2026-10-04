from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import EvaluationResult
from .evaluators import CommandEvaluator, EvaluatorError
from .process import run_process


class ReadinessError(RuntimeError):
    def __init__(self, message: str, receipt: dict[str, Any] | None = None):
        super().__init__(message)
        self.receipt = receipt or {}


def _primary_value(plan: Any, result: EvaluationResult) -> float:
    metric = result.metrics.get(plan.primary_metric.name)
    if metric is None:
        raise ReadinessError(
            f"smoke result is missing primary metric {plan.primary_metric.name!r}"
        )
    return float(metric.value)


def _validate_result(plan: Any, result: EvaluationResult) -> dict[str, Any]:
    if result.status != "ok":
        raise ReadinessError(f"smoke evaluator returned status={result.status}")

    required_metrics = {
        plan.primary_metric.name,
        *(guard.metric for guard in plan.guardrails),
        *(guard.metric for guard in plan.non_regression),
    }
    missing_metrics = sorted(required_metrics - set(result.metrics))
    if missing_metrics:
        raise ReadinessError(
            "smoke result is missing required metric(s): "
            + ", ".join(missing_metrics)
        )

    missing_checks = sorted(set(plan.mandatory_checks) - set(result.checks))
    if missing_checks:
        raise ReadinessError(
            "smoke result is missing mandatory check(s): "
            + ", ".join(missing_checks)
        )
    failed_checks = sorted(
        name
        for name in plan.mandatory_checks
        if not result.checks[name].passed
    )
    if failed_checks:
        raise ReadinessError(
            "smoke result failed mandatory check(s): "
            + ", ".join(failed_checks)
        )

    if plan.paired_comparison.enabled:
        if not result.cases:
            raise ReadinessError(
                "paired campaign smoke must emit at least one real case measurement"
            )
        missing_case_metric = sorted(
            {
                case.case_id
                for case in result.cases
                if plan.paired_comparison.metric not in case.metrics
            }
        )
        if missing_case_metric:
            raise ReadinessError(
                "paired smoke case(s) are missing metric "
                f"{plan.paired_comparison.metric!r}: "
                + ", ".join(missing_case_metric[:8])
            )

    return {
        "status": result.status,
        "metrics": {
            key: value.model_dump(mode="json")
            for key, value in result.metrics.items()
        },
        "checks": {
            key: value.model_dump(mode="json")
            for key, value in result.checks.items()
        },
        "protocol_sha256": result.protocol_sha256,
        "case_count": len(result.cases),
        "case_inputs": [
            {
                "case_id": case.case_id,
                "repetition": case.repetition,
                "input_sha256": case.input_sha256,
            }
            for case in result.cases
        ],
        "resource_units": result.resource_units,
    }


def run_bootstrap_readiness(
    *,
    plan: Any,
    workspace: Path,
    snapshot: str,
    state_root: Path,
    attempt: int,
    repeats: int = 2,
) -> dict[str, Any]:
    """Execute a core-owned evaluator smoke gate before a bootstrap plan can be READY."""

    command = list(plan.smoke_command or plan.evaluation_command)
    if not command:
        raise ReadinessError("bootstrap plan has no smoke/evaluation command")

    timeout = min(int(plan.evaluator_timeout_seconds), 300)
    receipt: dict[str, Any] = {
        "status": "RUNNING",
        "snapshot": snapshot,
        "command": command,
        "timeout_seconds": timeout,
        "runs": [],
    }
    results: list[EvaluationResult] = []

    for repetition in range(repeats):
        artifact_dir = (
            state_root
            / "bootstrap-readiness"
            / f"attempt-{attempt}"
            / f"run-{repetition + 1}"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evaluator = CommandEvaluator(command)
        evaluator.context = {
            "BANANLOOP_ARTIFACT_DIR": str(artifact_dir.resolve()),
            "BANANLOOP_EVALUATION_ID": (
                f"bootstrap:attempt:{attempt}:smoke:{repetition + 1}"
            ),
            "BANANLOOP_SNAPSHOT": snapshot,
            "BANANLOOP_EVALUATION_TIER": "bootstrap_smoke",
            "BANANLOOP_WORKSPACE": str(workspace.resolve()),
        }
        try:
            result = evaluator.evaluate(workspace, timeout=timeout)
            summary = _validate_result(plan, result)
            dirty = run_process(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=workspace,
                timeout=10,
            ).stdout.strip()
            if dirty:
                raise ReadinessError(
                    "smoke evaluator dirtied the managed workspace; write artifacts "
                    "to BANANLOOP_ARTIFACT_DIR instead: " + dirty[:1000]
                )
        except (EvaluatorError, ReadinessError) as exc:
            receipt["status"] = "FAILED"
            receipt["error"] = str(exc)
            run_process(["git", "reset", "--hard", snapshot], cwd=workspace, timeout=30)
            run_process(["git", "clean", "-fd"], cwd=workspace, timeout=30)
            raise ReadinessError(str(exc), receipt) from exc
        results.append(result)
        receipt["runs"].append(summary)
        run_process(["git", "reset", "--hard", snapshot], cwd=workspace, timeout=30)
        run_process(["git", "clean", "-fd"], cwd=workspace, timeout=30)

    first_inputs = {
        (case.case_id, case.repetition, case.input_sha256)
        for case in results[0].cases
    }
    stable_inputs = all(
        {
            (case.case_id, case.repetition, case.input_sha256)
            for case in result.cases
        }
        == first_inputs
        for result in results[1:]
    )
    if not stable_inputs:
        receipt["status"] = "FAILED"
        receipt["input_identity_stable"] = False
        receipt["error"] = "smoke evaluator changed case/input identity across A/A runs"
        raise ReadinessError(receipt["error"], receipt)

    protocols = [result.protocol_sha256 for result in results]
    stable_protocol = len(set(protocols)) <= 1
    receipt["protocol_sha256_values"] = protocols
    receipt["protocol_identity_stable"] = stable_protocol
    if not stable_protocol:
        receipt["status"] = "FAILED"
        receipt["error"] = "smoke evaluator changed protocol identity across A/A runs"
        raise ReadinessError(receipt["error"], receipt)

    values = [_primary_value(plan, result) for result in results]
    primary_range = max(values) - min(values)
    threshold = float(plan.primary_metric.min_improvement)
    receipt["input_identity_stable"] = True
    receipt["primary_values"] = values
    receipt["primary_range"] = primary_range
    receipt["min_improvement"] = threshold

    if primary_range >= threshold:
        receipt["status"] = "FAILED"
        receipt["error"] = (
            "A/A smoke noise is as large as the minimum accepted improvement: "
            f"range={primary_range:g}, min_improvement={threshold:g}"
        )
        raise ReadinessError(receipt["error"], receipt)

    if plan.paired_comparison.enabled and results[0].cases:
        metric = plan.paired_comparison.metric
        per_run = []
        for result in results:
            per_run.append(
                {
                    (case.case_id, case.repetition, case.input_sha256): case.metrics[metric]
                    for case in result.cases
                }
            )
        max_case_range = 0.0
        for key in first_inputs:
            case_values = [run[key] for run in per_run]
            max_case_range = max(max_case_range, max(case_values) - min(case_values))
        receipt["paired_metric"] = metric
        receipt["max_case_metric_range"] = max_case_range
        if max_case_range >= threshold:
            receipt["status"] = "FAILED"
            receipt["error"] = (
                "paired-case A/A noise is as large as the minimum accepted "
                f"improvement: range={max_case_range:g}, "
                f"min_improvement={threshold:g}"
            )
            raise ReadinessError(receipt["error"], receipt)

    receipt["status"] = "PASSED"
    return receipt
