"""Small, reusable training interface for the WineQT case study.

The original ``wine_case_study.py`` is an end-to-end reporting script. This
module provides a candidate-aware entrypoint that an evaluator can call with
its own train and evaluation partitions without running plots or tuning jobs.
Targets passed to :func:`train_predict` are binary (0 = quality <= 5,
1 = quality >= 6); evaluation labels are never needed for prediction.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier


def train_predict(X_train, y_train, X_eval) -> np.ndarray:
    """Fit a classifier on the supplied training partition and return P(class=1).

    ``X_train`` and ``X_eval`` may be pandas DataFrames or array-like matrices.
    ``y_train`` must contain binary labels, with 1 representing quality >= 6.
    The returned array has one positive-class probability per evaluation row.
    """
    model = RandomForestClassifier(
        n_estimators=300,
        max_features=0.8,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    class_one = np.flatnonzero(model.classes_ == 1)
    if class_one.size != 1:
        raise ValueError("training labels must include binary positive class 1")
    return model.predict_proba(X_eval)[:, class_one[0]]
