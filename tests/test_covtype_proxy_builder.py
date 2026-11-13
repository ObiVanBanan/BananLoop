from __future__ import annotations

import pytest
np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
from sklearn.model_selection import train_test_split

from benchmarks.autoresearchexam.build_covtype_proxy import (
    CovTypeProxyError,
    build_proxy_arrays,
)


def test_proxy_rows_are_outside_reproducible_official_source_sample() -> None:
    row_count = 140
    labels = np.arange(row_count, dtype=np.int64) % 7
    features = np.zeros((row_count, 54), dtype=np.float32)
    features[:, 0] = np.arange(row_count, dtype=np.float32)

    result = build_proxy_arrays(
        features, labels, official_sample_size=70,
        train_size=42, evaluation_size=14,
    )

    official, external = train_test_split(
        np.arange(row_count), train_size=70, stratify=labels, random_state=0,
    )
    feature_permutation = np.random.default_rng(0).permutation(54)
    source_id_column = int(np.flatnonzero(feature_permutation == 0)[0])
    emitted_ids = np.concatenate((
        result["train_features"][:, source_id_column],
        result["evaluation_features"][:, source_id_column],
    )).astype(np.int64)

    assert set(emitted_ids).isdisjoint(set(official.tolist()))
    assert set(emitted_ids) <= set(external.tolist())
    assert result["train_features"].shape == (42, 54)
    assert result["evaluation_features"].shape == (14, 54)
    assert np.isin(result["train_labels"], np.arange(7)).all()
    assert np.isin(result["evaluation_labels"], np.arange(7)).all()


def test_proxy_builder_rejects_invalid_or_overlapping_size_request() -> None:
    labels = np.arange(30, dtype=np.int64) % 7
    features = np.zeros((30, 54), dtype=np.float32)

    with pytest.raises(CovTypeProxyError, match="cannot support"):
        build_proxy_arrays(features, labels, official_sample_size=20,
                           train_size=8, evaluation_size=3)
