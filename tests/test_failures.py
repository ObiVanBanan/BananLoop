from __future__ import annotations

import json
from pathlib import Path

from bananloop.failures import FailureKind, classify_failure, sanitized_candidate_traceback
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


def test_sanitized_traceback_keeps_only_candidate_frames_and_exception_type(tmp_path: Path):
    workspace = tmp_path / "candidate"
    (workspace / "src").mkdir(parents=True)
    candidate_file = workspace / "src" / "model.py"
    candidate_file.write_text("raise RuntimeError('private canary')\n", encoding="utf-8")
    stderr = (
        "Traceback (most recent call last):\n"
        f'  File "{candidate_file}", line 1, in predict\n'
        "    raise RuntimeError('private canary')\n"
        '  File "/usr/lib/python3.12/runpy.py", line 1, in <module>\n'
        "RuntimeError: private canary\n"
    )

    result = sanitized_candidate_traceback(stderr, workspace)

    assert result == (
        "Sanitized Python traceback (candidate frames only):\n"
        '  File "src/model.py", line 1, in predict\nRuntimeError'
    )
    assert "private canary" not in result
    assert "/usr/lib" not in result


def test_sanitized_traceback_requires_candidate_frame_and_known_exception(tmp_path: Path):
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    assert sanitized_candidate_traceback(
        'Traceback (most recent call last):\n  File "/usr/lib/python.py", line 1, in x\nRuntimeError: canary',
        workspace,
    ) is None
    assert sanitized_candidate_traceback(
        'Traceback (most recent call last):\n  File "model.py", line 1, in x\ncanary',
        workspace,
    ) is None
