"""Sequential, resumable orchestration for a frozen 12-task Full series."""
from __future__ import annotations

import json
import os
import tempfile
import sys
from pathlib import Path
from typing import Any

from .full_arm_launch import launch_full_arm
from .runner import ARM_SECONDS


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_full_series(*, manifest_path: Path, preflight_path: Path,
                    candidate_checkout: Path, runtime_root: Path,
                    expected_manifest_sha256: str, candidate_source_sha: str,
                    series_root: Path, python_binary: Path,
                    harbor_binary: Path, model: str = "gpt-6-luna") -> dict[str, Any]:
    """Run every Full arm sequentially and persist one immutable result per task.

    Existing completed arm roots are never replayed. An interrupted started arm
    blocks resume so a possibly dispatched model call cannot be charged twice.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("tasks")
    if not isinstance(entries, list) or len(entries) != 12:
        raise ValueError("series manifest must contain exactly 12 task pairs")
    if manifest.get("candidate_source_sha") != candidate_source_sha:
        raise ValueError("manifest candidate SHA differs from the requested pin")
    if manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("manifest SHA differs from the explicit pin")
    if manifest.get("model") != model:
        raise ValueError("series model differs from its manifest")
    checkout = candidate_checkout.resolve(strict=True)
    python_paths = [str(checkout / "src"), str(checkout)]
    python_paths.extend(path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                         if path and path not in {str(checkout), str(checkout / "src")})
    os.environ["PYTHONPATH"] = os.pathsep.join(python_paths)
    if series_root.exists() and not (series_root / "series.json").is_file():
        raise ValueError("existing series root has no resumable series receipt")
    series_root.mkdir(parents=True, exist_ok=True)
    receipt_path = series_root / "series.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("manifest_sha256") != expected_manifest_sha256:
            raise ValueError("series receipt belongs to another manifest")
    else:
        receipt = {"schema": "bananloop-autonomous-v01-full-series-v1",
                   "manifest_sha256": expected_manifest_sha256,
                   "candidate_source_sha": candidate_source_sha,
                   "status": "RUNNING", "arms": []}
        _atomic_json(receipt_path, receipt)

    completed = {item["task_id"] for item in receipt["arms"]
                 if item.get("status") == "COMPLETED"}
    if receipt.get("status") == "STOPPED_UNKNOWN":
        return receipt
    for pair in entries:
        task = pair["task"]
        task_id = task["task_id"]
        if task_id in completed:
            continue
        arm_id = f"{pair['pair_id']}-bananloop_full"
        arm = next((item for item in pair.get("arms", [])
                    if item.get("system") == "bananloop_full"), None)
        if not isinstance(arm, dict) or arm.get("deadline_seconds") != ARM_SECONDS:
            raise ValueError(f"{task_id}: unsupported or missing Full arm")
        descriptor_path = runtime_root / f"{task_id}.json"
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        task_root = series_root / task_id
        arm_root = task_root / "arm"
        if arm_root.exists():
            raise ValueError(f"{task_id}: arm root exists without a completed series receipt")
        kwargs = {
            "initial_repo": str(candidate_checkout), "base_sha": candidate_source_sha,
            "runtime_root": str(runtime_root), "state_root": str(task_root / "state"),
            "jobs_root": str(arm_root / "harbor-jobs"),
            "manifest_sha256": expected_manifest_sha256, "model": model,
            "python_binary": str(python_binary), "harbor_binary": str(harbor_binary),
        }
        try:
            result = launch_full_arm(
                manifest_path=manifest_path, preflight_path=preflight_path,
                candidate_checkout=checkout,
                expected_manifest_sha256=expected_manifest_sha256,
                task_id=task_id, candidate_source_sha=candidate_source_sha,
                arm_id=arm_id, backend_config={"backend_kwargs": kwargs}, arm_root=arm_root,
                candidate_path=candidate_checkout / descriptor["candidate_path"],
            )
        except KeyboardInterrupt:
            receipt["arms"].append({
                "task_id": task_id, "arm_id": arm_id, "status": "UNKNOWN",
                "reason": "INTERRUPTED", "arm_root": str(arm_root),
            })
            receipt["status"] = "STOPPED_UNKNOWN"
            _atomic_json(receipt_path, receipt)
            raise
        outcome = result.get("result", {}).get("status")
        arm_record = {"task_id": task_id, "arm_id": arm_id,
                      "status": "COMPLETED" if outcome != "INTERRUPTED_UNKNOWN"
                      and result.get("cleanup_status") != "UNKNOWN" else "UNKNOWN",
                      "result": result}
        receipt["arms"].append(arm_record)
        receipt["status"] = "RUNNING" if arm_record["status"] == "COMPLETED" else "STOPPED_UNKNOWN"
        _atomic_json(receipt_path, receipt)
        if arm_record["status"] == "UNKNOWN":
            return receipt
        completed.add(task_id)

    receipt["status"] = "COMPLETED" if len(completed) == len(entries) else "RUNNING"
    _atomic_json(receipt_path, receipt)
    return receipt
