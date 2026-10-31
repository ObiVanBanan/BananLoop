from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from examples.autoresearchexam.sandbox import (
    CandidateSandbox,
    run_candidate,
    stage_candidate_artifact,
)


PINNED_IMAGE = (
    "python:3.13-slim@sha256:"
    "2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb"
)


def test_inert_linux_sandbox_probe_with_docker(tmp_path: Path) -> None:
    """Exercise the real Docker policy with trusted probe code, no candidate or task data."""
    required = os.getenv("BANANLOOP_REQUIRE_DOCKER_SMOKE") == "1"
    if shutil.which("docker") is None:
        if required:
            pytest.fail("Docker is required for this CI sandbox smoke")
        pytest.skip("Docker CLI is unavailable")
    daemon = subprocess.run(
        ["docker", "info"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    if daemon.returncode:
        if required:
            pytest.fail("Docker daemon is required for this CI sandbox smoke")
        pytest.skip("Docker daemon is unavailable")

    source_root = Path(__file__).resolve().parents[1] / "examples" / "autoresearchexam"
    with tempfile.TemporaryDirectory(prefix="bananloop-sandbox-probe-") as temp:
        artifact = stage_candidate_artifact(
            source_root,
            Path(temp) / "artifact",
            ("sandbox_probe.py",),
        )
        spec = CandidateSandbox(
            image=PINNED_IMAGE,
            artifact_dir=artifact,
            command=("python", "-I", "/candidate/sandbox_probe.py"),
            timeout_seconds=60,
            cpu_count=1,
            memory_bytes=8 * 1024**3,
            pids_limit=64,
            tmpfs_bytes=256 * 1024**2,
        )
        result = run_candidate(spec, case_input=b"")

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    report = json.loads(result.stdout.decode("utf-8"))
    assert report["status"] == "PASS", report
    assert report["uid"] == 65532
    assert int(report["cap_eff"], 16) == 0
    assert report["no_new_privs"] == "1"
    assert report["seccomp"] == "2"
    assert report["root_read_only"] is True
    assert report["candidate_read_only"] is True
    assert report["external_network_blocked"] is True
    assert report["cpu_quota"] <= 1.0001
    assert report["memory_limit_bytes"] <= 8 * 1024**3
    assert report["pids_limit"] <= 64
