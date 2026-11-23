from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import bananloop.v2_optimization_runtime as runtime_module


class _Graph:
    def __init__(self, iteration: int, spec):
        self.spec = spec
        self.nodes = [
            {"node_id": "candidate", "payload": {
                "candidate_iteration": iteration,
                "candidate_snapshot_id": "c" * 40,
                "parent_node_id": "task-root",
                "candidate_rationale": "bounded improvement",
                "candidate_limitations": ["needs validation"],
            }},
            {"node_id": "task-root", "payload": {
                "hypothesis_id": "hypothesis-1", "hypothesis": "Improve the ranker",
            }},
        ]
        self.writes = []
        self.conn = SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchall=lambda: [
            (json.dumps({"tier": "dev", "receipt_digest": "d" * 64}),),
        ]))

    def campaign_spec(self):
        return self.spec

    def scheduling_snapshot(self):
        return {"nodes": self.nodes}

    def command_response(self, _command_id):
        return None

    def graph_revision(self):
        return 7

    def mutate(self, request):
        self.writes.append(request)


class _Spec:
    max_candidate_iterations: int | None = None
    evaluation = SimpleNamespace(approval_digest="a" * 64)


@pytest.mark.parametrize(
    ("limit", "iteration", "expected_status", "expected_iteration"),
    [
        (None, 1, "SCHEDULED", 2),
        (None, 2, "SCHEDULED", 3),
        (1, 1, None, None),
        (2, 1, "SCHEDULED", 2),
        (2, 2, None, None),
    ],
)
def test_followup_scheduler_obeys_candidate_iteration_limit(
    monkeypatch, limit, iteration, expected_status, expected_iteration,
):
    class Spec:
        max_candidate_iterations = limit
        evaluation = _Spec.evaluation

    graph = _Graph(iteration, Spec())
    runtime = object.__new__(runtime_module.V2OptimizationRuntime)
    runtime.graph = graph
    runtime.campaign_id = "campaign-1"
    runtime.queue = SimpleNamespace(path=":memory:")
    runtime.trusted_evaluator = SimpleNamespace(
        public_dev_metric_summary=lambda _receipt: {"score": 0.75},
    )

    class Ledger:
        def __init__(self, _path):
            pass

        def snapshot(self, _campaign_id):
            return {
                resource: {"cap": 100, "spent": 0, "reserved": 0, "unknown": 0}
                for resource in ("agent_calls", "evaluator_calls", "experiments")
            }

        def close(self):
            pass

    class ProtocolAuthority:
        def __init__(self, _conn):
            pass

        def approved_protocol(self, _digest):
            protocol = SimpleNamespace(protocol=SimpleNamespace(
                repeatability_runs=2,
            ))
            return protocol, None

    monkeypatch.setattr(runtime_module, "OptimizationCampaignSpecV2", Spec)
    monkeypatch.setattr(runtime_module, "BudgetLedger", Ledger)
    monkeypatch.setattr(runtime_module, "EvaluatorProtocolAuthority", ProtocolAuthority)
    import bananloop.trusted_evaluator as trusted_evaluator_module
    monkeypatch.setattr(
        trusted_evaluator_module, "EvaluatorRunReceiptV1",
        SimpleNamespace(model_validate=lambda value: value),
    )

    result = runtime._schedule_followup_candidate(
        candidate_node_id="candidate",
        receipt=SimpleNamespace(receipt_digest="e" * 64),
        promotion={"snapshot_id": "f" * 40},
    )

    if expected_status is None:
        assert result is None
        assert graph.writes == []
    else:
        assert result["status"] == expected_status
        assert result["candidate_iteration"] == expected_iteration
        assert result["node_id"] == f"optimization-task-hypothesis-1-iteration-{expected_iteration}"
        assert len(graph.writes) == 1
        changes = graph.writes[0].changes
        assert changes[0].payload["candidate_iteration"] == expected_iteration
        assert changes[0].payload["source_snapshot_id"] == "f" * 40
        assert changes[0].payload["evidence_refs"] == ["evaluator://receipt/" + "d" * 64]
        summary = json.loads(changes[0].payload["dev_summary"])
        assert summary["only_public_dev_evidence"] is True
