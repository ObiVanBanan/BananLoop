from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import EvaluationAsset, EvaluationResult
from .evaluator_binding import (
    canonical_hash,
    runtime_descriptor,
    validate_receipt_linkage,
)
from .evaluators import CommandEvaluator, EvaluatorError
from .integrity import evaluation_fingerprint, verify_evaluation_assets
from .process import ProcessError, run_process
from .store import CampaignStore
from .workspace import WorkspaceError, WorkspaceManager


class ReplayInfrastructureError(RuntimeError):
    pass


class ReplayReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    replay_id: str
    source_kind: Literal["dev_action", "bootstrap_smoke"]
    source_action_id: str
    candidate_snapshot: str | None
    status: Literal["REPRODUCIBLE", "REPLAY_MISMATCH", "NOT_REPRODUCIBLE_INFRA"]
    reason: str | None = None
    original_result_sha256: str | None
    replay_result_sha256: str | None = None
    case_input_manifest_sha256: str | None = None
    protocol_sha256: str | None = None
    runtime_fingerprint: str | None
    comparisons: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = Field(default=None, ge=0)
    created_at: str
    receipt_sha256: str

    @model_validator(mode="after")
    def verify_identity(self):
        body = self.model_dump(mode="json")
        identity = body.pop("receipt_sha256")
        if self.duration_ms is None:
            body.pop("duration_ms", None)
        if identity != canonical_hash(body):
            raise ValueError("replay receipt hash mismatch")
        return self


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_hash(value: Any) -> str:
    return canonical_hash(value)


def _case_manifest(result: EvaluationResult) -> list[dict[str, Any]]:
    return sorted(
        [
            {"case_id": case.case_id, "repetition": case.repetition, "input_sha256": case.input_sha256}
            for case in result.cases
        ],
        key=lambda item: (item["case_id"], item["repetition"], item["input_sha256"]),
    )


def _float_equal(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _compare_results(
    original: EvaluationResult,
    replayed: EvaluationResult,
    *,
    metric_tolerances: dict[str, float],
    relative_metric_tolerances: dict[str, float] | None = None,
) -> dict[str, Any]:
    relative_metric_tolerances = relative_metric_tolerances or {}

    def tolerance_for(name: str, before: float, after: float) -> float:
        absolute = max(0.0, float(metric_tolerances.get(name, 0.0)))
        relative = max(0.0, float(relative_metric_tolerances.get(name, 0.0)))
        return max(absolute, relative * max(abs(before), abs(after)))

    comparisons: dict[str, Any] = {}
    mismatches: list[str] = []

    comparisons["result_status"] = original.status == replayed.status
    if original.status != replayed.status:
        mismatches.append("evaluation result status changed")

    original_cases = _case_manifest(original)
    replay_cases = _case_manifest(replayed)
    comparisons["case_input_identity"] = original_cases == replay_cases
    if original_cases != replay_cases:
        mismatches.append("case/input identities changed")

    comparisons["protocol_identity"] = original.protocol_sha256 == replayed.protocol_sha256
    if original.protocol_sha256 != replayed.protocol_sha256:
        mismatches.append("evaluation protocol identity changed")

    original_checks = {key: value.model_dump(mode="json") for key, value in original.checks.items()}
    replay_checks = {key: value.model_dump(mode="json") for key, value in replayed.checks.items()}
    comparisons["required_checks"] = original_checks == replay_checks
    if original_checks != replay_checks:
        mismatches.append("checks or their executed/skipped counts changed")

    metric_comparisons: dict[str, Any] = {}
    if set(original.metrics) != set(replayed.metrics):
        mismatches.append("aggregate metric set changed")
    for name in sorted(set(original.metrics) | set(replayed.metrics)):
        before = original.metrics.get(name)
        after = replayed.metrics.get(name)
        tolerance = tolerance_for(
            name,
            before.value if before is not None else 0.0,
            after.value if after is not None else 0.0,
        )
        passed = bool(
            before is not None
            and after is not None
            and before.unit == after.unit
            and before.n == after.n
            and _float_equal(before.value, after.value, tolerance)
        )
        metric_comparisons[name] = {
            "original": before.value if before else None,
            "replayed": after.value if after else None,
            "tolerance": tolerance,
            "passed": passed,
        }
        if not passed:
            mismatches.append(f"aggregate metric {name!r} changed outside tolerance")
    comparisons["metrics"] = metric_comparisons

    original_cases_by_key = {(case.case_id, case.repetition): case for case in original.cases}
    replay_cases_by_key = {(case.case_id, case.repetition): case for case in replayed.cases}
    per_case_mismatches: list[str] = []
    if set(original_cases_by_key) != set(replay_cases_by_key):
        per_case_mismatches.append("case/repetition keys changed")
    for key in sorted(set(original_cases_by_key) & set(replay_cases_by_key)):
        before = original_cases_by_key[key]
        after = replay_cases_by_key[key]
        if set(before.metrics) != set(after.metrics):
            per_case_mismatches.append(f"case {key!r} metric set changed")
            continue
        for name in sorted(before.metrics):
            tolerance = tolerance_for(name, before.metrics[name], after.metrics[name])
            if not _float_equal(before.metrics[name], after.metrics[name], tolerance):
                per_case_mismatches.append(f"case {key!r} metric {name!r} changed outside tolerance")
    comparisons["per_case_measurements"] = {
        "compared": len(set(original_cases_by_key) & set(replay_cases_by_key)),
        "passed": not per_case_mismatches,
        "mismatches": per_case_mismatches[:20],
    }
    if per_case_mismatches:
        mismatches.extend(per_case_mismatches[:20])

    def artifact_hashes(result: EvaluationResult) -> list[tuple[str, int, str]]:
        return sorted(
            (item.case_id, item.repetition, item.sha256)
            for item in result.artifact_manifest
        )

    artifact_equal = artifact_hashes(original) == artifact_hashes(replayed)
    comparisons["artifact_hashes"] = artifact_equal
    if not artifact_equal:
        mismatches.append("artifact identities or content hashes changed")

    comparisons["mismatches"] = mismatches
    return comparisons


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_child(root: Path, child: Path) -> Path:
    resolved_root = root.resolve()
    resolved_child = child.resolve()
    try:
        resolved_child.relative_to(resolved_root)
    except ValueError as exc:
        raise ReplayInfrastructureError("replay evidence path escapes campaign state") from exc
    return resolved_child


def _replay_dev_action(
    store: CampaignStore,
    workspace: WorkspaceManager,
    action_id: str,
) -> dict[str, Any]:
    """Re-run one completed DEV evaluator action without touching campaign state."""
    replay_id = uuid.uuid4().hex
    replay_root = store.path.parent / "replays" / replay_id
    replay_root.mkdir(parents=True, exist_ok=False)
    source_kind: Literal["dev_action", "bootstrap_smoke"] = (
        "bootstrap_smoke" if action_id.startswith("bootstrap:") else "dev_action"
    )
    relative_metric_tolerances: dict[str, float] = {}
    try:
        store.campaign()
        spec = store.spec()
        metric_tolerances = dict(spec.evaluation_evidence.metric_tolerances) if spec.evaluation_evidence else {}
        relative_metric_tolerances = dict(spec.evaluation_evidence.metric_relative_tolerances) if spec.evaluation_evidence else {}
    except (ReplayInfrastructureError, ValueError, KeyError, OSError) as exc:
        return _persist_receipt(
            replay_root, replay_id=replay_id, source_kind=source_kind, action_id=action_id,
            snapshot=None, status="NOT_REPRODUCIBLE_INFRA", reason=str(exc)[:1000],
            original_sha=None, replay_sha=None, case_manifest_sha=None, protocol=None,
            runtime_fp=None, comparisons={},
        )
    candidate_proof = None

    try:
        if action_id.startswith("bootstrap:"):
            metadata, original, command, timeout, snapshot, expected_runtime, assets, result_sha = _bootstrap_source(
                store, action_id
            )
            experiment_id = None
            tier = "bootstrap_smoke"
            calibration = metadata["readiness"]["calibration"]
            metric_tolerances = {
                str(name): float(value)
                for name, value in calibration.get("metric_noise_bounds", {}).items()
            }
            relative_metric_tolerances = {
                str(name): float(value)
                for name, value in calibration.get("metric_relative_noise_bounds", {}).items()
            }
            metric_tolerances.setdefault(
                calibration["primary_metric"],
                float(calibration["empirical_noise_bound"]),
            )
            paired = metadata["readiness"].get("paired_metric")
            paired_tolerance = calibration.get("paired_empirical_noise_bound")
            if paired and paired_tolerance is not None:
                metric_tolerances[paired] = max(
                    metric_tolerances.get(paired, 0.0), float(paired_tolerance)
                )
        else:
            action = store.action(action_id)
            if action is None:
                raise ReplayInfrastructureError(f"unknown evaluator action {action_id!r}")
            if action.get("kind") != "evaluator" or action.get("status") != "COMPLETED":
                raise ReplayInfrastructureError("replay requires a completed ordinary evaluator action")
            request = json.loads(action.get("request_json") or "{}")
            if request.get("tier", "dev") != "dev":
                raise ReplayInfrastructureError("only DEV evaluator actions may be replayed")
            if not action.get("response_json"):
                raise ReplayInfrastructureError("completed DEV action has no persisted result")
            binding_row = store.evaluator_binding()
            stored_receipt = store.evaluation_receipt(action_id)
            if binding_row is None or stored_receipt is None:
                raise ReplayInfrastructureError("action has no verifiable evaluator binding/receipt")
            try:
                receipt = validate_receipt_linkage(
                    stored_receipt["receipt"],
                    stored_receipt_hash=stored_receipt["receipt_hash"],
                    action=action,
                    binding_data=json.loads(binding_row["binding_json"]),
                    stored_binding_hash=binding_row["binding_hash"],
                )
            except Exception as exc:
                raise ReplayInfrastructureError(f"stored evaluator receipt is invalid: {exc}") from exc
            binding = json.loads(binding_row["binding_json"])
            original = EvaluationResult.model_validate_json(action["response_json"])
            command = list(request.get("command") or spec.evaluation_command)
            timeout = spec.evaluator_timeout_seconds
            snapshot = str(request.get("snapshot") or "")
            if not snapshot or snapshot != receipt.candidate_snapshot:
                raise ReplayInfrastructureError("DEV action snapshot does not match its receipt")
            if request.get("evaluation_fingerprint") != evaluation_fingerprint(spec.evaluation_assets):
                raise ReplayInfrastructureError("DEV action evaluator asset fingerprint is inconsistent")
            expected_runtime = binding.get("runtime")
            assets = spec.evaluation_assets
            result_sha = _json_hash(original.model_dump(mode="json"))
            candidate_proof = request.get("candidate_proof")
            experiment_id = action.get("experiment_id")
            tier = "dev"
    except (ReplayInfrastructureError, ValueError, KeyError, OSError) as exc:
        return _persist_receipt(
            replay_root, replay_id=replay_id, source_kind=source_kind, action_id=action_id,
            snapshot=None, status="NOT_REPRODUCIBLE_INFRA", reason=str(exc)[:1000],
            original_sha=None, replay_sha=None, case_manifest_sha=None, protocol=None,
            runtime_fp=None, comparisons={},
        )

    try:
        current_runtime = runtime_descriptor({"dev": command}) if source_kind == "bootstrap_smoke" else runtime_descriptor(
            binding["tier_commands"]
        )
    except (OSError, ValueError, KeyError) as exc:
        return _persist_receipt(
            replay_root, replay_id=replay_id, source_kind=source_kind, action_id=action_id,
            snapshot=snapshot, status="NOT_REPRODUCIBLE_INFRA", reason=str(exc)[:1000],
            original_sha=result_sha, replay_sha=None, case_manifest_sha=None,
            protocol=original.protocol_sha256, runtime_fp=None, comparisons={},
        )
    expected_runtime_fp = _json_hash(expected_runtime)
    actual_runtime_fp = _json_hash(current_runtime)
    replay_result_path = replay_root / "evaluation-result.json"

    if expected_runtime_fp != actual_runtime_fp:
        return _persist_receipt(
            replay_root,
            replay_id=replay_id,
            source_kind=source_kind,
            action_id=action_id,
            snapshot=snapshot,
            status="NOT_REPRODUCIBLE_INFRA",
            reason="effective evaluator runtime differs from the recorded runtime",
            original_sha=result_sha,
            replay_sha=None,
            case_manifest_sha=None,
            protocol=original.protocol_sha256,
            runtime_fp=actual_runtime_fp,
            comparisons={"runtime_fingerprint": {"expected": expected_runtime_fp, "actual": actual_runtime_fp}},
        )

    clone = replay_root / "workspace"
    artifact_dir = replay_root / "artifacts"
    artifact_dir.mkdir()
    try:
        if not workspace.workspace.is_dir():
            raise ReplayInfrastructureError("managed campaign workspace is missing")
        run_process(
            ["git", "clone", "--no-hardlinks", "--no-checkout", str(workspace.workspace), str(clone)],
            cwd=replay_root,
            timeout=120,
        )
        replay_workspace = WorkspaceManager(workspace.source_repo, clone)
        replay_workspace.checkout(snapshot)
        if candidate_proof:
            actual_proof = replay_workspace.snapshot_proof(
                str(candidate_proof["parent_snapshot"]), snapshot
            )
            if actual_proof != candidate_proof:
                raise ReplayInfrastructureError("candidate tree/diff no longer matches its stored proof")

        if source_kind == "bootstrap_smoke":
            frozen_root = _assert_child(store.path.parent, Path(metadata["asset_root"]))
            for asset in assets:
                source = _assert_child(frozen_root, frozen_root / asset.path)
                target = _assert_child(clone, clone / asset.path)
                if not source.is_file():
                    raise ReplayInfrastructureError(f"frozen bootstrap asset is missing: {asset.path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        violations = verify_evaluation_assets(clone, assets)
        if violations:
            raise ReplayInfrastructureError("frozen evaluator assets are missing or changed: " + ", ".join(violations))

        evaluator = CommandEvaluator(command)
        replay_workspace_path = clone.resolve()
        evaluator.context = {
            "BANANLOOP_ARTIFACT_DIR": str(artifact_dir.resolve()),
            "BANANLOOP_EVALUATION_ID": f"replay:{replay_id}",
            "BANANLOOP_SNAPSHOT": snapshot,
            "BANANLOOP_EVALUATION_TIER": tier,
            "BANANLOOP_WORKSPACE": str(replay_workspace_path),
        }
        if candidate_proof:
            evaluator.context.update({
                "BANANLOOP_PARENT_SNAPSHOT": str(candidate_proof["parent_snapshot"]),
                "BANANLOOP_CANDIDATE_TREE": str(candidate_proof["tree"]),
                "BANANLOOP_CANDIDATE_DIFF_SHA256": str(candidate_proof["diff_sha256"]),
                "BANANLOOP_CANDIDATE_CHANGED_PATHS": json.dumps(candidate_proof["changed_paths"], ensure_ascii=False),
            })
        if source_kind == "dev_action" and spec.budget.evaluation_units_per_call is not None:
            evaluator.context["BANANLOOP_EVALUATION_UNIT_LIMIT"] = str(spec.budget.evaluation_units_per_call)

        before = replay_workspace.head()
        replayed = evaluator.evaluate(clone, timeout=timeout)
        if replayed.status != "ok":
            raise ValueError(f"replayed evaluator returned status={replayed.status}")
        retain_experiment = experiment_id if isinstance(experiment_id, int) else None
        from .artifacts import retain_artifacts
        retain_artifacts(replayed, artifact_dir, retain_experiment, snapshot)
        after = replay_workspace.head()
        dirty = replay_workspace.working_tree_changed_paths()
        if before != snapshot or after != snapshot or dirty:
            raise ValueError("replayed evaluator changed the frozen workspace")
        _atomic_json(replay_result_path, replayed.model_dump(mode="json"))
        comparisons = _compare_results(
            original,
            replayed,
            metric_tolerances=metric_tolerances,
            relative_metric_tolerances=relative_metric_tolerances,
        )
        case_sha = _json_hash(_case_manifest(replayed))
        return _persist_receipt(
            replay_root,
            replay_id=replay_id,
            source_kind=source_kind,
            action_id=action_id,
            snapshot=snapshot,
            status="REPRODUCIBLE" if not comparisons["mismatches"] else "REPLAY_MISMATCH",
            reason=None if not comparisons["mismatches"] else "; ".join(comparisons["mismatches"][:5]),
            original_sha=result_sha,
            replay_sha=_digest(replay_result_path.read_bytes()),
            case_manifest_sha=case_sha,
            protocol=replayed.protocol_sha256,
            runtime_fp=actual_runtime_fp,
            comparisons=comparisons,
        )
    except (ReplayInfrastructureError, EvaluatorError, ProcessError, WorkspaceError, OSError) as exc:
        return _persist_receipt(
            replay_root,
            replay_id=replay_id,
            source_kind=source_kind,
            action_id=action_id,
            snapshot=snapshot,
            status="NOT_REPRODUCIBLE_INFRA",
            reason=str(exc)[:1000],
            original_sha=result_sha,
            replay_sha=_digest(replay_result_path.read_bytes()) if replay_result_path.exists() else None,
            case_manifest_sha=None,
            protocol=original.protocol_sha256,
            runtime_fp=actual_runtime_fp,
            comparisons={},
        )
    except (ValueError, KeyError) as exc:
        return _persist_receipt(
            replay_root,
            replay_id=replay_id,
            source_kind=source_kind,
            action_id=action_id,
            snapshot=snapshot,
            status="REPLAY_MISMATCH",
            reason=str(exc)[:1000],
            original_sha=result_sha,
            replay_sha=_digest(replay_result_path.read_bytes()) if replay_result_path.exists() else None,
            case_manifest_sha=None,
            protocol=original.protocol_sha256,
            runtime_fp=actual_runtime_fp,
            comparisons={},
        )
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def _bootstrap_source(store: CampaignStore, action_id: str):
    match = re.fullmatch(r"bootstrap:attempt:(\d+):smoke:(\d+)", action_id)
    if match is None:
        raise ReplayInfrastructureError("bootstrap replay action id must name an exact smoke repeat")
    attempt_number, repeat_number = map(int, match.groups())
    record_path = store.path.parent / "bootstrap-plan.json"
    if not record_path.is_file():
        raise ReplayInfrastructureError("bootstrap plan record is missing")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    spec = store.spec()
    evidence = spec.evaluation_evidence
    readiness_record = next(
        (item for item in record.get("bootstrap_attempts", []) if item.get("attempt") == attempt_number),
        None,
    )
    if readiness_record is None:
        raise ReplayInfrastructureError("bootstrap attempt is missing")
    readiness = readiness_record.get("readiness") or {}
    if readiness.get("status") != "PASSED":
        raise ReplayInfrastructureError("only a passed bootstrap smoke repeat may be replayed")
    if evidence is None or not evidence.readiness_receipt_sha256:
        raise ReplayInfrastructureError("campaign has no linked bootstrap readiness receipt")
    expected_hash = _digest(
        json.dumps(readiness, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    if expected_hash != evidence.readiness_receipt_sha256:
        raise ReplayInfrastructureError("bootstrap readiness record does not match its frozen receipt hash")
    runs = readiness.get("runs", [])
    if repeat_number < 1 or repeat_number > len(runs):
        raise ReplayInfrastructureError("bootstrap smoke repeat is missing")
    run = runs[repeat_number - 1]
    replay = run.get("replay") or {}
    state_root = store.path.parent.resolve()
    result_path = _assert_child(state_root, state_root / replay.get("result_path", ""))
    asset_root = _assert_child(state_root, state_root / replay.get("evaluation_assets_root", ""))
    if not result_path.is_file() or _digest(result_path.read_bytes()) != replay.get("result_sha256"):
        raise ReplayInfrastructureError("bootstrap smoke result sidecar is missing or changed")
    original = EvaluationResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    plan = readiness_record.get("plan")
    if not isinstance(plan, dict):
        raise ReplayInfrastructureError("bootstrap evaluator plan is missing")
    assets = [EvaluationAsset.model_validate(item) for item in replay.get("evaluation_assets", [])]
    return (
        {"readiness": readiness, "result_path": str(result_path), "asset_root": str(asset_root)},
        original,
        list(readiness.get("command") or plan.get("smoke_command") or plan.get("evaluation_command") or []),
        int(readiness.get("timeout_seconds") or 300),
        str(readiness_record.get("snapshot") or ""),
        replay.get("runtime") or {},
        assets,
        replay["result_sha256"],
    )


def _persist_receipt(
    replay_root: Path,
    *,
    replay_id: str,
    source_kind: Literal["dev_action", "bootstrap_smoke"],
    action_id: str,
    snapshot: str | None,
    status: Literal["REPRODUCIBLE", "REPLAY_MISMATCH", "NOT_REPRODUCIBLE_INFRA"],
    reason: str | None,
    original_sha: str | None,
    replay_sha: str | None,
    case_manifest_sha: str | None,
    protocol: str | None,
    runtime_fp: str | None,
    comparisons: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "schema_version": 1,
        "replay_id": replay_id,
        "source_kind": source_kind,
        "source_action_id": action_id,
        "candidate_snapshot": snapshot,
        "status": status,
        "reason": reason,
        "original_result_sha256": original_sha,
        "replay_result_sha256": replay_sha,
        "case_input_manifest_sha256": case_manifest_sha,
        "protocol_sha256": protocol,
        "runtime_fingerprint": runtime_fp,
        "comparisons": comparisons,
        "duration_ms": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt = ReplayReceipt(receipt_sha256=_json_hash(body), **body).model_dump(mode="json")
    _atomic_json(replay_root / "replay-receipt.json", receipt)
    receipt["receipt_path"] = str(replay_root / "replay-receipt.json")
    receipt["result_path"] = (
        str(replay_root / "evaluation-result.json")
        if (replay_root / "evaluation-result.json").is_file()
        else None
    )
    return receipt


def replay_dev_action(
    store: CampaignStore,
    workspace: WorkspaceManager,
    action_id: str,
) -> dict[str, Any]:
    """Run replay and durably record its separate wall-time accounting."""
    started = time.perf_counter()
    result = _replay_dev_action(store, workspace, action_id)
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("receipt_sha256", None)
    receipt["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    validated = ReplayReceipt(receipt_sha256=_json_hash(receipt), **receipt)
    encoded = validated.model_dump(mode="json")
    _atomic_json(receipt_path, encoded)
    result.update(encoded)
    result["receipt_path"] = str(receipt_path)
    result["result_path"] = (
        str(receipt_path.parent / "evaluation-result.json")
        if (receipt_path.parent / "evaluation-result.json").is_file()
        else None
    )
    return result
