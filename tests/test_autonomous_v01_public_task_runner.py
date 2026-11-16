from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.autonomous_v01.public_task_runner import PublicTaskRunnerError, run_public_task_final


IMAGE_ID = "sha256:" + "a" * 64


def _fake_docker(tmp_path: Path, *, allow_run: bool = True) -> Path:
    binary = tmp_path / "docker-fake"
    body = (
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "args=sys.argv[1:]\n"
        "if args[:2] == ['image','inspect']:\n"
        f" print({IMAGE_ID!r}); raise SystemExit(0)\n"
        "if args and args[0] == 'run':\n"
        f" assert {allow_run!r}, 'unexpected Docker run in missing-output case'\n"
        " assert args[args.index('--network') + 1] == 'none'\n"
        " assert '--read-only' in args and '--cap-drop=ALL' in args\n"
        " print(json.dumps({'status':'GRADED','metric':0.125,'metric_id':'imputation_r2','direction':'MAXIMIZE','dev_cases':8}))\n"
        " raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_final_public_scorer_uses_same_metric_on_final_directory_without_harbor(tmp_path):
    output = tmp_path / "final"
    output.mkdir()
    (output / "impute.py").write_text("def impute(panel, seed): return panel\n", encoding="utf-8")
    result = run_public_task_final(
        "act-tensor-sparse-panel-imputation-r2", output,
        image="local-task:1.1.0", expected_image_id=IMAGE_ID,
        timeout_seconds=30, docker_binary=str(_fake_docker(tmp_path)),
    )
    assert result["status"] == "SCORED"
    assert result["metric_id"] == "imputation_r2"
    assert result["metric"] == 0.125
    assert result["dev_cases"] == 8
    assert len(result["artifact_directory_sha256"]) == 64


def test_final_public_scorer_records_missing_output_without_running_container(tmp_path):
    output = tmp_path / "empty"
    output.mkdir()
    result = run_public_task_final(
        "act-tensor-sparse-panel-imputation-r2", output,
        image="local-task:1.1.0", expected_image_id=IMAGE_ID,
        timeout_seconds=30, docker_binary=str(_fake_docker(tmp_path, allow_run=False)),
    )
    assert result["status"] == "MISSING_OR_INVALID_ENTRYPOINT"
    assert result["metric"] is None


def test_final_public_scorer_rejects_symlinked_output(tmp_path):
    output = tmp_path / "final"
    output.mkdir()
    target = tmp_path / "outside.py"
    target.write_text("pass\n", encoding="utf-8")
    (output / "impute.py").symlink_to(target)
    with pytest.raises(PublicTaskRunnerError, match="symbolic link"):
        run_public_task_final(
            "act-tensor-sparse-panel-imputation-r2", output,
            image="local-task:1.1.0", expected_image_id=IMAGE_ID,
            timeout_seconds=30, docker_binary=str(_fake_docker(tmp_path)),
        )
