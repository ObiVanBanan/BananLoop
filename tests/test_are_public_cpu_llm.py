from __future__ import annotations

import subprocess

import pytest

from benchmarks.autoresearchexam import public_cpu_llm_dev as dev
from benchmarks.autoresearchexam.cpu_llm_runner import (
    AREPublicCPULLMEvaluatorRunner,
    TASK_ID,
    TASK_VERSION,
)


def _bench_stdout(agreement: str = "1.0000") -> str:
    return (
        "round 0 (dev_workload_0): reference 20.000 tok/s, candidate 21.000 tok/s, "
        "speedup 1.0500x, agreement 100/100\n"
        "round 1 (dev_workload_2): reference 19.000 tok/s, candidate 20.000 tok/s, "
        "speedup 1.0526x, agreement 101/101\n"
        f"token agreement : {agreement}\n"
        "speedup S       : 1.0513\n"
    )


def _pin_image(monkeypatch):
    monkeypatch.setattr(
        dev.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=dev.DEFAULT_IMAGE_ID, stderr=""),
    )


def test_cpu_llm_public_evaluator_accepts_agreeing_pilot(monkeypatch):
    _pin_image(monkeypatch)
    monkeypatch.setattr(dev, "_run_bounded_container", lambda *args, **kwargs: _bench_stdout())
    result = dev.evaluate_candidate(b"class Engine: pass\n")
    assert result["status"] == "GRADED"
    assert result["metric"] == pytest.approx(1.0513)
    assert result["token_agreement"] == 1.0
    assert result["draws"] == [0, 2]
    assert result["request_limit"] == 16
    assert result["pilot_only"] is True


def test_cpu_llm_public_evaluator_rejects_agreement_below_threshold(monkeypatch):
    _pin_image(monkeypatch)
    monkeypatch.setattr(dev, "_run_bounded_container", lambda *args, **kwargs: _bench_stdout("0.9800"))
    with pytest.raises(dev.PublicCPULLMError, match="below token-agreement"):
        dev.evaluate_candidate(b"class Engine: pass\n")


def test_cpu_llm_aggregate_pins_draws_and_request_limit():
    payload = AREPublicCPULLMEvaluatorRunner.aggregate_case_input(
        tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256="0" * 64, dev_image_id=dev.DEFAULT_IMAGE_ID,
        dev_cell_count=2,
    )
    assert b'"public_draws":[0,2]' in payload
    assert b'"requests_per_draw":16' in payload
    with pytest.raises(ValueError, match="two public draws"):
        AREPublicCPULLMEvaluatorRunner.aggregate_case_input(
            tier="dev", task_id=TASK_ID, task_version=TASK_VERSION,
            contract_sha256="0" * 64, dev_image_id=dev.DEFAULT_IMAGE_ID,
            dev_cell_count=4,
        )
