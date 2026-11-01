from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("sigint", [False, True], ids=["typed-pause", "sigint"])
def test_lifecycle_runner_recovers_and_reports_dev_only(tmp_path: Path, sigint: bool) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "examples" / "fixture" / "run_lifecycle_e2e.py"
    output_dir = tmp_path / ("sigint" if sigint else "typed-pause")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    command = [sys.executable, str(script), "--output-dir", str(output_dir)]
    if sigint:
        command.append("--sigint")

    result = subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "GOAL_MET"
    assert summary["final_assessment"] == "DEV_ONLY"
    assert summary["model_calls"] == 0
    assert summary["holdout_configured"] is False
    assert summary["paused_status"] == ("PAUSED_INFRA" if sigint else "PAUSED_USER")
    report = (output_dir / "logs" / "report.stdout.txt").read_text(encoding="utf-8")
    report.encode("cp1252")
    assert "Baseline -> final:" in report

def test_fake_codex_cli_signal_cancellation_recovers_through_full_lifecycle(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "examples" / "fixture" / "run_lifecycle_e2e.py"
    output_dir = tmp_path / "fake-codex-recovery"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(output_dir), "--fake-codex-recovery"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "GOAL_MET"
    assert summary["final_assessment"] == "DEV_ONLY"
    assert summary["model_calls"] == 0
    assert summary["holdout_configured"] is False
    assert summary["paused_status"] == "PAUSED_INFRA"
    assert summary["cancelled_action"] == "exp:1:propose"
    assert summary["proposal_invocations"] == 2
    assert summary["implementation_invocations"] == 1
