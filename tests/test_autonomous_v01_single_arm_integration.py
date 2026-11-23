import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

from benchmarks.autonomous_v01 import runner
from benchmarks.autonomous_v01.single_arm_integration import run_integrated_arm


pytestmark = pytest.mark.skipif(os.name != "posix", reason="whole-arm supervision requires POSIX")


def _evidence(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.test"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
    (checkout / "source.py").write_text("fixture = True\n")
    subprocess.run(["git", "-C", str(checkout), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-m", "fixture"], check=True, capture_output=True)
    source_sha = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    tasks = []
    for index in range(12):
        task_id = f"task-{index:02}"
        task = {"task_id": task_id, "task_sha256": f"{index:064x}", "adapter": {"adapter_id": "fixture"}}
        tasks.append({"pair_id": f"pair-{index:02}", "task": task,
                      "arms": [{"system": "bananloop_full", "deadline_seconds": runner.ARM_SECONDS}]})
    manifest = {"schema": "bananloop-autonomous-v01-experiment-manifest-v1",
                "kind": "ARE_PREPARATION_ONLY", "candidate_source_sha": source_sha, "tasks": tasks}
    manifest["manifest_sha256"] = runner.canonical_sha(manifest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps({
        "schema": "bananloop-autonomous-v01-preflight-v1",
        "status": "READY_NON_RPDI_DIAGNOSTIC", "blockers": [],
        "manifest_sha256": manifest["manifest_sha256"], "candidate_source_sha": source_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }))
    return checkout, manifest_path, preflight_path, manifest, source_sha


def test_offline_integration_gates_launches_worker_and_validates_receipt(tmp_path):
    checkout, manifest_path, preflight_path, manifest, source_sha = _evidence(tmp_path)
    task_id = "task-04"
    arm_id = "pair-04-bananloop_full"
    root = tmp_path / "arms" / arm_id
    record = run_integrated_arm(
        manifest_path=manifest_path, preflight_path=preflight_path,
        candidate_checkout=checkout, expected_manifest_sha256=manifest["manifest_sha256"],
        task_id=task_id, candidate_source_sha=source_sha, arm_id=arm_id,
        backend_config={"backend_kwargs": {}}, arm_root=root, fake_backend=True,
    )
    assert record["gate"]["status"] == "GATE_PASSED"
    assert record["worker"]["status"] == "COMPLETED"
    assert record["result"]["status"] == "OFFLINE_FIXTURE_COMPLETE"
    receipt = json.loads((root / "worker-result.json").read_text())
    assert receipt["task_id"] == task_id
    assert receipt["arm_id"] == arm_id
    assert receipt["manifest_sha256"] == manifest["manifest_sha256"]
    assert json.loads((root / "result.json").read_text())["gate"] == record["gate"]


def test_integration_rejects_unapproved_production_path_before_launch(tmp_path):
    with pytest.raises(RuntimeError, match="production Full launch is disabled"):
        run_integrated_arm(manifest_path=tmp_path / "missing.json",
            preflight_path=tmp_path / "missing-preflight.json", candidate_checkout=tmp_path,
            expected_manifest_sha256="0" * 64, task_id="task", candidate_source_sha="0" * 40,
            arm_id="pair-bananloop_full", backend_config={}, arm_root=tmp_path / "arm")


def test_integration_rejects_unmanifested_task(tmp_path):
    checkout, manifest_path, preflight_path, manifest, source_sha = _evidence(tmp_path)
    with pytest.raises(ValueError, match="selected task is not uniquely present"):
        run_integrated_arm(manifest_path=manifest_path, preflight_path=preflight_path,
            candidate_checkout=checkout, expected_manifest_sha256=manifest["manifest_sha256"],
            task_id="not-in-manifest", candidate_source_sha=source_sha,
            arm_id="pair-04-bananloop_full", backend_config={"backend_kwargs": {}},
            arm_root=tmp_path / "arm", fake_backend=True)
