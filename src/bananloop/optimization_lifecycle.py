"""Core-owned Optimization candidate lifecycle orchestration."""
from __future__ import annotations

import hashlib
from typing import Any

from .graph_store import MutateGraph, TransitionNodePhase, TransitionNodeStatus
from .trusted_evaluator import EvaluatorRunReceiptV1


class OptimizationLifecycleError(RuntimeError):
    """A candidate cannot safely advance through the Optimization lifecycle."""


class OptimizationAttemptCoordinator:
    """Run trusted DEV evaluation and project its durable receipt into the graph.

    Scheduling remains owned by the caller. This coordinator accepts only a
    node identity, dispatches the deterministic evaluator jobs through Core,
    and derives the graph transition exclusively from the durable run receipt.
    """

    def __init__(self, *, graph: Any, trusted_evaluator: Any):
        self.graph = graph
        self.trusted_evaluator = trusted_evaluator
        self.queue = trusted_evaluator.queue

    def evaluate_candidate(
        self, *, node_id: str, worker_id: str = "core-evaluator",
    ) -> EvaluatorRunReceiptV1:
        node = self._node(node_id)
        payload = node["payload"]

        # A committed phase transition is the replay record. Revalidate its
        # immutable receipt and return without dispatching or mutating again.
        transition = payload.get("last_phase_transition", {})
        receipt_ref = next(
            (ref for ref in transition.get("evidence_refs", ())
             if isinstance(ref, str) and ref.startswith("evaluator://receipt/")),
            None,
        )
        if transition.get("guard") == "CANDIDATE_READY" and receipt_ref:
            return self._require_receipt(receipt_ref.removeprefix("evaluator://receipt/"), payload)

        if node["phase"] != "OPTIMIZATION" or node["status"] != "READY":
            raise OptimizationLifecycleError(
                "candidate evaluation requires a READY OPTIMIZATION node"
            )
        candidate_snapshot_id = payload.get("candidate_snapshot_id")
        source_snapshot_id = payload.get("source_snapshot_id")
        if not isinstance(candidate_snapshot_id, str) or not isinstance(source_snapshot_id, str):
            raise OptimizationLifecycleError(
                "candidate evaluation requires pinned source and candidate snapshots"
            )

        job_ids = self.trusted_evaluator.enqueue_repeatability(
            candidate_snapshot_id=candidate_snapshot_id, tier="dev",
        )
        for job_id in job_ids:
            job = self.queue.get_job(job_id)
            if job.get("state") == "READY":
                self.trusted_evaluator.run_one(job_id, worker_id=worker_id)
            elif job.get("state") != "COMPLETED":
                # DISPATCHED/UNKNOWN can represent already-spent work. Do not
                # silently dispatch again or treat an uncertain run as a pass.
                raise OptimizationLifecycleError(
                    f"evaluator job {job_id!r} is {job.get('state')!r}; outcome is indeterminate"
                )

        receipt = self.trusted_evaluator.finalize_repeatability(job_ids)
        if (receipt.tier != "dev"
                or receipt.source_snapshot_id != source_snapshot_id
                or receipt.candidate_snapshot_id != candidate_snapshot_id):
            raise OptimizationLifecycleError(
                "durable evaluator receipt is stale against the scheduled candidate"
            )
        receipt_ref = f"evaluator://receipt/{receipt.receipt_digest}"
        key_suffix = hashlib.sha256(
            f"{node_id}\0{receipt.receipt_digest}".encode("utf-8")
        ).hexdigest()
        graph_revision = self.graph.graph_revision()
        self.graph.mutate(MutateGraph(
            command_id=f"rpdi11-candidate-ready-{key_suffix}",
            idempotency_key=f"rpdi11-candidate-ready-{key_suffix}",
            expected_graph_revision=graph_revision,
            changes=(
                TransitionNodeStatus(
                    type="TransitionNodeStatus", node_id=node_id,
                    expected_node_revision=int(node["revision"]),
                    expected_status="READY", to_status="COMPLETED",
                    guard="VERIFIED_COMPLETION", actor="CORE",
                    evidence_refs=(receipt_ref,),
                ),
                TransitionNodePhase(
                    type="TransitionNodePhase", node_id=node_id,
                    expected_node_revision=int(node["revision"]) + 1,
                    from_phase="OPTIMIZATION", to_phase="VERIFICATION",
                    guard="CANDIDATE_READY", actor="CORE",
                    evidence_refs=(receipt_ref,),
                ),
            ),
            evidence_refs=(receipt_ref,),
        ))
        return receipt

    def _node(self, node_id: str) -> dict[str, Any]:
        snapshot = self.graph.scheduling_snapshot()
        for node in snapshot["nodes"]:
            if node["node_id"] == node_id:
                return node
        raise OptimizationLifecycleError(f"Optimization node {node_id!r} does not exist")

    def _require_receipt(
        self, receipt_digest: str, payload: dict[str, Any],
    ) -> EvaluatorRunReceiptV1:
        spec = self.graph.campaign_spec()
        campaign_id = self.graph.campaign_id
        if campaign_id is None:
            raise OptimizationLifecycleError("graph has no immutable campaign identity")
        try:
            receipt = self.graph.evaluator_protocols.require_run_receipt(
                receipt_digest,
                campaign_id=campaign_id,
                spec_hash=spec.approved_hash(),
                approval_digest=spec.evaluation.approval_digest,
                protocol_digest=spec.evaluation.content_sha256,
                candidate_snapshot_id=payload["candidate_snapshot_id"],
            )
            if (receipt.source_snapshot_id != payload["source_snapshot_id"]
                    or receipt.tier != "dev"):
                raise OptimizationLifecycleError(
                    "candidate-ready receipt must match the source snapshot and DEV tier"
                )
            return receipt
        except Exception as exc:
            raise OptimizationLifecycleError(
                "candidate-ready graph transition has no valid durable evaluator receipt"
            ) from exc
