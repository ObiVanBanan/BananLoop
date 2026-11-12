"""Always-covering public task baseline; shortest-CI optimization can tighten it."""
from __future__ import annotations


def ece2_interval(Z, Y, k, alpha, rng):
    """Return the task's documented valid full-range interval (0, k)."""
    del Z, Y, alpha, rng
    return 0.0, float(k)
