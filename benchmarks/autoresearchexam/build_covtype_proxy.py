"""Build a labelled, external DEV diagnostic for the public CovType task.

This is explicitly a proxy panel, not an AutoResearchExam-compatible scorer.
It excludes every row in the task's public generator's initial 250k sample,
then applies the generator's public feature/class anonymization. Label and
feature prices are static because the official market transition formula is
not present in public task material.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import train_test_split

SOURCE_URL = "https://archive.ics.uci.edu/static/public/31/covertype.zip"
SOURCE_SHA256 = "89a975c2457cd48e824238ae43c5a3cb762e42c4b4078d9b44a4514055105f6d"
OFFICIAL_SAMPLE_SIZE = 250_000
ROOT_SEED = 20261113
TRAIN_POOL_SIZE = 30_000
EVALUATION_SIZE = 10_000


class CovTypeProxyError(RuntimeError):
    """The public external proxy data failed validation."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_uci_archive(archive_path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = archive_path.read_bytes()
    if _sha256(raw) != SOURCE_SHA256:
        raise CovTypeProxyError("UCI CoverType archive digest mismatch")
    try:
        member = zipfile.ZipFile(io.BytesIO(raw)).read("covtype.data.gz")
        matrix = np.loadtxt(gzip.GzipFile(fileobj=io.BytesIO(member)),
                            delimiter=",", dtype=np.float32)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        raise CovTypeProxyError("UCI CoverType archive has an unexpected public schema") from None
    if (matrix.ndim != 2 or matrix.shape[1] != 55 or matrix.shape[0] <= OFFICIAL_SAMPLE_SIZE
            or not np.isfinite(matrix).all()):
        raise CovTypeProxyError("UCI CoverType archive has an invalid public matrix")
    X = matrix[:, :54]
    y = matrix[:, 54].astype(np.int64) - 1
    if not np.isin(y, np.arange(7)).all():
        raise CovTypeProxyError("UCI CoverType labels are outside the public task schema")
    return X, y


def build_proxy_arrays(
    X: np.ndarray,
    y: np.ndarray,
    *,
    train_size: int = TRAIN_POOL_SIZE,
    evaluation_size: int = EVALUATION_SIZE,
    official_sample_size: int = OFFICIAL_SAMPLE_SIZE,
) -> dict[str, np.ndarray]:
    """Return arrays sampled only from outside the task generator's 250k set."""
    if (X.ndim != 2 or X.shape[1] != 54 or y.ndim != 1 or len(X) != len(y)
            or type(official_sample_size) is not int or official_sample_size < 1
            or type(train_size) is not int or train_size < 1
            or type(evaluation_size) is not int or evaluation_size < 1
            or len(X) <= official_sample_size + train_size + evaluation_size):
        raise CovTypeProxyError("public source arrays cannot support the pinned proxy split")

    # `train_test_split` uses the same population size, labels, stratification,
    # train size and seed as the task's public `prepare_agent_data.py`.
    official_indices, external_indices = train_test_split(
        np.arange(len(y)), train_size=official_sample_size, stratify=y,
        random_state=0,
    )
    if len(official_indices) != official_sample_size:
        raise CovTypeProxyError("failed to reproduce the task's public source sample")

    external_X = X[external_indices]
    external_y = y[external_indices]
    pool_idx, eval_idx = train_test_split(
        np.arange(len(external_y)), train_size=train_size,
        test_size=evaluation_size, stratify=external_y,
        random_state=ROOT_SEED,
    )

    # Match the public anonymization transform. Use a separate RNG for the
    # label remap, as the task's public preparation code does.
    transform_rng = np.random.default_rng(0)
    permutation = transform_rng.permutation(54)
    class_remap = transform_rng.permutation(7)
    anon_X = external_X[:, permutation].astype(np.float32, copy=False)
    anon_y = class_remap[external_y]
    return {
        "train_features": anon_X[pool_idx],
        "train_labels": anon_y[pool_idx].astype(np.int64, copy=False),
        "evaluation_features": anon_X[eval_idx],
        "evaluation_labels": anon_y[eval_idx].astype(np.int64, copy=False),
        "feature_costs": _task_feature_costs(permutation),
    }


def _task_feature_costs(permutation: np.ndarray) -> np.ndarray:
    costs = np.ones(54, dtype=np.float32)
    costs[0] = 3.0
    costs[14:54] = 3.0
    return costs[permutation]


def build_proxy(archive_path: Path, output_dir: Path) -> dict[str, Any]:
    X, y = load_uci_archive(archive_path)
    arrays = build_proxy_arrays(X, y)
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, array in arrays.items():
        path = output_dir / f"{name}.npy"
        np.save(path, array, allow_pickle=False)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

    meta = {
        "n_features": 54,
        "n_classes": 7,
        "n_train": int(len(arrays["train_features"])),
        "feature_cost": 6.0,
        "label_cost": 2000.0,
        "costs": arrays["feature_costs"].tolist(),
        "dynamic_price": "disabled_static_proxy",
    }
    (output_dir / "meta.json").write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n")
    hashes["meta.json"] = hashlib.sha256((output_dir / "meta.json").read_bytes()).hexdigest()

    manifest = {
        "schema": "autoresearchexam-covtype-external-proxy-v1",
        "task_id": "budgeted-covtype-dual-market-open",
        "task_version": "1.1.0",
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "official_generator_excluded_rows": OFFICIAL_SAMPLE_SIZE,
        "external_train_rows": int(len(arrays["train_features"])),
        "external_evaluation_rows": int(len(arrays["evaluation_features"])),
        "root_seed": ROOT_SEED,
        "market_simulation": {
            "label_budget": 2000.0,
            "label_prices": "static 1.0 per acquired example",
            "feature_budget_per_case": 6.0,
            "feature_prices": "task public metadata costs, fixed per acquisition",
        },
        "metric": "balanced_accuracy",
        "official_benchmark_compatible": False,
        "limitations": [
            "Independent public UCI rows, disjoint from the task generator's initial 250k source rows.",
            "Public instructions do not specify the dynamic market price transition formula.",
            "This panel is diagnostic only and must not be reported as an AutoResearchExam score.",
        ],
        "files_sha256": hashes,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path,
                        help="public UCI archive, verified against its pinned SHA-256")
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    result = build_proxy(options.archive, options.output)
    print(json.dumps({
        "status": "PROXY_READY",
        "official_benchmark_compatible": result["official_benchmark_compatible"],
        "train_rows": result["external_train_rows"],
        "evaluation_rows": result["external_evaluation_rows"],
        "manifest": str((options.output / "manifest.json").resolve()),
    }, sort_keys=True))
