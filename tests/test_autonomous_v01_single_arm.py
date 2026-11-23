import json
import hashlib
import os
import sys
from pathlib import Path

import pytest

from benchmarks.autonomous_v01.arm_watchdog import ArmDeadline
from benchmarks.autonomous_v01.single_arm import run_single_arm

pytestmark = pytest.mark.skipif(os.name != "posix", reason="single arm watchdog requires POSIX")


def test_fake_worker_persists_result_and_recovers_candidate(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text("value = 2\n")
    output = tmp_path / "arm" / "worker-result.json"
    code = f"import json,pathlib; pathlib.Path({str(output)!r}).write_text(json.dumps({{'status':'FAKE_COMPLETE'}}))"
    record = run_single_arm(
        arm_id="arm-1", task_id="task", system="fake", arm_root=tmp_path / "arm",
        command=[sys.executable, "-c", code], cwd=tmp_path,
        deadline=ArmDeadline.start(10), candidate_path=str(candidate), fake_backend=True,
    )
    assert record["result"]["status"] == "FAKE_COMPLETE"
    assert record["result"]["final_artifact"]["status"] == "CAPTURED"
    assert (tmp_path / "arm" / "final-artifacts" / "candidate.py").read_text() == "value = 2\n"
    assert json.loads((tmp_path / "arm" / "started.json").read_text())["deadline_epoch"] == record["deadline_epoch"]
    assert json.loads((tmp_path / "arm" / "result.json").read_text()) == record


def test_timeout_is_unknown_and_result_is_durable(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_bytes(b"partial candidate\n")
    record = run_single_arm(
        arm_id="arm-1", task_id="task", system="fake", arm_root=tmp_path / "arm",
        command=[sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path,
        deadline=ArmDeadline.start(0.3), candidate_path=str(candidate), fake_backend=True,
    )
    assert record["result"]["status"] == "INTERRUPTED_UNKNOWN"
    assert record["result"]["usage_unknown"] is True
    assert record["result"]["final_artifact"]["status"] == "CAPTURED"
    assert record["result"]["final_artifact"]["files"][0]["sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert (tmp_path / "arm" / "final-artifacts" / candidate.name).read_bytes() == candidate.read_bytes()
    assert (tmp_path / "arm" / "result.json").is_file()


def test_crashed_worker_preserves_candidate_but_keeps_outcome_unknown(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_bytes(b"saved before crash\n")
    record = run_single_arm(
        arm_id="arm-1", task_id="task", system="fake", arm_root=tmp_path / "arm",
        command=[sys.executable, "-c", "raise SystemExit(7)"], cwd=tmp_path,
        deadline=ArmDeadline.start(10), candidate_path=str(candidate), fake_backend=True,
    )
    assert record["result"]["status"] == "INTERRUPTED_UNKNOWN"
    assert record["result"]["usage_unknown"] is True
    item = record["result"]["final_artifact"]["files"][0]
    assert item["sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert (tmp_path / "arm" / "final-artifacts" / candidate.name).read_bytes() == candidate.read_bytes()


def test_existing_arm_replay_fails_closed(tmp_path):
    root = tmp_path / "arm"
    root.mkdir()
    with pytest.raises(ValueError, match="replay is fail closed"):
        run_single_arm(arm_id="arm-1", task_id="task", system="fake", arm_root=root,
                       command=[sys.executable, "-c", "pass"], cwd=tmp_path, fake_backend=True)


def test_live_launch_is_disabled_without_ownership_decision(tmp_path):
    with pytest.raises(TypeError):
        run_single_arm(arm_id="arm-1", task_id="task", system="full", arm_root=tmp_path / "arm",
                       command=[sys.executable, "-c", "pass"], cwd=tmp_path, allow_live=True)
    with pytest.raises(RuntimeError, match="container ownership"):
        run_single_arm(arm_id="arm-1", task_id="task", system="full", arm_root=tmp_path / "arm",
                       command=[sys.executable, "-c", "pass"], cwd=tmp_path)
