from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError

from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority,
    EvaluatorProtocolV1,
    EvaluatorTierManifestV1,
    EvaluatorVerificationError,
    IntegrationCoreEvidenceV1,
    MetricDiscoveryProposalV1,
    ProtocolVerifierOutputV1,
    VerifierReceiptV1,
    make_verifier_receipt,
)
from bananloop.trusted_evaluator import (
    EvaluatorBudgetProofV1,
    EvaluatorExecutionV1,
    EvaluatorRunReceiptV1,
)


def _protocol() -> EvaluatorProtocolV1:
    return EvaluatorProtocolV1(
        protocol_id="retrieval-quality", version=1, source_snapshot_id="a" * 40,
        evaluator_bundle_sha256="b" * 64, entrypoint=("python", "evaluate.py"),
        metric_id="answer_quality", metric_field="quality", direction="MAXIMIZE",
        aggregation="MEAN", tiers=(EvaluatorTierManifestV1(
            tier="dev", manifest_sha256="c" * 64, case_count=12,
        ), EvaluatorTierManifestV1(
            tier="holdout", manifest_sha256="d" * 64, case_count=20,
        ), EvaluatorTierManifestV1(
            tier="validation", manifest_sha256="9" * 64, case_count=16,
        )), repeatability_runs=3,
    )


def _proposal() -> MetricDiscoveryProposalV1:
    protocol = _protocol()
    payload = {
        "schema_version": 1, "campaign_id": "source-campaign", "spec_hash": "e" * 64,
        "source_snapshot_id": "a" * 40, "source_node_id": "metric-node",
        "source_job_id": "research-job", "source_fence": 2,
        "source_request_hash": "f" * 64, "source_output_digest": "1" * 64,
        "source_event_cursor": 9, "proposer_role_id": "researcher",
        "proposer_role_instance_id": "agent-" + "2" * 24,
        "proposer_binding_digest": "3" * 64, "protocol": protocol.model_dump(mode="json"),
        "evidence_refs": ("artifact://sha256/" + "4" * 64,),
    }
    digest = hashlib.sha256(__import__("json").dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    return MetricDiscoveryProposalV1(proposal_digest=digest, **payload)


def _authority() -> tuple[sqlite3.Connection, EvaluatorProtocolAuthority]:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA user_version=5")
    return conn, EvaluatorProtocolAuthority(conn)


def _receipt(proposal, *, decision="APPROVE", verifier_instance="agent-" + "5" * 24):
    checks = (
        "protocol_identity", "evaluator_bundle_review", "metric_contract", "tier_manifests",
        "frozen_case_scope", "no_hidden_data_dependency", "fair_comparison", "error_semantics",
    )
    output = ProtocolVerifierOutputV1(
        proposal_digest=proposal.proposal_digest, decision=decision, checks=checks,
        rationale="Protocol bytes and tier-specific evaluator behavior verified.",
    )
    return make_verifier_receipt(
        proposal, verifier_job_id="verify-job-1", verifier_fence=3,
        verifier_role_id="independent_verifier", verifier_role_instance_id=verifier_instance,
        verifier_binding_digest="6" * 64, verifier_request_hash="7" * 64,
        verifier_output_digest="8" * 64, output=output,
    )


def test_protocol_digest_is_canonical_and_protected_tiers_have_manifest_identity():
    protocol = _protocol()
    assert protocol.content_digest() == _protocol().content_digest()
    assert {item.tier for item in protocol.tiers} == {"dev", "validation", "holdout"}
    with pytest.raises(ValidationError, match="DEV tier"):
        EvaluatorProtocolV1(
            protocol_id="no-dev", version=1, source_snapshot_id="a" * 40,
            evaluator_bundle_sha256="b" * 64, entrypoint=("python", "eval.py"),
            metric_id="quality", metric_field="quality", direction="MAXIMIZE",
            aggregation="MEAN", tiers=(EvaluatorTierManifestV1(
                tier="holdout", manifest_sha256="d" * 64, case_count=1,
            ),),
        )
    with pytest.raises(ValidationError, match="command shell"):
        EvaluatorProtocolV1(
            protocol_id="shell", version=1, source_snapshot_id="a" * 40,
            evaluator_bundle_sha256="b" * 64, entrypoint=("bash", "run.sh"),
            metric_id="quality", metric_field="quality", direction="MAXIMIZE",
            aggregation="MEAN", tiers=(EvaluatorTierManifestV1(
                tier="dev", manifest_sha256="c" * 64, case_count=1,
            ),),
        )


def test_approval_requires_complete_verifier_checkset_and_cannot_self_approve():
    proposal = _proposal()
    with pytest.raises(ValidationError, match="every independent"):
        ProtocolVerifierOutputV1(
            proposal_digest=proposal.proposal_digest, decision="APPROVE",
            checks=("protocol_identity",), rationale="looks good",
        )
    with pytest.raises(ValidationError, match="cannot verify their own"):
        _receipt(proposal, verifier_instance=proposal.proposer_role_instance_id)


def test_frozen_reference_requires_matching_immutable_approval_receipt():
    proposal = _proposal()
    receipt = _receipt(proposal)
    conn, authority = _authority()
    try:
        authority._append_proposal(proposal)
        authority._append_verified_receipt(receipt)
        frozen, stored = authority.approved_protocol(receipt.approval_digest)
        assert frozen == proposal
        assert stored == receipt
        assert authority.require_frozen_reference(
            proposal.protocol.protocol_id, proposal.protocol.version,
            proposal.protocol.content_digest(), receipt.approval_digest,
            campaign_id=proposal.campaign_id,
        ) == receipt
        with pytest.raises(EvaluatorVerificationError, match="does not match"):
            authority.require_frozen_reference(
                proposal.protocol.protocol_id, proposal.protocol.version,
                "9" * 64, receipt.approval_digest, campaign_id=proposal.campaign_id,
            )
        with pytest.raises(EvaluatorVerificationError, match="does not match"):
            authority.require_frozen_reference(
                proposal.protocol.protocol_id, proposal.protocol.version,
                proposal.protocol.content_digest(), receipt.approval_digest,
                campaign_id="different-campaign",
            )
        with pytest.raises(EvaluatorVerificationError, match="no independent verifier approval"):
            authority.approved_protocol("0" * 64)
    finally:
        conn.close()


def test_verifier_receipt_cannot_be_replayed_against_another_campaign_or_protocol():
    proposal = _proposal()
    receipt = _receipt(proposal)
    conn, authority = _authority()
    try:
        authority._append_proposal(proposal)
        authority._append_verified_receipt(receipt)
        changed = receipt.model_copy(update={"campaign_id": "another-campaign"})
        with pytest.raises(ValidationError, match="digest"):
            VerifierReceiptV1.model_validate(changed.model_dump(mode="json"))
        changed_source = proposal.model_copy(update={"source_snapshot_id": "9" * 40})
        with pytest.raises(ValidationError, match="source snapshots differ"):
            MetricDiscoveryProposalV1.model_validate(changed_source.model_dump(mode="json"))
    finally:
        conn.close()


def test_proposals_and_verifier_decisions_are_append_only():
    proposal = _proposal()
    receipt = _receipt(proposal)
    conn, authority = _authority()
    try:
        authority._append_proposal(proposal)
        authority._append_verified_receipt(receipt)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE evaluator_protocol_proposals SET protocol_id='tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM evaluator_verifier_receipts")
    finally:
        conn.close()


def test_rejection_is_durable_but_never_yields_an_approval_digest():
    proposal = _proposal()
    receipt = _receipt(proposal, decision="REJECT")
    assert receipt.decision == "REJECT"
    assert receipt.approval_digest is None
    conn, authority = _authority()
    try:
        authority._append_proposal(proposal)
        authority._append_verified_receipt(receipt)
        with pytest.raises(EvaluatorVerificationError, match="no independent verifier approval"):
            authority.approved_protocol("a" * 64)
    finally:
        conn.close()


def test_durable_evaluator_run_receipt_requires_and_binds_frozen_approval():
    proposal = _proposal()
    approval = _receipt(proposal)
    conn, authority = _authority()
    try:
        authority._append_proposal(proposal)
        authority._append_verified_receipt(approval)
        protocol = proposal.protocol
        executions = tuple(EvaluatorExecutionV1(
            campaign_id=proposal.campaign_id, spec_hash=proposal.spec_hash,
            approval_digest=approval.approval_digest, protocol_id=protocol.protocol_id,
            protocol_version=protocol.version, protocol_digest=protocol.content_digest(),
            source_snapshot_id=protocol.source_snapshot_id,
            candidate_snapshot_id="b" * 40, tier="validation",
            evaluator_bundle_sha256=protocol.evaluator_bundle_sha256,
            tier_manifest_sha256=protocol.tiers[2].manifest_sha256,
            case_count=protocol.tiers[2].case_count, result_digest="9" * 64,
            repeatability_runs=protocol.repeatability_runs,
            output_artifact_digests=(),
        ) for _ in range(protocol.repeatability_runs))
        proofs = tuple(EvaluatorBudgetProofV1(
            campaign_id=proposal.campaign_id, spec_hash=proposal.spec_hash,
            job_id=f"evaluator-job-{index}", fence=1,
            receipt_digest=str(index) * 64, actual_amount=1,
            actual_usage={"evaluator_calls": 1},
        ) for index in range(1, protocol.repeatability_runs + 1))
        run_receipt = EvaluatorRunReceiptV1.from_completed_runs(executions, proofs)
        assert authority.append_run_receipt(run_receipt) == run_receipt
        assert authority.require_run_receipt(
            run_receipt.receipt_digest, campaign_id=proposal.campaign_id,
            spec_hash=proposal.spec_hash, approval_digest=approval.approval_digest,
            protocol_digest=protocol.content_digest(), candidate_snapshot_id="b" * 40,
        ) == run_receipt
        with pytest.raises(EvaluatorVerificationError, match="cross-bound"):
            authority.require_run_receipt(
                run_receipt.receipt_digest, campaign_id=proposal.campaign_id,
                spec_hash=proposal.spec_hash, approval_digest=approval.approval_digest,
                protocol_digest=protocol.content_digest(), candidate_snapshot_id="c" * 40,
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM evaluator_run_receipts")

        baseline_executions = tuple(replace(
            item, candidate_snapshot_id=protocol.source_snapshot_id,
        ) for item in executions)
        baseline_proofs = tuple(EvaluatorBudgetProofV1(
            campaign_id=proposal.campaign_id, spec_hash=proposal.spec_hash,
            job_id=f"baseline-job-{index}", fence=1,
            receipt_digest=f"{index + 3:x}" * 64, actual_amount=1,
            actual_usage={"evaluator_calls": 1},
        ) for index in range(1, protocol.repeatability_runs + 1))
        baseline_receipt = EvaluatorRunReceiptV1.from_completed_runs(
            baseline_executions, baseline_proofs,
        )
        authority.append_run_receipt(baseline_receipt)
        from bananloop.evaluator_verification import _digest
        evidence_fields = {
            "campaign_id": proposal.campaign_id, "spec_hash": proposal.spec_hash,
            "source_snapshot_id": protocol.source_snapshot_id,
            "approval_digest": approval.approval_digest,
            "protocol_digest": protocol.content_digest(),
            "candidate_snapshot_id": "b" * 40,
            "baseline_run_receipt_digest": baseline_receipt.receipt_digest,
            "candidate_run_receipt_digest": run_receipt.receipt_digest,
            "budget_settlement_digest": "1" * 64,
            "artifact_integrity_digest": "2" * 64,
            "regression_policy_digest": "3" * 64,
            "check_results": {
                "candidate_identity": True, "protocol_identity": True,
                "evaluation_receipt": True, "budget_receipt": True,
                "artifact_integrity": True, "regression_policy": True,
            },
            "decision": "PASS",
        }
        evidence = IntegrationCoreEvidenceV1(
            evidence_digest=_digest(evidence_fields), **evidence_fields,
        )
        assert authority._append_integration_evidence(evidence) == evidence

        holdout_tier = protocol.tiers[1]
        holdout_candidate = EvaluatorRunReceiptV1.from_completed_runs(tuple(
            replace(item, tier="holdout", tier_manifest_sha256=holdout_tier.manifest_sha256,
                    case_count=holdout_tier.case_count) for item in executions
        ), proofs)
        holdout_baseline = EvaluatorRunReceiptV1.from_completed_runs(tuple(
            replace(item, candidate_snapshot_id=protocol.source_snapshot_id, tier="holdout",
                    tier_manifest_sha256=holdout_tier.manifest_sha256,
                    case_count=holdout_tier.case_count) for item in executions
        ), baseline_proofs)
        authority.append_run_receipt(holdout_candidate)
        authority.append_run_receipt(holdout_baseline)
        holdout_fields = {
            **evidence_fields,
            "baseline_run_receipt_digest": holdout_baseline.receipt_digest,
            "candidate_run_receipt_digest": holdout_candidate.receipt_digest,
        }
        holdout_evidence = IntegrationCoreEvidenceV1(
            evidence_digest=_digest(holdout_fields), **holdout_fields,
        )
        with pytest.raises(EvaluatorVerificationError, match="HOLDOUT cannot feed graph decisions"):
            authority._append_integration_evidence(holdout_evidence)
    finally:
        conn.close()


def test_core_integration_evidence_digest_binds_every_deterministic_check():
    from bananloop.evaluator_verification import _digest

    payload = {
        "campaign_id": "campaign-1", "spec_hash": "1" * 64,
        "source_snapshot_id": "a" * 40, "approval_digest": "2" * 64,
        "protocol_digest": "3" * 64, "candidate_snapshot_id": "b" * 40,
        "baseline_run_receipt_digest": "4" * 64,
        "candidate_run_receipt_digest": "5" * 64,
        "budget_settlement_digest": "6" * 64,
        "artifact_integrity_digest": "7" * 64,
        "regression_policy_digest": "8" * 64,
        "check_results": {
            "candidate_identity": True, "protocol_identity": True,
            "evaluation_receipt": True, "budget_receipt": True,
            "artifact_integrity": True, "regression_policy": True,
        },
        "decision": "PASS",
    }
    evidence = IntegrationCoreEvidenceV1(evidence_digest=_digest(payload), **payload)
    assert evidence.decision == "PASS"
    tampered = {**payload, "check_results": {**payload["check_results"], "budget_receipt": False}}
    with pytest.raises(ValidationError, match="decision"):
        IntegrationCoreEvidenceV1(evidence_digest=_digest(payload), **tampered)
