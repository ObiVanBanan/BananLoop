from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from benchmarks.autonomous_v01.runner import canonical_sha

REPO_ROOT = Path(__file__).resolve().parents[1]


def _prepare_command(output: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "benchmarks.autonomous_v01.runner",
        "prepare",
        "--out",
        str(output),
        "--seed",
        "7",
        "--model",
        "gpt-6-luna",
        "--reasoning",
        "medium",
        "--codex-version",
        "codex-cli 0.171.0",
    ]


def _run_prepare(output: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _prepare_command(output),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def test_prepare_cli_writes_deterministic_digest_bound_offline_plan(tmp_path):
    first_path = tmp_path / "first-plan.json"
    second_path = tmp_path / "second-plan.json"

    first = _run_prepare(first_path)
    second = _run_prepare(second_path)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_bytes = first_path.read_bytes()
    second_bytes = second_path.read_bytes()
    assert first_bytes == second_bytes

    plan = json.loads(first_bytes)
    assert plan == json.loads(first.stdout)
    assert plan["kind"] == "ARE_PREPARATION_ONLY"
    assert plan["launch_authorized"] is False
    assert plan["seed"] == 7
    assert plan["model"] == "gpt-6-luna"
    assert plan["reasoning"] == "medium"
    assert plan["codex_version"] == "codex-cli 0.171.0"
    assert plan["full_feature_flags"]
    assert plan["full_feature_flags"].get("profile") == "FULL"
    assert plan["full_feature_flags"].get("enabled_features")
    assert len(plan["tasks"]) == 12
    assert sum(len(pair["arms"]) for pair in plan["tasks"]) == 24
    assert plan["manifest_sha256"] == canonical_sha(
        {key: value for key, value in plan.items() if key != "manifest_sha256"}
    )


def test_prepare_cli_never_invokes_external_provider_or_harbor_tools(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "unexpected-tool-call"
    script = "#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$MARKER\"\nexit 97\n"
    for name in ("codex", "docker", "harbor"):
        executable = bin_dir / name
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    env["MARKER"] = str(marker)

    result = _run_prepare(tmp_path / "offline-plan.json", env=env)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    plan = json.loads(result.stdout)
    assert plan["launch_authorized"] is False
    assert plan.get("model_calls", 0) == 0
    assert plan.get("harbor_evaluations", 0) == 0
    assert plan.get("ai_judge_calls", 0) == 0


def test_prepare_cli_refuses_existing_output_without_mutating_it(tmp_path):
    output = tmp_path / "existing-plan.json"
    original = b"preserve existing bytes\x00\xff\n"
    output.write_bytes(original)

    result = _run_prepare(output)

    assert result.returncode != 0
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".existing-plan.json.*.tmp"))
