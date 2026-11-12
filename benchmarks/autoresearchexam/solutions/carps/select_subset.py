"""Deterministic random-subset baseline for the public CARPS pilot."""
from __future__ import annotations

import numpy as np


def select(points, k, seed, time_budget_s):
    """Return a valid seeded k-subset; optimization can improve its discrepancy."""
    del time_budget_s
    rng = np.random.default_rng(int(seed))
    return rng.choice(len(points), size=int(k), replace=False)
