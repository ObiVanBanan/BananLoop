from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.autonomous_v01 import public_task_runner
from benchmarks.autonomous_v01.public_task_runner import PublicTaskRunnerError, run_public_task_final


IMAGE_ID = "sha256:" + "a" * 64


def _fake_docker(monkeypatch: pytest.MonkeyPatch, *, allow_run: bool = True) -> str:
    """Mock Docker at the subprocess boundary, without an OS-specific executable."""
    def fake_run(command, **_kwargs):
        assert command[0] == "docker-fake"
        args = command[1:]
        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=IMAGE_ID + "\n", stderr="")
        if args and args[0] == "run":
            assert allow_run, "unexpected Docker run in missing-output case"
            assert args[args.index("--network") + 1] == "none"
            assert "--read-only" in args and "--cap-drop=ALL" in args
            result = {
                "status": "GRADED", "metric": 0.125, "metric_id": "imputation_r2",
                "direction": "MAXIMIZE", "dev_cases": 8,
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result) + "\n", stderr="")
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="")

    monkeypatch.setattr(public_task_runner.subprocess, "run", fake_run)
    return "docker-fake"


def test_final_public_scorer_uses_same_metric_on_final_directory_without_harbor(tmp_path, monkeypatch):
    output = tmp_path / "final"
    output.mkdir()
    (output / "impute.py").write_text("def impute(panel, seed): return panel\n", encoding="utf-8")
    result = run_public_task_final(
        "act-tensor-sparse-panel-imputation-r2", output,
        image="local-task:1.1.0", expected_image_id=IMAGE_ID,
        timeout_seconds=30, docker_binary=_fake_docker(monkeypatch),
    )
    assert result["status"] == "SCORED"
    assert result["metric_id"] == "imputation_r2"
    assert result["metric"] == 0.125
    assert result["dev_cases"] == 8
    assert len(result["artifact_directory_sha256"]) == 64


def test_final_public_scorer_records_missing_output_without_running_container(tmp_path, monkeypatch):
    output = tmp_path / "empty"
    output.mkdir()
    result = run_public_task_final(
        "act-tensor-sparse-panel-imputation-r2", output,
        image="local-task:1.1.0", expected_image_id=IMAGE_ID,
        timeout_seconds=30, docker_binary=_fake_docker(monkeypatch, allow_run=False),
    )
    assert result["status"] == "MISSING_OR_INVALID_ENTRYPOINT"
    assert result["metric"] is None


def test_final_public_scorer_rejects_symlinked_output(tmp_path, monkeypatch):
    output = tmp_path / "final"
    output.mkdir()
    target = tmp_path / "outside.py"
    target.write_text("pass\n", encoding="utf-8")
    try:
        (output / "impute.py").symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        # Windows may have symlink support disabled for an unprivileged test
        # process. Keep the rejection check active wherever symlinks are usable.
        pytest.skip(f"cannot create a symlink in this environment: {exc}")
    with pytest.raises(PublicTaskRunnerError, match="symbolic link"):
        run_public_task_final(
            "act-tensor-sparse-panel-imputation-r2", output,
            image="local-task:1.1.0", expected_image_id=IMAGE_ID,
            timeout_seconds=30, docker_binary=_fake_docker(monkeypatch),
        )
