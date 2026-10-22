"""Editable baseline model for the Bank Marketing BananLoop playground."""

from collections.abc import Mapping

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler


NUMERIC_FEATURES = [
    "age",
    "campaign",
    "pdays",
    "previous",
    "emp.var.rate",
    "cons.price.idx",
    "cons.conf.idx",
    "euribor3m",
    "nr.employed",
]

CATEGORICAL_FEATURES = [
    "job",
    "marital",
    "education",
    "default",
    "housing",
    "loan",
    "contact",
    "month",
    "day_of_week",
    "poutcome",
]
ALL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def _records_to_matrix(rows):
    """Convert the project's list-of-records input to a 2D feature matrix."""
    if hasattr(rows, "loc") and hasattr(rows, "columns"):
        return rows.loc[:, ALL_FEATURES].to_numpy(dtype=object)

    if len(rows) and isinstance(rows[0], Mapping):
        return np.asarray(
            [[row[name] for name in ALL_FEATURES] for row in rows],
            dtype=object,
        )

    return np.asarray(rows, dtype=object)


def build_model():
    """Build the baseline estimator; experiments can vary this pipeline."""
    numeric_indices = list(range(len(NUMERIC_FEATURES)))
    categorical_indices = list(range(len(NUMERIC_FEATURES), len(ALL_FEATURES)))
    features = ColumnTransformer(
        [
            ("numeric", StandardScaler(), numeric_indices),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                categorical_indices,
            ),
        ],
        remainder="drop",
    )
    classifier = LogisticRegression(
        C=1.0,
        max_iter=250,
        solver="liblinear",
        random_state=17,
    )
    return make_pipeline(
        FunctionTransformer(_records_to_matrix, validate=False),
        features,
        classifier,
    )
