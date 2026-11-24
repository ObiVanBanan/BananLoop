"""Fail-closed durable launcher primitive for one autonomous benchmark arm.

The caller supplies a worker command. Production commands are intentionally
disabled until container ownership has been decided; tests may opt into the
explicit fake path.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .arm_watchdog import ARM_SECONDS, ArmDeadline, run_arm_command


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "INTERRUPTED_UNKNOWN", "usage_unknown": True,
            "failure_reason": reason, "final_artifact": {"status": "UNKNOWN", "files": []}}


def _recover_candidate(root: Path, candidate_path: str | None) -> dict[str, Any]:
    """Preserve candidate bytes even when the worker outcome is unknown."""
    if not candidate_path:
        return {"status": "UNKNOWN", "files": []}
    source = Path(candidate_path)
    if source.is_symlink() or not source.is_file():
        return {"status": "MISSING_OUTPUT", "files": []}
    try:
        content = source.read_bytes()
    except OSError:
        return {"status": "UNKNOWN", "files": []}
    import hashlib
    target = root / "final-artifacts" / source.name
    target.parent.mkdir(mode=0o700, exist_ok=True)
    if target.exists() or target.is_symlink():
        return {"status": "UNKNOWN", "files": []}
    with target.open("xb") as stream:
        stream.write(content)
    os.chmod(target, 0o400)
    return {"status": "CAPTURED", "files": [{
        "path": source.name, "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content)}]}


def run_single_arm(*, arm_id: str, task_id: str, system: str,
                   arm_root: Path, command: Sequence[str], cwd: Path,
                   deadline: ArmDeadline | None = None,
                   candidate_path: str | None = None,
                   fake_backend: bool = False) -> dict[str, Any]:
    """Run one worker once and durably return its result.

    ``command`` must invoke the caller's worker, which writes ``worker-result.json``
    in ``arm_root``. ``fake_backend`` is an explicit offline test escape hatch.
    """
    if not fake_backend:
        raise RuntimeError("production Full launch is disabled pending container ownership decision")
    if os.name != "posix":
        raise RuntimeError("single arm supervision requires POSIX")
    root = Path(arm_root).absolute()
    if root.exists() or root.is_symlink():
        raise ValueError("arm directory already exists; replay is fail closed")
    root.mkdir(parents=True, mode=0o700)
    deadline = deadline or ArmDeadline.start(ARM_SECONDS)
    identity = {"schema": "bananloop-autonomous-v01-single-arm-v1",
                "arm_id": arm_id, "task_id": task_id, "system": system,
                "deadline_epoch": deadline.epoch}
    _atomic_json(root / "started.json", identity)
    status = run_arm_command(command, cwd=Path(cwd), arm_root=root, deadline=deadline)
    worker_result = root / "worker-result.json"
    if status.get("status") == "COMPLETED" and worker_result.is_file() and not worker_result.is_symlink():
        try:
            result = json.loads(worker_result.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                raise ValueError("invalid worker result")
            if not candidate_path and not isinstance(result.get("final_artifact"), dict):
                result["final_artifact"] = {"status": "UNKNOWN", "files": []}
        except (OSError, ValueError, json.JSONDecodeError):
            result = _unknown("worker_result_invalid")
    else:
        result = _unknown(str(status.get("status", "worker_missing_result")))
    if candidate_path:
        result["final_artifact"] = _recover_candidate(root, candidate_path)
        if result["status"] == "INTERRUPTED_UNKNOWN":
            result["usage_unknown"] = True
    record = {**identity, "worker": status, "result": result}
    _atomic_json(root / "result.json", record)
    return record
