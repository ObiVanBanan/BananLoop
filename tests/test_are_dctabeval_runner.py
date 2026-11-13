from __future__ import annotations

import json

import pytest

from bananloop.trusted_evaluator import TrustedEvaluatorError
from benchmarks.autoresearchexam.dctabeval_runner import AREPublicDCTABEvalEvaluatorRunner


def _runner():
    return object.__new__(AREPublicDCTABEvalEvaluatorRunner)


def test_dctabeval_validation_score_enforces_roc_auc_bounds():
    runner = _runner()
    assert runner._public_score({"status": "GRADED", "validation_score": 0.73},
                                runner.validation_score_bounds) == 0.73
    for invalid in (-0.01, 1.01):
        with pytest.raises(TrustedEvaluatorError, match="metric bounds"):
            runner._public_score({"status": "GRADED", "validation_score": invalid},
                                 runner.validation_score_bounds)


def test_dctabeval_protocol_marks_bounded_pilot_noncomparable():
    from benchmarks.autoresearchexam.dctabeval_runner import TASK_ID, TASK_VERSION

    value = json.loads(AREPublicDCTABEvalEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256="a" * 64, dev_image_id=AREPublicDCTABEvalEvaluatorRunner.default_dev_image_id,
        dev_cell_count=AREPublicDCTABEvalEvaluatorRunner.default_dev_cell_count,
    ))
    assert value["pilot_only"] is True
    assert value["official_result_comparable"] is False
    assert "six-of-twelve" in value["measurement"]
