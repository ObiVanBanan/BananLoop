from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from bananloop.budget_ledger import BudgetLedger
from bananloop.worker_queue import DurableWorkerQueue
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
            require_integration_decision=self._require_integration_decision,
        )
        self._integration_receipt = None

    def _require_run_receipt(self, digest, **kwargs):
        assert digest == self._receipt.receipt_digest
        assert kwargs["candidate_snapshot_id"] == self.node["payload"]["candidate_snapshot_id"]
        return self._receipt

    def _require_integration_decision(self, digest, *, decision, **kwargs):
        assert self._integration_receipt is not None
        assert digest == self._integration_receipt.receipt_digest
        assert decision == self._integration_receipt.decision
        assert kwargs["source_snapshot_id"] == self._integration_receipt.source_snapshot_id
        assert kwargs["candidate_snapshot_id"] == self._integration_receipt.candidate_snapshot_id
        return self._integration_receipt

    def campaign_spec(self):
        return SimpleNamespace(
            approved_hash=lambda: "d" * 64,
            evaluation=SimpleNamespace(
                approval_digest="e" * 64, content_sha256="f" * 64,
                protocol_id="quality", version=1,
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
        self.node["phase"] = phase_change.to_phase
        self.node["status"] = (
            "COMPLETED" if phase_change.to_phase == "CONSOLIDATION" else "PENDING"
        )
        self.node["revision"] += 1
        self.node["payload"]["last_phase_transition"] = {
            "from_phase": phase_change.from_phase, "to_phase": phase_change.to_phase,
            "guard": phase_change.guard, "actor": phase_change.actor,
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

@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("source_snapshot_id", "f" * 40), ("tier", "validation"),
     ("protocol_id", "other"), ("protocol_version", 2)],
)
def test_candidate_ready_replay_rejects_stale_source_or_non_dev_receipt(
    field, invalid_value,
):
    graph, _, trusted, coordinator = _setup()
    setattr(graph._receipt, field, invalid_value)
    graph.node["phase"] = "VERIFICATION"
    graph.node["status"] = "PENDING"
    graph.node["payload"]["last_phase_transition"] = {
        "from_phase": "OPTIMIZATION", "to_phase": "VERIFICATION",
        "guard": "CANDIDATE_READY", "actor": "CORE",
        "evidence_refs": [f"evaluator://receipt/{graph._receipt.receipt_digest}"],
    }

    with pytest.raises(OptimizationLifecycleError, match="durable evaluator receipt"):
        coordinator.evaluate_candidate(node_id="candidate-1")

    assert trusted.enqueued == []
    assert trusted.runs == []
    assert graph.mutations == []

class _Integration:
    def __init__(self, queue, receipt):
        self.queue = queue
        self.receipt = receipt
        self.enqueued = []
        self.runs = []
        self.recoveries = 0
        self.authority = SimpleNamespace(
            record_completed_integration_job=self._recover,
        )

    def enqueue_integration(
        self, *, baseline_run_receipt_digest, candidate_run_receipt_digest,
    ):
        self.enqueued.append((baseline_run_receipt_digest, candidate_run_receipt_digest))
        return {"job_id": "integration-1"}, SimpleNamespace()

    def run_integration_one(self, *, verifier_job_id, worker_id, executor):
        self.runs.append((verifier_job_id, worker_id, executor))
        self.queue.states[verifier_job_id] = "COMPLETED"
        return self.receipt

    def _recover(self, queue, verifier_job_id):
        self.recoveries += 1
        assert queue.get_job(verifier_job_id)["state"] == "COMPLETED"
        return self.receipt


def _verification_coordinator(*, state="READY", decision="PASS", integration_type=_Integration):
    graph = _Graph()
    graph.node.update(phase="VERIFICATION", status="READY", revision=8)
    graph.node["payload"]["last_phase_transition"] = {
        "from_phase": "OPTIMIZATION", "to_phase": "VERIFICATION",
        "guard": "CANDIDATE_READY", "actor": "CORE",
        "evidence_refs": [f"evaluator://receipt/{graph._receipt.receipt_digest}"],
    }
    queue = _Queue({"integration-1": state})
    receipt = SimpleNamespace(
        receipt_digest="9" * 64, decision=decision,
        source_snapshot_id="a" * 40, candidate_snapshot_id="b" * 40,
    )
    graph._integration_receipt = receipt
    trusted = _TrustedEvaluator(queue, graph._receipt)
    integration = integration_type(queue, receipt)
    coordinator = OptimizationAttemptCoordinator(
        graph=graph, trusted_evaluator=trusted, integration_verifier=integration,
    )
    return graph, queue, integration, coordinator


@pytest.mark.parametrize(
    ("decision", "phase", "status", "guard"),
    [
        ("PASS", "CONSOLIDATION", "COMPLETED", "VERIFIED_RESULT"),
        ("FAIL", "OPTIMIZATION", "PENDING", "REGRESSION_FAILED"),
    ],
)
def test_integration_decision_projects_guarded_phase_and_replays(
    decision, phase, status, guard,
):
    graph, queue, integration, coordinator = _verification_coordinator(decision=decision)
    executor = object()

    receipt = coordinator.verify_candidate(
        node_id="candidate-1", baseline_run_receipt_digest="1" * 64,
        executor=executor, worker_id="reviewer-worker",
    )

    assert receipt is graph._integration_receipt
    assert integration.enqueued == [("1" * 64, graph._receipt.receipt_digest)]
    assert integration.runs == [("integration-1", "reviewer-worker", executor)]
    assert graph.node["phase"] == phase
    assert graph.node["status"] == status
    assert graph.mutations[0].changes[1].guard == guard
    assert graph.mutations[0].changes[1].actor == "VERIFIER"
    assert graph.mutations[0].changes[1].evidence_refs == (
        f"integration://receipt/{receipt.receipt_digest}",
    )

    replay = coordinator.verify_candidate(
        node_id="candidate-1", baseline_run_receipt_digest="1" * 64,
        executor=executor, worker_id="reviewer-worker",
    )

    assert replay is receipt
    assert len(graph.mutations) == 1
    assert len(integration.enqueued) == 1
    assert len(integration.runs) == 1
    assert queue.calls == ["integration-1"]


def test_integration_completed_job_is_recovered_without_redrive():
    graph, _, integration, coordinator = _verification_coordinator(state="COMPLETED")

    receipt = coordinator.verify_candidate(
        node_id="candidate-1", baseline_run_receipt_digest="1" * 64,
        executor=object(), worker_id="reviewer-worker",
    )

    assert receipt is graph._integration_receipt
    assert integration.runs == []
    assert integration.recoveries == 1
    assert graph.node["phase"] == "CONSOLIDATION"


def test_integration_unknown_job_is_not_redriven():
    graph, _, integration, coordinator = _verification_coordinator(state="UNKNOWN")

    with pytest.raises(OptimizationLifecycleError, match="indeterminate"):
        coordinator.verify_candidate(
            node_id="candidate-1", baseline_run_receipt_digest="1" * 64,
            executor=object(), worker_id="reviewer-worker",
        )

    assert integration.runs == []
    assert graph.mutations == []


class _SQLiteIntegration(_Integration):
    def enqueue_integration(
        self, *, baseline_run_receipt_digest, candidate_run_receipt_digest,
    ):
        self.enqueued.append((baseline_run_receipt_digest, candidate_run_receipt_digest))
        return self.queue.enqueue(
            job_id="integration-1", enqueue_key="integration:1",
            campaign_id="campaign-1", action_id="integration-verification:1",
            resource="agent_calls", upper_bound=1,
            request={"schema": "integration-verification-v1"},
        ), SimpleNamespace()

    def run_integration_one(self, *, verifier_job_id, worker_id, executor):
        lease = self.queue.claim(verifier_job_id, worker_id, lease_seconds=30)
        self.queue.mark_dispatched(verifier_job_id, worker_id, lease["fence"])
        result = {"decision": self.receipt.decision}
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.queue.complete(
            verifier_job_id, worker_id, lease["fence"], output_digest=digest,
            actual_amount=1, receipt_digest="7" * 64, result_payload=result,
        )
        self.runs.append((verifier_job_id, worker_id, executor))
        return self.receipt

    def _recover(self, queue, verifier_job_id):
        self.recoveries += 1
        assert queue.get_job(verifier_job_id)["state"] == "COMPLETED"
        assert queue.get_result(verifier_job_id)["result"] == {"decision": self.receipt.decision}
        return self.receipt


def test_integration_leg_settles_real_sqlite_queue_and_projects_graph(tmp_path):
    path = tmp_path / "worker-queue.sqlite3"
    ledger = BudgetLedger(path)
    ledger.create_root("campaign-1", {"agent_calls": 2})
    ledger.close()
    queue = DurableWorkerQueue(path)
    graph = _Graph()
    graph.node.update(phase="VERIFICATION", status="READY", revision=8)
    graph.node["payload"]["last_phase_transition"] = {
        "from_phase": "OPTIMIZATION", "to_phase": "VERIFICATION",
        "guard": "CANDIDATE_READY", "actor": "CORE",
        "evidence_refs": [f"evaluator://receipt/{graph._receipt.receipt_digest}"],
    }
    receipt = SimpleNamespace(
        receipt_digest="9" * 64, decision="PASS",
        source_snapshot_id="a" * 40, candidate_snapshot_id="b" * 40,
    )
    graph._integration_receipt = receipt
    trusted = _TrustedEvaluator(queue, graph._receipt)
    integration = _SQLiteIntegration(queue, receipt)
    coordinator = OptimizationAttemptCoordinator(
        graph=graph, trusted_evaluator=trusted, integration_verifier=integration,
    )
    try:
        coordinator.verify_candidate(
            node_id="candidate-1", baseline_run_receipt_digest="1" * 64,
            executor=object(), worker_id="reviewer-worker",
        )
        events = [event["event_type"] for event in queue.events("integration-1")]
        assert events == ["JOB_ENQUEUED", "JOB_LEASED", "JOB_DISPATCHED", "JOB_COMPLETED"]
        assert graph.node["phase"] == "CONSOLIDATION"
        assert graph.node["status"] == "COMPLETED"
    finally:
        queue.close()
