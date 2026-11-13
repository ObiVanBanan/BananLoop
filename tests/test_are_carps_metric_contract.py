from pathlib import Path

from benchmarks.autoresearchexam import carps_runner
from benchmarks.autoresearchexam.carps_runner import AREPublicCARPSEvaluatorRunner


def test_carps_protocol_negates_the_raw_public_discrepancy_once(monkeypatch):
    runner = object.__new__(AREPublicCARPSEvaluatorRunner)
    runner._active_public_task_root = Path("/public/carps")
    runner.total_dev_timeout_seconds = 180
    runner.dev_image = "public-image"
    runner.dev_image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(
        carps_runner,
        "evaluate_carps_public_dev",
        lambda *args, **kwargs: {
            "status": "GRADED", "panel_count": 8,
            "mean_exact_linf_star_discrepancy": 0.25,
        },
    )

    result = runner._evaluate_public_dev(b"candidate", timeout_seconds=60)

    assert result == {"status": "GRADED", "dev_score": -0.25, "dev_cells": 8}
    assert carps_runner.METRIC_DIRECTION == "MAXIMIZE"
