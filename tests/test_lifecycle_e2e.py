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


def test_final_holdout_interrupt_resumes_once_without_adaptive_replanning(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "examples" / "fixture" / "run_lifecycle_e2e.py"
    output_dir = tmp_path / "final-holdout-interruption"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(output_dir), "--interrupt-final-holdout"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "GOAL_MET"
    assert summary["final_assessment"] == "ACCEPTED"
    assert summary["interrupted_stage"] == "FINAL_HOLDOUT"
    assert summary["pending_stop_reason"] == "GOAL_MET"
    assert summary["cancelled_holdout_calls"] == 1
    assert summary["total_holdout_invocations"] == 2
    assert summary["completed_holdout_evaluations"] == 1
    assert summary["completed_holdout_receipts"] == 1
    assert summary["final_holdout_recorded_events"] == 1
    assert summary["agent_calls_after_terminal_intent"] == 0

def test_hil_cli_approval_continues_child_campaign_to_report(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "examples" / "fixture" / "run_lifecycle_e2e.py"
    output_dir = tmp_path / "hil-pivot-cli"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(output_dir), "--hil-pivot-cli"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "GOAL_MET"
    assert summary["operator_stop_visible"] is True
    assert summary["proposal_identity_displayed"] is True
    assert summary["approval_replay"] == "same_receipt"
    assert summary["decision_receipts"] == 1
    assert summary["child_lineage_parent"]
    assert summary["child_status"] == "GOAL_MET"
    assert summary["agent_calls_after_terminal_resume"] == summary["agent_calls_before_terminal_resume"]
    assert summary["final_assessment"] == "DEV_ONLY"
    assert summary["model_calls"] == 0
    assert summary["holdout_configured"] is False
    report = (output_dir / "logs" / "child-report.stdout.txt").read_text(encoding="utf-8")
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
