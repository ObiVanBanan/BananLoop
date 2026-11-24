from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT = REPO_ROOT / "examples" / "autoresearchexam" / "pilot.py"
MANIFEST = PILOT.with_name("manifest.json")


def _load_module():
    sys.path.insert(0, str(PILOT.parent))
    try:
        import pilot
    finally:
        sys.path.pop(0)
    return pilot


def test_issue64_manifest_preflight_fails_closed_without_paid_execution() -> None:
    pilot = _load_module()
    raw = MANIFEST.read_bytes()
    manifest = json.loads(raw)

    result = pilot.evaluate_preflight(manifest, manifest_bytes=raw)

    assert result["decision"] == "BLOCKED / INELIGIBLE"
    assert {item["gate"] for item in result["blockers"]} >= {
        "clean_integration_base",
        "data_rights_and_privacy",
        "agent_evaluator_and_proxy_isolation",
        "target_windows_sandbox_preflight",
        "provider_usd_admission_and_settlement",
    }
    assert result["execution_started"] is False
    assert result["model_calls_made"] == 0
    assert result["paid_arms_started"] == 0
    assert result["holdout_scored"] is False


def test_preflight_requires_evidence_for_passed_gates() -> None:
    pilot = _load_module()
    manifest = {"protocol_id": "fixture", "gates": [{"id": "sandbox", "status": "PASS", "evidence": []}]}

    result = pilot.evaluate_preflight(manifest, manifest_bytes=b"fixture")

    assert result["decision"] == "BLOCKED / INELIGIBLE"
    assert result["blockers"][0] == {
        "gate": "sandbox",
        "status": "UNKNOWN",
        "reason": "PASS has no evidence reference.",
    }
    assert {item["gate"] for item in result["blockers"][1:]} == set(pilot.REQUIRED_GATES) - {"sandbox"}


def test_preflight_reports_recorded_checks_but_never_starts_execution() -> None:
    pilot = _load_module()
    manifest = {
        "protocol_id": "fixture",
        "gates": [
            {"id": gate_id, "status": "PASS", "evidence": ["sha256:abc"]}
            for gate_id in pilot.REQUIRED_GATES
        ],
    }

    result = pilot.evaluate_preflight(manifest, manifest_bytes=b"fixture")

    assert result["decision"] == "PRECHECKS_RECORDED_NOT_EXECUTED"
    assert result["execution_started"] is False
    assert result["runner_implemented"] is False


def test_preflight_cli_emits_json_and_returns_blocked_exit_code() -> None:
    result = subprocess.run(
        [sys.executable, str(PILOT), "preflight", "--config", str(MANIFEST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["decision"] == "BLOCKED / INELIGIBLE"
    assert report["manifest_sha256"]
    assert report["model_calls_made"] == 0


def test_no_arm_execution_command_is_exposed() -> None:
    result = subprocess.run(
        [sys.executable, str(PILOT), "run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


@pytest.mark.parametrize("gates", [None, {}, [None], [{"status": "PASS"}]])
def test_invalid_gate_manifest_is_rejected(gates) -> None:
    pilot = _load_module()
    manifest = {"protocol_id": "fixture", "gates": gates}

    with pytest.raises(ValueError):
        pilot.evaluate_preflight(manifest, manifest_bytes=b"fixture")


def test_missing_required_gate_fails_closed() -> None:
    pilot = _load_module()
    manifest = {"protocol_id": "fixture", "gates": []}

    result = pilot.evaluate_preflight(manifest, manifest_bytes=b"fixture")

    assert result["decision"] == "BLOCKED / INELIGIBLE"
    assert {item["gate"] for item in result["blockers"]} == set(pilot.REQUIRED_GATES)
