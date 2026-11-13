"""External public DEV proxy for AutoResearchExam's CovType task.

Candidate code runs in a networkless, read-only container and receives only
the public unlabeled training pool plus values acquired through the policy
API. The evaluator keeps external labels and evaluation rows on the host. Its
static-price simulator is diagnostic only and is not official task scoring.
"""
from __future__ import annotations

import hashlib
import json
import math
import select
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np


TASK_ID = "budgeted-covtype-dual-market-open"
TASK_VERSION = "1.1.0"
PROXY_SCHEMA = "autoresearchexam-covtype-external-proxy-v1"
SOURCE_SHA256 = "89a975c2457cd48e824238ae43c5a3cb762e42c4b4078d9b44a4514055105f6d"
PROXY_MANIFEST_SHA256 = "7714bccea3285d349bd4288bded200ba06ad97385256491ec8f9e6ebc1370d48"
DEFAULT_IMAGE = "bananloop-are-covtype-proxy-worker:2026-11-13"
DEFAULT_IMAGE_ID = "sha256:db6a3d151f7777cb53d9fdf38d481b18dffb2e962dd88e0fc2700292e9b66486"
DEFAULT_PROXY_DIR = Path(
    "/home/sonik/bananloop-benchmark-env/runs/covtype-external-public-proxy-2026-11-13"
)
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
LABEL_BUDGET = 2000.0
LABEL_PRICE = 1.0
FEATURE_BUDGET = 6.0
MAX_FEATURE_QUERIES_PER_CASE = 54


class PublicCovTypeDevError(RuntimeError):
    """The CovType public proxy or candidate result failed validation."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_proxy_panel(proxy_dir: Path = DEFAULT_PROXY_DIR) -> dict[str, Any]:
    root = proxy_dir.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise PublicCovTypeDevError("external CovType proxy directory is missing or unsafe")
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PublicCovTypeDevError("external CovType proxy manifest is missing")
    if _sha256_file(manifest_path) != PROXY_MANIFEST_SHA256:
        raise PublicCovTypeDevError("external CovType proxy manifest differs from its frozen digest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PublicCovTypeDevError("external CovType proxy manifest is malformed") from None
    market = manifest.get("market_simulation") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or not isinstance(market, dict)
            or manifest.get("schema") != PROXY_SCHEMA
            or manifest.get("task_id") != TASK_ID
            or manifest.get("task_version") != TASK_VERSION
            or manifest.get("official_benchmark_compatible") is not False
            or manifest.get("metric") != "balanced_accuracy"
            or market.get("label_prices")
                != "static 1.0 per acquired example"):
        raise PublicCovTypeDevError("external CovType proxy manifest differs from its pinned scope")

    expected_files = {
        "train_features.npy", "train_labels.npy", "evaluation_features.npy",
        "evaluation_labels.npy", "feature_costs.npy", "meta.json",
    }
    hashes = manifest.get("files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != expected_files:
        raise PublicCovTypeDevError("external CovType proxy file list differs from its manifest")
    for name in expected_files:
        path = root / name
        if (path.is_symlink() or not path.is_file()
                or hashes.get(name) != _sha256_file(path)):
            raise PublicCovTypeDevError("external CovType proxy file digest mismatch")
    if manifest.get("source_sha256") != (
        "89a975c2457cd48e824238ae43c5a3cb762e42c4b4078d9b44a4514055105f6d"
    ) or manifest.get("official_generator_excluded_rows") != 250_000:
        raise PublicCovTypeDevError("external CovType proxy source exclusion is not pinned")
    return manifest


def _load_panel(proxy_dir: Path) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    manifest = verify_proxy_panel(proxy_dir)
    root = proxy_dir.resolve()
    try:
        arrays = {
            name: np.load(root / f"{name}.npy", allow_pickle=False)
            for name in (
                "train_features", "train_labels", "evaluation_features",
                "evaluation_labels", "feature_costs",
            )
        }
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PublicCovTypeDevError("external CovType proxy arrays are unreadable") from None
    x_train = arrays["train_features"]
    y_train = arrays["train_labels"]
    x_eval = arrays["evaluation_features"]
    y_eval = arrays["evaluation_labels"]
    costs = arrays["feature_costs"]
    if (not isinstance(meta, dict)
            or x_train.shape != (30_000, 54) or x_eval.shape != (10_000, 54)
            or y_train.shape != (30_000,) or y_eval.shape != (10_000,)
            or costs.shape != (54,) or not np.isfinite(x_train).all()
            or not np.isfinite(x_eval).all() or not np.isfinite(costs).all()
            or not np.isin(y_train, np.arange(7)).all()
            or not np.isin(y_eval, np.arange(7)).all()
            or not np.all(np.bincount(y_eval, minlength=7) > 0)
            or meta.get("n_features") != 54 or meta.get("n_classes") != 7
            or meta.get("label_cost") != LABEL_BUDGET
            or meta.get("dynamic_price") != "disabled_static_proxy"):
        raise PublicCovTypeDevError("external CovType proxy arrays have an invalid schema")
    return manifest, arrays


class _Worker:
    def __init__(self, process: subprocess.Popen[bytes], *, deadline: float):
        self.process = process
        self.deadline = deadline

    def call(self, operation: str, **payload: Any) -> Any:
        if self.process.poll() is not None:
            raise PublicCovTypeDevError("candidate worker exited before returning a result")
        assert self.process.stdin is not None and self.process.stdout is not None
        if time.monotonic() >= self.deadline:
            raise PublicCovTypeDevError("external CovType proxy time limit expired")
        request = {"op": operation, **payload}
        try:
            self.process.stdin.write(
                (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode()
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise PublicCovTypeDevError("candidate worker stopped accepting requests") from None

        remaining = self.deadline - time.monotonic()
        ready, _, _ = select.select([self.process.stdout], [], [], max(0.0, remaining))
        if not ready:
            raise PublicCovTypeDevError("external CovType proxy time limit expired")
        line = self.process.stdout.readline(1_000_001)
        if not line or len(line) > 1_000_000:
            raise PublicCovTypeDevError("candidate worker returned no bounded response")
        try:
            response = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            raise PublicCovTypeDevError("candidate worker returned malformed output") from None
        if (not isinstance(response, dict) or response.get("ok") is not True
                or set(response) != {"ok", "value"}):
            error = response.get("error") if isinstance(response, dict) else None
            if not isinstance(error, str) or len(error) > 64:
                error = "CandidateError"
            raise PublicCovTypeDevError(f"candidate operation failed ({error})")
        return response["value"]


def _start_worker(
    candidate: bytes,
    train_features_path: Path,
    meta_path: Path,
    *,
    image: str,
    expected_image_id: str,
    deadline: float,
    temp_dir: Path,
) -> tuple[_Worker, str]:
    if (not expected_image_id.startswith("sha256:")
            or len(expected_image_id) != 71):
        raise PublicCovTypeDevError("CovType worker image ID is not pinned")
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if inspect.returncode != 0 or inspect.stdout.strip() != expected_image_id:
        raise PublicCovTypeDevError("CovType worker image differs from its immutable pin")
    candidate_path = temp_dir / "policy.py"
    candidate_path.write_bytes(candidate)
    candidate_path.chmod(0o444)
    cidfile = temp_dir / "container.id"
    mounts = [
        f"type=bind,src={candidate_path},dst=/input/policy.py,readonly",
        f"type=bind,src={train_features_path},dst=/data/train_features.npy,readonly",
        f"type=bind,src={meta_path},dst=/data/meta.json,readonly",
    ]
    command = [
        "docker", "run", "--rm", "--interactive", "--cidfile", str(cidfile),
        "--network", "none", "--read-only", "--cpus", "1", "--memory", "2g",
        "--pids-limit", "128", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65532:65532",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
    ]
    for mount in mounts:
        command.extend(("--mount", mount))
    command.append(image)
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise PublicCovTypeDevError("Docker could not start the CovType worker") from None
    worker = _Worker(process, deadline=deadline)
    try:
        worker.call("init")
    except Exception:
        _stop_worker(worker, cidfile)
        raise
    return worker, str(cidfile)


def _stop_worker(worker: _Worker, cidfile: Path) -> None:
    process = worker.process
    try:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    if cidfile.is_file():
        container_id = cidfile.read_text(encoding="ascii", errors="ignore").strip()
        if container_id and all(c in "0123456789abcdef" for c in container_id):
            subprocess.run(["docker", "rm", "--force", container_id],
                           capture_output=True, timeout=15, check=False)


def _balanced_accuracy(truth: np.ndarray, prediction: np.ndarray) -> float:
    recalls = []
    for class_id in range(7):
        selected = truth == class_id
        count = int(selected.sum())
        if count <= 0:
            raise PublicCovTypeDevError("external evaluation panel is missing a class")
        recalls.append(float(np.mean(prediction[selected] == class_id)))
    value = float(np.mean(recalls))
    if not math.isfinite(value):
        raise PublicCovTypeDevError("candidate returned a non-finite proxy score")
    return value


def run_public_covtype_dev(
    candidate: bytes,
    *,
    proxy_dir: Path = DEFAULT_PROXY_DIR,
    image: str = DEFAULT_IMAGE,
    expected_image_id: str = DEFAULT_IMAGE_ID,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """Evaluate a candidate on the pinned external, static-price proxy panel."""
    if (not isinstance(candidate, bytes) or not candidate
            or len(candidate) > MAX_CANDIDATE_BYTES):
        raise PublicCovTypeDevError("CovType policy file is missing or oversized")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
            or not 30 <= timeout_seconds <= 3600):
        raise ValueError("timeout_seconds must be between 30 and 3600")
    start = time.monotonic()
    deadline = start + timeout_seconds
    manifest, arrays = _load_panel(proxy_dir)
    root = proxy_dir.resolve()
    worker: _Worker | None = None
    cidfile: Path | None = None
    predictions = np.empty(len(arrays["evaluation_labels"]), dtype=np.int64)
    label_prices = {class_id: LABEL_PRICE for class_id in range(7)}
    feature_prices = {
        feature_id: float(price)
        for feature_id, price in enumerate(arrays["feature_costs"])
    }
    labels: dict[int, int] = {}
    budget = LABEL_BUDGET
    rounds = 0

    with tempfile.TemporaryDirectory(prefix="bananloop-covtype-proxy-") as raw_temp:
        temp_dir = Path(raw_temp)
        worker, _ = _start_worker(
            candidate, root / "train_features.npy", root / "meta.json",
            image=image, expected_image_id=expected_image_id,
            deadline=deadline, temp_dir=temp_dir,
        )
        cidfile = temp_dir / "container.id"
        try:
            while budget > 0:
                rounds += 1
                if rounds > 2_000:
                    raise PublicCovTypeDevError("candidate exceeded the label-market request cap")
                queried = worker.call(
                    "select_queries", labeled=labels,
                    budget_left=budget, prices=label_prices,
                )
                if not isinstance(queried, list):
                    raise PublicCovTypeDevError("select_queries must return a list")
                if not queried:
                    break
                if any(type(row_id) is not int for row_id in queried):
                    raise PublicCovTypeDevError("select_queries returned a non-integer row ID")
                if len(set(queried)) != len(queried) or any(
                    row_id < 0 or row_id >= len(arrays["train_labels"])
                    or row_id in labels for row_id in queried
                ):
                    raise PublicCovTypeDevError("select_queries returned duplicate or invalid row IDs")
                accepted = queried[:int(budget // LABEL_PRICE)]
                for row_id in accepted:
                    labels[row_id] = int(arrays["train_labels"][row_id])
                budget -= len(accepted) * LABEL_PRICE
                if not accepted:
                    break

            # The public task contract makes one final call after acquisition
            # ends so the candidate can fit from the revealed labels.
            final_queries = worker.call(
                "select_queries", labeled=labels,
                budget_left=0.0, prices=label_prices,
            )
            if final_queries != []:
                raise PublicCovTypeDevError(
                    "select_queries must return an empty list after the label budget is exhausted"
                )

            for row_number, row in enumerate(arrays["evaluation_features"]):
                observed: dict[int, float] = {}
                feature_budget = FEATURE_BUDGET
                for _ in range(MAX_FEATURE_QUERIES_PER_CASE):
                    feature_id = worker.call(
                        "select_next", observed=observed,
                        budget_left=feature_budget, prices=feature_prices,
                    )
                    if feature_id is None:
                        break
                    if type(feature_id) is not int or not 0 <= feature_id < 54:
                        raise PublicCovTypeDevError("select_next returned an invalid feature ID")
                    if feature_id in observed:
                        raise PublicCovTypeDevError("select_next returned a duplicate feature ID")
                    price = feature_prices[feature_id]
                    if price > feature_budget:
                        raise PublicCovTypeDevError("select_next chose an unaffordable feature")
                    observed[feature_id] = float(row[feature_id])
                    feature_budget -= price
                else:
                    raise PublicCovTypeDevError("candidate exceeded the per-case feature request cap")
                prediction = worker.call("predict", observed=observed)
                if type(prediction) is not int or not 0 <= prediction < 7:
                    raise PublicCovTypeDevError("predict must return a Python integer class ID 0..6")
                predictions[row_number] = prediction
        finally:
            _stop_worker(worker, cidfile)

    score = _balanced_accuracy(arrays["evaluation_labels"], predictions)
    return {
        "schema": "autoresearchexam-covtype-public-proxy-result-v1",
        "status": "GRADED",
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "official_benchmark_compatible": False,
        "proxy_manifest_sha256": _sha256_file(root / "manifest.json"),
        "metric": "balanced_accuracy",
        "score": score,
        "evaluation_rows": int(len(predictions)),
        "label_rows_acquired": len(labels),
        "label_budget_used": float(len(labels) * LABEL_PRICE),
        "feature_budget_per_case": FEATURE_BUDGET,
        "static_market_simulation": True,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "limit_seconds": timeout_seconds,
        "candidate_sha256": _sha256_bytes(candidate),
    }
