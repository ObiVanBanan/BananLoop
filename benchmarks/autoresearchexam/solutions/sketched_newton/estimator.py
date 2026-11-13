"""Valid but intentionally weak public sketched-Newton baseline."""
from __future__ import annotations

import numpy as np


def estimate(stream, config):
    """Consume the one-pass stream and return the task's zero-matrix baseline."""
    for _record in stream:
        pass
    dimension = int(config["d"])
    return np.zeros((dimension, dimension), dtype=np.float64)
