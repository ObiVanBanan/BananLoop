from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmarks.autonomous_v01 import runner
from benchmarks.autonomous_v01 import full_arm_launch as launch


pytestmark = pytest.mark.skipif(os.name != "posix", reason="whole-arm supervision requires POSIX")


def evidence(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "t@e.test"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
    (checkout / "source.py").write_text("pass\n")
    subprocess.run(["git", "-C", str(checkout), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-m", "fixture"], check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    tasks = []
    for i in range(12):
        task = {"task_id": f"task-{i}", "task_sha256": f"{i:064x}"}
        tasks.append({"pair_id": f"pair-{i}", "task": task,
                      "arms": [{"system": "bananloop_full", "deadline_seconds": runner.ARM_SECONDS}]})
    manifest = {"schema": "bananloop-autonomous-v01-experiment-manifest-v1", "model": "fixture-model",
                "kind": "ARE_PREPARATION_ONLY", "candidate_source_sha": sha, "tasks": tasks}
    manifest["manifest_sha256"] = runner.canonical_sha(manifest)
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))
    pp = tmp_path / "preflight.json"
    pp.write_text(json.dumps({"schema": "bananloop-autonomous-v01-preflight-v1",
        "status": "READY_NON_RPDI_DIAGNOSTIC", "blockers": [],
        "manifest_sha256": manifest["manifest_sha256"], "candidate_source_sha": sha,
        "generated_at": datetime.now(timezone.utc).isoformat()}))
    return checkout, mp, pp, manifest, sha


def config_for(jobs_root, checkout, sha):
    return {"backend_kwargs": {"initial_repo": str(checkout), "base_sha": sha, "model": "fixture-model",
        "runtime_root": "/tmp/runtime", "state_root": "/tmp/state", "jobs_root": str(jobs_root),
        "manifest_sha256": "b" * 64}}


def test_gate_rejects_bad_manifest_before_creating_arm(tmp_path):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"
    with pytest.raises(ValueError):
        launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
            expected_manifest_sha256="0" * 64, task_id="task-4", candidate_source_sha=sha,
            arm_id="pair-4-bananloop_full", backend_config=config_for(root, checkout, sha), arm_root=root)
    assert not root.exists()


@pytest.mark.parametrize("change", ["initial_repo", "base_sha", "model"])
def test_backend_source_and_model_are_bound_before_launch(tmp_path, change):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"
    config = config_for(root, checkout, sha)
    if change == "initial_repo":
        config["backend_kwargs"]["initial_repo"] = str(tmp_path)
    elif change == "base_sha":
        config["backend_kwargs"]["base_sha"] = "0" * 40
    else:
        config["backend_kwargs"]["model"] = "different-model"
    with pytest.raises(ValueError, match="backend"):
        launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
            expected_manifest_sha256=manifest["manifest_sha256"], task_id="task-4",
            candidate_source_sha=sha, arm_id="pair-4-bananloop_full",
            backend_config=config, arm_root=root)
    assert not root.exists()


def test_json_worker_config_restores_paths_for_real_backend_constructor(tmp_path):
    checkout, _mp, _pp, manifest, sha = evidence(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    serialized = {"backend_kwargs": {
        "initial_repo": str(checkout), "base_sha": sha, "runtime_root": str(runtime),
        "state_root": str(tmp_path / "state"), "jobs_root": str(tmp_path / "jobs"),
        "harbor_binary": str(tmp_path / "harbor"), "manifest_sha256": manifest["manifest_sha256"],
        "model": manifest["model"],
    }}
    restored = launch._restore_backend_path_types(json.loads(json.dumps(serialized)))
    kwargs = restored["backend_kwargs"]
    for name in ("initial_repo", "runtime_root", "state_root", "jobs_root", "harbor_binary"):
        assert isinstance(kwargs[name], Path)
    backend = runner.BananLoopFullBackend(**kwargs)
    assert backend.initial_repo == checkout.resolve()
    assert backend.runtime_root == runtime.resolve()
    assert backend.jobs_root == (tmp_path / "jobs").absolute()


def test_runs_single_worker_and_cleans_only_derived_exact_projects(tmp_path, monkeypatch):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"
    candidate = tmp_path / "candidate.py"
    candidate.write_text("partial\n")
    removed = []

    def fake_run(command, *, cwd, arm_root, deadline):
        assert "full_arm_worker" in command[2]
        assert cwd == checkout
        assert not (arm_root / "harbor-jobs").exists()
        assert json.loads((arm_root / "worker-input.json").read_text())["config"]["backend_kwargs"]["jobs_root"] == str(arm_root / "harbor-jobs")
        # Simulate prepare_autonomous_task_job, which creates its own root.
        (arm_root / "harbor-jobs").mkdir(mode=0o700)
        receipt = {"status": "DONE", "arm_id": "pair-4-bananloop_full", "task_id": "task-4",
            "manifest_sha256": manifest["manifest_sha256"], "deadline_epoch": deadline.epoch,
        "usage": {"known": True}, "final_artifact": {"status": "CAPTURED", "files": [
            {"path": "candidate.py", "sha256": "worker-sha", "size_bytes": 8}]}}
        artifacts = arm_root / "final-artifacts"
        artifacts.mkdir()
        (artifacts / "candidate.py").write_text("worker copy\n")
        (arm_root / "worker-result.json").write_text(json.dumps(receipt))
        jobs = arm_root / "harbor-jobs" / "job"
        jobs.mkdir()
        (jobs / "config.json").write_text(json.dumps({"trial_name": "trial-x", "environment": {"type": "docker"},
            "task": {"steps": [], "verifier": {"environment_mode": "shared"}}}))
        return {"status": "COMPLETED", "returncode": 0, "deadline_epoch": deadline.epoch}

    def clean(project):
        removed.append(project)
        return {"status": "CLEANED", "removed": []}

    monkeypatch.setattr(launch, "_unused", None, raising=False)
    monkeypatch.setattr("benchmarks.autonomous_v01.arm_watchdog.run_arm_command", fake_run)
    monkeypatch.setattr("benchmarks.autonomous_v01.harbor_cleanup.cleanup_compose_project", clean)
    record = launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
        expected_manifest_sha256=manifest["manifest_sha256"], task_id="task-4", candidate_source_sha=sha,
        arm_id="pair-4-bananloop_full", backend_config=config_for(root, checkout, sha), arm_root=root, candidate_path=candidate)
    assert record["gate"]["authorization"] == "NOT_ESTABLISHED"
    assert record["result"]["status"] == "DONE"
    assert record["cleanup_status"] == "CLEANED"  # exact derived projects were cleaned
    assert record["result"]["final_artifact"]["status"] == "CAPTURED"
    assert record["result"]["final_artifact"]["files"][0]["sha256"] == "worker-sha"
    assert removed == ["trial-x__agent", "trial-x__env"]
    assert json.loads((root / "result.json").read_text()) == record


def test_unclear_trial_inventory_marks_unknown_and_does_not_cleanup(tmp_path, monkeypatch):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"
    candidate = tmp_path / "candidate.py"
    candidate.write_text("survives timeout\n")
    def timed_out(_command, *, arm_root, deadline, **_kwargs):
        (arm_root / "harbor-jobs").mkdir(mode=0o700)
        (arm_root / "harbor-jobs" / "partial").mkdir()
        return {"status": "TIMED_OUT", "returncode": -15, "deadline_epoch": deadline.epoch}
    monkeypatch.setattr("benchmarks.autonomous_v01.arm_watchdog.run_arm_command", timed_out)
    monkeypatch.setattr("benchmarks.autonomous_v01.harbor_cleanup.cleanup_compose_project",
                        lambda project: pytest.fail("must not clean unknown ownership"))
    record = launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
        expected_manifest_sha256=manifest["manifest_sha256"], task_id="task-4", candidate_source_sha=sha,
        arm_id="pair-4-bananloop_full", backend_config=config_for(root, checkout, sha), arm_root=root, candidate_path=candidate)
    assert record["result"]["status"] == "INTERRUPTED_UNKNOWN"
    assert record["result"]["usage_unknown"] is True
    assert record["result"]["final_artifact"]["status"] == "CAPTURED"
    assert record["cleanup_status"] == "UNKNOWN"


def test_pre_harbor_backend_outcome_is_preserved_when_harbor_never_started(tmp_path, monkeypatch):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"

    def worker_before_harbor(_command, *, arm_root, deadline, **_kwargs):
        receipt = {"status": "PROTOCOL_REJECTED", "arm_id": "pair-4-bananloop_full",
            "task_id": "task-4", "manifest_sha256": manifest["manifest_sha256"],
            "deadline_epoch": deadline.epoch, "usage": {"known": True},
            "final_artifact": {"status": "UNKNOWN", "files": []}}
        (arm_root / "worker-result.json").write_text(json.dumps(receipt))
        return {"status": "COMPLETED", "returncode": 0, "deadline_epoch": deadline.epoch}

    monkeypatch.setattr("benchmarks.autonomous_v01.arm_watchdog.run_arm_command", worker_before_harbor)
    monkeypatch.setattr("benchmarks.autonomous_v01.harbor_cleanup.cleanup_compose_project",
                        lambda project: pytest.fail("Harbor did not start"))
    record = launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
        expected_manifest_sha256=manifest["manifest_sha256"], task_id="task-4", candidate_source_sha=sha,
        arm_id="pair-4-bananloop_full", backend_config=config_for(root, checkout, sha), arm_root=root)

    assert record["result"]["status"] == "PROTOCOL_REJECTED"
    assert record["result"]["usage"] == {"known": True}
    assert record["cleanup_status"] == "CLEANED"
    assert record["harbor_inventory"]["projects"] == []


def test_keyboard_interrupt_is_reraised_after_unknown_result_is_durable(tmp_path, monkeypatch):
    checkout, mp, pp, manifest, sha = evidence(tmp_path)
    root = tmp_path / "arm"
    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("benchmarks.autonomous_v01.arm_watchdog.run_arm_command", interrupted)
    with pytest.raises(KeyboardInterrupt):
        launch.launch_full_arm(manifest_path=mp, preflight_path=pp, candidate_checkout=checkout,
            expected_manifest_sha256=manifest["manifest_sha256"], task_id="task-4",
            candidate_source_sha=sha, arm_id="pair-4-bananloop_full",
            backend_config=config_for(root, checkout, sha), arm_root=root)
    record = json.loads((root / "result.json").read_text())
    assert record["result"]["status"] == "INTERRUPTED_UNKNOWN"
    assert record["cleanup_status"] == "UNKNOWN"
