from __future__ import annotations

import json
from pathlib import Path

from bananloop.replay import _compare_results, replay_dev_action
from bananloop.contracts import EvaluationResult
from test_engine import make_engine, make_staged_engine, git


def test_dev_replay_isolated_and_does_not_change_campaign_or_source(tmp_path):
    store, engine, repo, base = make_engine(tmp_path)
    try:
        engine.workspace.ensure(base)
        engine._baseline()
        before = store.campaign()
        before_experiments = store.experiments()
        before_evaluations = store.evaluations()
        before_receipts = store.evaluation_receipts()

        receipt = replay_dev_action(store, engine.workspace, "baseline:evaluate")

        assert receipt["status"] == "REPRODUCIBLE"
        assert receipt["candidate_snapshot"] == base
        assert receipt["replay_result_sha256"]
        assert receipt["receipt_path"]
        assert json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))["receipt_sha256"] == receipt["receipt_sha256"]
        after = store.campaign()
        for key in ("experiment_count", "attempt_count", "agent_calls", "evaluator_calls", "champion_snapshot"):
            assert after[key] == before[key]
        assert store.experiments() == before_experiments
        assert store.evaluations() == before_evaluations
        assert store.evaluation_receipts() == before_receipts
        assert git(["rev-parse", "HEAD"], repo) == base
        assert git(["status", "--porcelain"], repo) == ""
    finally:
        store.close()


def test_replay_rejects_non_dev_tiers_without_exposing_results(tmp_path):
    store, engine, _, base = make_staged_engine(tmp_path)
    try:
        engine.workspace.ensure(base)
        engine._baseline()
        before_calls = store.campaign()["evaluator_calls"]
        before_rows = store.evaluations()

        receipt = replay_dev_action(store, engine.workspace, "baseline:validation")

        assert receipt["status"] == "NOT_REPRODUCIBLE_INFRA"
        assert "only DEV" in receipt["reason"]
        assert store.campaign()["evaluator_calls"] == before_calls
        assert store.evaluations() == before_rows
    finally:
        store.close()


def test_runtime_mismatch_is_reported_without_running_evaluator(tmp_path, monkeypatch):
    store, engine, _, base = make_engine(tmp_path)
    try:
        engine.workspace.ensure(base)
        engine._baseline()
        before_calls = store.campaign()["evaluator_calls"]
        from bananloop import replay

        original_runtime_descriptor = replay.runtime_descriptor
        monkeypatch.setattr(
            replay,
            "runtime_descriptor",
            lambda commands: {**original_runtime_descriptor(commands), "python_version": "0.0"},
        )
        receipt = replay_dev_action(store, engine.workspace, "baseline:evaluate")
        assert receipt["status"] == "NOT_REPRODUCIBLE_INFRA"
        assert "runtime differs" in receipt["reason"]
        assert store.campaign()["evaluator_calls"] == before_calls
    finally:
        store.close()


def test_pythonpath_drift_is_reported_as_infrastructure(tmp_path, monkeypatch):
    store, engine, _, base = make_engine(tmp_path)
    try:
        engine.workspace.ensure(base)
        engine._baseline()
        before_calls = store.campaign()["evaluator_calls"]
        monkeypatch.setenv("PYTHONPATH", str(tmp_path / "different-runtime"))
        receipt = replay_dev_action(store, engine.workspace, "baseline:evaluate")
        assert receipt["status"] == "NOT_REPRODUCIBLE_INFRA"
        assert "runtime differs" in receipt["reason"]
        assert store.campaign()["evaluator_calls"] == before_calls
    finally:
        store.close()


def test_replay_comparison_uses_input_identity_and_explicit_metric_tolerance():
    original = EvaluationResult.model_validate({
        "status": "ok",
        "metrics": {"quality": {"value": 1.0, "unit": "score", "n": 2}},
        "checks": {"tests": {"passed": True, "executed": 4, "skipped": 0}},
        "protocol_sha256": "a" * 64,
        "cases": [{"case_id": "case-1", "repetition": 0, "input_sha256": "b" * 64, "metrics": {"quality": 1.0}}],
    })
    within_data = original.model_dump(mode="json")
    within_data["metrics"]["quality"]["value"] = 1.04
    within_data["cases"][0]["metrics"]["quality"] = 1.04
    within = EvaluationResult.model_validate(within_data)
    assert not _compare_results(original, within, metric_tolerances={"quality": 0.05})["mismatches"]

    relative_data = original.model_dump(mode="json")
    relative_data["metrics"]["quality"]["value"] = 1.1
    relative_data["cases"][0]["metrics"]["quality"] = 1.1
    relative = EvaluationResult.model_validate(relative_data)
    assert not _compare_results(
        original,
        relative,
        metric_tolerances={"quality": 0.0},
        relative_metric_tolerances={"quality": 0.1},
    )["mismatches"]

    huge_drift_data = original.model_dump(mode="json")
    huge_drift_data["metrics"]["quality"]["value"] = 100.0
    huge_drift_data["cases"][0]["metrics"]["quality"] = 100.0
    huge_drift = EvaluationResult.model_validate(huge_drift_data)
    assert _compare_results(
        original,
        huge_drift,
        metric_tolerances={"quality": 0.0},
        relative_metric_tolerances={"quality": 2.0},
    )["mismatches"]

    changed_input_data = original.model_dump(mode="json")
    changed_input_data["cases"][0]["input_sha256"] = "c" * 64
    changed_input = EvaluationResult.model_validate(changed_input_data)
    comparison = _compare_results(original, changed_input, metric_tolerances={"quality": 0.0})
    assert "case/input identities changed" in comparison["mismatches"]
