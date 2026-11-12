#!/usr/bin/env python3
"""Run one preregistered association analysis on the public Board Games diagnostic."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).with_name("pilot_manifest.json")


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        rank = ((start + 1) + stop) / 2.0
        for pos in range(start, stop):
            out[order[pos]] = rank
        start = stop
    return out


def _pearson(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    da, db = [x - ma for x in a], [y - mb for y in b]
    denominator = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    if denominator == 0:
        raise ValueError("correlation is undefined for a constant input")
    return sum(x * y for x, y in zip(da, db)) / denominator


def spearman(x: list[float], y: list[float]) -> float:
    return _pearson(_ranks(x), _ranks(y))


def _quantile(sorted_values: list[float], p: float) -> float:
    position = (len(sorted_values) - 1) * p
    lo, hi = int(position), min(int(position) + 1, len(sorted_values) - 1)
    fraction = position - lo
    return sorted_values[lo] * (1 - fraction) + sorted_values[hi] * fraction


def _load_pairs(csv_path: Path, x_column: str, y_column: str) -> tuple[list[float], list[float], int]:
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not {x_column, y_column} <= set(reader.fieldnames):
            raise ValueError("registered columns are absent from the frozen CSV")
        xs: list[float] = []
        ys: list[float] = []
        excluded = 0
        for row in reader:
            try:
                xv, yv = float(row[x_column]), float(row[y_column])
                if not math.isfinite(xv) or not math.isfinite(yv):
                    raise ValueError
            except (TypeError, ValueError):
                excluded += 1
                continue
            xs.append(xv)
            ys.append(yv)
    if len(xs) < 20:
        raise ValueError("fewer than 20 complete rows; refusing unstable analysis")
    return xs, ys, excluded


def run(task_id: str, *, csv_path: Path | None = None, seed: int = 20261111,
        bootstrap_resamples: int = 10_000,
        analysis_id: str = "primary_association") -> dict:
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    if analysis_id not in {item["id"] for item in manifest["registered_analyses"]}:
        raise ValueError(f"analysis is not in the preregistered catalog: {analysis_id}")
    task = next((item for item in manifest["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ValueError(f"unknown preregistered task: {task_id}")
    data = (csv_path or (ROOT / manifest["source"]["data_path"])).resolve()
    raw = data.read_bytes()
    data_digest = hashlib.sha256(raw).hexdigest()
    if data_digest != manifest["source"]["data_sha256"]:
        raise ValueError("CSV digest differs from the preregistered source")
    xs, ys, excluded = _load_pairs(data, task["x"], task["y"])
    rng = random.Random(seed)
    rho = spearman(xs, ys)
    draws: list[float] = []
    if analysis_id == "primary_association":
        for _ in range(bootstrap_resamples):
            indices = [rng.randrange(len(xs)) for _ in xs]
            try:
                draws.append(spearman([xs[i] for i in indices], [ys[i] for i in indices]))
            except ValueError:
                continue
        if len(draws) < bootstrap_resamples * 0.95:
            raise ValueError("too many degenerate bootstrap resamples")
        draws.sort()
        lower, upper = _quantile(draws, 0.025), _quantile(draws, 0.975)
        method_result = {"spearman_rho": rho, "ci95_lower": lower, "ci95_upper": upper,
                         "association_evidence": "SUPPORTED" if lower > 0 or upper < 0 else "INCONCLUSIVE"}
        method_name = "Spearman with paired row bootstrap percentile interval"
    elif analysis_id == "permutation_test":
        shuffles = min(2_000, bootstrap_resamples)
        extreme = 0
        for _ in range(shuffles):
            permuted = list(ys)
            rng.shuffle(permuted)
            extreme += abs(spearman(xs, permuted)) >= abs(rho)
        p_value = (extreme + 1) / (shuffles + 1)
        method_result = {"spearman_rho": rho, "permutation_resamples": shuffles,
                         "two_sided_p_value": p_value,
                         "association_evidence": "SUPPORTED" if p_value < 0.05 else "INCONCLUSIVE"}
        method_name = "Two-sided permutation test of Spearman correlation"
    elif analysis_id == "split_half_robustness":
        indices = list(range(len(xs)))
        rng.shuffle(indices)
        midpoint = len(indices) // 2
        left, right = indices[:midpoint], indices[midpoint:]
        method_result = {"full_spearman_rho": rho,
                         "first_half_rho": spearman([xs[i] for i in left], [ys[i] for i in left]),
                         "second_half_rho": spearman([xs[i] for i in right], [ys[i] for i in right]),
                         "half_sample_sizes": [len(left), len(right)]}
        method_name = "Seeded random split-half Spearman robustness check"
    elif analysis_id == "outlier_sensitivity":
        x_sorted, y_sorted = sorted(xs), sorted(ys)
        x_low, x_high = _quantile(x_sorted, 0.01), _quantile(x_sorted, 0.99)
        y_low, y_high = _quantile(y_sorted, 0.01), _quantile(y_sorted, 0.99)
        kept = [i for i in range(len(xs)) if x_low <= xs[i] <= x_high and y_low <= ys[i] <= y_high]
        method_result = {"full_spearman_rho": rho,
                         "trimmed_spearman_rho": spearman([xs[i] for i in kept], [ys[i] for i in kept]),
                         "rows_after_joint_1pct_tail_exclusion": len(kept)}
        method_name = "Sensitivity after excluding joint 1st/99th percentile tails"
    elif analysis_id == "data_quality_audit":
        method_result = {"rows_total": len(xs) + excluded, "complete_rows": len(xs),
                         "excluded_rows": excluded, "x_unique_values": len(set(xs)),
                         "y_unique_values": len(set(ys)), "full_spearman_rho": rho}
        method_name = "Pairwise completeness and observed-value audit"
    else:
        raise ValueError(f"unknown registered analysis: {analysis_id}")
    result = {
        "schema": "bananloop-discoverybench-boardgames-result-v1",
        "qualification": manifest["qualification"],
        "task_id": task_id,
        "question": task["question"],
        "selected_variables": {"x": task["x"], "y": task["y"]},
        "data": {"sha256": data_digest, "rows_total": manifest["source"]["rows"],
                 "sample_size": len(xs), "excluded_rows": excluded},
        "method": {"analysis_id": analysis_id, "description": method_name,
                   "statistic": manifest["evaluation"]["primary_statistic"],
                   "bootstrap": (manifest["evaluation"]["uncertainty"]
                                 if analysis_id == "primary_association" else None),
                   "seed": seed, "resamples_requested": bootstrap_resamples,
                   "valid_resamples": (len(draws) if analysis_id == "primary_association" else None)},
        "result": method_result,
        "limitations": ["This is a public synthetic diagnostic, not an official benchmark score.",
                        "Observational association does not establish causality.",
                        "The bootstrap treats rows as exchangeable resampling units."],
    }
    result["result_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


class BoardGamesExperimentExecutor:
    """Expose five preregistered public analyses to a single BananLoop campaign."""

    def __init__(self, repo: Path, task_id: str):
        self.repo = repo.resolve()
        self.task_id = task_id
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if task_id not in {item["task_id"] for item in manifest["tasks"]}:
            raise ValueError(f"unknown preregistered Board Games task: {task_id}")
        self.csv_path = (self.repo / manifest["source"]["data_path"]).resolve()
        if not self.csv_path.is_relative_to(self.repo):
            raise ValueError("registered CSV path escapes the campaign repository")
        self.catalog = tuple(dict(item) for item in manifest["registered_analyses"])

    def __call__(self, experiment_id: str, role_context: dict) -> dict:
        result = run(self.task_id, csv_path=self.csv_path, analysis_id=experiment_id)
        values = result["result"]
        summary = (f"{self.task_id} {experiment_id}: {result['method']['description']}. "
                   + ", ".join(f"{key}={value}" for key, value in values.items()))
        return {"status": "COMPLETED", "summary": summary[:1800],
                "stance": "SUPPORTS" if values.get("association_evidence") == "SUPPORTED" else "UNCERTAIN",
                "confidence": 0.95, "details": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--bootstrap-resamples", type=int, default=10_000)
    parser.add_argument("--analysis-id", default="primary_association")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 100 <= args.bootstrap_resamples <= 100_000:
        parser.error("bootstrap resamples must be between 100 and 100000")
    result = run(args.task_id, csv_path=args.csv, seed=args.seed,
                 bootstrap_resamples=args.bootstrap_resamples, analysis_id=args.analysis_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_id": args.task_id, "result_sha256": result["result_sha256"],
                      "result": result["result"]}, sort_keys=True))


if __name__ == "__main__":
    main()
