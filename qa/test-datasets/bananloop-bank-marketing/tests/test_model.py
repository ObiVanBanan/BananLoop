from __future__ import annotations

import numpy as np

from src.model import ALL_FEATURES, NUMERIC_FEATURES, build_model


def _row(offset: int) -> dict[str, str]:
    row = {name: str(30 + offset) for name in NUMERIC_FEATURES}
    row.update(
        {
            "job": "admin." if offset % 2 else "technician",
            "marital": "single" if offset % 2 else "married",
            "education": "university.degree",
            "default": "no",
            "housing": "yes",
            "loan": "no",
            "contact": "cellular",
            "month": "may",
            "day_of_week": "mon",
            "poutcome": "nonexistent",
        }
    )
    assert set(row) == set(ALL_FEATURES)
    return row


def test_model_accepts_project_list_of_feature_records():
    rows = [_row(index) for index in range(8)]
    labels = [0, 1, 0, 1, 0, 1, 0, 1]

    model = build_model()
    model.fit(rows, labels)
    probabilities = model.predict_proba(rows)

    assert probabilities.shape == (len(rows), 2)
    assert np.isfinite(probabilities).all()
