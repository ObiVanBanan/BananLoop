from __future__ import annotations

from bananloop.v2_optimization_cli import _initial_optimization_task_nodes


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

