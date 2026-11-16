"""Tail-window batch-means estimator for projected iterate fluctuations."""
from __future__ import annotations

import numpy as np


def estimate(stream, config):
    """Consume the one-pass stream and estimate the limiting covariance."""
    iterates = []
    for record in stream:
        value = record
        if isinstance(record, dict):
            for key in ("projected_iterate", "iterate", "x"):
                if key in record:
                    value = record[key]
                    break
        try:
            value = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError):
            continue
        if value.ndim == 1 and value.size == int(config["d"]):
            iterates.append(value)

    dimension = int(config["d"])
    if len(iterates) < 4:
        return np.zeros((dimension, dimension), dtype=np.float64)

    tail = np.asarray(iterates[len(iterates) // 2 :], dtype=np.float64)
    batch_length = max(1, int(np.sqrt(len(tail))))
    batch_count = len(tail) // batch_length
    if batch_count < 2:
        return np.zeros((dimension, dimension), dtype=np.float64)

    batches = tail[: batch_count * batch_length].reshape(
        batch_count, batch_length, dimension
    ).mean(axis=1)
    covariance = np.atleast_2d(np.cov(batches, rowvar=False, ddof=1))
    return batch_length * covariance
