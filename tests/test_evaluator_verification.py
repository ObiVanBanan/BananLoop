from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import zipfile
from types import SimpleNamespace
from dataclasses import replace

import pytest
from pydantic import ValidationError

from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority,
    EvaluatorProtocolDraftV1,
    EvaluatorProtocolV1,
    EvaluatorVerificationAdapter,
    EvaluatorTierManifestV1,
    EvaluatorVerificationError,
    IntegrationCoreEvidenceV1,
    IntegrationVerificationAdapter,
    IntegrationVerifierOutputV1,
    MetricDiscoveryProposalV1,
    ProtocolVerifierOutputV1,
    VerifierReceiptV1,
    make_verifier_receipt,
)
from bananloop.contracts import (
    CapabilityPolicyV2, ExplorationCampaignSpecV2, ProtocolCase, ProtocolManifest,
    parse_campaign_spec_document,
)
from bananloop.exploration import EvidenceOnlyOutputV2
from bananloop.budget_ledger import BudgetLedger
from bananloop.graph_store import (
    AddNode, ExplorationCompletionV2, GraphRepository, MutateGraph,
    TransitionNodeStatus,
)
from bananloop.migrations import create_v5_graph_schema
from bananloop.roles import (
    PinnedRoleExecutorV1, RoleContextSourceV1, RoleContextValueV1,
    RoleDefinitionV1, RoleRegistryV1, TeamFactoryV1, TeamManifestAuthority,
    TeamRequestV1, build_role_context,
)
from bananloop.worker_queue import DurableWorkerQueue
from bananloop.optimization_lifecycle import OptimizationAttemptCoordinator
from bananloop.trusted_evaluator import (
    EvaluatorBudgetProofV1,
    EvaluatorExecutionV1,
    EvaluatorRunReceiptV1,
    TrustedEvaluatorRunner,
    _case_protocol_digest,
    TrustedEvaluatorAdapter,
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
        verifier_output_digest="8" * 64, review_material_digest="9" * 64,
        output=output,
    )


def build_full_optimization_runtime(
    *, graph, queue, campaign_id, state_root, source_snapshot_id,
    candidate_snapshot_id, protocol, manifest, registry,
):
    """Build the real evaluator/integration adapters used by lifecycle E2E tests."""
    class DeterministicRunner(TrustedEvaluatorRunner):
        def __init__(self):
            super().__init__(bwrap_binary="not-used-in-test")
            self.protocol_hash = manifest.protocol_sha256

        def _sandbox_available(self):
            return True

        def _execute_isolated(
            self, argv, *, readonly, output, candidate_snapshot_id, tier, timeout,
        ):
            private_map = json.loads(
                (readonly / "tier" / "input-map.json").read_text(encoding="utf-8")
            )
            measured_cases = [{
                "case_id": item["case_id"], "repetition": item["repetition"],
                "input_sha256": item["input_sha256"], "metrics": {},
            } for item in private_map]
            if candidate_snapshot_id == source_snapshot_id:
                score = 0.6
            else:
                candidate_file = (state_root / "v2_optimization" / "snapshots"
                                  / campaign_id / candidate_snapshot_id / "src" / "app.py")
                try:
                    score = 0.5 if "regression candidate" in candidate_file.read_text() else 0.8
                except FileNotFoundError:
                    score = 0.8
            return json.dumps({
                "status": "ok", "metrics": {"quality": score},
                "protocol_sha256": self.protocol_hash, "cases": measured_cases,
                "diagnostics": {},
            })

    trusted = TrustedEvaluatorAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id,
        state_root=state_root, runner=DeterministicRunner(),
    )
    team = TeamFactoryV1.build(
        registry,
        TeamRequestV1(
            campaign_id=campaign_id, spec_hash=graph.campaign_spec().approved_hash(),
            requested_instances={
                "integration_verifier": 1,
                **({"optimizer": 1} if any(role.role_id == "optimizer" for role in registry.roles) else {}),
            }, specializations=(),
            available_agent_calls=10, available_model_tokens=1000,
        ),
        expected_registry_digest=registry.digest(),
    )
    manifest_authority = TeamManifestAuthority(queue.path)
    manifest_authority.pin(team)
    manifest_authority.close()
    member = next(item for item in team.members if item.role.role_id == "integration_verifier")

    def invoke(request, _gateway, **_pinned):
        public_evidence = json.loads(request["role_context"]["public_task"])
        return IntegrationVerifierOutputV1(
            candidate_snapshot_id=request["candidate_snapshot_id"],
            protocol_digest=request["protocol_digest"], decision=public_evidence["decision"],
            checks=(
                "candidate_identity", "protocol_identity", "evaluation_receipt",
                "budget_receipt", "artifact_integrity", "regression_policy",
            ),
        ).model_dump(mode="json")

    executor = PinnedRoleExecutorV1.from_manifest(team, member.instance_id, invoke)
    integration = IntegrationVerificationAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id,
        trusted_evaluator=trusted,
    )
    coordinator = OptimizationAttemptCoordinator(
        graph=graph, trusted_evaluator=trusted, integration_verifier=integration,
    )
    return coordinator, trusted, executor


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
        optimization_spec = parse_campaign_spec_document({
            "schema_version": 2, "mode": "OPTIMIZATION",
            "goal": "Run the approved evaluator against candidate snapshots",
            "source_repo": "https://example.invalid/repo", "base_commit": "a" * 40,
            "autonomy": {"mode": "SUPERVISED", "scope_digest": "b" * 64, "scope_version": 1},
            "budget": {"max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
                       "max_evaluator_calls": 10, "deadline_seconds": 3600,
                       "plateau_experiments": 3},
            "role_registry_digest": "c" * 64,
            "evaluation": {
                "protocol_id": protocol.protocol_id, "version": protocol.version,
                "content_sha256": protocol.content_digest(),
                "approval_digest": approval.approval_digest,
            },
            "parent": None,
        })
        runtime_spec_hash = optimization_spec.approved_hash()
        assert optimization_spec.evaluation.approval_digest == approval.approval_digest
        assert runtime_spec_hash != proposal.spec_hash
        assert authority.require_frozen_reference(
            protocol.protocol_id, protocol.version, protocol.content_digest(),
            approval.approval_digest, campaign_id=proposal.campaign_id,
            spec_hash=proposal.spec_hash,
        ) == approval
        with pytest.raises(EvaluatorVerificationError, match="does not match"):
            authority.require_frozen_reference(
                protocol.protocol_id, protocol.version, protocol.content_digest(),
                approval.approval_digest, campaign_id=proposal.campaign_id,
                spec_hash=runtime_spec_hash,
            )
        executions = tuple(EvaluatorExecutionV1(
            campaign_id=proposal.campaign_id, spec_hash=runtime_spec_hash,
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
            campaign_id=proposal.campaign_id, spec_hash=runtime_spec_hash,
            job_id=f"evaluator-job-{index}", fence=1,
            receipt_digest=str(index) * 64, actual_amount=1,
            actual_usage={"evaluator_calls": 1},
        ) for index in range(1, protocol.repeatability_runs + 1))
        run_receipt = EvaluatorRunReceiptV1.from_completed_runs(executions, proofs)
        assert authority.append_run_receipt(run_receipt) == run_receipt
        assert authority.require_run_receipt(
            run_receipt.receipt_digest, campaign_id=proposal.campaign_id,
            spec_hash=runtime_spec_hash, approval_digest=approval.approval_digest,
            protocol_digest=protocol.content_digest(), candidate_snapshot_id="b" * 40,
        ) == run_receipt
        with pytest.raises(EvaluatorVerificationError, match="cross-bound"):
            authority.require_run_receipt(
                run_receipt.receipt_digest, campaign_id=proposal.campaign_id,
                spec_hash=runtime_spec_hash, approval_digest=approval.approval_digest,
                protocol_digest=protocol.content_digest(), candidate_snapshot_id="c" * 40,
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM evaluator_run_receipts")

        baseline_executions = tuple(replace(
            item, candidate_snapshot_id=protocol.source_snapshot_id,
        ) for item in executions)
        baseline_proofs = tuple(EvaluatorBudgetProofV1(
            campaign_id=proposal.campaign_id, spec_hash=runtime_spec_hash,
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
            "campaign_id": proposal.campaign_id, "spec_hash": runtime_spec_hash,
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



@pytest.mark.parametrize(
    ("candidate_quality", "reviewer_decision", "decision", "phase", "status", "guard"),
    [
        (0.8, "PASS", "PASS", "CONSOLIDATION", "COMPLETED", "VERIFIED_RESULT"),
        (0.5, "PASS", "FAIL", "OPTIMIZATION", "PENDING", "REGRESSION_FAILED"),
        (0.8, "FAIL", "FAIL", "OPTIMIZATION", "PENDING", "REGRESSION_FAILED"),
    ],
)
def test_real_integration_adapter_queue_receipt_graph_transition_and_replay(
    tmp_path, candidate_quality, reviewer_decision, decision, phase, status, guard,
):
    proposal = _proposal()
    approval = _receipt(proposal)
    protocol = proposal.protocol
    campaign_id = proposal.campaign_id
    role_definition = RoleDefinitionV1(
        role_id="integration_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="pin-test",
        prompt_digest="a" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "public_task"),
        priority=1, min_instances=1, max_instances=1,
        calls_per_instance=1, tokens_per_instance=100,
    )
    registry = RoleRegistryV1(
        registry_id="test-integration-registry", version=1,
        roles=(role_definition,),
    )
    spec = parse_campaign_spec_document({
        "schema_version": 2, "mode": "OPTIMIZATION",
        "goal": "Verify a candidate with the frozen protocol",
        "source_repo": "https://example.invalid/repo", "base_commit": "a" * 40,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "b" * 64, "scope_version": 1,
            "capability_policy": {
                "tools": [], "read_paths": ["src/**"], "write_paths": [],
                "backend": "ISOLATED", "network_access": False,
            },
        },
        "budget": {
            "max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
            "max_evaluator_calls": 10, "deadline_seconds": 3600,
            "plateau_experiments": 3,
        },
        "role_registry_digest": registry.digest(),
        "evaluation": {
            "protocol_id": protocol.protocol_id, "version": protocol.version,
            "content_sha256": protocol.content_digest(),
            "approval_digest": approval.approval_digest,
        },
        "parent": None,
    })
    spec_hash = spec.approved_hash()
    db_path = tmp_path / "campaign.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root(campaign_id, {"agent_calls": 20})
    ledger.close()

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.execute(
        "CREATE TABLE campaigns(id INTEGER PRIMARY KEY, spec_json TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO campaigns(id,spec_json) VALUES(1,?)",
        (json.dumps(spec.model_dump(mode="json")),),
    )
    graph = GraphRepository(conn, campaign_id=campaign_id)
    queue = DurableWorkerQueue(db_path)
    protocol_authority = EvaluatorProtocolAuthority(graph.conn)
    protocol_authority._append_proposal(proposal)
    protocol_authority._append_verified_receipt(approval)

    validation_tier = next(tier for tier in protocol.tiers if tier.tier == "validation")
    cases = tuple(
        SimpleNamespace(case_id=f"case-{index}", repetition=0,
                        input_sha256=f"{index:064x}")
        for index in range(validation_tier.case_count)
    )
    baseline_result = SimpleNamespace(
        metrics={"quality": SimpleNamespace(value=0.75)}, cases=cases,
    )
    candidate_result = SimpleNamespace(
        metrics={"quality": SimpleNamespace(value=candidate_quality)}, cases=cases,
    )

    def make_run(candidate_snapshot_id, prefix):
        execution = EvaluatorExecutionV1(
            campaign_id=campaign_id, spec_hash=spec_hash,
            approval_digest=approval.approval_digest,
            protocol_id=protocol.protocol_id, protocol_version=protocol.version,
            protocol_digest=protocol.content_digest(),
            source_snapshot_id=protocol.source_snapshot_id,
            candidate_snapshot_id=candidate_snapshot_id, tier="validation",
            evaluator_bundle_sha256=protocol.evaluator_bundle_sha256,
            tier_manifest_sha256=validation_tier.manifest_sha256,
            case_count=validation_tier.case_count, result_digest=prefix * 64,
            repeatability_runs=protocol.repeatability_runs,
            output_artifact_digests=(),
        )
        proofs = tuple(EvaluatorBudgetProofV1(
            campaign_id=campaign_id, spec_hash=spec_hash,
            job_id=f"{prefix}-proof-{index}", fence=1,
            receipt_digest=f"{index:x}" * 64, actual_amount=1,
            actual_usage={"evaluator_calls": 1},
        ) for index in range(1, protocol.repeatability_runs + 1))
        run = EvaluatorRunReceiptV1.from_completed_runs(
            tuple(execution for _ in range(protocol.repeatability_runs)), proofs,
        )
        protocol_authority.append_run_receipt(run)
        return run

    baseline = make_run(protocol.source_snapshot_id, "5")
    candidate = make_run("b" * 40, "6")
    trusted = TrustedEvaluatorAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id, state_root=tmp_path,
    )
    trusted._snapshot_files = lambda *_args: None
    trusted._manifest_for_receipt = lambda _receipt: SimpleNamespace(cases=cases)
    trusted._load_evaluation_result = lambda digest: (
        baseline_result if digest == baseline.result_digest else candidate_result
    )
    trusted._verify_budget_proofs = lambda *_args, **_kwargs: True
    # This focused adapter test seeds the VALIDATION receipt; the full lifecycle
    # test below exercises real evaluator enqueue/dispatch/settlement.
    trusted.queue = SimpleNamespace(get_job=lambda _job_id: {"state": "COMPLETED"})
    trusted.enqueue_repeatability = lambda **_kwargs: ("validation-1", "validation-2")
    trusted.finalize_repeatability = lambda _job_ids: candidate

    team = TeamFactoryV1.build(
        registry,
        TeamRequestV1(
            campaign_id=campaign_id, spec_hash=spec_hash,
            requested_instances={"integration_verifier": 1}, specializations=(),
            available_agent_calls=10, available_model_tokens=1000,
        ),
        expected_registry_digest=registry.digest(),
    )
    manifest_authority = TeamManifestAuthority(db_path)
    manifest_authority.pin(team)
    manifest_authority.close()
    member = team.members[0]

    def invoke(request, _gateway, **_pinned):
        return IntegrationVerifierOutputV1(
            candidate_snapshot_id=request["candidate_snapshot_id"],
            protocol_digest=request["protocol_digest"], decision=reviewer_decision,
            checks=(
                "candidate_identity", "protocol_identity", "evaluation_receipt",
                "budget_receipt", "artifact_integrity", "regression_policy",
            ),
        ).model_dump(mode="json")

    executor = PinnedRoleExecutorV1.from_manifest(team, member.instance_id, invoke)
    integration = IntegrationVerificationAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id,
        trusted_evaluator=trusted,
    )
    coordinator = OptimizationAttemptCoordinator(
        graph=graph, trusted_evaluator=trusted, integration_verifier=integration,
    )
    graph.mutate(MutateGraph(
        command_id="add-verification-node", idempotency_key="add-verification-node",
        expected_graph_revision=0,
        changes=(AddNode(
            type="AddNode", node_id="candidate-node", phase="VERIFICATION",
            payload={
                "source_snapshot_id": protocol.source_snapshot_id,
                "candidate_snapshot_id": candidate.candidate_snapshot_id,
                "last_phase_transition": {
                    "from_phase": "OPTIMIZATION", "to_phase": "VERIFICATION",
                    "guard": "CANDIDATE_READY", "actor": "CORE",
                    "evidence_refs": [f"evaluator://receipt/{candidate.receipt_digest}"],
                },
            },
        ),),
    ))
    graph.mutate(MutateGraph(
        command_id="ready-verification-node", idempotency_key="ready-verification-node",
        expected_graph_revision=1,
        changes=(TransitionNodeStatus(
            type="TransitionNodeStatus", node_id="candidate-node",
            expected_node_revision=0, expected_status="PENDING", to_status="READY",
            guard="SCHEDULER_READY", actor="CORE",
        ),),
    ))
    try:
        receipt = coordinator.verify_candidate(
            node_id="candidate-node",
            baseline_run_receipt_digest=baseline.receipt_digest,
            executor=executor, worker_id="integration-worker",
        )
        graph_revision = graph.graph_revision()
        replay = coordinator.verify_candidate(
            node_id="candidate-node",
            baseline_run_receipt_digest=baseline.receipt_digest,
            executor=executor, worker_id="integration-worker",
        )
        assert receipt.decision == decision
        assert replay == receipt
        node = next(
            node for node in graph.scheduling_snapshot()["nodes"]
            if node["node_id"] == "candidate-node"
        )
        assert (node["phase"], node["status"]) == (phase, status)
        assert node["payload"]["last_phase_transition"]["guard"] == guard
        assert node["payload"]["last_phase_transition"]["evidence_refs"] == [
            f"integration://receipt/{receipt.receipt_digest}"
        ]
        assert graph.graph_revision() == graph_revision == 3
        job_ids = [row["job_id"] for row in queue.conn.execute(
            "SELECT job_id FROM worker_jobs WHERE request_json LIKE '%integration-verification-v1%'"
        )]
        assert len(job_ids) == 1
        assert queue.get_job(job_ids[0])["state"] == "COMPLETED"
        assert [event["event_type"] for event in queue.events(job_ids[0])] == [
            "JOB_ENQUEUED", "JOB_LEASED", "JOB_DISPATCHED", "JOB_COMPLETED",
        ]
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_integration_receipts"
        ).fetchone()[0] == 1
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_integration_evidence"
        ).fetchone()[0] == 1
        # A successful local integration transition records evidence only.
        # Global champion promotion is a separate explicit Core CAS command.
        assert graph.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='champion_state'"
        ).fetchone() is None
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            graph.conn.execute(
                "UPDATE evaluator_integration_receipts SET decision='FAIL' "
                "WHERE receipt_digest=?",
                (receipt.receipt_digest,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            graph.conn.execute(
                "UPDATE evaluator_integration_evidence SET decision='FAIL' "
                "WHERE evidence_digest=(SELECT core_evidence_digest "
                "FROM evaluator_integration_receipts WHERE receipt_digest=?)",
                (receipt.receipt_digest,),
            )
    finally:
        queue.close()
        graph.close()



def test_optimization_lifecycle_integrates_durable_evaluator_and_verification_queues(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "source-repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.PIPE,
        ).decode().strip()

    git("init", "-q")
    git("config", "user.email", "rpdi11@example.invalid")
    git("config", "user.name", "RPDI-11 test")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("base\n", encoding="utf-8")
    git("add", "src/app.py")
    git("commit", "-qm", "source snapshot")
    source_snapshot_id = git("rev-parse", "HEAD")
    (repo / "src" / "app.py").write_text("candidate\n", encoding="utf-8")
    git("add", "src/app.py")
    git("commit", "-qm", "candidate snapshot")
    candidate_snapshot_id = git("rev-parse", "HEAD")

    input_bytes = b'{"prompt":"controlled case","label":"ok"}'
    input_sha = hashlib.sha256(input_bytes).hexdigest()
    bundle_io = __import__("io").BytesIO()
    with zipfile.ZipFile(bundle_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evaluate.py", "# deterministic test asset\n")
    bundle_bytes = bundle_io.getvalue()
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
    preliminary = EvaluatorProtocolV1(
        protocol_id="lifecycle-quality", version=1,
        source_snapshot_id=source_snapshot_id,
        evaluator_bundle_sha256=bundle_sha,
        entrypoint=("python", "{evaluator}/evaluate.py"),
        metric_id="quality", metric_field="quality",
        direction="MAXIMIZE", aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(
                tier="dev", manifest_sha256="0" * 64, case_count=1,
            ),
            EvaluatorTierManifestV1(
                tier="validation", manifest_sha256="0" * 64, case_count=1,
            ),
        ),
        repeatability_runs=2,
    )
    manifest = ProtocolManifest(
        protocol_sha256=_case_protocol_digest(preliminary),
        cases=(ProtocolCase(
            case_id="controlled-case", repetition=0, input_sha256=input_sha,
        ),),
    )
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    protocol = preliminary.model_copy(update={
        "tiers": (
            EvaluatorTierManifestV1(
                tier="dev", manifest_sha256=manifest_sha, case_count=1,
            ),
            EvaluatorTierManifestV1(
                tier="validation", manifest_sha256=manifest_sha, case_count=1,
            ),
        ),
    })

    campaign_id = "campaign-1"
    proposal_payload = {
        "schema_version": 1, "campaign_id": campaign_id, "spec_hash": "e" * 64,
        "source_snapshot_id": source_snapshot_id, "source_node_id": "metric-node",
        "source_job_id": "research-job", "source_fence": 2,
        "source_request_hash": "f" * 64, "source_output_digest": "1" * 64,
        "source_event_cursor": 9, "proposer_role_id": "researcher",
        "proposer_role_instance_id": "agent-" + "2" * 24,
        "proposer_binding_digest": "3" * 64,
        "protocol": protocol.model_dump(mode="json"),
        "evidence_refs": ("artifact://sha256/" + "4" * 64,),
    }
    proposal_digest = hashlib.sha256(json.dumps(
        proposal_payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode()).hexdigest()
    proposal = MetricDiscoveryProposalV1(
        proposal_digest=proposal_digest, **proposal_payload,
    )
    approval = _receipt(proposal)

    role_definition = RoleDefinitionV1(
        role_id="integration_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="pin-test",
        prompt_digest="a" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "public_task"),
        priority=1, min_instances=1, max_instances=1,
        calls_per_instance=5, tokens_per_instance=500,
    )
    optimizer_role = RoleDefinitionV1(
        role_id="optimizer", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="pin-test", prompt_digest="b" * 64,
        capability_policy=CapabilityPolicyV2(
            tools=("read_file", "write_file", "commit_candidate"),
            read_paths=("src/**",), write_paths=("src/**",), backend="LOCAL",
            network_access=False,
        ),
        context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                        "constraints", "evidence_refs", "dev_summary"),
        priority=1, min_instances=1, max_instances=1,
        calls_per_instance=5, tokens_per_instance=500,
    )
    registry = RoleRegistryV1(
        registry_id="full-lifecycle-registry", version=1,
        roles=(role_definition, optimizer_role),
    )
    spec = parse_campaign_spec_document({
        "schema_version": 2, "mode": "OPTIMIZATION",
        "goal": "Run trusted evaluator lifecycle end to end",
        "source_repo": str(repo), "base_commit": source_snapshot_id,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "b" * 64, "scope_version": 1,
            "capability_policy": {
                "tools": ["read_file", "write_file", "commit_candidate"],
                "read_paths": ["src/**"], "write_paths": ["src/**"],
                "backend": "LOCAL", "network_access": False,
            },
        },
        "budget": {
            "max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
            "max_evaluator_calls": 20, "deadline_seconds": 3600,
            "plateau_experiments": 3,
        },
        "role_registry_digest": registry.digest(),
        "evaluation": {
            "protocol_id": protocol.protocol_id, "version": protocol.version,
            "content_sha256": protocol.content_digest(),
            "approval_digest": approval.approval_digest,
        },
        "parent": None,
    })
    spec_hash = spec.approved_hash()

    state_root = tmp_path / "state"
    published = state_root / "rpdi05" / "published"
    published.mkdir(parents=True)
    for data, digest in (
        (bundle_bytes, bundle_sha), (manifest_bytes, manifest_sha),
        (input_bytes, input_sha),
    ):
        (published / digest).write_bytes(data)

    db_path = state_root / "campaign.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root(
        campaign_id, {"agent_calls": 20, "evaluator_calls": 20,
                      "experiments": 10, "attempts": 20},
    )
    ledger.close()
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.execute(
        "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO campaigns(id,spec_json) VALUES(1,?)",
        (json.dumps(spec.model_dump(mode="json")),),
    )
    graph = GraphRepository(conn, campaign_id=campaign_id)
    queue = DurableWorkerQueue(db_path)
    protocol_authority = EvaluatorProtocolAuthority(graph.conn)
    protocol_authority._append_proposal(proposal)
    protocol_authority._append_verified_receipt(approval)

    coordinator, trusted, executor = build_full_optimization_runtime(
        graph=graph, queue=queue, campaign_id=campaign_id, state_root=state_root,
        source_snapshot_id=source_snapshot_id, candidate_snapshot_id=candidate_snapshot_id,
        protocol=protocol, manifest=manifest, registry=registry,
    )
    try:
        graph.mutate(MutateGraph(
            command_id="add-lifecycle-candidate", idempotency_key="add-lifecycle-candidate",
            expected_graph_revision=0,
            changes=(AddNode(
                type="AddNode", node_id="lifecycle-candidate", phase="OPTIMIZATION",
                payload={
                    "source_snapshot_id": source_snapshot_id,
                    "candidate_snapshot_id": candidate_snapshot_id,
                },
            ),),
        ))
        graph.mutate(MutateGraph(
            command_id="ready-lifecycle-candidate", idempotency_key="ready-lifecycle-candidate",
            expected_graph_revision=1,
            changes=(TransitionNodeStatus(
                type="TransitionNodeStatus", node_id="lifecycle-candidate",
                expected_node_revision=0, expected_status="PENDING", to_status="READY",
                guard="SCHEDULER_READY", actor="CORE",
            ),),
        ))

        dev_receipt = coordinator.evaluate_candidate(
            node_id="lifecycle-candidate", worker_id="core-evaluator",
        )
        assert dev_receipt.tier == "dev"
        assert dev_receipt.candidate_snapshot_id == candidate_snapshot_id

        baseline_jobs = trusted.enqueue_repeatability(
            candidate_snapshot_id=source_snapshot_id, tier="validation",
        )
        for job_id in baseline_jobs:
            trusted.run_one(job_id, worker_id="baseline-evaluator")
        baseline = trusted.finalize_repeatability(baseline_jobs)
        assert baseline.tier == "validation"
        assert baseline.candidate_snapshot_id == source_snapshot_id

        node = next(
            node for node in graph.scheduling_snapshot()["nodes"]
            if node["node_id"] == "lifecycle-candidate"
        )
        graph.mutate(MutateGraph(
            command_id="ready-lifecycle-verification",
            idempotency_key="ready-lifecycle-verification",
            expected_graph_revision=graph.graph_revision(),
            changes=(TransitionNodeStatus(
                type="TransitionNodeStatus", node_id="lifecycle-candidate",
                expected_node_revision=int(node["revision"]),
                expected_status="PENDING", to_status="READY",
                guard="SCHEDULER_READY", actor="CORE",
            ),),
        ))

        integration_receipt = coordinator.verify_candidate(
            node_id="lifecycle-candidate",
            baseline_run_receipt_digest=baseline.receipt_digest,
            executor=executor, worker_id="integration-worker",
        )
        graph_revision = graph.graph_revision()
        run_count = graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_run_receipts"
        ).fetchone()[0]
        queue_event_count = len(queue.events_after(0))

        replay = coordinator.verify_candidate(
            node_id="lifecycle-candidate",
            baseline_run_receipt_digest=baseline.receipt_digest,
            executor=executor, worker_id="integration-worker",
        )
        assert replay == integration_receipt
        assert integration_receipt.decision == "PASS"
        assert graph.graph_revision() == graph_revision
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_run_receipts"
        ).fetchone()[0] == run_count
        assert len(queue.events_after(0)) == queue_event_count

        node = next(
            node for node in graph.scheduling_snapshot()["nodes"]
            if node["node_id"] == "lifecycle-candidate"
        )
        assert (node["phase"], node["status"]) == ("CONSOLIDATION", "COMPLETED")
        assert node["payload"]["last_phase_transition"]["guard"] == "VERIFIED_RESULT"
        assert node["payload"]["last_phase_transition"]["evidence_refs"] == [
            f"integration://receipt/{integration_receipt.receipt_digest}"
        ]
        assert queue.conn.execute(
            "SELECT COUNT(*) FROM worker_jobs WHERE resource='evaluator_calls'"
        ).fetchone()[0] == 6
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_run_receipts"
        ).fetchone()[0] == 3
        assert graph.conn.execute(
            "SELECT COUNT(*) FROM evaluator_integration_receipts"
        ).fetchone()[0] == 1

        graph.mutate(MutateGraph(
            command_id="add-v2-runtime-proposal", idempotency_key="add-v2-runtime-proposal",
            expected_graph_revision=graph.graph_revision(),
            changes=(AddNode(
                type="AddNode", node_id="v2-runtime-proposal", phase="OPTIMIZATION",
                payload={"source_snapshot_id": source_snapshot_id,
                         "hypothesis": "Improve the deterministic ranker.",
                         "scheduler": {"priority": 5}},
            ),),
        ))
        from bananloop.v2_optimization_runtime import V2OptimizationRuntime

        runtime = V2OptimizationRuntime(
            graph=graph, queue=queue, campaign_id=campaign_id, state_root=state_root,
            role_registry=registry, trusted_evaluator=trusted,
            integration_verifier=coordinator.integration_verifier,
        )

        def optimizer_reply(public_input, **_pinned):
            assert "visible_documents" in public_input
            encoded = json.dumps(public_input).lower()
            assert "controlled-case" not in encoded
            assert "controlled case" not in encoded
            return {
                "hypothesis_id": "ranker-hypothesis",
                "rationale": "Use the improved deterministic ranker implementation.",
                "changes": {"src/app.py": "optimized candidate\n"},
            }

        proposed = runtime.run_one(
            visible_paths=["src/app.py"], hypothesis_id="ranker-hypothesis",
            model_reply=optimizer_reply, worker_id="v2-optimizer-test",
        )
        assert proposed["status"] == "CANDIDATE_CREATED"
        assert proposed["candidate_snapshot_id"] != source_snapshot_id
        published_revision = graph.graph_revision()
        published_events = len(queue.events_after(0))
        replayed = runtime.candidate_adapter.publish_completed(proposed["job_id"])
        assert replayed["replayed"] is True
        assert graph.graph_revision() == published_revision
        assert len(queue.events_after(0)) == published_events
        optimized = runtime.run_campaign(
            visible_paths=["src/app.py"], integration_verifier_executor=executor,
            max_steps=2, worker_id="v2-optimization-cycle",
        )
        assert optimized["steps_this_call"] == 2
        assert [step["status"] for step in optimized["steps"]] == [
            "DEV_EVALUATED", "INDEPENDENT_VERIFICATION_PASS",
        ]
        verified = optimized["steps"][-1]
        assert verified["champion"]["snapshot_id"] == proposed["candidate_snapshot_id"]

        graph.mutate(MutateGraph(
            command_id="add-v2-runtime-regression", idempotency_key="add-v2-runtime-regression",
            expected_graph_revision=graph.graph_revision(),
            changes=(AddNode(
                type="AddNode", node_id="v2-runtime-regression", phase="OPTIMIZATION",
                payload={"source_snapshot_id": source_snapshot_id,
                         "hypothesis": "Try an intentionally regressing implementation.",
                         "scheduler": {"priority": 10}},
            ),),
        ))

        def regressing_reply(_public_input, **_pinned):
            return {
                "hypothesis_id": "regressing-hypothesis",
                "rationale": "This candidate is deliberately worse for the rejection test.",
                "changes": {"src/app.py": "regression candidate\n"},
            }

        regressing = runtime.run_one(
            visible_paths=["src/app.py"], hypothesis_id="regressing-hypothesis",
            model_reply=regressing_reply, worker_id="v2-regression-optimizer",
        )
        assert regressing["status"] == "CANDIDATE_CREATED"
        assert runtime.run_one(
            visible_paths=["src/app.py"], worker_id="v2-regression-evaluator",
        )["status"] == "DEV_EVALUATED"
        rejected = runtime.run_one(
            visible_paths=["src/app.py"], integration_verifier_executor=executor,
            worker_id="v2-regression-verifier",
        )
        assert rejected["status"] == "INDEPENDENT_VERIFICATION_FAIL"
        assert rejected["champion"] is None

        # Reopen the durable graph and queue after the rejected attempt. The
        # next role context must still carry the persisted failure evidence.
        restarted_graph = GraphRepository.open(str(db_path), campaign_id=campaign_id)
        restarted_queue = DurableWorkerQueue(db_path)
        restarted_trusted = TrustedEvaluatorAdapter(
            graph=restarted_graph, queue=restarted_queue, campaign_id=campaign_id,
            state_root=state_root, runner=trusted.runner,
        )
        restarted_integration = IntegrationVerificationAdapter(
            graph=restarted_graph, queue=restarted_queue, campaign_id=campaign_id,
            trusted_evaluator=restarted_trusted,
        )
        runtime = V2OptimizationRuntime(
            graph=restarted_graph, queue=restarted_queue, campaign_id=campaign_id,
            state_root=state_root, role_registry=registry,
            trusted_evaluator=restarted_trusted,
            integration_verifier=restarted_integration,
        )

        def memory_guided_reply(public_input, **_pinned):
            constraints = public_input["role_context"]["constraints"]
            assert any("previous candidate did not pass" in item.lower() for item in constraints)
            assert public_input["role_context"]["evidence_refs"]
            return {
                "hypothesis_id": "revised-hypothesis",
                "rationale": "Use the prior rejection evidence to revise the implementation.",
                "changes": {"src/app.py": "recovered optimized candidate\n"},
            }

        revised = runtime.run_one(
            visible_paths=["src/app.py"], hypothesis_id="revised-hypothesis",
            model_reply=memory_guided_reply, worker_id="v2-recovery-optimizer",
        )
        assert revised["status"] == "CANDIDATE_CREATED"
        assert runtime.run_one(
            visible_paths=["src/app.py"], worker_id="v2-recovery-evaluator",
        )["status"] == "DEV_EVALUATED"
        recovered = runtime.run_one(
            visible_paths=["src/app.py"], integration_verifier_executor=executor,
            worker_id="v2-recovery-verifier",
        )
        assert recovered["status"] == "INDEPENDENT_VERIFICATION_PASS"
        assert recovered["champion"]["snapshot_id"] == revised["candidate_snapshot_id"]
        restarted_queue.close()
        restarted_graph.close()
    finally:
        queue.close()
        graph.close()



def test_protocol_proposal_and_approval_use_real_pinned_queue_workflow(tmp_path):
    campaign_id = "research-campaign"
    source_snapshot_id = "a" * 40
    input_bytes = b'{"private":"CASE_BYTES_SENTINEL"}'
    input_sha = hashlib.sha256(input_bytes).hexdigest()
    bundle_io = __import__("io").BytesIO()
    with zipfile.ZipFile(bundle_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evaluate.py", "SOURCE_REVIEW_SENTINEL = True\\n")
    bundle_bytes = bundle_io.getvalue()
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
    preliminary = EvaluatorProtocolV1(
        protocol_id="independently-reviewed", version=1,
        source_snapshot_id=source_snapshot_id,
        evaluator_bundle_sha256=bundle_sha,
        entrypoint=("python", "evaluate.py"),
        metric_id="quality", metric_field="quality",
        direction="MAXIMIZE", aggregation="MEAN",
        tiers=(EvaluatorTierManifestV1(
            tier="dev", manifest_sha256="0" * 64, case_count=1,
        ),),
    )
    manifest = ProtocolManifest(
        protocol_sha256=_case_protocol_digest(preliminary),
        cases=(ProtocolCase(case_id="PRIVATE_CASE_ID_SENTINEL", repetition=0,
                            input_sha256=input_sha),),
    )
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    protocol = preliminary.model_copy(update={"tiers": (EvaluatorTierManifestV1(
        tier="dev", manifest_sha256=manifest_sha, case_count=1,
    ),)})
    reviewer_policy = CapabilityPolicyV2(
        tools=(), read_paths=(), write_paths=(), backend="ISOLATED",
        network_access=False,
    )
    researcher_role = RoleDefinitionV1(
        role_id="researcher", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="pin-test",
        prompt_digest="1" * 64, capability_policy=reviewer_policy,
        context_fields=("campaign_goal", "node_id", "public_task"),
        priority=1, min_instances=1, max_instances=1,
        calls_per_instance=2, tokens_per_instance=200,
    )
    verifier_role = RoleDefinitionV1(
        role_id="independent_verifier", adapter="codex", provider="openai",
        model="gpt-6-luna", model_version="pin-test",
        prompt_digest="2" * 64, capability_policy=reviewer_policy,
        context_fields=("campaign_goal", "public_task"),
        priority=2, min_instances=1, max_instances=1,
        calls_per_instance=2, tokens_per_instance=200,
    )
    registry = RoleRegistryV1(
        registry_id="protocol-workflow-registry", version=1,
        roles=(researcher_role, verifier_role),
    )
    spec = parse_campaign_spec_document({
        "schema_version": 2, "mode": "EXPLORATION",
        "goal": "Draft and independently verify a metric protocol",
        "source_repo": "https://example.invalid/source",
        "base_commit": source_snapshot_id,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "3" * 64,
            "scope_version": 1,
            "capability_policy": {
                "tools": [], "read_paths": [], "write_paths": [],
                "backend": "ISOLATED", "network_access": False,
            },
        },
        "budget": {}, "role_registry_digest": registry.digest(),
        "evaluation": None,
    })
    spec_hash = spec.approved_hash()
    db_path = tmp_path / "protocol-workflow.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root(campaign_id, {"agent_calls": 20})
    ledger.close()

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    create_v5_graph_schema(conn)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO campaigns(id,spec_json) VALUES(1,?)",
        (json.dumps(spec.model_dump(mode="json")),),
    )
    graph = GraphRepository(conn, campaign_id=campaign_id)
    queue = DurableWorkerQueue(db_path)
    published_root = tmp_path / "rpdi05" / "published"
    published_root.mkdir(parents=True)
    for digest, content in (
        (bundle_sha, bundle_bytes), (manifest_sha, manifest_bytes),
        (input_sha, input_bytes),
    ):
        (published_root / digest).write_bytes(content)

    manifest = TeamFactoryV1.build(
        registry,
        TeamRequestV1(
            campaign_id=campaign_id, spec_hash=spec_hash,
            requested_instances={"researcher": 1, "independent_verifier": 1},
            specializations=(), available_agent_calls=10,
            available_model_tokens=1000,
        ),
        expected_registry_digest=registry.digest(),
    )
    pinned_manifest_authority = TeamManifestAuthority(db_path)
    pinned_manifest_authority.pin(manifest)
    pinned_manifest_authority.close()
    researcher = next(member for member in manifest.members
                      if member.role.role_id == "researcher")
    verifier = next(member for member in manifest.members
                    if member.role.role_id == "independent_verifier")
    assert researcher.instance_id != verifier.instance_id

    graph.mutate(MutateGraph(
        command_id="add-metric-node", idempotency_key="add-metric-node",
        expected_graph_revision=0,
        changes=(AddNode(
            type="AddNode", node_id="metric-node", phase="EXPLORATION",
            payload={"source_snapshot_id": source_snapshot_id},
        ),),
    ))
    graph.mutate(MutateGraph(
        command_id="ready-metric-node", idempotency_key="ready-metric-node",
        expected_graph_revision=1,
        changes=(TransitionNodeStatus(
            type="TransitionNodeStatus", node_id="metric-node",
            expected_node_revision=0, expected_status="PENDING", to_status="READY",
            guard="SCHEDULER_READY", actor="CORE",
        ),),
    ))

    researcher_action = "research:metric-node"
    researcher_context = build_role_context(
        researcher,
        RoleContextSourceV1(
            campaign_goal=RoleContextValueV1(
                value=spec.goal, classification="PUBLIC_SPEC",
                source_ref="campaign://spec/goal",
            ),
            fields={
                "node_id": RoleContextValueV1(
                    value="metric-node", classification="PUBLIC_NODE",
                    source_ref="graph://nodes/metric-node",
                ),
                "public_task": RoleContextValueV1(
                    value="Draft a bounded evaluator protocol",
                    classification="PUBLIC_NODE", source_ref="graph://nodes/metric-node",
                ),
            },
        ),
    )
    researcher_binding = manifest.job_binding(
        researcher.instance_id, action_id=researcher_action,
        context_digest=researcher_context.digest(),
    )
    role_allocation = {
        "manifest_digest": manifest.manifest_digest,
        "role_instance_id": researcher.instance_id, "role_id": researcher.role.role_id,
        "calls_limit": researcher.role.calls_per_instance,
        "tokens_limit": researcher.role.tokens_per_instance,
        "token_upper_bound": researcher.role.tokens_per_instance // researcher.role.calls_per_instance,
    }
    researcher_request = {
        "schema": "evidence-only-research-v2", "campaign_id": campaign_id,
        "spec_hash": spec_hash, "node_id": "metric-node",
        "base_commit": source_snapshot_id,
        "capability_manifest": {"base_commit": source_snapshot_id},
        "role_binding": researcher_binding,
        "role_context": researcher_context.model_dump(mode="json", exclude_unset=True),
        "team_manifest_digest": manifest.manifest_digest,
        "role_registry_digest": registry.digest(),
    }
    queue.enqueue(
        job_id="researcher-job-1", enqueue_key="researcher-job-1",
        campaign_id=campaign_id, action_id=researcher_action,
        resource="agent_calls", upper_bound=1, request=researcher_request,
        role_allocation=role_allocation,
    )

    bundle_ref = f"artifact://sha256/{bundle_sha}"
    manifest_ref = f"artifact://sha256/{manifest_sha}"
    input_ref = f"artifact://sha256/{input_sha}"
    draft = EvaluatorProtocolDraftV1(
        protocol=protocol, evidence_refs=(bundle_ref, manifest_ref, input_ref),
    )
    output = EvidenceOnlyOutputV2(
        node_id="metric-node", evidence_refs=(bundle_ref, manifest_ref, input_ref),
        artifacts=(
            {"path": "evaluator_bundle.zip", "sha256": bundle_sha},
            {"path": "protocol_manifest.json", "sha256": manifest_sha},
            {"path": "case_input.json", "sha256": input_sha},
        ),
        limitations=("test protocol bytes are deterministic",),
        next_experiment="run independent protocol review",
        protocol_draft=draft,
    )
    result_payload = output.model_dump(mode="json")
    output_digest = hashlib.sha256(json.dumps(
        result_payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    lease = queue.claim("researcher-job-1", "research-worker", lease_seconds=30)
    queue.mark_dispatched("researcher-job-1", "research-worker", lease["fence"])
    queue.complete(
        "researcher-job-1", "research-worker", lease["fence"],
        output_digest=output_digest, actual_amount=1,
        receipt_digest="4" * 64, result_payload=result_payload,
    )
    job = queue.get_job("researcher-job-1")
    graph.complete_exploration(ExplorationCompletionV2(
        command_id="complete-metric-node", idempotency_key="complete-metric-node",
        expected_graph_revision=2, node_id="metric-node", node_revision=1,
        job_id="researcher-job-1", worker_fence=lease["fence"],
        source_request_hash=job["request_hash"], output_digest=output_digest,
        spec_hash=spec_hash, evidence_refs=(bundle_ref,),
        artifact_digests=(bundle_sha, manifest_sha, input_sha), limitations=output.limitations,
        next_experiment=output.next_experiment,
    ))

    protocol_authority = EvaluatorProtocolAuthority(graph.conn)
    proposal = protocol_authority.record_completed_proposal(
        graph, queue, "researcher-job-1",
    )
    assert proposal.source_job_id == "researcher-job-1"
    assert proposal.source_fence == lease["fence"]
    assert proposal.proposer_role_instance_id == researcher.instance_id

    verification = EvaluatorVerificationAdapter(
        graph=graph, queue=queue, campaign_id=campaign_id,
        published_artifact_root=published_root,
    )
    verifier_job = verification.enqueue_verifier(proposal.proposal_digest)
    assert verifier_job["state"] == "READY"
    public_task = verifier_job["request"]["role_context"]["public_task"]
    assert "SOURCE_REVIEW_SENTINEL" in public_task
    assert "evaluate.py" in public_task
    assert manifest_sha in public_task
    assert "PRIVATE_CASE_ID_SENTINEL" not in public_task
    assert input_sha not in public_task
    assert "CASE_BYTES_SENTINEL" not in public_task
    assert input_ref not in public_task
    (published_root / input_sha).write_bytes(b"tampered private input")
    with pytest.raises(EvaluatorVerificationError, match="do not match their digest"):
        verification.enqueue_verifier(proposal.proposal_digest)
    (published_root / input_sha).write_bytes(input_bytes)
    assert verifier_job["request"]["role_binding"]["role_instance_id"] == verifier.instance_id
    assert verifier.instance_id != proposal.proposer_role_instance_id
    checks = (
        "protocol_identity", "evaluator_bundle_review", "metric_contract",
        "tier_manifests", "frozen_case_scope", "no_hidden_data_dependency",
        "fair_comparison", "error_semantics",
    )

    def invoke_verifier(request, _gateway, **_pinned):
        return ProtocolVerifierOutputV1(
            proposal_digest=request["proposal_digest"], decision="APPROVE",
            checks=checks, rationale="Independent deterministic protocol review.",
        ).model_dump(mode="json")

    wrong_executor = PinnedRoleExecutorV1.from_manifest(
        manifest, researcher.instance_id, invoke_verifier,
    )
    with pytest.raises(EvaluatorVerificationError, match="pinned independent verifier"):
        verification.run_verifier_one(
            verifier_job_id=verifier_job["job_id"], worker_id="verify-worker",
            executor=wrong_executor,
        )
    assert queue.get_job(verifier_job["job_id"])["state"] == "READY"

    unpinned_executor = SimpleNamespace(
        manifest_digest=None, member=verifier,
        dispatch=lambda *_args, **_kwargs: invoke_verifier(
            verifier_job["request"], None,
        ),
    )
    with pytest.raises(EvaluatorVerificationError, match="pinned independent verifier"):
        verification.run_verifier_one(
            verifier_job_id=verifier_job["job_id"], worker_id="verify-worker",
            executor=unpinned_executor,
        )
    assert queue.get_job(verifier_job["job_id"])["state"] == "READY"

    verifier_executor = PinnedRoleExecutorV1.from_manifest(
        manifest, verifier.instance_id, invoke_verifier,
    )
    receipt = verification.run_verifier_one(
        verifier_job_id=verifier_job["job_id"], worker_id="verify-worker",
        executor=verifier_executor,
    )
    assert receipt.decision == "APPROVE"
    assert receipt.verifier_role_instance_id == verifier.instance_id
    assert receipt.verifier_role_instance_id != proposal.proposer_role_instance_id
    assert receipt.approval_digest

    approved_proposal, stored_receipt = protocol_authority.approved_protocol(
        receipt.approval_digest,
    )
    assert approved_proposal == proposal
    assert stored_receipt == receipt
    assert protocol_authority.require_frozen_reference(
        protocol.protocol_id, protocol.version, protocol.content_digest(),
        receipt.approval_digest, campaign_id=campaign_id,
    ) == receipt

    queue.conn.execute(
        "UPDATE worker_jobs SET fence=fence+1 WHERE job_id='researcher-job-1'"
    )
    with pytest.raises(EvaluatorVerificationError, match="fence or request"):
        protocol_authority.record_completed_proposal(graph, queue, "researcher-job-1")

    queue.close()
    graph.close()
    reopened = sqlite3.connect(db_path, isolation_level=None)
    reloaded_authority = EvaluatorProtocolAuthority(reopened)
    reloaded_proposal, reloaded_receipt = reloaded_authority.approved_protocol(
        receipt.approval_digest,
    )
    assert reloaded_proposal.proposal_digest == proposal.proposal_digest
    assert reloaded_receipt.receipt_digest == receipt.receipt_digest
    reopened.close()
