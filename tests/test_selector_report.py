from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.selector_ab_v2.report import (
    SelectorReportError,
    build_report,
    validate_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "benchmarks" / "selector_ab_v2"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frozen(tmp_path: Path):
    original = json.loads((PREREG / "manifest.json").read_text(encoding="utf-8-sig"))
    portfolio = json.loads((PREREG / "frozen_portfolio.json").read_text(encoding="utf-8-sig"))
    project = tmp_path / "project"
    manifest = json.loads(json.dumps(original))
    # Synthetic reporter fixtures retain stable example labels independent of live v2 labels.
    manifest["replicate_design"]["paired_seeds"] = [53, 101, 211, 307, 401]
    manifest["replicate_design"]["arm_order_by_replicate"] = {
        "53": ["llm", "deterministic"],
        "101": ["deterministic", "llm"],
        "211": ["deterministic", "llm"],
        "307": ["llm", "deterministic"],
        "401": ["deterministic", "llm"],
    }
    manifest["status"] = "prepared_not_authorized_frozen_portfolio_not_historical"
    manifest["resource_authorization"].update({
        "status": "NOT_AUTHORIZED_NOT_RUN",
        "max_provider_cost_usd": None,
        "max_total_tokens": None,
        "max_wall_clock_hours": None,
    })
    for relative in manifest["task"]["input_sha256"]:
        source = ROOT / manifest["task"]["path"] / relative
        target = project / manifest["task"]["path"] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        manifest["task"]["input_sha256"][relative] = _sha(data)
    for relative in manifest["selector_implementation_sha256"]:
        source = ROOT / relative
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        manifest["selector_implementation_sha256"][relative] = _sha(data)
    portfolio["source_task_sha256"] = manifest["task"]["input_sha256"]["TASK.md"]
    portfolio_path = tmp_path / "frozen_portfolio.json"
    portfolio_bytes = (json.dumps(portfolio, indent=2) + "\n").encode()
    portfolio_path.write_bytes(portfolio_bytes)
    manifest["portfolio"]["sha256"] = _sha(portfolio_bytes)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return validate_inputs(manifest_path, portfolio_path, project), manifest_path, portfolio_path, project


def _row(frozen, seed: int, arm: str, **overrides):
    manifest = frozen["manifest"]
    row = {
        "experiment_id": manifest["experiment_id"],
        "seed": seed,
        "arm": arm,
        "execution_ordinal": frozen["manifest"]["replicate_design"]["arm_order_by_replicate"][str(seed)].index(arm) + 1,
        "manifest_sha256": frozen["manifest_sha256"],
        "portfolio_sha256": frozen["portfolio_sha256"],
        "task_input_sha256": manifest["task"]["input_sha256"],
        "selector_implementation_sha256": manifest["selector_implementation_sha256"],
        "status": "completed",
        "dev_reward": 0.75 if arm == "llm" else 0.5,
        "selected_candidate_id": frozen["candidate_ids"][0],
        "cost_usd": None,
        "tokens": None,
        "duration_seconds": 12.5,
        "failure_reason": None,
    }
    row.update(overrides)
    return row


def _authorize_test_fixture(manifest_path, portfolio_path, project):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    auth = manifest["resource_authorization"]
    auth.update({
        "status": "AUTHORIZED",
        "authorization_reference": "synthetic-unit-test-only",
        "max_provider_cost_usd": 5.0,
        "max_total_tokens": 10000,
        "max_wall_clock_hours": 5.0,
    })
    manifest["status"] = "authorized_frozen_portfolio_not_historical"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return validate_inputs(manifest_path, portfolio_path, project)


def test_empty_ledger_reports_all_missing_unknown_costs_and_no_winner(tmp_path):
    frozen, *_ = _frozen(tmp_path)
    report = build_report(frozen, [])
    assert report["status"] == "no_trials"
    assert report["expected_trial_slots"] == 10
    assert report["missing_trial_slots"] == 10
    assert report["completed_pairs"] == 0
    assert report["known_provider_cost_usd"] is None
    assert report["unknown_provider_cost_trials"] == 0
    assert report["unknown_provider_cost_slots"] == 10
    assert report["resource_cap_status"] == "not_authorized"
    assert report["winner_claim"] is None
    assert report["resource_authorization"]["status"] == "NOT_AUTHORIZED_NOT_RUN"


def test_completed_pair_deltas_and_known_unknown_resource_metrics(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    frozen = _authorize_test_fixture(manifest_path, portfolio_path, project)
    rows = [
        _row(frozen, 53, "llm", execution_ordinal=1, dev_reward=0.875, cost_usd=0.04, tokens=512),
        _row(frozen, 53, "deterministic", execution_ordinal=2, cost_usd=0.0, tokens=0),
        _row(frozen, 101, "llm", execution_ordinal=2, status="failed", dev_reward=None, selected_candidate_id=None,
             failure_reason="provider timeout", cost_usd=None, tokens=None, duration_seconds=None),
    ]
    report = build_report(frozen, rows)
    assert report["status"] == "incomplete"
    assert report["present_trial_rows"] == 3
    assert report["missing_trial_slots"] == 7
    assert report["failed_trials"] == 1
    assert report["completed_pairs"] == 1
    assert report["pairs"][0]["llm_advantage_on_dev"] == pytest.approx(0.375)
    assert report["descriptive_mean_llm_advantage_on_dev"] == pytest.approx(0.375)
    assert report["known_provider_cost_usd"] == pytest.approx(0.04)
    assert report["unknown_provider_cost_trials"] == 1
    assert report["known_tokens"] == 512
    assert report["unknown_token_trials"] == 1
    assert report["known_duration_seconds"] == pytest.approx(25.0)
    assert report["unknown_duration_trials"] == 1
    assert report["resource_cap_status"] == "unknown_measurements"
    assert report["winner_claim"] is None


def test_duplicate_slots_and_bad_trial_hashes_are_rejected(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    frozen = _authorize_test_fixture(manifest_path, portfolio_path, project)
    valid = _row(frozen, 53, "llm")
    with pytest.raises(SelectorReportError, match="duplicate"):
        build_report(frozen, [valid, valid])
    bad = _row(frozen, 53, "llm", portfolio_sha256="0" * 64)
    with pytest.raises(SelectorReportError, match="portfolio hash"):
        build_report(frozen, [bad])


def test_trials_cannot_be_reported_without_authorization_and_caps_are_enforced(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    with pytest.raises(SelectorReportError, match="forbidden while resource authorization"):
        build_report(frozen, [_row(frozen, 53, "llm")])
    frozen = _authorize_test_fixture(manifest_path, portfolio_path, project)
    with pytest.raises(SelectorReportError, match="provider cost exceeds authorized cap"):
        build_report(frozen, [_row(frozen, 53, "llm", cost_usd=6.0)])


def test_authorized_status_must_agree_with_resource_authorization(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    frozen = _authorize_test_fixture(manifest_path, portfolio_path, project)
    assert frozen["manifest"]["status"] == "authorized_frozen_portfolio_not_historical"
    assert frozen["manifest"]["resource_authorization"]["status"] == "AUTHORIZED"
    contradictory = json.loads(manifest_path.read_text(encoding="utf-8"))
    contradictory["status"] = "prepared_not_authorized_frozen_portfolio_not_historical"
    manifest_path.write_text(json.dumps(contradictory, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SelectorReportError, match="contradicts resource authorization"):
        validate_inputs(manifest_path, portfolio_path, project)


def test_codex_subscription_quota_can_be_used_as_provider_enforced_token_cap(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    _authorize_test_fixture(manifest_path, portfolio_path, project)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    auth = manifest["resource_authorization"]
    auth["max_total_tokens"] = None
    auth["token_cap_basis"] = "provider_enforced_codex_subscription_quota"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    frozen = validate_inputs(manifest_path, portfolio_path, project)
    assert frozen["manifest"]["resource_authorization"]["max_total_tokens"] is None
    assert frozen["manifest"]["resource_authorization"]["token_cap_basis"] == "provider_enforced_codex_subscription_quota"

    auth["token_cap_basis"] = "unverified"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SelectorReportError, match="null max_total_tokens requires"):
        validate_inputs(manifest_path, portfolio_path, project)


def test_frozen_arm_order_is_required_and_enforced_per_trial(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    frozen = _authorize_test_fixture(manifest_path, portfolio_path, project)
    expected = frozen["manifest"]["replicate_design"]["arm_order_by_replicate"]["53"]
    first_arm = expected[0]
    wrong_ordinal = 2
    with pytest.raises(SelectorReportError, match="contradicts frozen arm order"):
        build_report(frozen, [_row(frozen, 53, first_arm, execution_ordinal=wrong_ordinal)])
    missing = _row(frozen, 53, first_arm)
    del missing["execution_ordinal"]
    with pytest.raises(SelectorReportError, match="execution_ordinal"):
        build_report(frozen, [missing])


def test_input_or_portfolio_tampering_fails_closed(tmp_path):
    frozen, manifest_path, portfolio_path, project = _frozen(tmp_path)
    fixture_file = project / frozen["manifest"]["task"]["path"] / "TASK.md"
    fixture_file.write_text("changed", encoding="utf-8")
    with pytest.raises(SelectorReportError, match="task input hash mismatch"):
        validate_inputs(manifest_path, portfolio_path, project)
