from __future__ import annotations

import json
from pathlib import Path

from bananloop.failures import FailureKind, classify_failure
from bananloop.report import build_report
from test_engine import make_engine


def test_failure_taxonomy_separates_implementation_infrastructure_and_science():
    assert classify_failure("IMPLEMENTATION_BLOCKED")["kind"] == FailureKind.IMPLEMENTATION
    assert classify_failure("INVALID_EVALUATION")["kind"] == FailureKind.INFRASTRUCTURE
    assert classify_failure("VALIDATION_REJECTED")["kind"] == FailureKind.SCIENTIFIC_OUTCOME
    assert classify_failure("PRIMARY_NOT_IMPROVED")["kind"] == FailureKind.SCIENTIFIC_OUTCOME
    assert classify_failure("GUARDRAIL_FAILED:regressions")["kind"] == FailureKind.SCIENTIFIC_OUTCOME
    assert classify_failure("SCOPE_VIOLATION")["kind"] == FailureKind.POLICY
    assert classify_failure("FUTURE_FAILURE_CATEGORY")["kind"] == FailureKind.UNKNOWN


def test_classified_failure_is_durable_and_visible_in_report(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.run()
        assert any(
            event["event_type"] == "FAILURE_CLASSIFIED"
            and json.loads(event["payload_json"]).get("kind") == "scientific_outcome"
            for event in store.events()
        )
        exp = store.experiments()[0]
        store.update_experiment(
            exp["id"], status="REJECTED", failure_category="IMPLEMENTATION_BLOCKED"
        )

        classified_events = [
            event
            for event in store.events()
            if event["event_type"] == "FAILURE_CLASSIFIED"
            and json.loads(event["payload_json"])["experiment_id"] == exp["id"]
        ]
        assert json.loads(classified_events[-1]["payload_json"])["kind"] == "implementation"
        report_item = next(
            item for item in build_report(store)["experiments"] if item["id"] == exp["id"]
        )
        assert report_item["failure_classification"]["kind"] == "implementation"
        assert report_item["failure_classification"]["retryable"] is True
        assert report_item["failure_classification"]["suggested_remediation"]
    finally:
        store.close()
