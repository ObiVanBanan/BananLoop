"""Public-only bounded DEV panel for label-efficient-risk-estimator.

The task's public development labels are used only for this DEV diagnostic.
The official validation grader remains separate. The reduced 16-seed-per-cell
panel is deliberately identified as a pilot estimate, not the official 4,000
seed score.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import contextlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TASK_ID = "label-efficient-risk-estimator"
TASK_VERSION = "1.1.0"
TASK_SHA256 = "7fe491cec532818d52e699a6db6d87325bf421f280aa94a10f497a4afb06e12c"
INSTRUCTION_SHA256 = "66a209cd63aaaa9060abd535855f03521e2bed022a65507c7cd5ae67a7129d3a"
TASK_DOCKERFILE_SHA256 = "4b91fc76584c48c7b9a74e61a3b18594f40c50dcddf32bcb3a43b234bcd166d6"
TASK_REQUIREMENTS_SHA256 = "8afe2dab8e71cdfff123950a7104eed064b3d478f1dc5710e041c22ff96454c4"
PUBLIC_DEV_MANIFEST_SHA256 = "7878313d9126dd145b2dd3bbc41de18546271e5e3bbe7cc6a638143a3f692895"
SELECTION_SHA256 = "071671c5d6a0bf073fb46e277c71fcea46259d0e48ee49943c4c57a26adf0316"
DEFAULT_IMAGE = "bananloop-are-labelrisk-public-dev:1.1.0"
DEFAULT_IMAGE_ID = "sha256:14d2cd7c8f7b36314841805af2784620b406757f92ddc7603b8fcef2057c19e1"
PUBLIC_POOL_IDS = tuple(f"dev{i:02d}" for i in range(12))
LABEL_BUDGETS = (50, 100, 200, 400)
SEEDS_PER_CELL = 16
ROOT_SEED = 20261113
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
RESULT_PREFIX = "BANANLOOP_LABELRISK_DEV="
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicLabelRiskError(RuntimeError):
    """The public label-risk DEV fixture or its result failed validation."""


class _SeedDeadline(BaseException):
    """One candidate seed exceeded the task's five-second per-seed limit."""


def _raise_seed_deadline(_signum: int, _frame: Any) -> None:
    raise _SeedDeadline()


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_public_inputs(task_root: Path) -> dict[str, Any]:
    root = task_root.expanduser()
    if root.is_symlink() or not root.is_dir() or root.name not in {TASK_ID, "app"}:
        raise PublicLabelRiskError("public task root is missing or unsafe")
    root = root.resolve()
    pins = {
        "task.toml": TASK_SHA256,
        "instruction.md": INSTRUCTION_SHA256,
        "environment/Dockerfile": TASK_DOCKERFILE_SHA256,
        "environment/requirements.txt": TASK_REQUIREMENTS_SHA256,
    }
    for relative, digest in pins.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or _hash(path) != digest:
            raise PublicLabelRiskError("public task file differs from its frozen digest")
    dev = root / "environment/app/data/dev"
    items = []
    for pool_id in PUBLIC_POOL_IDS:
        folder = dev / pool_id
        expected_names = ("labels.npy", "meta.json", "surrogate_probs.npy", "target_probs.npy")
        if folder.is_symlink() or not folder.is_dir():
            raise PublicLabelRiskError("public DEV pool is missing or unsafe")
        for name in expected_names:
            path = folder / name
            if path.is_symlink() or not path.is_file():
                raise PublicLabelRiskError("public DEV pool file is missing or unsafe")
            items.append({"path": f"{pool_id}/{name}", "sha256": _hash(path), "size": path.stat().st_size})
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != PUBLIC_DEV_MANIFEST_SHA256:
        raise PublicLabelRiskError("public DEV pools differ from their frozen manifest digest")
    selection_path = Path(__file__).with_name("task-selection.json")
    if selection_path.is_symlink() or _hash(selection_path) != SELECTION_SHA256:
        raise PublicLabelRiskError("frozen task selection changed")
    try:
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        chosen = [item for item in selection["tasks"] if item.get("task_id") == TASK_ID]
        import tomllib
        manifest = tomllib.loads((root / "task.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        raise PublicLabelRiskError("public task manifest is malformed") from None
    if (len(chosen) != 1 or chosen[0].get("version") != TASK_VERSION
            or chosen[0].get("task_sha256") != TASK_SHA256
            or chosen[0].get("instruction_sha256") != INSTRUCTION_SHA256
            or manifest.get("task", {}).get("version") != TASK_VERSION):
        raise PublicLabelRiskError("public task identity differs from the pinned selection")
    return {"task_sha256": TASK_SHA256, "instruction_sha256": INSTRUCTION_SHA256,
            "dev_manifest_sha256": PUBLIC_DEV_MANIFEST_SHA256,
            "pool_count": len(PUBLIC_POOL_IDS)}


def evaluate_candidate(
    candidate_path: Path, task_root: Path, *, seeds_per_cell: int = SEEDS_PER_CELL,
) -> dict[str, Any]:
    """Run the fixed public pilot panel and compare against matched random sampling."""
    try:
        import numpy as np
    except ImportError:
        raise PublicLabelRiskError("numpy is required for the public label-risk panel") from None
    if type(seeds_per_cell) is not int or seeds_per_cell != SEEDS_PER_CELL:
        raise PublicLabelRiskError("this panel pins exactly 16 seeds per pool-budget cell")
    candidate_path = candidate_path.expanduser().resolve()
    task_root = task_root.expanduser().resolve()
    if (candidate_path.is_symlink() or not candidate_path.is_file()
            or candidate_path.stat().st_size > MAX_CANDIDATE_BYTES):
        raise PublicLabelRiskError("candidate estimator is missing, unsafe, or oversized")
    pins = verify_public_inputs(task_root)
    dataset_root = task_root / "environment/app/data/dev"
    cell_ratios: list[float] = []
    cell_records: list[dict[str, Any]] = []
    failures = 0
    failure_types: dict[str, int] = {}
    failure_examples: list[str] = []
    started = time.monotonic()

    for pool_number, pool_id in enumerate(PUBLIC_POOL_IDS):
        folder = dataset_root / pool_id
        target = np.load(folder / "target_probs.npy", allow_pickle=False)
        surrogate = np.load(folder / "surrogate_probs.npy", allow_pickle=False)
        labels = np.load(folder / "labels.npy", allow_pickle=False)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if (target.shape != (2000, 2) or surrogate.shape != target.shape
                or labels.shape != (2000,) or meta != {"pool_id": pool_id, "n_pool": 2000, "n_classes": 2}
                or not np.isfinite(target).all() or not np.isfinite(surrogate).all()
                or not np.allclose(target.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
                or not np.allclose(surrogate.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
                or not np.isin(labels, [0, 1]).all()):
            raise PublicLabelRiskError("public DEV pool schema or values are invalid")
        losses = -np.log(np.clip(target[np.arange(2000), labels], 1e-12, 1.0))
        true_risk = float(np.mean(losses))
        pool = {"target_probs": target, "surrogate_probs": surrogate,
                "n_pool": 2000, "n_classes": 2}
        for budget_index, budget in enumerate(LABEL_BUDGETS):
            candidate_sq: list[float] = []
            baseline_sq: list[float] = []
            for repetition in range(SEEDS_PER_CELL):
                seed = int(np.random.SeedSequence([ROOT_SEED, pool_number, budget_index, repetition]).generate_state(1)[0])
                baseline_rng = np.random.default_rng(seed)
                baseline_indices = baseline_rng.choice(2000, size=budget, replace=False)
                baseline_estimate = float(np.mean(losses[baseline_indices]))
                baseline_sq.append((baseline_estimate - true_risk) ** 2)

                try:
                    call_started = time.monotonic()
                    previous_handler = signal.getsignal(signal.SIGALRM)
                    previous_handler = signal.signal(
                        signal.SIGALRM, _raise_seed_deadline,
                    )
                    signal.setitimer(signal.ITIMER_REAL, 5.0)
                    with open(os.devnull, "w", encoding="utf-8") as sink:
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                            module_spec = importlib.util.spec_from_file_location(
                                f"labelrisk_candidate_{pool_id}_{budget}_{repetition}", candidate_path,
                            )
                            if module_spec is None or module_spec.loader is None:
                                raise ValueError("candidate module cannot be loaded")
                            module = importlib.util.module_from_spec(module_spec)
                            module_spec.loader.exec_module(module)
                            estimator_type = getattr(module, "Estimator", None)
                            if not callable(estimator_type):
                                raise ValueError("candidate lacks Estimator")
                            rng = np.random.default_rng(seed)
                            estimator = estimator_type(pool, budget, rng)
                            for _ in range(budget):
                                index = estimator.next_index()
                                if isinstance(index, (bool, np.bool_)) or not isinstance(index, (int, np.integer)):
                                    raise ValueError("next_index must return an integer")
                                index = int(index)
                                if not 0 <= index < 2000:
                                    raise ValueError("next_index is outside the public pool")
                                estimator.observe(index, int(labels[index]))
                            estimate = estimator.estimate()
                    elapsed = time.monotonic() - call_started
                    if elapsed > 5.0 or isinstance(estimate, (bool, np.bool_)) or not isinstance(estimate, (int, float, np.number)) or not math.isfinite(float(estimate)):
                        raise ValueError("candidate exceeded the per-seed clock or returned an invalid estimate")
                    candidate_sq.append((float(estimate) - true_risk) ** 2)
                except BaseException as exc:
                    failures += 1
                    failure_types[type(exc).__name__] = failure_types.get(type(exc).__name__, 0) + 1
                    if len(failure_examples) < 3:
                        failure_examples.append(f"{type(exc).__name__}: {exc}")
                    candidate_sq.append(max(1.0, float(np.max(losses)) ** 2) * 1_000_000.0)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0.0)
                    signal.signal(signal.SIGALRM, previous_handler)
            baseline_median = float(np.median(baseline_sq))
            candidate_median = float(np.median(candidate_sq))
            ratio = candidate_median / max(baseline_median, 1e-15)
            cell_ratios.append(ratio)
            cell_records.append({"pool_id": pool_id, "budget": budget,
                                 "seeds": SEEDS_PER_CELL,
                                 "candidate_median_squared_error": candidate_median,
                                 "random_baseline_median_squared_error": baseline_median,
                                 "relative_score": ratio})

    if failures:
        return {"status": "ERROR", "failure_count": failures,
                "cell_count": len(cell_records),
                "seeds_per_cell": SEEDS_PER_CELL,
                "task_sha256": pins["task_sha256"],
                "instruction_sha256": INSTRUCTION_SHA256,
                "public_dev_manifest_sha256": PUBLIC_DEV_MANIFEST_SHA256}
    metric = float(np.median(cell_ratios))
    return {"status": "GRADED", "metric": metric, "cell_count": len(cell_records),
            "seeds_per_cell": SEEDS_PER_CELL, "candidate_failures": failures,
            "candidate_failure_types": failure_types,
            "candidate_failure_examples": failure_examples,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "task_sha256": pins["task_sha256"],
            "instruction_sha256": pins["instruction_sha256"],
            "public_dev_manifest_sha256": pins["dev_manifest_sha256"],
            "root_seed": ROOT_SEED, "budgets": list(LABEL_BUDGETS),
            "pool_ids": list(PUBLIC_POOL_IDS), "cell_results": cell_records}


def run_public_labelrisk_dev(
    candidate: bytes, *, task_root: Path, image: str = DEFAULT_IMAGE,
    expected_image_id: str = DEFAULT_IMAGE_ID, timeout_seconds: int = 1200,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    """Run the bounded public panel inside the pinned networkless task image."""
    from uuid import uuid4
    from .public_devkit import _run_bounded_container

    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_CANDIDATE_BYTES:
        raise PublicLabelRiskError("candidate is empty or oversized")
    if (not isinstance(image, str) or not image or image.startswith("-")
            or any(char.isspace() for char in image)
            or not IMAGE_ID.fullmatch(expected_image_id)):
        raise PublicLabelRiskError("public label-risk image pin is malformed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise PublicLabelRiskError("public label-risk timeout must be 1 to 3600 seconds")
    source_task = task_root.expanduser().resolve()
    verify_public_inputs(source_task)
    try:
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicLabelRiskError("could not inspect the pinned public label-risk image") from None
    if inspected.returncode or inspected.stdout.strip() != expected_image_id:
        raise PublicLabelRiskError("public label-risk image differs from its frozen digest")
    with tempfile.TemporaryDirectory(prefix="bananloop-are-labelrisk-") as tmp_name:
        temp = Path(tmp_name)
        temp.chmod(0o755)
        out = temp / "output"
        out.mkdir(mode=0o755)
        candidate_path = out / "estimator.py"
        candidate_path.write_bytes(candidate)
        candidate_path.chmod(0o444)
        evaluator = temp / "evaluator"
        evaluator.mkdir(mode=0o755)
        public_root = temp / "public" / TASK_ID
        for relative in (
            "task.toml", "instruction.md", "environment/Dockerfile",
            "environment/requirements.txt",
        ):
            destination = public_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((source_task / relative).read_bytes())
            destination.chmod(0o444)
        source_dev = source_task / "environment/app/data/dev"
        for pool_id in PUBLIC_POOL_IDS:
            for filename in ("labels.npy", "meta.json", "surrogate_probs.npy", "target_probs.npy"):
                relative = Path("environment/app/data/dev") / pool_id / filename
                destination = public_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((source_dev / pool_id / filename).read_bytes())
                destination.chmod(0o444)
        for folder in sorted(public_root.rglob("*"), reverse=True):
            if folder.is_dir():
                folder.chmod(0o755)
        public_root.chmod(0o755)
        source = Path(__file__)
        selection = source.with_name("task-selection.json")
        if source.is_symlink() or selection.is_symlink() or not selection.is_file():
            raise PublicLabelRiskError("public label-risk evaluator pins are missing or unsafe")
        (evaluator / source.name).write_bytes(source.read_bytes())
        (evaluator / "task-selection.json").write_bytes(selection.read_bytes())
        for path in evaluator.iterdir():
            path.chmod(0o444)
        name = "bananloop-are-labelrisk-" + uuid4().hex
        command = [
            docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
            "--name", name, "--pids-limit", "256", "--memory", "2g", "--cpus", "1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--mount", f"type=bind,src={out},dst=/app/output,readonly",
            "--mount", f"type=bind,src={evaluator},dst=/opt/bananloop-evaluator,readonly",
            "--mount", f"type=bind,src={public_root},dst=/opt/are-public/{TASK_ID},readonly",
            "--entrypoint", "python3", image,
            "/opt/bananloop-evaluator/public_labelrisk_dev.py",
            "/app/output/estimator.py", f"/opt/are-public/{TASK_ID}",
        ]
        try:
            stdout = _run_bounded_container(
                command, docker_binary=docker_binary, container_name=name,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            raise PublicLabelRiskError("public label-risk DEV container failed") from None
        if stdout is None:
            raise PublicLabelRiskError("public label-risk container exited without an aggregate")
        lines = [line for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
        if len(lines) != 1:
            raise PublicLabelRiskError("public label-risk output lacks one aggregate result")
        try:
            result = json.loads(lines[0][len(RESULT_PREFIX):])
            metric = result["metric"]
            if (result.get("status") != "GRADED" or result.get("cell_count") != 48
                    or result.get("seeds_per_cell") != SEEDS_PER_CELL
                    or not isinstance(metric, (int, float)) or isinstance(metric, bool)
                    or not math.isfinite(metric) or metric < 0
                    or result.get("public_dev_manifest_sha256") != PUBLIC_DEV_MANIFEST_SHA256):
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise PublicLabelRiskError("public label-risk aggregate is malformed") from None
        return result


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    try:
        result = evaluate_candidate(Path(sys.argv[1]), Path(sys.argv[2]))
    except BaseException:
        return 3
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
