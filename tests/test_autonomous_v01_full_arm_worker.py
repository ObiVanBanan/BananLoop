import json

import pytest

from benchmarks.autonomous_v01 import full_arm_worker, runner


def _inputs(tmp_path):
    task = {"task_id": "task-a", "task_sha256": "a" * 64, "adapter": {"adapter_id": "x"}}
    manifest = {
        "schema": "bananloop-autonomous-v01-experiment-manifest-v1",
        "tasks": [{"pair_id": "are-01", "task": task,
                   "arms": [{"system": "bananloop_full", "deadline_seconds": runner.ARM_SECONDS}]}],
    }
    manifest["manifest_sha256"] = runner.canonical_sha(manifest)
    config = {"backend_kwargs": {"initial_repo": tmp_path, "base_sha": "b" * 40,
                                 "runtime_root": tmp_path, "state_root": tmp_path / "state",
                                 "jobs_root": tmp_path / "jobs"}}
    return manifest, task, config


def test_runs_fake_backend_and_atomically_persists_usage_and_result(tmp_path, monkeypatch):
    manifest, task, config = _inputs(tmp_path)
    class FakeBackend:
        def __init__(self, **kwargs):
            assert kwargs["manifest_sha256"] == manifest["manifest_sha256"]
        def run(self, arm_id, got_task, arm_root, deadline, usage):
            assert arm_id == "are-01-bananloop_full"
            assert got_task == task
            usage.record(arm_id, "fake", {"dispatched": True, "usage": {"input_tokens": 3, "output_tokens": 4}})
            return {"status": "FAKE_COMPLETE", "final_artifact": {"status": "EMPTY", "files": []}}
    monkeypatch.setattr(runner, "BananLoopFullBackend", FakeBackend)
    record = full_arm_worker.run_full_arm(manifest=manifest, task=task, config=config,
        arm_id="are-01-bananloop_full", arm_root=tmp_path / "arm", deadline_epoch=runner.time.time() + 60)
    saved = json.loads((tmp_path / "arm" / "worker-result.json").read_text())
    assert saved == record
    assert record["usage"]["input_tokens"] == 3
    assert record["status"] == "FAKE_COMPLETE"


def test_rejects_task_manifest_mismatch_before_backend_creation(tmp_path, monkeypatch):
    manifest, task, config = _inputs(tmp_path)
    task = {**task, "task_id": "different-task"}
    monkeypatch.setattr(runner, "BananLoopFullBackend", lambda **kwargs: pytest.fail("backend must not be created"))
    with pytest.raises(ValueError, match="task identity/content"):
        full_arm_worker.run_full_arm(manifest=manifest, task=task, config=config,
            arm_id="are-01-bananloop_full", arm_root=tmp_path / "arm", deadline_epoch=runner.time.time() + 60)


def test_rejects_manifest_digest_mismatch(tmp_path):
    manifest, task, config = _inputs(tmp_path)
    manifest["model"] = "tampered"
    with pytest.raises(ValueError, match="manifest digest"):
        full_arm_worker.run_full_arm(manifest=manifest, task=task, config=config,
            arm_id="are-01-bananloop_full", arm_root=tmp_path / "arm", deadline_epoch=runner.time.time() + 60)


def test_rejects_arm_id_for_other_manifest_pair(tmp_path):
    manifest, task, config = _inputs(tmp_path)
    with pytest.raises(ValueError, match="arm_id does not match"):
        full_arm_worker.run_full_arm(manifest=manifest, task=task, config=config,
            arm_id="other-pair-bananloop_full", arm_root=tmp_path / "arm", deadline_epoch=runner.time.time() + 60)


def test_rejects_nonfinite_deadline(tmp_path):
    manifest, task, config = _inputs(tmp_path)
    with pytest.raises(ValueError, match="finite epoch"):
        full_arm_worker.run_full_arm(manifest=manifest, task=task, config=config,
            arm_id="are-01-bananloop_full", arm_root=tmp_path / "arm", deadline_epoch=float("inf"))
