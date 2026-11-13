"""Compact CatBoost baseline for the public pooled categorical task."""
from __future__ import annotations

import numpy as np
from catboost import CatBoostClassifier


def fit_predict(train_X, train_y, pool_X, eval_X, seed):
    """Fit a categorical gradient-boosting baseline and rank evaluation rows."""
    del pool_X
    model = CatBoostClassifier(
        iterations=300,
        depth=7,
        learning_rate=0.05,
        loss_function="Logloss",
        random_seed=int(seed),
        thread_count=8,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(train_X, np.asarray(train_y, dtype=np.int64), cat_features=list(range(train_X.shape[1])))
    return model.predict_proba(eval_X)[:, 1].astype(np.float64, copy=False)
