"""Synthetic-testable AutoResearchExam v1.1 scorer contracts.

Adapted from the pinned Apache-2.0 scorer sources at
23c0d92e11af1dea821f972b5f0a739060958002. Changes: isolated the metric and
reference-sampling contracts for synthetic tests. This module consumes synthetic
or caller-supplied arrays only; it does not load task data or run candidates.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable

import numpy as np

UPSTREAM_COMMIT = "23c0d92e11af1dea821f972b5f0a739060958002"
RISK_BUDGETS = (50, 100, 200, 400)
RISK_SE_CAP = 100.0
RISK_RATIO_CAP = 1.0e4
RISK_SEED_SALT = "2508-09093/label-efficient-risk-estimator/v1"


def score_imputation_call(raw: object, truth: np.ndarray, mask: np.ndarray) -> tuple[float, bool]:
    """Score one imputer return; invalid output follows the task's zero-score rule."""
    truth_array = np.asarray(truth, dtype=np.float64)
    mask_array = np.asarray(mask, dtype=bool)
    try:
        prediction = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return 0.0, False
    if (
        truth_array.ndim != 2
        or prediction.shape != truth_array.shape
        or mask_array.shape != truth_array.shape
        or not np.isfinite(prediction).all()
        or not np.isfinite(truth_array).all()
    ):
        return 0.0, False

    scores: list[float] = []
    for feature in range(truth_array.shape[1]):
        selected = mask_array[:, feature]
        if int(selected.sum()) < 2:
            continue
        feature_truth = truth_array[selected, feature]
        total = float(np.sum((feature_truth - feature_truth.mean()) ** 2))
        if not math.isfinite(total) or total <= 1e-12:
            continue
        residual = float(np.sum((feature_truth - prediction[selected, feature]) ** 2))
        scores.append(1.0 - residual / total)
    if not scores:
        return 0.0, False
    score = float(np.mean(scores))
    return (score, True) if math.isfinite(score) else (0.0, False)


def risk_cell_seed(instance_id: str, budget: int) -> int:
    digest = hashlib.sha256(f"{RISK_SEED_SALT}|{instance_id}|{budget}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def risk_seed_permutation(cell_seed: int, seed: int, n_pool: int) -> np.ndarray:
    return np.random.default_rng([cell_seed, seed, 0x5EED]).permutation(n_pool)


def risk_exact(target_probs: np.ndarray, labels: np.ndarray) -> float:
    probabilities = np.asarray(target_probs, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.int64)
    if probabilities.ndim != 2 or targets.shape != (probabilities.shape[0],):
        raise ValueError("risk inputs have incompatible shapes")
    selected = probabilities[np.arange(targets.shape[0]), targets]
    return float(np.mean(-np.log(np.clip(selected, 1e-300, None))))


def risk_reference_indices(cell_seed: int, seed: int, n_pool: int, budget: int) -> np.ndarray:
    if budget not in RISK_BUDGETS or budget > n_pool:
        raise ValueError("risk budget is unsupported for this pool")
    permutation = risk_seed_permutation(cell_seed, seed, n_pool)
    rng = np.random.default_rng([cell_seed, seed])
    return permutation[rng.choice(n_pool, size=budget, replace=False)]


def risk_reference_squared_errors(
    target_probs: np.ndarray,
    labels: np.ndarray,
    budget: int,
    cell_seed: int,
    seeds: Iterable[int],
) -> np.ndarray:
    probabilities = np.asarray(target_probs, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.int64)
    if probabilities.ndim != 2 or targets.shape != (probabilities.shape[0],):
        raise ValueError("risk inputs have incompatible shapes")
    n_pool = targets.shape[0]
    risk = risk_exact(probabilities, targets)
    losses = -np.log(np.clip(probabilities[np.arange(n_pool), targets], 1e-300, None))
    errors = []
    for seed in seeds:
        indices = risk_reference_indices(cell_seed, int(seed), n_pool, budget)
        estimate = float(np.mean(losses[indices]))
        errors.append((estimate - risk) ** 2)
    return np.asarray(errors, dtype=np.float64)


def risk_seed_squared_error(estimate: float, risk: float, *, valid: bool = True) -> float:
    """Apply the grader's per-seed failure penalty and squared-error cap."""
    try:
        predicted = float(estimate)
        truth = float(risk)
    except (TypeError, ValueError, OverflowError):
        return RISK_SE_CAP
    if not valid or not math.isfinite(predicted) or not math.isfinite(truth):
        return RISK_SE_CAP
    return float(min(RISK_SE_CAP, (predicted - truth) ** 2))


def risk_cell_ratio(candidate_squared_errors: np.ndarray, reference_squared_errors: np.ndarray) -> float:
    candidate = np.asarray(candidate_squared_errors, dtype=np.float64)
    reference = np.asarray(reference_squared_errors, dtype=np.float64)
    if candidate.size == 0 or reference.size == 0 or candidate.shape != reference.shape:
        raise ValueError("paired risk error arrays must be non-empty and have identical shapes")
    numerator = float(np.median(candidate))
    denominator = float(np.median(reference))
    if not math.isfinite(denominator) or denominator <= 0.0:
        denominator = 1e-12
    return float(min(RISK_RATIO_CAP, numerator / denominator))


def aggregate_risk_cells(cell_ratios: Iterable[float]) -> float:
    values = np.asarray(list(cell_ratios), dtype=np.float64)
    if values.size == 0 or not np.isfinite(values).all():
        return RISK_RATIO_CAP
    return float(np.median(values))
