"""Bounded public-DEV pilot evaluator for AutoResearchExam dctabeval v1.1.0.

This module evaluates only the task's public ``dev.csv`` through the pinned
``local_panel.py``. It never opens the task's grader context or contacts the
ARE grader. Six fixed draws are scored by mean ROC-AUC. This bounded pilot is
not comparable to the official twelve-split, 24-hour task run and must not be
reported as an official benchmark result. Candidate code runs in
a Docker container with no network, a read-only root, and a per-draw timeout.

The task root must be the public checkout of
``dctabeval-aeac-pooled-cat-statistics``. The task selection, task version,
instruction, public DEV data, and local panel are content-pinned below.
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
from typing import Any
from uuid import uuid4

from .public_devkit import PublicDevkitError, _run_bounded_container


TASK_ID = "dctabeval-aeac-pooled-cat-statistics"
TASK_VERSION = "1.1.0"
PANEL_DRAWS = (0, 1, 2, 3, 4, 5)
PANEL_COUNT = len(PANEL_DRAWS)
PILOT_SCOPE = "bounded six-of-twelve split pilot; not comparable to official 24-hour result"
EVAL_ROWS_PER_DRAW = 800
MAX_CANDIDATE_BYTES = 131_072
MAX_TIMEOUT_SECONDS = 3_600
PER_DRAW_TIMEOUT_SECONDS = 100

# These pins are derived from public repository materials and
# task-selection.json. Update them only when the selected task or public DEV
# panel is deliberately re-frozen.
EXPECTED_SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"
EXPECTED_TASK_SHA256 = "321de9811c9ffd08e0eb8de105eb6536ad9aad4bf43e54b19900496917117bae"
EXPECTED_INSTRUCTION_SHA256 = "23f85e039f68534902627ccc2cdb6f9acea4b039f466f54a4f5032d6ba291e71"
EXPECTED_LOCAL_PANEL_SHA256 = "188a6e82775bfb2d2ab578f91c9eccd9d7c9057f7e8f4ea37bcccefa1349e8e3"
EXPECTED_DEV_CSV_SHA256 = "69c4ba536a99da69ac2c2e9caa176aeadc47780dbcf9a316541f3f454911fdcb"
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicDCTABEvalError(RuntimeError):
    """The public dctabeval panel could not return a trusted aggregate."""


def _require_regular_beneath(root: Path, path: Path) -> None:
    """Reject symlinks and paths escaping the explicitly public task root."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise PublicDCTABEvalError("public evaluator input escaped its task root") from None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PublicDCTABEvalError("public evaluator input contains a symlink")
    if not path.is_file():
        raise PublicDCTABEvalError("pinned public evaluator input is missing")


def public_dctabeval_pins(task_root: Path) -> dict[str, Any]:
    """Verify the frozen public inputs and return their non-sensitive pins.

    ``task_root`` points at the selected task checkout. Only task.toml,
    instruction.md, local_panel.py, and the public dev.csv are read.
    """
    root = task_root.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise PublicDCTABEvalError("public task root is missing or unsafe")
    root = root.resolve()
    if root.name != TASK_ID:
        raise PublicDCTABEvalError("public task root does not match the selected task")
    paths = {
        "task": root / "task.toml",
        "instruction": root / "instruction.md",
        "local_panel": root / "environment/app/local_panel.py",
        "dev_csv": root / "environment/app/data/dev.csv",
    }
    raw: dict[str, bytes] = {}
    for name, path in paths.items():
        _require_regular_beneath(root, path)
        limit = 512 * 1024 if name == "dev_csv" else 64 * 1024
        if path.stat().st_size > limit:
            raise PublicDCTABEvalError(f"pinned public input is oversized: {name}")
        raw[name] = path.read_bytes()
    digests = {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()}
    if digests != {
        "task": EXPECTED_TASK_SHA256,
        "instruction": EXPECTED_INSTRUCTION_SHA256,
        "local_panel": EXPECTED_LOCAL_PANEL_SHA256,
        "dev_csv": EXPECTED_DEV_CSV_SHA256,
    }:
        raise PublicDCTABEvalError("public dctabeval task or DEV inputs differ from the frozen pins")

    try:
        import tomllib

        task = tomllib.loads(raw["task"].decode("utf-8"))
    except (ImportError, UnicodeDecodeError, ValueError):
        raise PublicDCTABEvalError("public task metadata is malformed") from None
    if (task.get("task", {}).get("name") != "bespokelabs/dctabeval-aeac-pooled-cat-statistics"
            or task.get("task", {}).get("version") != TASK_VERSION):
        raise PublicDCTABEvalError("public task metadata does not match its pinned version")

    selection_path = Path(__file__).with_name("task-selection.json")
    if selection_path.is_symlink() or not selection_path.is_file():
        raise PublicDCTABEvalError("frozen AutoResearchExam task selection is missing or unsafe")
    selection_bytes = selection_path.read_bytes()
    if hashlib.sha256(selection_bytes).hexdigest() != EXPECTED_SELECTION_SHA256:
        raise PublicDCTABEvalError("AutoResearchExam task selection differs from its frozen pin")
    try:
        selection = json.loads(selection_bytes)
        matches = [item for item in selection["tasks"] if item.get("task_id") == TASK_ID]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise PublicDCTABEvalError("frozen task selection is malformed") from None
    if (len(matches) != 1 or matches[0].get("version") != TASK_VERSION
            or matches[0].get("task_sha256") != digests["task"]
            or matches[0].get("instruction_sha256") != digests["instruction"]):
        raise PublicDCTABEvalError("public task does not match the frozen ten-task selection")

    return {
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "task_sha256": digests["task"],
        "instruction_sha256": digests["instruction"],
        "local_panel_sha256": digests["local_panel"],
        "dev_csv_sha256": digests["dev_csv"],
        "panel_draws": list(PANEL_DRAWS),
        "metric": "mean_roc_auc",
        "pilot_scope": PILOT_SCOPE,
    }


def run_public_dctabeval_dev(
    candidate: bytes,
    *,
    task_root: Path,
    image: str,
    expected_image_id: str,
    timeout_seconds: int = 1_200,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    """Score a candidate on the fixed six-draw public local panel.

    Only aggregate result fields are returned. Individual draw scores,
    predictions, labels, candidate output, and exception text are withheld.
    The public task image must be prebuilt and pinned by its immutable image ID.
    """
    if (not isinstance(candidate, bytes) or not candidate
            or len(candidate) > MAX_CANDIDATE_BYTES):
        raise PublicDCTABEvalError("candidate is empty or exceeds the task output size limit")
    if (not isinstance(image, str) or not image or image.startswith("-")
            or any(ch.isspace() for ch in image)
            or not isinstance(expected_image_id, str)
            or not IMAGE_ID_RE.fullmatch(expected_image_id)):
        raise PublicDCTABEvalError("public DEV image pin is malformed")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise PublicDCTABEvalError("public DEV timeout is outside the one-hour task limit")

    pins = public_dctabeval_pins(task_root)
    root = task_root.expanduser().resolve()

    started = time.monotonic()
    try:
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicDCTABEvalError("could not inspect the pinned public DEV image") from None
    if inspected.returncode != 0 or inspected.stdout.strip() != expected_image_id:
        raise PublicDCTABEvalError("public DEV image ID differs from its protocol pin")
    remaining = int(timeout_seconds - (time.monotonic() - started))
    if remaining < 1:
        raise PublicDCTABEvalError("public DEV budget expired during image verification")

    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-are-dctabeval-") as temp_name:
            temp = Path(temp_name)
            temp.chmod(0o755)
            output = temp / "output"
            output.mkdir(mode=0o755)
            solution = output / "solution.py"
            solution.write_bytes(candidate)
            solution.chmod(0o444)
            inputs = temp / "public-inputs"
            (inputs / "data").mkdir(parents=True, mode=0o755)
            pinned_sources = (
                (root / "environment/app/local_panel.py", inputs / "local_panel.py",
                 pins["local_panel_sha256"]),
                (root / "environment/app/data/dev.csv", inputs / "data/dev.csv",
                 pins["dev_csv_sha256"]),
            )
            for source, target, expected_digest in pinned_sources:
                _require_regular_beneath(root, source)
                data = source.read_bytes()
                if hashlib.sha256(data).hexdigest() != expected_digest:
                    raise PublicDCTABEvalError("public DEV input changed after pin verification")
                target.write_bytes(data)
                target.chmod(0o444)
            inputs.chmod(0o755)
            (inputs / "data").chmod(0o755)
            harness = temp / "harness"
            harness.mkdir(mode=0o755)
            runner = harness / "evaluate.py"
            runner.write_text(_CONTAINER_RUNNER, encoding="utf-8")
            runner.chmod(0o444)
            container_name = "bananloop-are-dctabeval-" + uuid4().hex
            argv = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", container_name, "--pids-limit", "128", "--memory", "4g", "--cpus", "8",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                "--mount", f"type=bind,src={output},dst=/app/output,readonly",
                "--mount", f"type=bind,src={inputs},dst=/opt/are-public-inputs,readonly",
                "--mount", f"type=bind,src={harness},dst=/opt/bananloop-evaluator,readonly",
                "--entrypoint", "python3", image,
                "/opt/bananloop-evaluator/evaluate.py",
                "--draws", ",".join(str(draw) for draw in PANEL_DRAWS),
            ]
            stdout = _run_bounded_container(
                argv, docker_binary=docker_binary, container_name=container_name,
                timeout_seconds=remaining,
            )
            if stdout is None:
                raise PublicDCTABEvalError("public DEV container returned no aggregate")
            matches = list(_RESULT_RE.finditer(stdout))
            if len(matches) != 1:
                raise PublicDCTABEvalError("public DEV output lacks one terminal aggregate")
            result = json.loads(matches[0].group("payload"))
            if not isinstance(result, dict):
                raise PublicDCTABEvalError("public DEV aggregate has an invalid shape")
            metric = result.get("metric")
            panel_count = result.get("panel_count")
            if (result.get("status") != "GRADED"
                    or isinstance(metric, bool) or not isinstance(metric, (int, float))
                    or not math.isfinite(metric) or not 0.0 <= float(metric) <= 1.0
                    or type(panel_count) is not int or panel_count != PANEL_COUNT):
                raise PublicDCTABEvalError("public DEV aggregate is invalid or does not match the fixed panel")
            # Do not expose the worker's per-draw records or any candidate text.
            return {"status": "GRADED", "metric": float(metric), "panel_count": panel_count}
    except OSError:
        raise PublicDCTABEvalError("could not prepare the isolated public DEV container") from None
    except (PublicDevkitError, json.JSONDecodeError, AttributeError, TypeError):
        raise PublicDCTABEvalError("public DEV container could not produce a valid aggregate") from None


_RESULT_RE = re.compile(r"(?m)^BANANLOOP_DCTAB_PUBLIC_DEV=(?P<payload>\{[^\r\n]*\})$")


_CONTAINER_RUNNER = r'''from __future__ import annotations
import argparse
import importlib.util
import json
import math
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

INPUTS = Path("/opt/are-public-inputs")
OUTPUT = Path("/app/output/solution.py")
PANEL = INPUTS / "local_panel.py"
DEV_CSV = INPUTS / "data/dev.csv"
PREFIX = "BANANLOOP_DCTAB_ONE_DRAW="
PER_DRAW_TIMEOUT = 100
MAX_CHILD_OUTPUT = 1_048_576

def _load_panel():
    sys.path.insert(0, str(INPUTS))
    spec = importlib.util.spec_from_file_location("are_dctab_local_panel", PANEL)
    if spec is None or spec.loader is None:
        raise RuntimeError
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def _child(draw):
    import numpy as np
    panel_module = _load_panel()
    panel = panel_module.make_local_panel(draw, dev_csv=DEV_CSV)
    spec = importlib.util.spec_from_file_location("are_dctab_candidate", OUTPUT)
    if spec is None or spec.loader is None:
        raise RuntimeError
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "fit_predict", None)
    if not callable(fn):
        raise RuntimeError
    import inspect
    parameters = list(inspect.signature(fn).parameters.values())
    if (len(parameters) != 5 or any(p.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD) for p in parameters)):
        raise RuntimeError
    scores = np.asarray(fn(
        panel["train_X"], panel["train_y"], panel["pool_X"],
        panel["eval_X"], int(panel["seed"]),
    ))
    if (scores.shape != (800,) or not np.issubdtype(scores.dtype, np.number)
            or np.issubdtype(scores.dtype, np.complexfloating)
            or not np.isfinite(scores).all()):
        raise RuntimeError
    values = scores.astype(np.float64, copy=False).tolist()
    sys.stdout.write(PREFIX + json.dumps(values, separators=(",", ":")) + "\n")
    return 0

def _run_child(draw):
    command = [sys.executable, str(Path(__file__).resolve()), "--one-draw", str(draw)]
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True,
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
             "HOME": "/tmp", "PYTHONHASHSEED": "0",
             "OMP_NUM_THREADS": "8", "OPENBLAS_NUM_THREADS": "8",
             "MKL_NUM_THREADS": "8", "NUMEXPR_NUM_THREADS": "8"},
    )
    output = bytearray()
    deadline = time.monotonic() + PER_DRAW_TIMEOUT
    try:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError
            readable, _, _ = select.select([fd], [], [], min(left, 0.25))
            if readable:
                chunk = os.read(fd, min(65536, MAX_CHILD_OUTPUT + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_CHILD_OUTPUT:
                    raise RuntimeError
            elif process.poll() is not None:
                continue
        if process.wait(timeout=max(1, min(5, deadline - time.monotonic()))) != 0:
            raise RuntimeError
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
    try:
        text = output.decode("utf-8")
        lines = [line for line in text.splitlines() if line.startswith(PREFIX)]
        if len(lines) != 1:
            raise ValueError
        values = json.loads(lines[0][len(PREFIX):])
        import numpy as np
        array = np.asarray(values, dtype=np.float64)
        if array.shape != (800,) or not np.isfinite(array).all():
            raise ValueError
        return array
    except (UnicodeDecodeError, ValueError, TypeError):
        raise RuntimeError from None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", default="0,1,2,3,4,5")
    parser.add_argument("--one-draw", type=int)
    args = parser.parse_args()
    if args.one_draw is not None:
        if not 0 <= args.one_draw < 6:
            return 2
        return _child(args.one_draw)
    import numpy as np
    from sklearn.metrics import roc_auc_score
    draws = tuple(int(value) for value in args.draws.split(",") if value)
    if draws != (0, 1, 2, 3, 4, 5):
        return 2
    panel_module = _load_panel()
    aucs = []
    for draw in draws:
        panel = panel_module.make_local_panel(draw, dev_csv=DEV_CSV)
        predictions = _run_child(draw)
        auc = float(roc_auc_score(panel["eval_y"], predictions))
        if not math.isfinite(auc) or not 0.0 <= auc <= 1.0:
            return 3
        aucs.append(auc)
    result = {"status": "GRADED", "metric": float(np.mean(aucs)),
              "panel_count": len(aucs)}
    print("BANANLOOP_DCTAB_PUBLIC_DEV=" + json.dumps(result, sort_keys=True,
          separators=(",", ":")), flush=True)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(4)
'''
