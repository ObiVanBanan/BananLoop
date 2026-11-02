"""Core-owned Optimization candidate lifecycle orchestration."""
from __future__ import annotations

import hashlib
from typing import Any

from .graph_store import MutateGraph, TransitionNodePhase, TransitionNodeStatus
from .trusted_evaluator import EvaluatorRunReceiptV1
from .evaluator_verification import IntegrationVerifierReceiptV1


class OptimizationLifecycleError(RuntimeError):
    """A candidate cannot safely advance through the Optimization lifecycle."""


class OptimizationAttemptCoordinator:
    """Run trusted DEV evaluation and project its durable receipt into the graph.

    Scheduling remains owned by the caller. This coordinator accepts only a
    node identity, dispatches the deterministic evaluator jobs through Core,
    and derives the graph transition exclusively from the durable run receipt.
    """

    def __init__(
        self, *, graph: Any, trusted_evaluator: Any,
        integration_verifier: Any | None = None,
    ):
        self.graph = graph
        self.trusted_evaluator = trusted_evaluator
        self.integration_verifier = integration_verifier
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

    def verify_candidate(
        self, *, node_id: str, baseline_run_receipt_digest: str,
        candidate_validation_run_receipt_digest: str,
        executor: Any, worker_id: str,
    ) -> IntegrationVerifierReceiptV1:
        node = self._node(node_id)
        payload = node["payload"]
        transition = payload.get("last_phase_transition", {})
        decision = {
            "VERIFIED_RESULT": "PASS",
            "REGRESSION_FAILED": "FAIL",
        }.get(transition.get("guard"))
        integration_ref = next(
            (ref for ref in transition.get("evidence_refs", ())
             if isinstance(ref, str) and ref.startswith("integration://receipt/")),
            None,
        )
        if decision is not None and integration_ref:
            return self._require_integration_receipt(
                integration_ref.removeprefix("integration://receipt/"),
                decision=decision, payload=payload,
            )
        if self.integration_verifier is None:
            raise OptimizationLifecycleError("integration verifier is not configured")
        if node["phase"] != "VERIFICATION" or node["status"] != "READY":
            raise OptimizationLifecycleError(
                "integration verification requires a READY VERIFICATION node"
            )
        if transition.get("guard") != "CANDIDATE_READY":
            raise OptimizationLifecycleError(
                "VERIFICATION node is missing its candidate-ready evaluator receipt"
            )
        evaluator_ref = next(
            (ref for ref in transition.get("evidence_refs", ())
             if isinstance(ref, str) and ref.startswith("evaluator://receipt/")),
            None,
        )
        if evaluator_ref is None:
            raise OptimizationLifecycleError(
                "VERIFICATION node has no durable evaluator receipt reference"
            )
        candidate_snapshot_id = payload.get("candidate_snapshot_id")
        source_snapshot_id = payload.get("source_snapshot_id")
        if not isinstance(candidate_snapshot_id, str) or not isinstance(source_snapshot_id, str):
            raise OptimizationLifecycleError(
                "integration verification requires pinned source and candidate snapshots"
            )

        try:
            job, _evidence = self.integration_verifier.enqueue_integration(
                baseline_run_receipt_digest=baseline_run_receipt_digest,
                candidate_run_receipt_digest=candidate_validation_run_receipt_digest,
            )
            verifier_job_id = job["job_id"]
            queue = self.integration_verifier.queue
            queued = queue.get_job(verifier_job_id)
            if queued.get("state") == "READY":
                receipt = self.integration_verifier.run_integration_one(
                    verifier_job_id=verifier_job_id, worker_id=worker_id,
                    executor=executor,
                )
            elif queued.get("state") == "COMPLETED":
                # Recover from a crash after queue settlement but before graph
                # projection by rebuilding the immutable receipt from the
                # durable completed result.
                receipt = self.integration_verifier.authority.record_completed_integration_job(
                    queue, verifier_job_id,
                )
            else:
                raise OptimizationLifecycleError(
                    f"integration job {verifier_job_id!r} is {queued.get('state')!r}; "
                    "outcome is indeterminate"
                )
        except OptimizationLifecycleError:
            raise
        except Exception as exc:
            raise OptimizationLifecycleError(
                "integration verifier dispatch or durable receipt recovery failed"
            ) from exc

        if (receipt.source_snapshot_id != source_snapshot_id
                or receipt.candidate_snapshot_id != candidate_snapshot_id
                or receipt.decision not in {"PASS", "FAIL"}):
            raise OptimizationLifecycleError(
                "integration receipt is stale against the scheduled candidate"
            )
        receipt_ref = f"integration://receipt/{receipt.receipt_digest}"
        passed = receipt.decision == "PASS"
        phase_change = TransitionNodePhase(
            type="TransitionNodePhase", node_id=node_id,
            expected_node_revision=int(node["revision"]) + 1,
            from_phase="VERIFICATION",
            to_phase="CONSOLIDATION" if passed else "OPTIMIZATION",
            guard="VERIFIED_RESULT" if passed else "REGRESSION_FAILED",
            actor="VERIFIER", evidence_refs=(receipt_ref,),
        )
        key_suffix = hashlib.sha256(
            f"{node_id}\0{receipt.receipt_digest}".encode("utf-8")
        ).hexdigest()
        self.graph.mutate(MutateGraph(
            command_id=f"rpdi11-integration-decision-{key_suffix}",
            idempotency_key=f"rpdi11-integration-decision-{key_suffix}",
            expected_graph_revision=self.graph.graph_revision(),
            changes=(
                TransitionNodeStatus(
                    type="TransitionNodeStatus", node_id=node_id,
                    expected_node_revision=int(node["revision"]),
                    expected_status="READY", to_status="COMPLETED",
                    guard="VERIFIED_COMPLETION", actor="CORE",
                    evidence_refs=(receipt_ref,),
                ),
                phase_change,
            ),
            evidence_refs=(receipt_ref,),
        ))
        return receipt

    def _require_integration_receipt(
        self, receipt_digest: str, *, decision: str, payload: dict[str, Any],
    ) -> IntegrationVerifierReceiptV1:
        spec = self.graph.campaign_spec()
        campaign_id = self.graph.campaign_id
        if campaign_id is None:
            raise OptimizationLifecycleError("graph has no immutable campaign identity")
        try:
            return self.graph.evaluator_protocols.require_integration_decision(
                receipt_digest, decision=decision, campaign_id=campaign_id,
                approval_digest=spec.evaluation.approval_digest,
                spec_hash=spec.approved_hash(),
                source_snapshot_id=payload["source_snapshot_id"],
                protocol_digest=spec.evaluation.content_sha256,
                candidate_snapshot_id=payload["candidate_snapshot_id"],
            )
        except Exception as exc:
            raise OptimizationLifecycleError(
                "integration graph transition has no valid durable verifier receipt"
            ) from exc

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
            if (receipt.protocol_id != spec.evaluation.protocol_id
                    or receipt.protocol_version != spec.evaluation.version
                    or receipt.source_snapshot_id != payload["source_snapshot_id"]
                    or receipt.tier != "dev"):
                raise OptimizationLifecycleError(
                    "candidate-ready receipt must match the pinned protocol, source snapshot, and DEV tier"
                )
            return receipt
        except Exception as exc:
            raise OptimizationLifecycleError(
                "candidate-ready graph transition has no valid durable evaluator receipt"
            ) from exc
