from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from benchmarks.autoresearchexam import public_covtype_dev as dev
from benchmarks.autoresearchexam.covtype_runner import (
    BACKEND_ID,
    DEV_CASE_ID,
    METRIC_FIELD,
    METRIC_ID,
    TASK_ID,
    TASK_VERSION,
    VALIDATION_CASE_ID,
    AREPublicCovTypeEvaluatorRunner,
)
from benchmarks.autoresearchexam.public_covtype_dev import _balanced_accuracy


def test_covtype_aggregate_scopes_keep_proxy_and_official_validation_distinct() -> None:
    common = {
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "contract_sha256": "a" * 64,
        "dev_image_id": dev.DEFAULT_IMAGE_ID,
        "dev_cell_count": 1,
    }
    dev_scope = json.loads(AREPublicCovTypeEvaluatorRunner.aggregate_case_input(
        tier="dev", **common,
    ))
    validation_scope = json.loads(AREPublicCovTypeEvaluatorRunner.aggregate_case_input(
        tier="validation", **common,
    ))

    assert AREPublicCovTypeEvaluatorRunner.backend_id == BACKEND_ID
    assert AREPublicCovTypeEvaluatorRunner.dev_aggregate_case_id == DEV_CASE_ID
    assert AREPublicCovTypeEvaluatorRunner.validation_aggregate_case_id == VALIDATION_CASE_ID
    assert dev_scope["metric_id"] == validation_scope["metric_id"] == METRIC_ID
    assert dev_scope["metric_field"] == validation_scope["metric_field"] == METRIC_FIELD
    assert dev_scope["official_benchmark_compatible"] is False
    assert dev_scope["pilot_only"] is True
    assert dev_scope["label_price_model"] == "static 1.0 per label"
    assert validation_scope["measurement"].startswith("one official task")
    assert validation_scope["mcp_contract_sha256"] == "a" * 64


def test_covtype_proxy_score_is_seven_class_balanced_accuracy() -> None:
    truth = np.repeat(np.arange(7, dtype=np.int64), 2)
    prediction = truth.copy()
    prediction[0] = 1

    assert _balanced_accuracy(truth, prediction) == pytest.approx(13 / 14)
    assert dev.PROXY_MANIFEST_SHA256 == "7714bccea3285d349bd4288bded200ba06ad97385256491ec8f9e6ebc1370d48"
