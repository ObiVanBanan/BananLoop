import numpy as np


def impute(panel, seed):
    return np.nan_to_num(panel, nan=0.0)
