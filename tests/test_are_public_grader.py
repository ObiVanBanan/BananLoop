from __future__ import annotations

import json

import pytest

from benchmarks.autoresearchexam.public_grader import PublicAREGrader, PublicGraderError


def _grader(tmp_path, call_tool):
    return PublicAREGrader(
        tmp_path / "are.sqlite3", task_id="budgeted-imputation-mcar50",
        contract_sha256="a" * 64, call_tool=call_tool, clock=lambda: 1234.5,
    )


def test_baseline_and_candidate_results_are_sanitized_and_pinned(tmp_path):
    calls = []
    next_id = 0

    def call_tool(name, arguments):
        nonlocal next_id
        calls.append((name, arguments))
        if name == "submit":
            next_id += 1
            return {"id": next_id, "status": "queued", "hint": "server note"}
        return {
            "id": arguments["id"], "status": "graded", "validation_score": 0.125,
            "grading_seconds": 6.4, "error": None,
            "grader_output_tail": "DO NOT STORE OR FORWARD this grader tail",
        }

    grader = _grader(tmp_path, call_tool)
    candidate = "def impute(): pass\n"
    submitted = grader.submit_once(candidate_iteration=0, repetition=1,
                                   candidate=candidate, note="baseline")
    assert submitted["state"] == "QUEUED"
    assert submitted["submission_id"] == 1
    assert grader.poll(0, 1)["state"] == "GRADED"
    result = grader.get(0, 1)
    assert result["phase"] == "baseline"
    assert result["public_result"] == {
        "submission_id": 1, "status": "GRADED", "validation_score": 0.125,
        "grading_seconds": 6.4, "grader_error": False,
    }
    with pytest.raises(PublicGraderError, match="refusing resubmit"):
        grader.submit_once(candidate_iteration=0, repetition=1, candidate=candidate)
    assert all("grader_output_tail" not in json.dumps(call) for call in calls)


def test_ambiguous_submit_is_durably_not_retried(tmp_path):
    attempts = 0

    def uncertain(name, _arguments):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("response lost after request may have been processed")

    grader = _grader(tmp_path, uncertain)
    with pytest.raises(PublicGraderError, match="will not be retried"):
        grader.submit_once(candidate_iteration=0, repetition=1, candidate=b"baseline")
    assert grader.get(0, 1)["state"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(PublicGraderError, match="SUBMISSION_UNKNOWN"):
        grader.submit_once(candidate_iteration=0, repetition=1, candidate=b"baseline")
    assert attempts == 1


def test_different_task_or_contract_cannot_reuse_ledger(tmp_path):
    _grader(tmp_path, lambda *_: {"id": 1, "status": "queued"})
    with pytest.raises(PublicGraderError, match="another task, contract"):
        PublicAREGrader(
            tmp_path / "are.sqlite3", task_id="other-task", contract_sha256="a" * 64,
            call_tool=lambda *_: {},
        )


def test_task_deadline_is_persisted_and_cannot_be_reset_on_resume(tmp_path):
    path = tmp_path / "are.sqlite3"
    first = PublicAREGrader(
        path, task_id="budgeted-imputation-mcar50", contract_sha256="a" * 64,
        call_tool=lambda *_: {}, clock=lambda: 1000.0,
    )
    assert first.deadline_epoch == 4600.0
    resumed = PublicAREGrader(
        path, task_id="budgeted-imputation-mcar50", contract_sha256="a" * 64,
        call_tool=lambda *_: {}, clock=lambda: 3000.0,
    )
    assert resumed.deadline_epoch == 4600.0
    with pytest.raises(PublicGraderError, match="another task, contract"):
        PublicAREGrader(
            path, task_id="budgeted-imputation-mcar50", contract_sha256="a" * 64,
            call_tool=lambda *_: {}, time_limit_seconds=1800, clock=lambda: 3000.0,
        )
    with pytest.raises(PublicGraderError, match="another task, contract"):
        PublicAREGrader(
            tmp_path / "are.sqlite3", task_id="budgeted-imputation-mcar50",
            contract_sha256="b" * 64, call_tool=lambda *_: {},
        )


def test_baseline_is_separate_from_five_candidate_iterations(tmp_path):
    next_id = 0

    def call_tool(name, arguments):
        nonlocal next_id
        if name == "submit":
            next_id += 1
            return {"id": next_id, "status": "queued"}
        return {"id": arguments["id"], "status": "graded", "validation_score": 0.0,
                "grading_seconds": 1.0}

    grader = _grader(tmp_path, call_tool)

    def submit_and_grade(candidate_iteration, repetition, candidate):
        grader.submit_once(candidate_iteration=candidate_iteration,
                           repetition=repetition, candidate=candidate)
        grader.poll(candidate_iteration, repetition)

    submit_and_grade(0, 1, b"baseline")
    submit_and_grade(0, 2, b"baseline")
    for candidate_iteration in range(1, 6):
        candidate = f"candidate-{candidate_iteration}".encode()
        submit_and_grade(candidate_iteration, 1, candidate)
        if candidate_iteration == 1:
            with pytest.raises(PublicGraderError, match="strictly sequential"):
                grader.submit_once(candidate_iteration=2, repetition=1, candidate=b"candidate-2")
        submit_and_grade(candidate_iteration, 2, candidate)
    with pytest.raises(ValueError, match="candidate_iteration must be an integer from 0 to 5"):
        grader.submit_once(candidate_iteration=6, repetition=1, candidate=b"candidate-6")


def test_git_snapshots_are_durably_mapped_to_baseline_and_candidate_slots(tmp_path):
    next_id = 0

    def call_tool(name, arguments):
        nonlocal next_id
        if name == "submit":
            next_id += 1
            return {"id": next_id, "status": "queued"}
        return {"id": arguments["id"], "status": "graded", "validation_score": 0.1,
                "grading_seconds": 1.0}

    grader = _grader(tmp_path, call_tool)
    source = "1" * 40
    first_candidate = "2" * 40
    baseline = b"baseline bytes"
    candidate = b"candidate bytes"
    baseline_result = grader.submit_snapshot_once(
        candidate_snapshot_id=source, source_snapshot_id=source, candidate=baseline,
    )
    assert baseline_result["candidate_iteration"] == 0
    assert baseline_result["candidate_snapshot_id"] == source
    grader.poll(0, 1)
    grader.submit_snapshot_once(
        candidate_snapshot_id=source, source_snapshot_id=source, candidate=baseline,
    )
    grader.poll(0, 2)
    candidate_result = grader.submit_snapshot_once(
        candidate_snapshot_id=first_candidate, source_snapshot_id=source, candidate=candidate,
    )
    assert candidate_result["candidate_iteration"] == 1
    assert candidate_result["candidate_snapshot_id"] == first_candidate
    with pytest.raises(PublicGraderError, match="previous submission is not graded"):
        grader.submit_snapshot_once(
            candidate_snapshot_id="3" * 40, source_snapshot_id=source,
            candidate=b"premature candidate",
        )


def test_cached_reuse_requires_equal_repeatability_scores(tmp_path):
    next_id = 0

    def call_tool(name, arguments):
        nonlocal next_id
        if name == "submit":
            next_id += 1
            return {"id": next_id, "status": "queued"}
        return {"id": arguments["id"], "status": "graded",
                "validation_score": 0.1 * arguments["id"], "grading_seconds": 1.0}

    grader = _grader(tmp_path, call_tool)
    source = "1" * 40
    grader.submit_snapshot_once(
        candidate_snapshot_id=source, source_snapshot_id=source, candidate=b"baseline",
    )
    grader.poll(0, 1)
    grader.submit_snapshot_once(
        candidate_snapshot_id=source, source_snapshot_id=source, candidate=b"baseline",
    )
    grader.poll(0, 2)
    with pytest.raises(PublicGraderError, match="repeatability scores differ"):
        grader.completed_snapshot_result(source, b"baseline")


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, "0.3"])
def test_invalid_public_score_fails_closed(tmp_path, score):
    def call_tool(name, arguments):
        if name == "submit":
            return {"id": 2, "status": "queued"}
        return {"id": arguments["id"], "status": "graded", "validation_score": score,
                "grading_seconds": 1.0, "grader_output_tail": "hidden"}

    grader = _grader(tmp_path, call_tool)
    grader.submit_once(candidate_iteration=0, repetition=1, candidate=b"baseline")
    with pytest.raises(PublicGraderError, match="finite validation score"):
        grader.poll(0, 1)
