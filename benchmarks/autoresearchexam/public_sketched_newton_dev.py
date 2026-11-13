"""Bounded public-DEV evaluation for AutoResearchExam sketched Newton.

This runs exactly one fixed, public development instance with its official
300,000-step stream. It compares only the candidate's variance in the public
direction with the public Xi target and returns the negative relative error,
so larger is better. The candidate runs in a pinned networkless container;
the public DEV target and official grader are never mounted together with a
private verifier or accessed over the network.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from .public_devkit import PublicDevkitError, _run_bounded_container


TASK_ID = "sketched-newton-cov-estimator"
TASK_VERSION = "1.1.0"
TASK_SHA256 = "df9b2bacf143f20d139d8cad8112ce8ee5a3902a679cf6c99b28dd77fffae9f8"
INSTRUCTION_SHA256 = "1e843187ae1ca642747afec357b4f931b0fb8eeaf077e15b9545a157a2b4a2da"
RUNNER_SHA256 = "537bb3ada2a0846cf62342a4acab31e34f45b87af4171cb59aca4a3b0670fbe9"
DEV0_JSON_SHA256 = "7300ccc77bda12ba260acd95c8331a03501bf4716e5e5b1e119c808fb4f9ade6"
DEV0_XI_SHA256 = "a50f6e83bf182f10e3e2d95a546469fd46ba7f54ffca418040f899a8eaf9dd8c"
SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"
DEFAULT_IMAGE = "bananloop-are-newton-public-dev:1.1.0"
DEFAULT_IMAGE_ID = "sha256:50b2c2cf5ae3009161edcd000c6485d1f0c99445cb8de86d3c410552aab750c8"
MAX_CANDIDATE_BYTES = 131_072
MAX_TIMEOUT_SECONDS = 3_600
RESULT_PREFIX = "BANANLOOP_NEWTON_PUBLIC_DEV="
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicNewtonDevError(RuntimeError):
    """The pinned public sketched-Newton DEV panel failed closed."""


def _read_pin(root: Path, relative: str, digest: str, *, limit: int) -> bytes:
    path = root / relative
    try:
        path.relative_to(root)
    except ValueError:
        raise PublicNewtonDevError("public input escaped the task root") from None
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PublicNewtonDevError("public input path contains a symlink")
    if not path.is_file() or path.stat().st_size > limit:
        raise PublicNewtonDevError("pinned public input is missing or oversized")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PublicNewtonDevError("pinned public input digest changed")
    return raw


def verify_public_newton_inputs(task_root: Path) -> dict[str, str]:
    root = task_root.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise PublicNewtonDevError("public task root is missing or unsafe")
    root = root.resolve()
    if root.name != TASK_ID:
        raise PublicNewtonDevError("public task root does not match its selected task")
    task = _read_pin(root, "task.toml", TASK_SHA256, limit=64_000)
    instruction = _read_pin(root, "instruction.md", INSTRUCTION_SHA256, limit=64_000)
    runner = _read_pin(root, "environment/app/harness/runner.py", RUNNER_SHA256, limit=64_000)
    inst = _read_pin(root, "environment/app/harness/dev/inst_0.json", DEV0_JSON_SHA256, limit=8_000)
    xi = _read_pin(root, "environment/app/harness/dev/inst_0_xi_star.npy", DEV0_XI_SHA256, limit=64_000)
    selection_path = Path(__file__).with_name("task-selection.json")
    if selection_path.is_symlink() or not selection_path.is_file():
        raise PublicNewtonDevError("frozen AutoResearchExam selection is missing or unsafe")
    selection = selection_path.read_bytes()
    if hashlib.sha256(selection).hexdigest() != SELECTION_SHA256:
        raise PublicNewtonDevError("frozen AutoResearchExam selection digest changed")
    try:
        matches = [item for item in json.loads(selection)["tasks"] if item.get("task_id") == TASK_ID]
        import tomllib
        metadata = tomllib.loads(task.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise PublicNewtonDevError("public task metadata or selection is malformed") from None
    if (len(matches) != 1 or matches[0].get("version") != TASK_VERSION
            or matches[0].get("task_sha256") != TASK_SHA256
            or matches[0].get("instruction_sha256") != INSTRUCTION_SHA256
            or metadata.get("task", {}).get("version") != TASK_VERSION
            or "limiting covariance" not in instruction.decode("utf-8").lower()
            or not runner or not inst or not xi):
        raise PublicNewtonDevError("public Newton materials differ from the selected task pins")
    return {
        "task_sha256": TASK_SHA256,
        "instruction_sha256": INSTRUCTION_SHA256,
        "runner_sha256": RUNNER_SHA256,
        "dev_instance_sha256": DEV0_JSON_SHA256,
        "dev_target_sha256": DEV0_XI_SHA256,
        "selection_sha256": SELECTION_SHA256,
    }


_EVALUATOR = r'''from __future__ import annotations
import importlib.util, json, math, sys
from pathlib import Path
import numpy as np

TASK = Path("/app/harness")
INSTANCE_PATH = TASK / "dev" / "inst_0.json"
XI_PATH = TASK / "dev" / "inst_0_xi_star.npy"
CANDIDATE = Path("/app/output/estimator.py")
PREFIX = "BANANLOOP_NEWTON_PUBLIC_DEV="

def main():
    runner_path = TASK / "runner.py"
    spec = importlib.util.spec_from_file_location("are_newton_runner", runner_path)
    if spec is None or spec.loader is None:
        return 2
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    instance = json.loads(INSTANCE_PATH.read_text(encoding="utf-8"))
    xi = np.load(XI_PATH, allow_pickle=False)
    config = runner.make_config(instance["d"], instance["T"], instance["tau"])
    candidate_spec = importlib.util.spec_from_file_location("are_newton_candidate", CANDIDATE)
    if candidate_spec is None or candidate_spec.loader is None:
        return 3
    candidate = importlib.util.module_from_spec(candidate_spec)
    candidate_spec.loader.exec_module(candidate)
    estimate_fn = getattr(candidate, "estimate", None)
    if not callable(estimate_fn):
        return 4
    stream = runner.make_stream(instance, config, int(instance["seed_base"]),
                                int(instance["seed_base"]) + 1)
    estimate = np.asarray(estimate_fn(stream, config), dtype=np.float64)
    d = int(config["d"])
    if estimate.shape != (d, d) or not np.isfinite(estimate).all():
        return 5
    w = np.asarray(config["w"], dtype=np.float64)
    target = float(w @ xi @ w)
    observed = float(w @ estimate @ w)
    if not math.isfinite(target) or target <= 0.0 or not math.isfinite(observed):
        return 6
    error = abs((observed - target) / target)
    if not math.isfinite(error):
        return 7
    print(PREFIX + json.dumps({"status":"GRADED", "negative_relative_error":-error,
          "relative_error":error, "panel_count":1, "steps":int(instance["T"])},
          sort_keys=True, separators=(",", ":")), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


def run_public_newton_dev(
    candidate: bytes, *, task_root: Path, image: str = DEFAULT_IMAGE,
    expected_image_id: str = DEFAULT_IMAGE_ID, timeout_seconds: int = 1_200,
    docker_binary: str = "docker",
) -> dict[str, float | int | str]:
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_CANDIDATE_BYTES:
        raise PublicNewtonDevError("candidate is empty or exceeds the output size limit")
    if (not isinstance(image, str) or not image or image.startswith("-")
            or any(char.isspace() for char in image)
            or not isinstance(expected_image_id, str) or not IMAGE_ID_RE.fullmatch(expected_image_id)):
        raise PublicNewtonDevError("public Newton DEV image pin is malformed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise PublicNewtonDevError("public Newton DEV timeout must be from 1 to 3600 seconds")
    root = task_root.expanduser().resolve()
    verify_public_newton_inputs(root)
    started = time.monotonic()
    try:
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicNewtonDevError("could not inspect the pinned public Newton image") from None
    if inspected.returncode or inspected.stdout.strip() != expected_image_id:
        raise PublicNewtonDevError("public Newton DEV image differs from its pinned digest")
    remaining = int(timeout_seconds - (time.monotonic() - started))
    if remaining < 1:
        raise PublicNewtonDevError("public Newton DEV budget expired during image verification")
    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-are-newton-") as tmp_name:
            temp = Path(tmp_name)
            temp.chmod(0o755)
            out = temp / "output"
            out.mkdir(mode=0o755)
            source = out / "estimator.py"
            source.write_bytes(candidate)
            source.chmod(0o444)
            harness = temp / "harness"
            harness.mkdir(mode=0o755)
            script = harness / "evaluate.py"
            script.write_text(_EVALUATOR, encoding="utf-8")
            script.chmod(0o444)
            name = "bananloop-are-newton-" + uuid4().hex
            command = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", name, "--pids-limit", "256", "--memory", "8g", "--cpus", "8",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g,mode=1777",
                "--mount", f"type=bind,src={out},dst=/app/output,readonly",
                "--mount", f"type=bind,src={harness},dst=/opt/bananloop-evaluator,readonly",
                "--entrypoint", "python3", image, "/opt/bananloop-evaluator/evaluate.py",
            ]
            stdout = _run_bounded_container(
                command, docker_binary=docker_binary, container_name=name,
                timeout_seconds=remaining,
            )
            if stdout is None:
                raise PublicNewtonDevError("public Newton DEV container exited without a score")
            lines = [line for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
            if len(lines) != 1:
                raise PublicNewtonDevError("public Newton DEV output lacks one aggregate score")
            result = json.loads(lines[0][len(RESULT_PREFIX):])
            error = result.get("relative_error") if isinstance(result, dict) else None
            score = result.get("negative_relative_error") if isinstance(result, dict) else None
            if (result.get("status") != "GRADED" or type(result.get("panel_count")) is not int
                    or result["panel_count"] != 1 or type(result.get("steps")) is not int
                    or result["steps"] != 300_000 or isinstance(error, bool)
                    or not isinstance(error, (int, float)) or not math.isfinite(error) or error < 0
                    or isinstance(score, bool) or not isinstance(score, (int, float))
                    or not math.isfinite(score) or score != -error):
                raise PublicNewtonDevError("public Newton DEV aggregate is malformed")
            return {"status": "GRADED", "metric": float(score),
                    "relative_error": float(error), "panel_count": 1,
                    "steps": 300_000}
    except (OSError, PublicDevkitError, json.JSONDecodeError, AttributeError, TypeError):
        raise PublicNewtonDevError("public Newton DEV container could not return a valid aggregate") from None
