from __future__ import annotations

import hashlib
import sqlite3

import pytest
from pydantic import ValidationError

from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority,
    EvaluatorProtocolV1,
    EvaluatorTierManifestV1,
    EvaluatorVerificationError,
    MetricDiscoveryProposalV1,
    ProtocolVerifierOutputV1,
    VerifierReceiptV1,
    make_verifier_receipt,
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
    assert {item.tier for item in protocol.tiers} == {"dev", "holdout"}
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
        with pytest.raises(ValidationError, match="digest"):
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
