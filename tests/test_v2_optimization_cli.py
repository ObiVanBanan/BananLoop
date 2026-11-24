from __future__ import annotations

from bananloop.v2_optimization_cli import _initial_optimization_task_nodes
from bananloop.v2_optimization_runtime import (
    _candidate_node_id, _iteration_schedule_command_id,
)


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
