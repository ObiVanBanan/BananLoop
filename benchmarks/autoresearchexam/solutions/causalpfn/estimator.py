import numpy as np


def estimate_cate(X_train, t_train, y_train, X_test):
    effect = float(y_train[t_train == 1].mean() - y_train[t_train == 0].mean())
    return np.full(len(X_test), effect, dtype=np.float64)
