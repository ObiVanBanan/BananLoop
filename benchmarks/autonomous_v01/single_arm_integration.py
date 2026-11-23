"""Offline-capable subprocess integration for a single autonomous Full arm.

The command line transports only JSON and filesystem paths across the process
boundary. A successful gate is evidence validation, never production approval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .arm_watchdog import ARM_SECONDS, ArmDeadline
from .single_arm import run_single_arm
from .single_arm_gate import validate_single_arm_gate


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be readable JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _worker_main(payload_path: Path, *, offline_fake: bool) -> int:
    payload = _object(payload_path, "worker payload")
    manifest = _object(Path(payload["manifest_path"]), "manifest")
    task_id, arm_id = payload["task_id"], payload["arm_id"]
    entries = manifest.get("tasks")
    matches = [e for e in entries if isinstance(e, dict) and isinstance(e.get("task"), dict)
               and e["task"].get("task_id") == task_id] if isinstance(entries, list) else []
    if len(matches) != 1:
        raise ValueError("worker task is not uniquely present in manifest")
    task = matches[0]["task"]
    if arm_id != f"{matches[0].get('pair_id')}-bananloop_full":
        raise ValueError("worker arm does not match selected manifest pair")
    config = _object(Path(payload["backend_config_path"]), "backend config")
    if offline_fake:
        # Explicitly test-only backend. It does not invoke a model, Docker, or network.
        from . import runner
        class OfflineFixtureBackend:
            def __init__(self, **_kwargs: Any):
                pass
            def run(self, selected_arm: str, selected_task: dict[str, Any], arm_root: Path,
                    _deadline: float, _ledger: Any) -> dict[str, Any]:
                return {"status": "OFFLINE_FIXTURE_COMPLETE", "final_artifact":
                        {"status": "EMPTY", "files": []}, "fixture":
                        {"arm_id": selected_arm, "task_id": selected_task["task_id"]}}
        runner.BananLoopFullBackend = OfflineFixtureBackend
    from .full_arm_worker import run_full_arm
    run_full_arm(manifest=manifest, task=task, config=config, arm_id=arm_id,
                 arm_root=Path(payload["arm_root"]), deadline_epoch=float(payload["deadline_epoch"]))
    return 0


def run_integrated_arm(*, manifest_path: Path, preflight_path: Path,
                       candidate_checkout: Path, expected_manifest_sha256: str,
                       task_id: str, candidate_source_sha: str, arm_id: str,
                       backend_config: dict[str, Any], arm_root: Path,
                       candidate_path: str | None = None,
                       max_preflight_age_seconds: int = 3600,
                       fake_backend: bool = False,
                       deadline: ArmDeadline | None = None) -> dict[str, Any]:
    """Validate evidence, prepare an exclusive arm directory, and supervise worker.

    ``fake_backend`` is an explicit offline fixture switch. Live production
    launches remain unavailable until authenticated container ownership and
    cleanup behavior are established.
    """
    if not fake_backend:
        raise RuntimeError("production Full launch is disabled pending authenticated Harbor container ownership/cleanup")
    gate = validate_single_arm_gate(manifest_path=manifest_path, preflight_path=preflight_path,
        candidate_checkout=candidate_checkout, expected_manifest_sha256=expected_manifest_sha256,
        task_id=task_id, candidate_source_sha=candidate_source_sha, arm_id=arm_id,
        max_preflight_age_seconds=max_preflight_age_seconds)
    manifest = _object(Path(manifest_path), "manifest")
    entries = manifest["tasks"]
    selected = [e for e in entries if e["task"]["task_id"] == task_id]
    if len(selected) != 1:
        raise ValueError("selected task is not unique")
    full_arms = [a for a in selected[0].get("arms", []) if isinstance(a, dict)
                 and a.get("system") == "bananloop_full"]
    if len(full_arms) != 1 or full_arms[0].get("deadline_seconds") != ARM_SECONDS:
        raise ValueError("selected Full arm has unsupported deadline")

    root = Path(arm_root).absolute()
    # Keep all worker inputs serializable and outside the exclusive arm root so
    # that single_arm can create that root exactly once.
    inputs = root.parent / f".{root.name}.integration-inputs"
    inputs.mkdir(mode=0o700, parents=True)
    payload_path, config_path = inputs / "worker.json", inputs / "backend-config.json"
    deadline = deadline or ArmDeadline.start(full_arms[0]["deadline_seconds"])
    _atomic_json(config_path, backend_config)
    payload = {"manifest_path": str(Path(manifest_path).resolve()), "task_id": task_id,
               "arm_id": arm_id, "backend_config_path": str(config_path),
               "arm_root": str(root), "deadline_epoch": deadline.epoch}
    _atomic_json(payload_path, payload)
    package_root = str(Path(__file__).resolve().parents[2])
    worker_code = ("import sys; sys.path.insert(0, " + repr(package_root) + "); "
                   "from benchmarks.autonomous_v01.single_arm_integration import main; "
                   "raise SystemExit(main())")
    command: Sequence[str] = [sys.executable, "-c", worker_code,
                              "--worker", str(payload_path), "--offline-fixture"]
    try:
        record = run_single_arm(arm_id=arm_id, task_id=task_id, system="bananloop_full",
            arm_root=root, command=command, cwd=Path(candidate_checkout), deadline=deadline,
            candidate_path=candidate_path, fake_backend=True)
        result = record.get("result", {})
        receipt_path = root / "worker-result.json"
        if record.get("worker", {}).get("status") == "COMPLETED":
            receipt = _object(receipt_path, "worker receipt")
            expected = {"arm_id": arm_id, "task_id": task_id,
                        "manifest_sha256": expected_manifest_sha256,
                        "deadline_epoch": deadline.epoch}
            if any(receipt.get(k) != v for k, v in expected.items()):
                raise ValueError("worker receipt identity does not match launch")
        record["gate"] = gate
        _atomic_json(root / "result.json", record)
        return record
    finally:
        import shutil
        shutil.rmtree(inputs, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(argv)
    if args.worker is None or not args.offline_fixture:
        parser.error("only the explicit offline fixture worker mode is available")
    try:
        return _worker_main(args.worker, offline_fake=True)
    except Exception as exc:
        print(f"single-arm worker failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
