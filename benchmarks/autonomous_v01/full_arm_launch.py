"""Gated, supervised production launcher for one manifest Full arm.

This module performs no model work at import time. Docker cleanup is limited to
projects derived from the worker's private Harbor jobs directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be readable JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _preserve_candidate(root: Path, candidate_path: str | Path | None) -> dict[str, Any]:
    if candidate_path is None:
        return {"status": "UNKNOWN", "files": []}
    source = Path(candidate_path)
    if source.is_symlink() or not source.is_file():
        return {"status": "MISSING_OUTPUT", "files": []}
    try:
        data = source.read_bytes()
        dest_dir = root / "final-artifacts"
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        dest = dest_dir / source.name
        with dest.open("xb") as stream:
            stream.write(data)
        os.chmod(dest, 0o400)
    except OSError:
        return {"status": "UNKNOWN", "files": []}
    return {"status": "CAPTURED", "files": [{"path": source.name,
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}]}


def _worker_code(package_root: str) -> str:
    return ("import json,sys; sys.path.insert(0, " + repr(package_root) + "); "
            "from pathlib import Path; from benchmarks.autonomous_v01.full_arm_launch import _restore_backend_path_types; "
            "from benchmarks.autonomous_v01.full_arm_worker import run_full_arm; "
            "p=json.loads(Path(sys.argv[1]).read_text()); "
            "p['config']=_restore_backend_path_types(p['config']); "
            "run_full_arm(manifest=p['manifest'], task=p['task'], config=p['config'], "
            "arm_id=p['arm_id'], arm_root=Path(p['arm_root']), deadline_epoch=p['deadline_epoch'])")


def _restore_backend_path_types(config: dict[str, Any]) -> dict[str, Any]:
    """Restore pathlib arguments lost when backend config crosses JSON IPC."""
    from copy import deepcopy

    restored = deepcopy(config)
    kwargs = restored.get("backend_kwargs")
    if not isinstance(kwargs, dict):
        raise ValueError("backend_config.backend_kwargs must be an object")
    for key in ("initial_repo", "runtime_root", "state_root", "jobs_root", "harbor_binary"):
        if key in kwargs and kwargs[key] is not None:
            kwargs[key] = Path(kwargs[key])
    return restored


def launch_full_arm(*, manifest_path: Path, preflight_path: Path,
                    candidate_checkout: Path, expected_manifest_sha256: str,
                    task_id: str, candidate_source_sha: str, arm_id: str,
                    backend_config: dict[str, Any], arm_root: Path,
                    candidate_path: str | Path | None = None,
                    max_preflight_age_seconds: int = 3600,
                    deadline: Any = None) -> dict[str, Any]:
    """Run exactly one gated Full arm and durably record worker/cleanup state."""
    # Lazy imports keep importing this entry point offline and side-effect free.
    from .arm_watchdog import ARM_SECONDS, ArmDeadline, run_arm_command
    from .single_arm_gate import validate_single_arm_gate
    from .runner import canonical_sha

    gate = validate_single_arm_gate(manifest_path=manifest_path, preflight_path=preflight_path,
        candidate_checkout=candidate_checkout, expected_manifest_sha256=expected_manifest_sha256,
        task_id=task_id, candidate_source_sha=candidate_source_sha, arm_id=arm_id,
        max_preflight_age_seconds=max_preflight_age_seconds)
    manifest = _read_object(Path(manifest_path), "manifest")
    matches = [e for e in manifest["tasks"] if e["task"]["task_id"] == task_id]
    if len(matches) != 1:
        raise ValueError("selected task is not unique")
    task = matches[0]["task"]
    if manifest.get("manifest_sha256") != canonical_sha({k: v for k, v in manifest.items() if k != "manifest_sha256"}):
        raise ValueError("manifest SHA changed after gate validation")
    if not isinstance(backend_config, dict) or not isinstance(backend_config.get("backend_kwargs"), dict):
        raise ValueError("backend_config.backend_kwargs must be an object")
    kwargs = backend_config["backend_kwargs"]
    checkout = Path(candidate_checkout).resolve(strict=True)
    try:
        configured_repo = Path(kwargs["initial_repo"]).expanduser().resolve(strict=True)
    except (KeyError, OSError, TypeError) as exc:
        raise ValueError("backend initial_repo must identify the gated candidate checkout") from exc
    if configured_repo != checkout:
        raise ValueError("backend initial_repo differs from candidate checkout")
    if kwargs.get("base_sha") != candidate_source_sha:
        raise ValueError("backend base_sha differs from candidate source SHA")
    if not isinstance(manifest.get("model"), str) or kwargs.get("model") != manifest["model"]:
        raise ValueError("backend model differs from the frozen manifest model")
    root = Path(arm_root).absolute()
    if root.exists() or root.is_symlink():
        raise ValueError("arm directory already exists; replay is fail closed")
    root.mkdir(parents=True, mode=0o700)
    jobs_root = root / "harbor-jobs"
    # Bind Harbor's writes to an isolated directory whose contents can be
    # associated with this one arm after the worker exits.
    config = json.loads(json.dumps(backend_config))
    config["backend_kwargs"]["jobs_root"] = str(jobs_root)
    config["backend_kwargs"]["manifest_sha256"] = expected_manifest_sha256
    deadline = deadline or ArmDeadline.start(ARM_SECONDS)
    identity = {"schema": "bananloop-autonomous-v01-full-launch-v1", "arm_id": arm_id,
                "task_id": task_id, "manifest_sha256": expected_manifest_sha256,
                "candidate_source_sha": candidate_source_sha, "deadline_epoch": deadline.epoch}
    _atomic_json(root / "started.json", identity)
    payload = {"manifest": manifest, "task": task, "config": config, "arm_id": arm_id,
               "arm_root": str(root), "deadline_epoch": deadline.epoch}
    payload_path = root / "worker-input.json"
    _atomic_json(payload_path, payload)
    package_root = str(Path(__file__).resolve().parents[2])
    command: Sequence[str] = [sys.executable, "-c", _worker_code(package_root), str(payload_path)]
    pending_base_exception: BaseException | None = None
    try:
        worker = run_arm_command(command, cwd=Path(candidate_checkout), arm_root=root, deadline=deadline)
    except BaseException as exc:
        # Persist UNKNOWN and perform bounded cleanup before propagating process
        # interrupts or other BaseException subclasses to the caller.
        if not isinstance(exc, Exception):
            pending_base_exception = exc
        worker = {"status": "INTERRUPTED", "returncode": None,
                  "error": type(exc).__name__, "deadline_epoch": deadline.epoch}

    result_path = root / "worker-result.json"
    try:
        receipt = _read_object(result_path, "worker receipt") if worker.get("status") == "COMPLETED" else {}
        if receipt and (not isinstance(receipt.get("status"), str) or any(receipt.get(key) != value for key, value in {
            "arm_id": arm_id, "task_id": task_id, "manifest_sha256": expected_manifest_sha256,
            "deadline_epoch": deadline.epoch}.items())):
            raise ValueError("worker receipt identity differs from launch")
    except ValueError:
        receipt = {}
    # The worker owns artifact capture.  Re-copying its source into the same
    # exclusive destination can turn a valid CAPTURED receipt into UNKNOWN.
    worker_artifact = receipt.get("final_artifact") if receipt else None
    if isinstance(worker_artifact, dict) and worker_artifact.get("status") == "CAPTURED":
        partial = worker_artifact
    else:
        partial = _preserve_candidate(root, candidate_path)
    if worker.get("status") != "COMPLETED" or not receipt:
        result = {"status": "INTERRUPTED_UNKNOWN", "usage_unknown": True,
                  "failure_reason": worker.get("status", "worker receipt missing"),
                  "final_artifact": partial}
    else:
        result = dict(receipt)
        if not (isinstance(result.get("final_artifact"), dict)
                and result["final_artifact"].get("status") == "CAPTURED"):
            result["final_artifact"] = partial if candidate_path else result.get("final_artifact", {"status": "UNKNOWN", "files": []})

    # Cleanup only after worker process-group exit. UNKNOWN project derivation
    # authorizes no Docker calls; partial cleanup receipts remain visible.
    from .harbor_trial_projects import identify_trial_compose_projects
    from .harbor_cleanup import cleanup_compose_project
    # A worker receipt with one of these backend terminal states proves it
    # returned before the Harbor run invocation. With its isolated jobs_root
    # absent, there are no Harbor side effects to clean and its outcome remains
    # authoritative.
    pre_harbor_statuses = {
        "FAILED_UNSAFE_WORKSPACE_ROOT", "FAILED_WORKSPACE_ALREADY_EXISTS",
        "TIMED_OUT_BEFORE_CLONE", "TIMED_OUT_DURING_CLONE",
        "FAILED_TO_CLONE_INITIAL_REPOSITORY", "TIMED_OUT_BEFORE_CHECKOUT",
        "TIMED_OUT_DURING_CHECKOUT", "FAILED_TO_PIN_INITIAL_REVISION",
        "FAILED_TASK_RUNTIME_DESCRIPTOR", "FAILED_TASK_RUNTIME_PIN",
        "FAILED_DEV_COMMAND_PIN", "FAILED_TASK_INSTRUCTION_PIN",
        "UNSAFE_RUNTIME_SYMLINK", "FAILED_TASK_SOURCE_PIN",
        "FAILED_EVALUATOR_FIXTURE_PIN", "FAILED_SAFE_OVERLAY_PIN",
        "FAILED_PROTOCOL_REVIEW_ATTESTATION_PIN", "FAILED_INSTRUCTION_PATH_COLLISION",
        "FAILED_INITIAL_CANDIDATE_MISSING", "FAILED_CANDIDATE_CHANGED_BY_INSTRUCTION_OVERLAY",
        "TIMED_OUT_DURING_INSTRUCTION_OVERLAY", "FAILED_TO_FREEZE_TASK_INSTRUCTION_OVERLAY",
        "FAILED_PUBLIC_DEV_TIER_MISSING", "EXPLORATION_REPORT_INVALID",
        "NO_OPTIMIZATION_CHILD", "PROTOCOL_REJECTED", "TIMED_OUT_DURING_PIVOT",
        "BENCHMARK_PIVOT_SCOPE_MISMATCH", "TIMED_OUT_DURING_HARBOR_PREPARATION",
        "CHILD_CONFIGURATION_FAILED",
    }
    no_harbor_started = (not jobs_root.exists() and not jobs_root.is_symlink() and (
        worker.get("status") in {"FAILED_TO_START", "TIMED_OUT_BEFORE_START"}
        or (bool(receipt) and receipt.get("status") in pre_harbor_statuses)))
    # Missing isolated job state is a clean no-op only with direct evidence
    # that this worker never reached the Harbor invocation.
    if no_harbor_started:
        inventory = {"status": "KNOWN", "projects": [], "configs_seen": 0,
                     "inventory_complete": True, "error": None}
    else:
        inventory = identify_trial_compose_projects(jobs_root)
    cleanup: list[dict[str, Any]] = []
    if no_harbor_started:
        cleanup = []
    elif inventory.get("status") == "KNOWN" and inventory.get("projects") and inventory.get("configs_seen", 0) > 0:
        cleanup = [{"project": project, **cleanup_compose_project(project)}
                   for project in inventory["projects"]]
    else:
        cleanup = [{"status": "UNKNOWN", "error": inventory.get("error", "trial project inventory incomplete")}]
    # A known config inventory authorizes cleanup of those exact projects;
    # it does not establish a whole-host Docker inventory.
    cleanup_ok = (no_harbor_started or (inventory.get("status") == "KNOWN"
                  and inventory.get("projects") and len(cleanup) == len(inventory["projects"])
                  and all(item.get("status") == "CLEANED" for item in cleanup)))
    if not cleanup_ok:
        if receipt:
            result["worker_status"] = receipt["status"]
        result["status"] = "INTERRUPTED_UNKNOWN"
        result["usage_unknown"] = True
        result["cleanup_unknown"] = True
    record = {**identity, "gate": gate, "worker": worker, "result": result,
              "harbor_inventory": inventory, "cleanup": cleanup,
              "cleanup_status": "CLEANED" if cleanup_ok else "UNKNOWN"}
    _atomic_json(root / "result.json", record)
    if pending_base_exception is not None:
        raise pending_base_exception
    return record
