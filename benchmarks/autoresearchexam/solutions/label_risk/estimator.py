"""Control-variate baseline for the public label-efficient risk task."""
from __future__ import annotations

import numpy as np


class Estimator:
    def __init__(self, pool: dict, budget: int, rng: np.random.Generator) -> None:
        target = np.asarray(pool["target_probs"], dtype=np.float64)
        surrogate = np.asarray(pool["surrogate_probs"], dtype=np.float64)
        self._n = int(pool["n_pool"])
        self._budget = int(budget)
        self._target = target
        losses = -np.log(np.clip(target, 1e-12, 1.0))
        self._proxy = np.sum(surrogate * losses, axis=1)
        self._indices = rng.choice(self._n, size=self._budget, replace=False)
        self._cursor = 0
        self._residuals: list[float] = []

    def next_index(self) -> int:
        index = int(self._indices[self._cursor])
        self._cursor += 1
        return index

    def observe(self, index: int, label: int) -> None:
        target_loss = -float(np.log(max(float(self._target_prob(index, label)), 1e-12)))
        self._residuals.append(target_loss - float(self._proxy[index]))

    def estimate(self) -> float:
        if not self._residuals:
            return float(np.mean(self._proxy))
        return float(np.mean(self._proxy) + np.mean(self._residuals))

    def _target_prob(self, index: int, label: int) -> float:
        # Bound the retained state to the queried rows; the target matrix is
        # captured on construction to avoid recomputing or copying the pool.
        return float(self._target[index, label])
