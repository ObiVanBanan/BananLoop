from __future__ import annotations

import json
import sys
from pathlib import Path

from bananloop.cli import parser
from bananloop.contracts import (
    CampaignSpec,
    Direction,
    EvaluationProtocolSpec,
    EvaluationResult,
    MetricSpec,
    PairedComparisonSpec,
    SamplePlanSpec,
)
from bananloop.policy import decide


def _spec() -> CampaignSpec:
    return CampaignSpec(
        goal="improve stochastic output",
        source_repo=".",
        base_commit="deadbeef",
        evaluation_command=[sys.executable, "eval.py"],
        evaluation_protocol=EvaluationProtocolSpec(
            sample_plan=SamplePlanSpec(size=7, seed=123, repeats=1),
            paired=PairedComparisonSpec(
                metric="similarity",
                min_improved_fraction=5 / 7,
                worst_case_tolerance=0.05,
            ),
        ),
        primary_metric=MetricSpec(
            name="quality",
            direction=Direction.MAXIMIZE,
            target=0.9,
            min_improvement=0.01,
            minimum=0,
            maximum=1,
        ),
    )


def _result(primary: float, values: list[float], keys: list[str] | None = None) -> EvaluationResult:
    keys = keys or [f"case-{index}" for index in range(len(values))]
    return EvaluationResult.model_validate(
        {
            "status": "ok",
            "metrics": {"quality": primary},
            "cases": [
                {
                    "case_key": key,
                    "metrics": {"similarity": value},
                }
                for key, value in zip(keys, values, strict=True)
            ],
        }
    )


def test_paired_policy_accepts_broad_improvement():
    champion = _result(0.50, [0.50] * 7)
    candidate = _result(0.56, [0.58, 0.57, 0.56, 0.55, 0.54, 0.50, 0.49])

    out = decide(_spec(), champion, candidate)

    assert out.accepted is True
    assert out.paired_summary["n"] == 7
    assert out.paired_summary["improved"] == 5
    assert out.paired_summary["worst_delta"] >= -0.05


def test_paired_policy_rejects_average_gain_with_concentrated_regression():
    champion = _result(0.50, [0.50] * 7)
    candidate = _result(0.56, [0.80, 0.80, 0.80, 0.40, 0.40, 0.40, 0.32])

    out = decide(_spec(), champion, candidate)

    assert out.accepted is False
    assert "PAIRED_IMPROVED_FRACTION_FAILED" in out.reasons
    assert "PAIRED_WORST_CASE_REGRESSION" in out.reasons


def test_paired_policy_rejects_changed_case_set():
    champion = _result(0.50, [0.50] * 7)
    keys = [f"case-{index}" for index in range(6)] + ["different-case"]
    candidate = _result(0.60, [0.60] * 7, keys)

    out = decide(_spec(), champion, candidate)

    assert out.accepted is False
    assert "PAIRED_CASE_SET_CHANGED" in out.reasons


def test_legacy_string_artifact_is_normalized():
    result = EvaluationResult.model_validate(
        {
            "metrics": {"quality": 1.0},
            "artifacts": ["results/image.png"],
        }
    )
    assert result.artifacts[0].path == "results/image.png"
    assert result.artifacts[0].kind == "file"


def test_cli_parses_universal_evaluation_protocol():
    args = parser().parse_args(
        [
            "init",
            "--sample-size",
            "7",
            "--sample-seed",
            "123",
            "--sample-repeats",
            "2",
            "--paired-metric",
            "similarity",
            "--paired-min-improved-fraction",
            "0.7",
            "--paired-worst-case-tolerance",
            "0.05",
        ]
    )

    assert args.sample_size == 7
    assert args.sample_seed == 123
    assert args.sample_repeats == 2
    assert args.paired_metric == "similarity"
    assert args.paired_min_improved_fraction == 0.7
    assert args.paired_worst_case_tolerance == 0.05
