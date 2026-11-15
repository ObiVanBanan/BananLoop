"""Networkless Docker runner for the three v0.1 public DEV scorers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

ACT = "act-tensor-sparse-panel-imputation-r2"
CATE = "causalpfn-cate-pehe-ihdp-surfaceb"
HICARD = "hicard-latent-encoder"

IMAGE_IDS = {ACT: "act-tensor-sparse-panel-imputation-r2",
             CATE: "causalpfn-cate-pehe-ihdp-surfaceb",
             HICARD: "hicard-latent-encoder"}
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
FINAL_OUTPUTS = {
    ACT: "impute.py",
    CATE: "estimator.py",
    HICARD: "encoder.py",
}


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


def run_public_task_final(task_id: str, artifact_root: Path, *, image: str,
                          expected_image_id: str, timeout_seconds: int,
                          docker_binary: str = "docker") -> dict[str, Any]:
    """Score a frozen final output directory with the shared public DEV scorer.

    This path is for post-run assessment of final-as-is artifacts. It never
    submits to Harbor/ARE and does not feed results back to an arm.
    """
    if task_id not in IMAGE_IDS:
        raise PublicTaskRunnerError("task has no v0.1 public scorer")
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise PublicTaskRunnerError("final artifact root must be a regular directory")
    if (not image or image.startswith("-") or any(ch.isspace() for ch in image)
            or not IMAGE_ID_RE.fullmatch(expected_image_id)):
        raise PublicTaskRunnerError("public task image pin is malformed")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 3600:
        raise PublicTaskRunnerError("public scorer timeout is outside 1..3600 seconds")
    started = time.monotonic()
    try:
        inspect = subprocess.run([docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                                 capture_output=True, text=True, timeout=min(15, timeout_seconds), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PublicTaskRunnerError("could not inspect pinned public DEV image") from None
    if inspect.returncode != 0 or inspect.stdout.strip() != expected_image_id:
        raise PublicTaskRunnerError("public DEV image differs from its frozen digest")
    script = Path(__file__).with_name("public_task_dev.py").resolve()
    if script.is_symlink() or not script.is_file():
        raise PublicTaskRunnerError("public task scoring script is missing or unsafe")
    with tempfile.TemporaryDirectory(prefix="bananloop-v01-final-score-") as name:
        root = Path(name)
        copied = root / "candidate"
        copied.mkdir(mode=0o700)
        total_bytes = 0
        file_hashes = []
        for source in sorted(artifact_root.rglob("*")):
            relative = source.relative_to(artifact_root)
            if source.is_dir() and not source.is_symlink():
                continue
            if source.is_symlink():
                raise PublicTaskRunnerError("final output contains a symbolic link")
            metadata = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PublicTaskRunnerError("final output contains a non-regular or hard-linked file")
            if metadata.st_size > 64_000_000 or total_bytes + metadata.st_size > 128_000_000:
                raise PublicTaskRunnerError("final output exceeds the archived source size limit")
            destination = copied / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source, destination)
            destination.chmod(0o444)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            file_hashes.append({"path": relative.as_posix(), "sha256": digest,
                                "size_bytes": metadata.st_size})
            total_bytes += metadata.st_size
        entrypoint = copied / FINAL_OUTPUTS[task_id]
        if not entrypoint.is_file() or entrypoint.is_symlink():
            return {"status": "MISSING_OR_INVALID_ENTRYPOINT", "task_id": task_id,
                    "files": file_hashes, "metric": None}
        os.chmod(copied, 0o555)
        container = "bananloop-v01-final-" + uuid4().hex
        command = [docker_binary, "run", "--rm", "--pull=never", "--network", "none",
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit", "128", "--memory", "14g", "--cpus", "8",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777", "--name", container,
                   "--mount", f"type=bind,src={copied},dst=/tmp/are-output,readonly",
                   "--mount", f"type=bind,src={script},dst=/tmp/are-public-task-dev.py,readonly",
                   "--entrypoint", "python3", image,
                   "/tmp/are-public-task-dev.py", "score", task_id,
                   f"/tmp/are-output/{FINAL_OUTPUTS[task_id]}", "/app/data/dev"]
        remaining = int(timeout_seconds - (time.monotonic() - started))
        if remaining < 1:
            return {"status": "TIMED_OUT_BEFORE_SCORING", "task_id": task_id,
                    "files": file_hashes, "metric": None}
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run([docker_binary, "kill", container], capture_output=True,
                               timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return {"status": "TIMED_OUT", "task_id": task_id,
                    "files": file_hashes, "metric": None}
    if proc.returncode != 0:
        return {"status": "SCORER_FAILED", "task_id": task_id,
                "files": file_hashes, "metric": None}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    metric = result.get("metric") if isinstance(result, dict) else None
    if (not isinstance(result, dict) or result.get("status") != "GRADED"
            or result.get("metric_id") != {
                ACT: "imputation_r2", CATE: "mean_pehe",
                HICARD: "onehot_mse_reduction_pct",
            }[task_id]
            or result.get("dev_cases") != {ACT: 8, CATE: 100, HICARD: 24}[task_id]
            or isinstance(metric, bool) or not isinstance(metric, (int, float))
            or not math.isfinite(float(metric))):
        return {"status": "INVALID_SCORER_RESULT", "task_id": task_id,
                "files": file_hashes, "metric": None}
    return {"status": "SCORED", "task_id": task_id, "metric": float(metric),
            "metric_id": result["metric_id"], "direction": result.get("direction"),
            "dev_cases": result["dev_cases"], "files": file_hashes,
            "artifact_directory_sha256": hashlib.sha256(json.dumps(
                file_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "dev_image_id": expected_image_id,
            "elapsed_seconds": round(time.monotonic() - started, 3)}
