"""Evidence rules for the offline paired benchmark report."""

import json
from pathlib import Path

import pytest

from benchmarks.operator_roadmap_v1.report import ReportError, build_report


SUITE = Path(__file__).resolve().parents[1] / "benchmarks/operator_roadmap_v1/suite_manifest.json"


def _manifest():
    source = json.loads(SUITE.read_text(encoding="utf-8"))
    source["tasks"] = source["tasks"][:1]
    source["repeats"] = 2
    return source


def _row(manifest, arm, repeat, **changes):
    result = {
        "task_id": manifest["tasks"][0]["id"],
        "arm": arm,
        "repeat": repeat,
        "dataset_revision": manifest["dataset_revision"],
        "budget": manifest["common_budget"],
        "config_sha256": manifest["arm_configs"][arm]["config_sha256"],
        "status": "completed",
        "reward": 0.4 if arm == manifest["arms"][0] else 0.6,
        "evidence_path": f"example/{arm}/{repeat}/reward.json",
    }
    result.update(changes)
    return result


def test_missing_and_failed_trials_are_not_imputed():
    manifest = _manifest()
    first, second = manifest["arms"]
    rows = [_row(manifest, first, 1, cost_usd=0.5),
            _row(manifest, second, 1, cost_usd=0.7),
            _row(manifest, first, 2, status="failed", reward=None,
                 evidence_path=None, failure_reason="timeout", cost_usd=None)]
    result = build_report(manifest, rows)
    assert result["expected_pairs"] == 2
    assert result["completed_pairs"] == 1
    assert result["missing_trial_slots"] == 1
    assert result["unknown_cost_trials"] == 1
    assert result["known_cost_usd"] == pytest.approx(1.2)
    assert result["mean_reward_delta"] == pytest.approx(0.2)
    assert result["pairs"][1]["reward_delta"] is None
    assert result["pairs"][1]["arms"][second]["status"] == "missing"
    assert result["pairs"][1]["arms"][first]["failure_reason"] == "timeout"


def test_mismatched_budget_revision_and_duplicate_trial_are_rejected():
    manifest = _manifest()
    first = manifest["arms"][0]
    valid = _row(manifest, first, 1)
    with pytest.raises(ReportError, match="duplicate"):
        build_report(manifest, [valid, valid])
    with pytest.raises(ReportError, match="budget"):
        build_report(manifest, [_row(manifest, first, 1, budget={**manifest["common_budget"], "max_experiments": 7})])
    with pytest.raises(ReportError, match="revision"):
        build_report(manifest, [_row(manifest, first, 1, dataset_revision="other")])
    with pytest.raises(ReportError, match="configuration"):
        build_report(manifest, [_row(manifest, first, 1, config_sha256="a" * 64)])


def test_incomplete_trial_cannot_claim_score_or_zero_cost_by_default():
    manifest = _manifest()
    first = manifest["arms"][0]
    with pytest.raises(ReportError, match="cannot claim reward"):
        build_report(manifest, [_row(manifest, first, 1, status="incomplete")])
    result = build_report(manifest, [_row(manifest, first, 1, status="incomplete", reward=None)])
    assert result["known_cost_usd"] == 0
    assert result["unknown_cost_trials"] == 1
    assert result["mean_reward_delta"] is None
