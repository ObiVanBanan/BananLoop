from __future__ import annotations

from bananloop.trusted_evaluator import TrustedEvaluatorError, TrustedPublicEvaluatorFailure
from bananloop.v2_optimization_cli import (
    _aggregate_successful_request_usage, _initial_optimization_task_nodes,
    _attach_request_usage,
    _public_evaluator_failure_detail,
)
from bananloop.v2_optimization_runtime import (
    _candidate_node_id, _iteration_schedule_command_id,
)


def test_successful_request_usage_aggregates_complete_dispatched_history():
    history = {
        "optimizer": [{
            "request_id": None, "status": "COMPLETED", "dispatched": True,
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        }],
        "integration_verifier": [{
            "request_id": "resp-2", "status": "COMPLETED", "dispatched": True,
            "usage": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
        }],
    }

    assert _aggregate_successful_request_usage(history, spent=2) == {
        "input_tokens": 30, "output_tokens": 10, "total_tokens": 40,
    }


def test_successful_request_usage_fails_closed_for_incomplete_or_cached_history():
    complete = {
        "optimizer": [{
            "status": "COMPLETED", "dispatched": True,
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        }],
    }
    partial = {"optimizer": [{
        "status": "COMPLETED", "dispatched": True,
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }]}
    cached = {"optimizer": [{
        "status": "CACHED", "dispatched": True,
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }]}

    assert _aggregate_successful_request_usage(complete, spent=2) == "UNKNOWN"
    assert _aggregate_successful_request_usage(partial, spent=1) == "UNKNOWN"
    assert _aggregate_successful_request_usage(cached, spent=1) == "UNKNOWN"
    assert _aggregate_successful_request_usage({"optimizer": []}, spent=0) == "UNKNOWN"


def test_resume_task_detection_ignores_completed_candidate_nodes():
    root = {
        "node_id": "optimization-task-sdpa_attention",
        "phase": "OPTIMIZATION",
        "payload": {
            "hypothesis_id": "sdpa_attention",
            "hypothesis": "Use SDPA",
            "source_snapshot_id": "a" * 40,
        },
    }
    candidate = {
        "node_id": "optimization-task-sdpa_attention-candidate-123",
        "phase": "OPTIMIZATION",
        "payload": {
            "hypothesis_id": "sdpa_attention",
            "candidate_snapshot_id": "b" * 40,
            "parent_node_id": root["node_id"],
            "source_snapshot_id": "a" * 40,
        },
    }

    assert _initial_optimization_task_nodes([root, candidate]) == [root]



def test_failure_site_excludes_exception_message():
    from bananloop.provider_diagnostics import safe_failure_site

    namespace = {"__name__": "bananloop.synthetic"}
    exec("def fail():\n    raise AttributeError('private value')", namespace)
    try:
        namespace["fail"]()
    except AttributeError as exc:
        assert safe_failure_site(exc) == "bananloop.synthetic:fail:2"
    else:
        raise AssertionError("fixture did not raise")


def test_failure_site_reports_wrapped_project_exception():
    from bananloop.provider_diagnostics import safe_failure_site

    namespace = {"__name__": "benchmarks.synthetic"}
    exec("def fail():\n    raise ValueError('private value')", namespace)
    try:
        try:
            namespace["fail"]()
        except ValueError:
            raise RuntimeError("details withheld") from None
    except RuntimeError as exc:
        assert safe_failure_site(exc) == "benchmarks.synthetic:fail:2"


def test_public_evaluator_failure_code_survives_suppressed_wrapper_only_if_allowlisted():
    try:
        try:
            raise TrustedPublicEvaluatorFailure("PUBLIC_DEV_CANDIDATE_TIMEOUT")
        except TrustedEvaluatorError:
            raise TrustedEvaluatorError("generic failure") from None
    except TrustedEvaluatorError as raised:
        assert _public_evaluator_failure_detail(raised) == "PUBLIC_DEV_CANDIDATE_TIMEOUT"

    try:
        raise TrustedEvaluatorError("generic failure")
    except TrustedEvaluatorError as raised:
        assert _public_evaluator_failure_detail(raised) is None


def test_nested_candidate_node_ids_stay_within_graph_schema_limit():
    parent = "nested-" + "x" * 193
    first = _candidate_node_id(parent, "opt-" + "a" * 40)
    nested = _candidate_node_id(first, "opt-" + "b" * 40)

    assert len(first) <= 200
    assert len(nested) <= 200
    assert first.endswith("-candidate-" + "a" * 12)
    assert nested.endswith("-candidate-" + "b" * 12)


def test_followup_schedule_mutation_key_is_bounded_and_identity_specific():
    candidate = "candidate-" + "x" * 151
    receipt = "a" * 64
    legacy = f"schedule-optimization-iteration:{candidate}:{receipt}"
    command_id = _iteration_schedule_command_id(candidate, receipt)

    assert len(legacy) > 200
    assert len(command_id) <= 200
    assert command_id == _iteration_schedule_command_id(candidate, receipt)
    assert command_id != _iteration_schedule_command_id(candidate, "b" * 64)



def test_usage_summary_preserves_known_tokens_after_invalid_response():
    report = {"request_history": {"optimizer": [
        {"status": "COMPLETED", "dispatched": True, "request_id": "r1",
         "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
        {"status": "FAILED", "dispatched": True, "request_id": "r2",
         "usage": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23}},
        {"status": "FAILED", "dispatched": True, "request_id": None, "usage": "UNKNOWN"},
    ]}}
    _attach_request_usage(report, spent=3)
    assert report["usage"] == "UNKNOWN"
    assert report["usage_summary"]["known_usage"] == {
        "input_tokens": 30, "output_tokens": 5, "total_tokens": 35}
    assert report["usage_summary"]["known_usage_requests"] == 2
    assert report["usage_summary"]["unknown_usage_requests"] == 1
    assert report["usage_summary"]["dispatched_requests"] == 3
    report["request_history"]["optimizer"].pop()
    _attach_request_usage(report, spent=2)
    assert report["usage"]["total_tokens"] == 35
    assert report["usage_summary"]["complete"] is True
