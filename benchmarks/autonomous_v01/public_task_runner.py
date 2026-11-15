"""Networkless Docker runner for the three v0.1 public DEV scorers."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .public_task_dev import ACT, CATE, HICARD

IMAGE_IDS = {ACT: "act-tensor-sparse-panel-imputation-r2",
             CATE: "causalpfn-cate-pehe-ihdp-surfaceb",
             HICARD: "hicard-latent-encoder"}
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_SOURCE_BYTES = 2 * 1024 * 1024


class PublicTaskRunnerError(RuntimeError):
    """Pinned public task DEV did not produce a valid aggregate."""


def run_public_task_dev(task_id: str, candidate: bytes, *, image: str,
                        expected_image_id: str, timeout_seconds: int,
                        docker_binary: str = "docker") -> dict[str, Any]:
    if task_id not in IMAGE_IDS:
        raise PublicTaskRunnerError("task has no v0.1 scorer")
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_SOURCE_BYTES:
        raise PublicTaskRunnerError("candidate is empty or exceeds the public evaluator limit")
    if (not image or image.startswith("-") or any(ch.isspace() for ch in image)
            or not IMAGE_ID_RE.fullmatch(expected_image_id)):
        raise PublicTaskRunnerError("public task image pin is malformed")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 3600:
        raise PublicTaskRunnerError("public task scorer timeout is outside 1..3600 seconds")
    script = Path(__file__).with_name("public_task_dev.py").resolve()
    if script.is_symlink() or not script.is_file():
        raise PublicTaskRunnerError("public task scoring script is missing or unsafe")
    started = time.monotonic()
    try:
        inspect = subprocess.run([docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                                 capture_output=True, text=True, timeout=min(15, timeout_seconds), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PublicTaskRunnerError("could not inspect pinned public DEV image") from None
    if inspect.returncode != 0 or inspect.stdout.strip() != expected_image_id:
        raise PublicTaskRunnerError("public DEV image differs from its frozen digest")
    remaining = int(timeout_seconds - (time.monotonic() - started))
    if remaining < 1:
        raise PublicTaskRunnerError("public DEV time budget expired during image inspection")
    with tempfile.TemporaryDirectory(prefix="bananloop-v01-public-dev-") as name:
        root = Path(name)
        candidate_path = root / "candidate.py"
        candidate_path.write_bytes(candidate)
        candidate_path.chmod(0o444)
        container = "bananloop-v01-dev-" + uuid4().hex
        command = [docker_binary, "run", "--rm", "--pull=never", "--network", "none",
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit", "128", "--memory", "14g", "--cpus", "8",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
                   "--name", container,
                   "--mount", f"type=bind,src={candidate_path},dst=/tmp/are-candidate.py,readonly",
                   "--mount", f"type=bind,src={script},dst=/tmp/are-public-task-dev.py,readonly",
                   "--entrypoint", "python3", image, "/tmp/are-public-task-dev.py", "score",
                   task_id, "/tmp/are-candidate.py", "/app/data/dev"]
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=max(1, remaining), check=False)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run([docker_binary, "kill", container], capture_output=True,
                               timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise PublicTaskRunnerError("public DEV scorer exceeded its task-specific time budget") from None
        if proc.returncode != 0:
            raise PublicTaskRunnerError("public DEV container failed")
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise PublicTaskRunnerError("public DEV scorer did not return one aggregate JSON object")
        try:
            result = json.loads(lines[0])
        except ValueError:
            raise PublicTaskRunnerError("public DEV aggregate is malformed JSON") from None
    expected_count = {ACT: 8, CATE: 100, HICARD: 24}[task_id]
    if (not isinstance(result, dict) or result.get("status") != "GRADED"
            or result.get("metric_id") != {
                ACT: "imputation_r2", CATE: "mean_pehe",
                HICARD: "onehot_mse_reduction_pct"}[task_id]
            or result.get("dev_cases") != expected_count
            or not isinstance(result.get("metric"), (float, int))
            or isinstance(result.get("metric"), bool)
            or not math.isfinite(float(result["metric"]))):
        raise PublicTaskRunnerError("public DEV aggregate does not match the pinned task contract")
    return {**result, "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "dev_image_id": expected_image_id, "elapsed_seconds": round(time.monotonic()-started, 3)}
