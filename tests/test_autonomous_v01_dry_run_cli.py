from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import benchmarks.autonomous_v01.runner as autonomous_runner
from benchmarks.autonomous_v01.arm_watchdog import ArmDeadline, run_arm_command

REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="dry-run watchdog requires POSIX")


def _tree_digest(root: Path) -> dict[str, str]:
    """Snapshot contents and symlinks so a refused rerun cannot mutate the root."""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            snapshot[relative] = "directory"
    return snapshot


def test_dry_run_cli_executes_and_archives_24_fake_arms_once(tmp_path):
    output_root = tmp_path / "new-dry-run"
    command = [sys.executable, "-m", "benchmarks.autonomous_v01.runner",
               "dry-run", "--out", str(output_root)]

    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True,
                               text=True, timeout=180, check=False)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "OFFLINE_FAKE_COMPLETE"
    assert len(report["runs"]) == 24
    assert report["model_calls"] == 0
    assert report["harbor_evaluations"] == 0
    assert report["ai_judge_calls"] == 0

    summary_path = output_root / "summary.json"
    assert json.loads(summary_path.read_text(encoding="utf-8")) == report
    arm_ids = {run["arm_id"] for run in report["runs"]}
    assert len(arm_ids) == 24
    archive_root = output_root / "final-archive"
    archive_index = json.loads((archive_root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    archived_paths = {item["path"] for item in archive_index["files"]}
    for run in report["runs"]:
        assert run["result"]["status"] == "COMPLETED"
        artifact = run["result"]["final_artifact"]
        assert artifact["status"] == "CAPTURED"
        assert artifact["files"]
        for artifact_file in artifact["files"]:
            archived_path = f"{run['arm_id']}/final-artifacts/{artifact_file['path']}"
            assert archived_path in {item["path"] for item in archive_index["files"]}
            assert artifact_file["sha256"] == next(
                item["sha256"] for item in archive_index["files"]
                if item["path"] == archived_path
            )
    for arm_id in arm_ids:
        assert f"{arm_id}/result.json" in archived_paths
        assert (archive_root / arm_id / "result.json").is_file()
    for item in archive_index["files"]:
        archived_file = archive_root / item["path"]
        assert archived_file.is_file()
        assert hashlib.sha256(archived_file.read_bytes()).hexdigest() == item["sha256"]

    before_rerun = _tree_digest(output_root)
    rerun = subprocess.run(command, cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=30, check=False)
    assert rerun.returncode != 0
    assert _tree_digest(output_root) == before_rerun


def test_timed_out_worker_preserves_partial_candidate_in_final_artifact_snapshot(
    tmp_path, monkeypatch,
):
    task = json.loads(autonomous_runner.CATALOGUE.read_text(encoding="utf-8"))["tasks"][0]
    arm_id = task["task_id"] + "--codex_direct"
    arm_root = tmp_path / arm_id
    arm_root.mkdir()
    candidate_path = task["adapter"]["candidate_path"]
    partial_bytes = b"ordinary incomplete candidate bytes\x00\xff\n"

    def timeout_after_partial_write(command, *, cwd, arm_root, deadline):
        worker = [
            str(autonomous_runner.sys.executable), "-c",
            "from pathlib import Path; import sys,time; p=Path(sys.argv[1]); "
            "p.parent.mkdir(parents=True,exist_ok=True); f=p.open('wb'); "
            "f.write(bytes.fromhex(sys.argv[2])); f.flush(); time.sleep(30)",
            candidate_path, partial_bytes.hex(),
        ]
        return run_arm_command(worker, cwd=cwd, arm_root=arm_root, deadline=deadline)

    monkeypatch.setattr(autonomous_runner, "run_arm_command", timeout_after_partial_write)
    result = autonomous_runner._run_offline_command_arm(
        arm_id, task, arm_root, ArmDeadline.start(0.3),
    )

    assert result["status"] == "TIMED_OUT"
    assert result["final_artifact"]["status"] == "CAPTURED"
    assert result["final_artifact"]["files"] == [{
        "path": Path(candidate_path).name,
        "sha256": hashlib.sha256(partial_bytes).hexdigest(),
        "size_bytes": len(partial_bytes),
    }]
    assert (arm_root / "final-artifacts" / Path(candidate_path).name).read_bytes() == partial_bytes


def test_resume_preserves_finished_arms_marks_partial_arm_unknown_and_runs_missing_arm(tmp_path):
    output_root = tmp_path / "interrupted-dry-run"
    command = [sys.executable, "-m", "benchmarks.autonomous_v01.runner",
               "dry-run", "--out", str(output_root)]
    initial = subprocess.run(command, cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=180, check=False)
    assert initial.returncode == 0, initial.stderr
    initial_report = json.loads(initial.stdout)
    first, partial, missing = initial_report["runs"][:3]
    first_root = output_root / first["arm_id"]
    partial_root = output_root / partial["arm_id"]
    missing_root = output_root / missing["arm_id"]
    workspace_root = output_root / ".workspaces"

    first_before = _tree_digest(first_root)
    partial_logs_before = {
        name: (partial_root / name).read_bytes()
        for name in ("arm-worker.stdout", "arm-worker.stderr")
    }
    shutil.rmtree(output_root / "final-archive")
    (output_root / "summary.json").unlink()
    (partial_root / "result.json").unlink()
    shutil.rmtree(missing_root)
    shutil.rmtree(workspace_root / missing["arm_id"])

    resumed = subprocess.run(command + ["--resume"], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=180, check=False)
    assert resumed.returncode == 0, resumed.stderr
    report = json.loads(resumed.stdout)
    assert report["status"] == "OFFLINE_FAKE_COMPLETED_WITH_NON_SUCCESS_ARMS"
    assert len(report["runs"]) == 24
    records = {item["arm_id"]: item for item in report["runs"]}

    assert _tree_digest(first_root) == first_before
    assert records[first["arm_id"]] == first
    assert records[partial["arm_id"]]["result"]["status"] == "INTERRUPTED_UNKNOWN"
    assert {
        name: (partial_root / name).read_bytes()
        for name in ("arm-worker.stdout", "arm-worker.stderr")
    } == partial_logs_before
    assert (partial_root / "result.json").is_file()
    assert records[missing["arm_id"]]["result"]["status"] == "COMPLETED"
    assert records[missing["arm_id"]]["result"]["final_artifact"]["status"] == "CAPTURED"
    assert (missing_root / "arm-worker.stdout").is_file()
    assert (workspace_root / missing["arm_id"]).is_dir()

    archive_root = output_root / "final-archive"
    index = json.loads((archive_root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    archived_paths = {item["path"] for item in index["files"]}
    for arm_id in records:
        assert f"{arm_id}/result.json" in archived_paths
    for item in index["files"]:
        path = archive_root / item["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
