from __future__ import annotations

from types import SimpleNamespace

import pytest

from bananloop.graph_store import TransitionNodePhase, TransitionNodeStatus
from bananloop.optimization_lifecycle import (
    OptimizationAttemptCoordinator,
    OptimizationLifecycleError,
)


class _Queue:
    def __init__(self, states):
        self.states = dict(states)
        self.calls = []

    def get_job(self, job_id):
        self.calls.append(job_id)
        return {"state": self.states[job_id]}


class _Graph:
    campaign_id = "campaign-1"

    def __init__(self):
        self.node = {
            "node_id": "candidate-1", "phase": "OPTIMIZATION", "status": "READY",
            "revision": 4, "payload": {
                "source_snapshot_id": "a" * 40,
                "candidate_snapshot_id": "b" * 40,
            },
        }
        self.mutations = []
        self._receipt = SimpleNamespace(
            receipt_digest="c" * 64, campaign_id="campaign-1",
            spec_hash="d" * 64, approval_digest="e" * 64,
            protocol_id="quality", protocol_version=1, protocol_digest="f" * 64,
            source_snapshot_id="a" * 40, candidate_snapshot_id="b" * 40,
            tier="dev",
        )
        self.evaluator_protocols = SimpleNamespace(
            require_run_receipt=self._require_run_receipt,
        )

    def _require_run_receipt(self, digest, **kwargs):
        assert digest == self._receipt.receipt_digest
        assert kwargs["candidate_snapshot_id"] == self.node["payload"]["candidate_snapshot_id"]
        return self._receipt

    def campaign_spec(self):
        return SimpleNamespace(
            approved_hash=lambda: "d" * 64,
            evaluation=SimpleNamespace(
                approval_digest="e" * 64, content_sha256="f" * 64,
            ),
        )

    def scheduling_snapshot(self):
        return {"nodes": [self.node]}

    def graph_revision(self):
        return len(self.mutations)

    def mutate(self, request):
        self.mutations.append(request)
        status_change, phase_change = request.changes
        assert isinstance(status_change, TransitionNodeStatus)
        assert isinstance(phase_change, TransitionNodePhase)
        assert status_change.expected_node_revision == self.node["revision"]
        self.node["status"] = "COMPLETED"
        self.node["revision"] += 1
        assert phase_change.expected_node_revision == self.node["revision"]
        self.node["phase"] = "VERIFICATION"
        self.node["status"] = "PENDING"
        self.node["revision"] += 1
        self.node["payload"]["last_phase_transition"] = {
            "from_phase": "OPTIMIZATION", "to_phase": "VERIFICATION",
            "guard": "CANDIDATE_READY", "actor": "CORE",
            "evidence_refs": list(phase_change.evidence_refs),
        }
        return {"status": "COMMITTED"}


class _TrustedEvaluator:
    def __init__(self, queue, receipt):
        self.queue = queue
        self.receipt = receipt
        self.enqueued = []
        self.runs = []
        self.finalized = []

    def enqueue_repeatability(self, *, candidate_snapshot_id, tier):
        self.enqueued.append((candidate_snapshot_id, tier))
        return ("eval-1", "eval-2")

    def run_one(self, job_id, *, worker_id):
        self.runs.append((job_id, worker_id))
        self.queue.states[job_id] = "COMPLETED"

    def finalize_repeatability(self, job_ids):
        self.finalized.append(job_ids)
        return self.receipt


def _setup(states=("READY", "READY")):
    graph = _Graph()
    queue = _Queue(dict(zip(("eval-1", "eval-2"), states)))
    trusted = _TrustedEvaluator(queue, graph._receipt)
    return graph, queue, trusted, OptimizationAttemptCoordinator(
        graph=graph, trusted_evaluator=trusted,
    )


def test_candidate_evaluation_dispatches_then_atomically_advances_and_replays():
    graph, queue, trusted, coordinator = _setup(("READY", "COMPLETED"))

    receipt = coordinator.evaluate_candidate(node_id="candidate-1", worker_id="worker-a")

    assert receipt is graph._receipt
    assert trusted.enqueued == [("b" * 40, "dev")]
    assert trusted.runs == [("eval-1", "worker-a")]
    assert trusted.finalized == [("eval-1", "eval-2")]
    assert len(graph.mutations) == 1
    assert graph.node["phase"] == "VERIFICATION"
    assert graph.node["status"] == "PENDING"
    assert graph.mutations[0].changes[0].evidence_refs == (
        f"evaluator://receipt/{receipt.receipt_digest}",
    )

    replay = coordinator.evaluate_candidate(node_id="candidate-1")

    assert replay is receipt
    assert len(graph.mutations) == 1
    assert trusted.runs == [("eval-1", "worker-a")]
    assert queue.calls == ["eval-1", "eval-2"]


def test_candidate_evaluation_does_not_redrive_indeterminate_job():
    graph, _, trusted, coordinator = _setup(("UNKNOWN", "READY"))

    with pytest.raises(OptimizationLifecycleError, match="indeterminate"):
        coordinator.evaluate_candidate(node_id="candidate-1")

    assert trusted.runs == []
    assert trusted.finalized == []
    assert graph.mutations == []
