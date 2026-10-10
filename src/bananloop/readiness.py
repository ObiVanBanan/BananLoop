from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import statistics
import time
from pathlib import Path
from typing import Any, Callable

from .artifacts import retain_artifacts
from .contracts import EvaluationResult
from .evaluators import CommandEvaluator, EvaluatorError
from .integrity import discover_evaluation_assets, sha256_evaluation_asset
from .evaluator_binding import canonical_hash, runtime_descriptor
from .process import ProcessError, run_process


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


def _run_readiness_command(
    *,
    command: list[str],
    workspace: Path,
    snapshot: str,
    state_root: Path,
    run_dir: Path,
    run_id: str,
    tier: str,
    timeout: int,
    assets: list[Any],
    frozen_asset_root: Path,
    plan: Any,
    on_evaluator_start: Callable[[], dict[str, Any]] | None = None,
    on_evaluator_finish: Callable[[dict[str, Any], str, str | None], None] | None = None,
) -> tuple[EvaluationResult, dict[str, Any]]:
    artifact_dir = run_dir / "artifacts"
    managed_workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    evaluator = CommandEvaluator(command)
    evaluator.context = {
        "BANANLOOP_ARTIFACT_DIR": str(artifact_dir.resolve()),
        "BANANLOOP_EVALUATION_ID": run_id,
        "BANANLOOP_SNAPSHOT": snapshot,
        "BANANLOOP_EVALUATION_TIER": tier,
        "BANANLOOP_WORKSPACE": str(managed_workspace.resolve()),
    }
    try:
        run_process(
            ["git", "clone", "--no-hardlinks", "--no-checkout", str(workspace), str(managed_workspace)],
            cwd=run_dir,
            timeout=120,
        )
        run_process(["git", "checkout", "--detach", snapshot], cwd=managed_workspace, timeout=30)
        run_process(["git", "config", "user.email", "bananloop@localhost"], cwd=managed_workspace, timeout=10)
        run_process(["git", "config", "user.name", "BananLoop Readiness"], cwd=managed_workspace, timeout=10)

        for asset in assets:
            source = frozen_asset_root / asset.path
            target = managed_workspace / asset.path
            if not target.is_file() or sha256_evaluation_asset(target) != asset.sha256:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        head = run_process(["git", "rev-parse", "HEAD"], cwd=managed_workspace, timeout=10).stdout.strip()
        if head != snapshot:
            raise ReadinessError(f"managed readiness workspace snapshot drifted: expected {snapshot}, found {head}")
        invocation = on_evaluator_start() if on_evaluator_start else None
        try:
            result = evaluator.evaluate(managed_workspace, timeout=timeout)
        except EvaluatorError as exc:
            cause = exc.__cause__
            unknown = isinstance(cause, ProcessError) and "timed out" in str(cause).casefold()
            if invocation is not None and on_evaluator_finish:
                on_evaluator_finish(
                    invocation,
                    "UNKNOWN" if unknown else "FAILED",
                    "timeout" if unknown else "evaluator_error",
                )
            raise
        else:
            if invocation is not None and on_evaluator_finish:
                on_evaluator_finish(invocation, "COMPLETED", None)
        summary = _validate_result(plan, result)
        retain_artifacts(result, artifact_dir, None, snapshot)
        head = run_process(["git", "rev-parse", "HEAD"], cwd=managed_workspace, timeout=10).stdout.strip()
        if head != snapshot:
            raise ReadinessError("readiness evaluator changed workspace snapshot in the managed checkout")
        dirty = run_process(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=managed_workspace,
            timeout=10,
        ).stdout.strip()
        if dirty:
            raise ReadinessError(
                "readiness evaluator dirtied the managed workspace; write artifacts "
                "to BANANLOOP_ARTIFACT_DIR instead: " + dirty[:1000]
            )
    except (EvaluatorError, ProcessError, ReadinessError):
        raise
    finally:
        if managed_workspace.exists():
            _remove_smoke_workspace(managed_workspace)

    result_path = run_dir / "evaluation-result.json"
    result_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    input_manifest = [
        {"case_id": case.case_id, "repetition": case.repetition, "input_sha256": case.input_sha256}
        for case in result.cases
    ]
    summary["replay"] = {
        "result_path": result_path.relative_to(state_root).as_posix(),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "case_input_manifest_sha256": canonical_hash(input_manifest),
        "evaluation_assets": [asset.model_dump(mode="json") for asset in assets],
    }
    return result, summary


def _compare_smoke_full(
    smoke: EvaluationResult,
    full: EvaluationResult,
    *,
    metric_noise_bounds: dict[str, float],
    metric_relative_noise_bounds: dict[str, float],
    case_metric_noise_bounds: dict[tuple[str, int], dict[str, float]],
    case_metric_relative_noise_bounds: dict[tuple[str, int], dict[str, float]],
) -> dict[str, Any]:
    mismatches: list[str] = []
    if not smoke.protocol_sha256 or smoke.protocol_sha256 != full.protocol_sha256:
        mismatches.append("smoke and full evaluators have different protocol identities")
    smoke_cases = {(case.case_id, case.repetition): case for case in smoke.cases}
    full_cases = {(case.case_id, case.repetition): case for case in full.cases}
    missing = sorted(set(smoke_cases) - set(full_cases))
    if missing:
        mismatches.append(f"full evaluator omitted {len(missing)} smoke case identities")

    compared = 0
    for key in sorted(set(smoke_cases) & set(full_cases)):
        before = smoke_cases[key]
        after = full_cases[key]
        if before.input_sha256 != after.input_sha256:
            mismatches.append(f"case {key!r} input identity differs between smoke and full")
            continue
        if set(before.metrics) != set(after.metrics):
            mismatches.append(f"case {key!r} metric set differs between smoke and full")
            continue
        for name, before_value in before.metrics.items():
            after_value = after.metrics[name]
            key_noise = case_metric_noise_bounds.get(key, {}).get(
                name, metric_noise_bounds.get(name, 0.0)
            )
            key_relative_noise = case_metric_relative_noise_bounds.get(key, {}).get(
                name, metric_relative_noise_bounds.get(name, 0.0)
            )
            tolerance = max(
                max(0.0, float(key_noise)),
                max(0.0, float(key_relative_noise))
                * abs(before_value),
            )
            if abs(before_value - after_value) > tolerance:
                mismatches.append(f"case {key!r} metric {name!r} differs beyond A/A tolerance")
        compared += 1

    comparison_mode = "per_case" if smoke_cases else "aggregate_contract_only"
    if set(smoke.metrics) != set(full.metrics):
        mismatches.append("smoke and full evaluators expose different aggregate metric names")
    else:
        for name in smoke.metrics:
            if smoke.metrics[name].unit != full.metrics[name].unit:
                mismatches.append(f"aggregate metric {name!r} unit differs between smoke and full")
    return {
        "status": "FAILED" if mismatches else ("INCONCLUSIVE" if not smoke_cases else "PASSED"),
        "comparison_mode": comparison_mode,
        "smoke_case_count": len(smoke_cases),
        "full_case_count": len(full_cases),
        "compared_case_count": compared,
        "protocol_sha256": smoke.protocol_sha256,
        "mismatches": mismatches,
    }


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


def _relative_noise_bound(noise: float, center: float) -> float:
    """Avoid unstable relative tolerances when the measured center is noise-sized."""
    magnitude = abs(float(center))
    absolute_noise = max(0.0, float(noise))
    if magnitude == 0.0 or magnitude <= absolute_noise:
        return 0.0
    return absolute_noise / magnitude


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
    repeats: int = 3,
    on_receipt_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a core-owned evaluator smoke gate before a bootstrap plan can be READY."""

    if not 2 <= repeats <= 5:
        raise ValueError("readiness repeats must be bounded between 2 and 5")

    smoke_command = list(plan.smoke_command or plan.evaluation_command)
    full_command = list(plan.evaluation_command)
    if not smoke_command or not full_command:
        raise ReadinessError("bootstrap plan has no smoke/evaluation command")

    timeout = min(int(plan.evaluator_timeout_seconds), 300)
    state_root = state_root.resolve()
    assets_by_path = {
        asset.path: asset
        for command in (smoke_command, full_command)
        for asset in discover_evaluation_assets(workspace, command)
    }
    assets = [assets_by_path[key] for key in sorted(assets_by_path)]
    frozen_asset_root = state_root / "bootstrap-readiness" / f"attempt-{attempt}" / "evaluation-assets"
    for asset in assets:
        source = workspace / asset.path
        if not source.is_file() or sha256_evaluation_asset(source) != asset.sha256:
            raise ReadinessError(f"bootstrap evaluator asset changed before readiness: {asset.path}")
        target = frozen_asset_root / asset.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    runtime = runtime_descriptor({"smoke": smoke_command, "dev": full_command})
    runtime_fingerprint = canonical_hash(runtime)
    receipt: dict[str, Any] = {
        "status": "RUNNING",
        "snapshot": snapshot,
        "command": smoke_command,
        "full_evaluation_command": full_command,
        "timeout_seconds": timeout,
        "repeat_count": repeats,
        "runs": [],
        "evaluator_invocations": [],
    }

    def persist_receipt() -> None:
        if on_receipt_update is not None:
            on_receipt_update(receipt)

    def begin_invocation(action_id: str, tier: str) -> dict[str, Any]:
        invocation = {"action_id": action_id, "tier": tier, "status": "RUNNING"}
        receipt["evaluator_invocations"].append(invocation)
        persist_receipt()
        return invocation

    def finish_invocation(
        invocation: dict[str, Any], status: str, failure_kind: str | None
    ) -> None:
        invocation["status"] = status
        if failure_kind is not None:
            invocation["failure_kind"] = failure_kind
        persist_receipt()

    results: list[EvaluationResult] = []

    for repetition in range(repeats):
        run_dir = (
            state_root
            / "bootstrap-readiness"
            / f"attempt-{attempt}"
            / f"run-{repetition + 1}"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            result, summary = _run_readiness_command(
                command=smoke_command,
                workspace=workspace,
                snapshot=snapshot,
                state_root=state_root,
                run_dir=run_dir,
                run_id=f"bootstrap:attempt:{attempt}:smoke:{repetition + 1}",
                tier="bootstrap_smoke",
                timeout=timeout,
                assets=assets,
                frozen_asset_root=frozen_asset_root,
                plan=plan,
                on_evaluator_start=lambda run_id=f"bootstrap:attempt:{attempt}:smoke:{repetition + 1}": begin_invocation(run_id, "bootstrap_smoke"),
                on_evaluator_finish=finish_invocation,
            )
        except (EvaluatorError, ProcessError, ReadinessError) as exc:
            receipt["status"] = "FAILED"
            receipt["error"] = str(exc)
            raise ReadinessError(str(exc), receipt) from exc
        results.append(result)
        summary["replay"].update({
            "runtime": runtime,
            "runtime_fingerprint": runtime_fingerprint,
            "evaluation_assets_root": frozen_asset_root.relative_to(state_root).as_posix(),
        })
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
    metric_names = set(results[0].metrics)
    if any(set(result.metrics) != metric_names for result in results[1:]):
        receipt["status"] = "FAILED"
        receipt["error"] = "smoke evaluator changed aggregate metric names across A/A runs"
        raise ReadinessError(receipt["error"], receipt)
    metric_noise_bounds: dict[str, float] = {}
    metric_relative_noise_bounds: dict[str, float] = {}
    case_metric_noise_bounds: dict[tuple[str, int], dict[str, float]] = {}
    case_metric_relative_noise_bounds: dict[tuple[str, int], dict[str, float]] = {}
    for name in sorted(metric_names):
        measurements = [result.metrics[name] for result in results]
        if any(
            item.unit != measurements[0].unit or item.n != measurements[0].n
            for item in measurements[1:]
        ):
            receipt["status"] = "FAILED"
            receipt["error"] = f"smoke evaluator changed aggregate metric metadata for {name!r} across A/A runs"
            raise ReadinessError(receipt["error"], receipt)
        metric_noise_bounds[name] = max(item.value for item in measurements) - min(
            item.value for item in measurements
        )
        metric_median = statistics.median(item.value for item in measurements)
        metric_relative_noise_bounds[name] = _relative_noise_bound(
            metric_noise_bounds[name], metric_median
        )
    case_maps = [
        {(case.case_id, case.repetition): case for case in result.cases}
        for result in results
    ]
    for key in sorted(case_maps[0]):
        measurements = [case_map[key] for case_map in case_maps]
        metric_names = set(measurements[0].metrics)
        if any(set(item.metrics) != metric_names for item in measurements[1:]):
            receipt["status"] = "FAILED"
            receipt["error"] = f"smoke evaluator changed per-case metric names for {key!r} across A/A runs"
            raise ReadinessError(receipt["error"], receipt)
        case_metric_noise_bounds[key] = {}
        case_metric_relative_noise_bounds[key] = {}
        for name in sorted(metric_names):
            case_values = [item.metrics[name] for item in measurements]
            noise = max(case_values) - min(case_values)
            center = statistics.median(case_values)
            case_metric_noise_bounds[key][name] = noise
            case_metric_relative_noise_bounds[key][name] = _relative_noise_bound(noise, center)
    receipt["case_metric_noise_bounds"] = [
        {
            "case_id": case_id,
            "repetition": repetition,
            "metrics": case_metric_noise_bounds[(case_id, repetition)],
            "relative_metrics": case_metric_relative_noise_bounds[(case_id, repetition)],
        }
        for case_id, repetition in sorted(case_metric_noise_bounds)
    ]
    receipt["input_identity_stable"] = True
    receipt["primary_values"] = values
    receipt["primary_range"] = primary_range
    receipt["min_improvement"] = threshold
    receipt["calibration"] = {
        "repeat_count": len(values),
        "primary_metric": plan.primary_metric.name,
        "median": median,
        "empirical_noise_bound": primary_range,
        "metric_noise_bounds": metric_noise_bounds,
        "metric_relative_noise_bounds": metric_relative_noise_bounds,
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

    full_run_dir = state_root / "bootstrap-readiness" / f"attempt-{attempt}" / "full-evaluation"
    try:
        full_result, full_summary = _run_readiness_command(
            command=full_command,
            workspace=workspace,
            snapshot=snapshot,
            state_root=state_root,
            run_dir=full_run_dir,
            run_id=f"bootstrap:attempt:{attempt}:full-evaluation",
            tier="dev",
            timeout=timeout,
            assets=assets,
            frozen_asset_root=frozen_asset_root,
            plan=plan,
            on_evaluator_start=lambda: begin_invocation(
                f"bootstrap:attempt:{attempt}:full", "bootstrap_full"
            ),
            on_evaluator_finish=finish_invocation,
        )
    except (EvaluatorError, ProcessError, ReadinessError) as exc:
        receipt["status"] = "FAILED"
        receipt["full_evaluation_error"] = str(exc)
        receipt["error"] = "full evaluator baseline failed during smoke/full equivalence check: " + str(exc)
        raise ReadinessError(receipt["error"], receipt) from exc
    full_summary["replay"].update({
        "runtime": runtime,
        "runtime_fingerprint": runtime_fingerprint,
        "evaluation_assets_root": frozen_asset_root.relative_to(state_root).as_posix(),
    })
    equivalence_noise_bounds = dict(metric_noise_bounds)
    paired_metric = plan.paired_comparison.metric if plan.paired_comparison.enabled else None
    paired_noise_bound = receipt["calibration"].get("paired_empirical_noise_bound")
    if paired_metric and paired_noise_bound is not None:
        equivalence_noise_bounds[paired_metric] = max(
            equivalence_noise_bounds.get(paired_metric, 0.0), float(paired_noise_bound)
        )
    equivalence = _compare_smoke_full(
        results[0],
        full_result,
        metric_noise_bounds=equivalence_noise_bounds,
        metric_relative_noise_bounds=metric_relative_noise_bounds,
        case_metric_noise_bounds=case_metric_noise_bounds,
        case_metric_relative_noise_bounds=case_metric_relative_noise_bounds,
    )
    receipt["full_evaluation"] = full_summary
    receipt["smoke_full_equivalence"] = equivalence
    if equivalence["status"] == "FAILED":
        receipt["status"] = "FAILED"
        receipt["error"] = "smoke and full evaluator are not equivalent on their shared cases"
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
