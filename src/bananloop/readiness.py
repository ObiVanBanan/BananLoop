from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import statistics
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import EvaluationProtocol, EvaluationResult
from .evaluators import CommandEvaluator, EvaluatorError
from .evaluators.command import _split_command_environment
from .envfiles import project_environment
from .integrity import discover_evaluation_assets, sha256_evaluation_asset
from .process import ProcessError, parse_json_output, run_process


def run_protocol_manifest(
    *,
    command: list[str],
    workspace: Path,
    snapshot: str,
    source_repo: Path,
    timeout: int = 30,
) -> tuple[EvaluationProtocol, dict[str, Any]]:
    """Enumerate exact evaluator inputs without running the expensive scorer."""

    if not command:
        raise ReadinessError(
            "fixed evaluation protocol requires protocol_manifest_command"
        )
    command_env, argv = _split_command_environment(command)
    trusted_env = project_environment(source_repo.resolve())
    context = {
        "BANANLOOP_EVALUATION_TIER": "bootstrap_manifest",
        "BANANLOOP_MANIFEST_ONLY": "1",
        "BANANLOOP_SNAPSHOT": snapshot,
        "BANANLOOP_WORKSPACE": str(workspace.resolve()),
    }
    before_head = run_process(
        ["git", "rev-parse", "HEAD"], cwd=workspace, timeout=10
    ).stdout.strip()
    before_dirty = run_process(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        timeout=10,
    ).stdout.strip()
    try:
        result = run_process(
            argv,
            cwd=workspace,
            timeout=min(int(timeout), 30),
            check=True,
            env={**trusted_env, **command_env, **context},
        )
        protocol = EvaluationProtocol.model_validate(
            parse_json_output(result.stdout)
        )
    except (ProcessError, ValidationError) as exc:
        raise ReadinessError(
            "evaluation protocol manifest failed: " + str(exc),
            {
                "status": "FAILED",
                "stage": "PROTOCOL_MANIFEST",
                "error": str(exc),
            },
        ) from exc

    after_head = run_process(
        ["git", "rev-parse", "HEAD"], cwd=workspace, timeout=10
    ).stdout.strip()
    after_dirty = run_process(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        timeout=10,
    ).stdout.strip()
    if before_head != snapshot or after_head != snapshot:
        raise ReadinessError(
            "protocol manifest command changed or observed the wrong evaluator snapshot",
            {"status": "FAILED", "stage": "PROTOCOL_MANIFEST"},
        )
    if after_dirty != before_dirty:
        raise ReadinessError(
            "protocol manifest command dirtied the managed workspace",
            {"status": "FAILED", "stage": "PROTOCOL_MANIFEST"},
        )

    groups = protocol.repetition_sets()
    repetition_sets = sorted(
        {tuple(sorted(values)) for values in groups.values()}
    )
    receipt = {
        "status": "PASSED",
        "stage": "PROTOCOL_MANIFEST",
        "manifest_sha256": protocol.manifest_sha256(),
        "case_count": protocol.case_count(),
        "measurement_count": len(protocol.inputs),
        "repetition_sets": [list(values) for values in repetition_sets],
    }
    return protocol, receipt


class ReadinessError(RuntimeError):
    def __init__(self, message: str, receipt: dict[str, Any] | None = None):
        super().__init__(message)
        self.receipt = receipt or {}


def _remove_smoke_workspace(path: Path) -> None:
    def make_writable_and_retry(function, name: str, exc_info) -> None:
        error = exc_info[1]
        if isinstance(error, PermissionError):
            path_stat = os.lstat(name)
            reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            is_reparse_point = bool(
                stat.S_ISLNK(path_stat.st_mode)
                or getattr(path_stat, "st_file_attributes", 0) & reparse_attribute
            )
            if is_reparse_point:
                raise PermissionError(
                    f"refusing to change permissions through symlink/reparse point: {name}"
                ) from error
            try:
                if os.name == "nt":
                    # Windows lacks chmod's no-follow form. lstat/reparse-point
                    # rejection above ensures the path is not a link target.
                    os.chmod(name, path_stat.st_mode | stat.S_IWUSR)
                else:
                    os.chmod(
                        name,
                        path_stat.st_mode | stat.S_IWUSR,
                        follow_symlinks=False,
                    )
            except (NotImplementedError, OSError) as chmod_error:
                raise PermissionError(
                    f"cannot safely make smoke workspace path writable: {name}"
                ) from chmod_error
            function(name)
            return
        raise error

    shutil.rmtree(path, onerror=make_writable_and_retry)


def _primary_value(plan: Any, result: EvaluationResult) -> float:
    metric = result.metrics.get(plan.primary_metric.name)
    if metric is None:
        raise ReadinessError(
            f"smoke result is missing primary metric {plan.primary_metric.name!r}"
        )
    return float(metric.value)


def _signal_sanity(plan: Any, results: list[EvaluationResult]) -> dict[str, Any]:
    contract = plan.signal_sanity
    if contract.status == "unavailable":
        evidence = {
            "status": "UNAVAILABLE",
            "reason": contract.reason,
            "evidence_kind": None,
            "metric": None,
            "positive_case_ids": [],
            "negative_case_ids": [],
            "repeat_results": [],
        }
    else:
        primary = plan.primary_metric
        if contract.metric != primary.name:
            raise ReadinessError(
                "signal sanity metric must equal the primary metric "
                f"{primary.name!r}; got {contract.metric!r}",
                {"signal_sanity": {"status": "FAILED", "error": "wrong_metric"}},
            )
        positive_ids = list(contract.positive_case_ids)
        negative_ids = list(contract.negative_case_ids)
        repeat_results: list[dict[str, Any]] = []
        for index, result in enumerate(results, start=1):
            values: dict[str, list[float]] = {}
            for case in result.cases:
                if case.case_id in positive_ids or case.case_id in negative_ids:
                    value = case.metrics.get(primary.name)
                    if value is None:
                        raise ReadinessError(
                            f"signal sanity case {case.case_id!r} is missing primary metric {primary.name!r}",
                            {"signal_sanity": {"status": "FAILED", "error": "missing_primary_metric", "case_id": case.case_id}},
                        )
                    values.setdefault(case.case_id, []).append(float(value))
            missing = sorted((set(positive_ids) | set(negative_ids)) - set(values))
            if missing:
                raise ReadinessError(
                    f"signal sanity smoke run {index} is missing configured case(s): " + ", ".join(missing),
                    {"signal_sanity": {"status": "FAILED", "error": "missing_cases", "missing_case_ids": missing, "repeat": index}},
                )
            # First average repetitions within each case, then average cases so
            # repeated records cannot increase an example's weight.
            per_case = {case_id: statistics.fmean(samples) for case_id, samples in values.items()}
            positive_mean = statistics.fmean(per_case[case_id] for case_id in positive_ids)
            negative_mean = statistics.fmean(per_case[case_id] for case_id in negative_ids)
            separation = (
                positive_mean - negative_mean
                if primary.direction == "maximize"
                else negative_mean - positive_mean
            )
            repeat_results.append({
                "repeat": index,
                "positive_mean": positive_mean,
                "negative_mean": negative_mean,
                "directed_separation": separation,
                "per_case_means": {key: per_case[key] for key in sorted(per_case)},
                "passed": separation >= float(primary.min_improvement),
            })
        evidence = {
            # Bootstrap-controlled case labels are not provenance proof. A
            # passing separation check is diagnostic only, never scorer approval.
            "status": "DIAGNOSTIC_ONLY" if all(item["passed"] for item in repeat_results) else "FAILED",
            "reason": None,
            "evidence_kind": contract.evidence_kind,
            "metric": primary.name,
            "direction": primary.direction.value,
            "min_improvement": float(primary.min_improvement),
            "positive_case_ids": sorted(positive_ids),
            "negative_case_ids": sorted(negative_ids),
            "repeat_results": repeat_results,
        }
        if evidence["status"] == "FAILED":
            error = "signal sanity positive/negative groups do not separate by primary min_improvement in every A/A run"
            raise ReadinessError(error, {"signal_sanity": evidence | {"error": error}})

    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    evidence["receipt_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return evidence


def _validate_result(plan: Any, result: EvaluationResult) -> dict[str, Any]:
    if result.status != "ok":
        raise ReadinessError(f"smoke evaluator returned status={result.status}")

    # Readiness proves that the evaluator can execute the measurement protocol,
    # not that a tiny smoke subset can already satisfy every scientific contract.
    # The primary metric is the minimum aggregate signal required here. Guardrail
    # and non-regression metrics are enforced on full DEV/VALIDATION/HOLDOUT
    # results by policy.validate_evaluation()/decide().
    required_metrics = {plan.primary_metric.name}
    missing_metrics = sorted(required_metrics - set(result.metrics))
    if missing_metrics:
        raise ReadinessError(
            "smoke result is missing required metric(s): "
            + ", ".join(missing_metrics)
        )
    deferred_metrics = sorted({
        *(guard.metric for guard in plan.guardrails),
        *(guard.metric for guard in plan.non_regression),
    })
    observed_deferred_metrics = {
        name: (
            result.metrics[name].model_dump(mode="json")
            if name in result.metrics
            else None
        )
        for name in deferred_metrics
    }

    # Scientific mandatory checks belong to the full DEV/VALIDATION/HOLDOUT
    # evaluation contract. A readiness smoke may intentionally run only a small
    # subset, so checks such as "case_count == 100" can be absent or false here
    # without making the evaluator itself unreadable/unusable. Full evaluation
    # still enforces every mandatory check through policy.validate_evaluation().
    deferred_checks = {
        name: (
            result.checks[name].model_dump(mode="json")
            if name in result.checks
            else None
        )
        for name in plan.mandatory_checks
    }

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
        "deferred_mandatory_checks": deferred_checks,
        "deferred_scientific_metrics": observed_deferred_metrics,
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
    repeats: int = 3,
    source_repo: Path | None = None,
    runtime_resources: list[str] | None = None,
) -> dict[str, Any]:
    """Execute a core-owned evaluator smoke gate before a bootstrap plan can be READY."""

    if not 2 <= repeats <= 5:
        raise ValueError("readiness repeats must be bounded between 2 and 5")

    command = list(plan.smoke_command or plan.evaluation_command)
    if not command:
        raise ReadinessError("bootstrap plan has no smoke/evaluation command")

    # Readiness must stay cheap. The full DEV evaluator may legitimately need a
    # long timeout, but bootstrap smoke must never silently become a full
    # benchmark repeated several times.
    timeout = min(int(plan.evaluator_timeout_seconds), 90)
    receipt: dict[str, Any] = {
        "status": "RUNNING",
        "snapshot": snapshot,
        "command": command,
        "timeout_seconds": timeout,
        "full_evaluator_timeout_seconds": int(plan.evaluator_timeout_seconds),
        "smoke_timeout_cap_seconds": 90,
        "repeat_count": repeats,
        "runs": [],
        "mandatory_checks_mode": "deferred_to_full_evaluation",
        "mandatory_checks": list(plan.mandatory_checks),
        "guardrail_metrics_mode": "deferred_to_full_evaluation",
        "guardrail_metrics": sorted({guard.metric for guard in plan.guardrails}),
        "non_regression_metrics": sorted({
            guard.metric for guard in plan.non_regression
        }),
    }
    results: list[EvaluationResult] = []
    trusted_source = (source_repo or workspace).resolve()
    trusted_env = project_environment(trusted_source)
    runtime_resources = list(runtime_resources or [])

    for repetition in range(repeats):
        run_dir = (
            state_root
            / "bootstrap-readiness"
            / f"attempt-{attempt}"
            / f"run-{repetition + 1}"
        )
        artifact_dir = run_dir / "artifacts"
        smoke_workspace = run_dir / "workspace"
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evaluator = CommandEvaluator(command, base_env=trusted_env)
        evaluator.context = {
            "BANANLOOP_ARTIFACT_DIR": str(artifact_dir.resolve()),
            "BANANLOOP_EVALUATION_ID": (
                f"bootstrap:attempt:{attempt}:smoke:{repetition + 1}"
            ),
            "BANANLOOP_SNAPSHOT": snapshot,
            "BANANLOOP_EVALUATION_TIER": "bootstrap_smoke",
            "BANANLOOP_WORKSPACE": str(smoke_workspace.resolve()),
        }
        try:
            run_process(
                ["git", "clone", "--no-hardlinks", "--no-checkout", str(workspace), str(smoke_workspace)],
                cwd=run_dir,
                timeout=120,
            )
            run_process(["git", "checkout", "--detach", snapshot], cwd=smoke_workspace, timeout=30)
            run_process(["git", "config", "user.email", "bananloop@localhost"], cwd=smoke_workspace, timeout=10)
            run_process(["git", "config", "user.name", "BananLoop Readiness"], cwd=smoke_workspace, timeout=10)

            # Re-bind non-secret runtime files discovered in the source checkout.
            # Secret dotenv files are never materialized into agent/evaluator workspaces;
            # their values enter only the trusted evaluator process environment.
            for relative in runtime_resources:
                source = trusted_source / relative
                target = smoke_workspace / relative
                if source.is_file() and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            # The clone supplies only committed files. Preserve evaluator assets
            # referenced by the command even when the source checkout ignored them,
            # without copying arbitrary ignored runtime state between repeats.
            for asset in discover_evaluation_assets(workspace, command):
                source = workspace / asset.path
                target = smoke_workspace / asset.path
                if not target.is_file() or sha256_evaluation_asset(target) != asset.sha256:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            head = run_process(["git", "rev-parse", "HEAD"], cwd=smoke_workspace, timeout=10).stdout.strip()
            if head != snapshot:
                raise ReadinessError(
                    f"smoke workspace snapshot drifted before run {repetition + 1}: "
                    f"expected {snapshot}, found {head}"
                )
            result = evaluator.evaluate(smoke_workspace, timeout=timeout)
            summary = _validate_result(plan, result)
            head = run_process(["git", "rev-parse", "HEAD"], cwd=smoke_workspace, timeout=10).stdout.strip()
            if head != snapshot:
                raise ReadinessError(
                    f"smoke evaluator changed workspace snapshot during run "
                    f"{repetition + 1}: expected {snapshot}, found {head}"
                )
            dirty = run_process(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=smoke_workspace,
                timeout=10,
            ).stdout.strip()
            if dirty:
                raise ReadinessError(
                    "smoke evaluator dirtied the managed workspace; write artifacts "
                    "to BANANLOOP_ARTIFACT_DIR instead: " + dirty[:1000]
                )
        except (EvaluatorError, ProcessError, ReadinessError) as exc:
            receipt["status"] = "FAILED"
            receipt["error"] = str(exc)
            raise ReadinessError(str(exc), receipt) from exc
        finally:
            if smoke_workspace.exists():
                _remove_smoke_workspace(smoke_workspace)
        results.append(result)
        receipt["runs"].append(summary)

    first_inputs = sorted(
        (case.case_id, case.repetition, case.input_sha256)
        for case in results[0].cases
    )
    if len(first_inputs) != len(set(first_inputs)):
        receipt["status"] = "FAILED"
        receipt["input_identity_stable"] = False
        receipt["error"] = "smoke evaluator emitted duplicate case/input identities"
        raise ReadinessError(receipt["error"], receipt)
    stable_inputs = all(
        sorted(
            (case.case_id, case.repetition, case.input_sha256)
            for case in result.cases
        )
        == first_inputs
        for result in results[1:]
    )
    if not stable_inputs:
        receipt["status"] = "FAILED"
        receipt["input_identity_stable"] = False
        receipt["error"] = "smoke evaluator changed case/input identity across A/A runs"
        raise ReadinessError(receipt["error"], receipt)

    protocols = [result.protocol_sha256 for result in results]
    stable_protocol = all(protocols) and len(set(protocols)) == 1
    receipt["protocol_sha256_values"] = protocols
    receipt["protocol_identity_stable"] = stable_protocol
    if not stable_protocol:
        receipt["status"] = "FAILED"
        receipt["error"] = (
            "smoke evaluator must provide one stable protocol identity across A/A runs"
        )
        raise ReadinessError(receipt["error"], receipt)

    values = [_primary_value(plan, result) for result in results]
    median = statistics.median(values)
    primary_range = max(values) - min(values)
    threshold = float(plan.primary_metric.min_improvement)
    receipt["input_identity_stable"] = True
    receipt["primary_values"] = values
    receipt["primary_range"] = primary_range
    receipt["min_improvement"] = threshold
    receipt["calibration"] = {
        "repeat_count": len(values),
        "primary_metric": plan.primary_metric.name,
        "median": median,
        "empirical_noise_bound": primary_range,
        "min_improvement": threshold,
        "noise_gate_passed": primary_range < threshold,
    }

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
        receipt["calibration"]["paired_metric"] = metric
        receipt["calibration"]["paired_empirical_noise_bound"] = max_case_range
        paired_tolerance = float(plan.paired_comparison.worst_case_tolerance)
        receipt["calibration"]["paired_noise_threshold"] = paired_tolerance
        receipt["calibration"]["paired_noise_gate_passed"] = max_case_range <= paired_tolerance
        if max_case_range > paired_tolerance:
            receipt["status"] = "FAILED"
            receipt["error"] = (
                "paired-case A/A noise exceeds its worst-case tolerance: "
                f"range={max_case_range:g}, worst_case_tolerance={paired_tolerance:g}"
            )
            raise ReadinessError(receipt["error"], receipt)

    try:
        receipt["signal_sanity"] = _signal_sanity(plan, results)
    except ReadinessError as exc:
        receipt["status"] = "FAILED"
        receipt["error"] = str(exc)
        # Preserve the complete readiness evidence for bounded bootstrap repair.
        if exc.receipt:
            receipt.update(exc.receipt)
        raise ReadinessError(str(exc), receipt) from exc

    receipt["status"] = "PASSED"
    return receipt
