"""Scorers for the three newly pinned CPU ARE public development tasks.

This module scores public DEV material only. It never reads ``are-grader-ctx``
or connects to the ARE grader. Candidate entrypoints run in a child process;
the parent applies the documented metric to candidate outputs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

ACT = "act-tensor-sparse-panel-imputation-r2"
CATE = "causalpfn-cate-pehe-ihdp-surfaceb"
HICARD = "hicard-latent-encoder"
TASK_IDS = (ACT, CATE, HICARD)


def _finite_metric(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("public DEV metric is non-finite")
    return value


def _module(path: Path):
    spec = importlib.util.spec_from_file_location("are_public_candidate", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load candidate entrypoint")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_worker(task_id: str, candidate: Path, inputs: Path, output: Path) -> int:
    """Worker entry point; receives inputs without the metric's target arrays."""
    module = _module(candidate)
    if task_id == ACT:
        with np.load(inputs, allow_pickle=False) as z:
            panel, seed = z["panel"], int(z["seed"])
        value = module.impute(panel, seed)
        np.savez(output, pred=np.asarray(value))
    elif task_id == CATE:
        preds, statuses = [], []
        with np.load(inputs, allow_pickle=False) as z:
            n = int(z["n"])
            for i in range(n):
                try:
                    value = module.estimate_cate(
                        z[f"X_train_{i:03d}"], z[f"t_train_{i:03d}"],
                        z[f"y_train_{i:03d}"], z[f"X_test_{i:03d}"],
                    )
                    arr = np.asarray(value)
                    if arr.dtype != np.float64 or arr.shape != (75,) or not np.all(np.isfinite(arr)):
                        raise ValueError("invalid CATE prediction contract")
                    preds.append(arr)
                    statuses.append(0)
                except BaseException:
                    preds.append(np.zeros(75, dtype=np.float64))
                    statuses.append(1)
        np.savez(output, preds=np.stack(preds), status=np.asarray(statuses, dtype=np.int64))
    elif task_id == HICARD:
        with np.load(inputs, allow_pickle=False) as z:
            encoder = module.Encoder(int(z["n_categories"]), int(z["max_width"]))
            encoder.fit(z["X_train"], z["G_train"])
            E_train = encoder.transform(z["X_train"], z["G_train"])
            E_test = encoder.transform(z["X_test"], z["G_test"])
        np.savez(output, E_train=np.asarray(E_train), E_test=np.asarray(E_test))
    else:
        raise ValueError("unknown public task")
    return 0


def _run_worker(task_id: str, candidate: Path, payload: dict[str, np.ndarray],
                *, timeout: int) -> dict[str, np.ndarray]:
    with tempfile.TemporaryDirectory(prefix="are-v01-public-dev-") as name:
        root = Path(name)
        inp, out = root / "input.npz", root / "output.npz"
        np.savez(inp, **payload)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "worker", task_id,
             str(candidate), str(inp), str(out)],
            capture_output=True, timeout=timeout, check=False,
            env={**__import__("os").environ, "OMP_NUM_THREADS":"1", "OPENBLAS_NUM_THREADS":"1",
                 "MKL_NUM_THREADS":"1", "NUMEXPR_NUM_THREADS":"1"},
        )
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError("candidate worker failed")
        with np.load(out, allow_pickle=False) as z:
            return {key: np.array(z[key], copy=True) for key in z.files}


def score_act(candidate: Path, dev_root: Path) -> dict[str, Any]:
    scores = []
    files = sorted(dev_root.glob("instance_*.npz"))
    if len(files) != 8:
        raise ValueError("ACT public DEV requires exactly eight pinned panels")
    for path in files:
        with np.load(path, allow_pickle=False) as z:
            panel, mask, truth, seed = (np.array(z[k], copy=True)
                                        for k in ("panel", "eval_mask", "eval_true", "seed"))
        result = _run_worker(ACT, candidate, {"panel": panel, "seed": seed}, timeout=240)
        pred = result.get("pred")
        if (not isinstance(pred, np.ndarray) or pred.shape != panel.shape
                or pred.dtype.kind != "f" or not np.all(np.isfinite(pred))
                or np.any(pred < -1.0) or np.any(pred > 1.0)):
            scores.append(0.0)  # documented trivial imputer fallback
            continue
        yhat = np.asarray(pred[mask], dtype=np.float64)
        y = np.asarray(truth, dtype=np.float64)
        if yhat.shape != y.shape or y.size == 0:
            scores.append(0.0)
            continue
        denom = float(np.sum((y - y.mean()) ** 2))
        scores.append(0.0 if denom <= 0 else 1.0 - float(np.sum((y-yhat)**2)) / denom)
    return {"status":"GRADED", "metric":_finite_metric(np.mean(scores)),
            "metric_id":"imputation_r2", "direction":"MAXIMIZE", "dev_cases":len(scores)}


def score_cate(candidate: Path, dev_root: Path) -> dict[str, Any]:
    files = sorted(dev_root.glob("realization_*.npz"))
    if len(files) != 100:
        raise ValueError("CausalPFN public DEV requires exactly 100 pinned realizations")
    payload: dict[str, np.ndarray] = {"n": np.array(len(files), dtype=np.int64)}
    truth: list[np.ndarray] = []
    baseline: list[float] = []
    for i, path in enumerate(files):
        with np.load(path, allow_pickle=False) as z:
            for key in ("X_train", "t_train", "y_train", "X_test"):
                payload[f"{key}_{i:03d}"] = np.array(z[key], copy=True)
            truth.append(np.array(z["tau_test"], dtype=np.float64, copy=True))
            t, y = np.asarray(z["t_train"]), np.asarray(z["y_train"])
            treated, control = y[t == 1.0], y[t == 0.0]
            baseline.append(float(treated.mean()-control.mean()) if len(treated) and len(control) else 0.0)
    result = _run_worker(CATE, candidate, payload, timeout=1200)
    preds, statuses = result.get("preds"), result.get("status")
    if (not isinstance(preds, np.ndarray) or preds.shape != (100, 75)
            or not isinstance(statuses, np.ndarray) or statuses.shape != (100,)):
        raise ValueError("CausalPFN public DEV worker returned malformed arrays")
    per = []
    for y, pred, status, fallback in zip(truth, preds, statuses, baseline):
        valid = (int(status) == 0 and pred.shape == y.shape and np.all(np.isfinite(pred)))
        use = pred if valid else np.full(y.shape, fallback, dtype=np.float64)
        per.append(float(np.sqrt(np.mean((y-use)**2))) )
    return {"status":"GRADED", "metric":_finite_metric(np.mean(per)),
            "metric_id":"mean_pehe", "direction":"MINIMIZE", "dev_cases":len(per),
            "fallback_cases":int(np.count_nonzero(statuses))}


def score_hicard(candidate: Path, dev_root: Path) -> dict[str, Any]:
    from sklearn.ensemble import RandomForestRegressor

    files = sorted(dev_root.glob("instance_*.npz"))
    if len(files) != 24:
        raise ValueError("HiCard public DEV requires exactly 24 pinned instances")
    per = []
    for path in files:
        with np.load(path, allow_pickle=False) as z:
            X, G, y = (np.array(z[k], copy=True) for k in ("X", "G", "y"))
            tr, te = np.array(z["train_idx"]), np.array(z["test_idx"])
            ncat, baseline = int(z["n_categories"]), float(z["onehot_mse"])
        payload = {"X_train":np.ascontiguousarray(X[tr], dtype=np.float64),
                   "G_train":np.ascontiguousarray(G[tr], dtype=np.int64),
                   "X_test":np.ascontiguousarray(X[te], dtype=np.float64),
                   "G_test":np.ascontiguousarray(G[te], dtype=np.int64),
                   "n_categories":np.array(ncat), "max_width":np.array(8)}
        try:
            result = _run_worker(HICARD, candidate, payload, timeout=120)
            Etr, Ete = result.get("E_train"), result.get("E_test")
            if (not isinstance(Etr, np.ndarray) or not isinstance(Ete, np.ndarray)
                    or Etr.dtype != np.float64 or Ete.dtype != np.float64
                    or Etr.ndim != 2 or Ete.ndim != 2
                    or Etr.shape[0] != len(tr) or Ete.shape[0] != len(te)
                    or Etr.shape[1] != Ete.shape[1] or not 0 <= Etr.shape[1] <= 8
                    or not np.all(np.isfinite(Etr)) or not np.all(np.isfinite(Ete))
                    or (Etr.size and np.max(np.abs(Etr)) > np.finfo(np.float32).max)
                    or (Ete.size and np.max(np.abs(Ete)) > np.finfo(np.float32).max)):
                return {"status":"GRADED", "metric":0.0, "metric_id":"onehot_mse_reduction_pct",
                        "direction":"MAXIMIZE", "dev_cases":24, "invalid_output":True}
            forest = RandomForestRegressor(n_estimators=100, random_state=0, n_jobs=8)
            Xtr = np.asarray(X[tr], dtype=np.float32)
            Xte = np.asarray(X[te], dtype=np.float32)
            dtr = Xtr if Etr.shape[1] == 0 else np.hstack([Xtr, Etr.astype(np.float32)])
            dte = Xte if Ete.shape[1] == 0 else np.hstack([Xte, Ete.astype(np.float32)])
            forest.fit(dtr, np.asarray(y[tr], dtype=np.float64))
            mse = float(np.mean((np.asarray(y[te])-forest.predict(dte))**2))
            per.append(100.0*(baseline-mse)/baseline if baseline > 0 else 0.0)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, AttributeError):
            return {"status":"GRADED", "metric":0.0, "metric_id":"onehot_mse_reduction_pct",
                    "direction":"MAXIMIZE", "dev_cases":24, "invalid_output":True}
    return {"status":"GRADED", "metric":_finite_metric(np.mean(per)),
            "metric_id":"onehot_mse_reduction_pct", "direction":"MAXIMIZE", "dev_cases":len(per)}


def evaluate(task_id: str, candidate: Path, dev_root: Path) -> dict[str, Any]:
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("candidate must be a regular file")
    if task_id == ACT:
        return score_act(candidate, dev_root)
    if task_id == CATE:
        return score_cate(candidate, dev_root)
    if task_id == HICARD:
        return score_hicard(candidate, dev_root)
    raise ValueError("task has no v0.1 public scorer")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("task_id")
    worker.add_argument("candidate", type=Path)
    worker.add_argument("inputs", type=Path)
    worker.add_argument("output", type=Path)
    score = sub.add_parser("score")
    score.add_argument("task_id")
    score.add_argument("candidate", type=Path)
    score.add_argument("dev_root", type=Path)
    args = parser.parse_args()
    if args.command == "worker":
        return candidate_worker(args.task_id, args.candidate, args.inputs, args.output)
    print(json.dumps(evaluate(args.task_id, args.candidate, args.dev_root),
                     sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
