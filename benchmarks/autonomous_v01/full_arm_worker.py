"""Offline-testable entry point for one BananLoop Full arm."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import runner


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
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run_full_arm(*, manifest: dict[str, Any], task: dict[str, Any],
                 config: dict[str, Any], arm_id: str, arm_root: Path,
                 deadline_epoch: float) -> dict[str, Any]:
    """Run a single frozen Full arm and atomically persist its worker receipt.

    ``config`` contains ``backend_kwargs`` for BananLoopFullBackend and an
    optional ``usage_path`` (defaults to ``arm_root/observed-usage.sqlite3``).
    """
    if not isinstance(manifest, dict) or not isinstance(task, dict) or not isinstance(config, dict):
        raise ValueError("manifest, task, and config must be objects")
    digest = manifest.get("manifest_sha256")
    if digest != runner.canonical_sha({k: v for k, v in manifest.items() if k != "manifest_sha256"}):
        raise ValueError("manifest digest does not match its contents")
    if manifest.get("schema") != "bananloop-autonomous-v01-experiment-manifest-v1":
        raise ValueError("unsupported Full experiment manifest")
    entries = manifest.get("tasks")
    matches = [entry for entry in entries if isinstance(entry, dict)
               and entry.get("task", {}).get("task_id") == task.get("task_id")] if isinstance(entries, list) else []
    if len(matches) != 1 or matches[0].get("task") != task:
        raise ValueError("task identity/content does not match the frozen manifest")
    full_arms = [arm for arm in matches[0].get("arms", [])
                 if isinstance(arm, dict) and arm.get("system") == "bananloop_full"]
    if len(full_arms) != 1 or full_arms[0].get("deadline_seconds") != runner.ARM_SECONDS:
        raise ValueError("manifest does not contain exactly one supported Full arm")
    expected_arm_id = f"{matches[0].get('pair_id')}-bananloop_full"
    if not isinstance(arm_id, str) or arm_id != expected_arm_id:
        raise ValueError("arm_id does not match the selected manifest pair and Full system")
    if (isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
            or not math.isfinite(deadline_epoch)):
        raise ValueError("inherited deadline must be a finite epoch timestamp")
    if deadline_epoch > time.time() + runner.ARM_SECONDS + 1:
        raise ValueError("inherited deadline exceeds the manifest arm budget")
    # If launched under single_arm supervision, verify the serialized epoch,
    # watchdog process group, and inherited monotonic deadline before backend setup.
    runner.inherited_arm_deadline(float(deadline_epoch))
    backend_kwargs = config.get("backend_kwargs")
    if not isinstance(backend_kwargs, dict):
        raise ValueError("config.backend_kwargs must be an object")
    backend_kwargs = dict(backend_kwargs)
    if "manifest_sha256" in backend_kwargs and backend_kwargs["manifest_sha256"] != digest:
        raise ValueError("backend manifest identity differs from the frozen manifest")
    backend_kwargs["manifest_sha256"] = digest
    root = Path(arm_root).absolute()
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "worker-result.json"
    if result_path.is_symlink():
        raise ValueError("worker result path must not be a symlink")
    usage_path = Path(config.get("usage_path", root / "observed-usage.sqlite3"))
    backend = runner.BananLoopFullBackend(**backend_kwargs)
    ledger = runner.UsageLedger(usage_path)
    result = backend.run(arm_id, task, root, float(deadline_epoch), ledger)
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        raise ValueError("Full backend returned an invalid result")
    # single_arm consumes worker-result.json as a result object and requires
    # top-level status/final_artifact fields. Keep those fields at top level.
    record = {
        **result,
        "schema": "bananloop-autonomous-v01-full-worker-result-v1",
        "arm_id": arm_id,
        "task_id": task["task_id"],
        "manifest_sha256": digest,
        "deadline_epoch": float(deadline_epoch),
        "usage": ledger.summary(),
    }
    _atomic_json(result_path, record)
    return record
