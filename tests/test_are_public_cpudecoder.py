from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.autoresearchexam import public_cpudecoder_dev as dev
from benchmarks.autoresearchexam.cpudecoder_runner import (
    AREPublicCPUDecoderEvaluatorRunner,
    TASK_ID,
    TASK_VERSION,
)


def _record(*, equivalent: int = 3) -> dict:
    return {
        "metric": 1.125,
        "n_instances": 3,
        "n_equivalent": equivalent,
        "failure": None,
        "per_instance": [
            {"seed": seed, "equivalent": i < equivalent}
            for i, seed in enumerate(dev.PUBLIC_SEEDS)
        ],
    }


def test_cpu_decoder_public_evaluator_accepts_pinned_equivalent_panel(monkeypatch):
    monkeypatch.setattr(
        dev.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=dev.DEFAULT_IMAGE_ID, stderr=""),
    )
    monkeypatch.setattr(
        dev,
        "_run_bounded_container",
        lambda *args, **kwargs: dev._RESULT_PREFIX + json.dumps(_record()),
    )
    result = dev.evaluate_candidate(b"def build_executor(*a): return lambda x: x\n")
    assert result["status"] == "GRADED"
    assert result["metric"] == 1.125
    assert result["seeds"] == [0, 7, 15]
    assert result["equivalent_instances"] == 3
    assert result["pilot_only"] is True


def test_cpu_decoder_public_evaluator_rejects_non_equivalent_instance(monkeypatch):
    monkeypatch.setattr(
        dev.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=dev.DEFAULT_IMAGE_ID, stderr=""),
    )
    monkeypatch.setattr(
        dev,
        "_run_bounded_container",
        lambda *args, **kwargs: dev._RESULT_PREFIX + json.dumps(_record(equivalent=2)),
    )
    with pytest.raises(dev.PublicCPUDecoderError, match="not equivalent"):
        dev.evaluate_candidate(b"candidate")


def test_cpu_decoder_aggregate_pins_identity_and_dev_seeds():
    payload = AREPublicCPUDecoderEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256="0" * 64, dev_image_id=dev.DEFAULT_IMAGE_ID,
        dev_cell_count=len(dev.PUBLIC_SEEDS),
    )
    value = json.loads(payload)
    assert value["public_seeds"] == [0, 7, 15]
    assert value["official_public_seed_count"] == 16
    assert value["pilot_only"] is True
    with pytest.raises(ValueError, match="three public seeds"):
        AREPublicCPUDecoderEvaluatorRunner.aggregate_case_input(
            tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
            contract_sha256="0" * 64, dev_image_id=dev.DEFAULT_IMAGE_ID,
            dev_cell_count=16,
        )


def test_cpu_decoder_validation_uses_same_speedup_metric_on_distinct_split():
    payload = AREPublicCPUDecoderEvaluatorRunner.aggregate_case_input(
        tier="validation", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256="1" * 64, dev_image_id=dev.DEFAULT_IMAGE_ID,
        dev_cell_count=len(dev.PUBLIC_SEEDS),
    )
    value = json.loads(payload)
    assert value["metric_id"] == "cpu_decoder_geomean_speedup"
    assert value["metric_field"] == "geometric_mean_speedup"
    assert "same metric and unit as DEV, distinct split" in value["measurement"]
    assert value["repeatability_scope"].startswith("one official submission")


def test_cpu_decoder_rejects_unpinned_public_task_root(tmp_path: Path):
    with pytest.raises(dev.PublicCPUDecoderError, match="missing or unsafe"):
        dev.verify_public_inputs(tmp_path / TASK_ID)
