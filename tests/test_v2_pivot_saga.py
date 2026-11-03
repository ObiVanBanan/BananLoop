from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from bananloop.budget_ledger import BudgetLedger, BudgetLedgerError
from bananloop.contracts import (
    AutonomyScopeV2,
    PivotDecisionCommand,
    PivotExecutionPlanV2,
    PivotProposal,
    RuntimePaths,
    parse_campaign_spec_document,
)
from bananloop.migrations import create_v5_graph_schema
from bananloop.registry import CampaignRegistry, RegistryError
from bananloop.v2_pivot_saga import PivotSagaError, V2PivotSaga, _budget_caps, _parse_v2_spec
from bananloop.api import create_server
from bananloop.application import CampaignApplication
from bananloop.store import StoreError
from bananloop.trusted_evaluator import TrustedEvaluatorAdapter
from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority, EvaluatorProtocolV1, EvaluatorTierManifestV1,
    EvaluatorVerificationError, MetricDiscoveryProposalV1, ProtocolVerifierOutputV1,
    make_verifier_receipt,
    _digest as _protocol_digest,
)
import threading
import urllib.request


SHA = "a" * 64


def _spec_payload(repo: Path, *, mode: str, scope: AutonomyScopeV2, goal: str):
    return {
        "schema_version": 2,
        "mode": "EXPLORATION",
        "goal": goal,
        "source_repo": str(repo.resolve()),
        "base_commit": "b" * 40,
        "autonomy": {
            "mode": mode,
            "scope_digest": scope.content_digest(),
            "scope_version": scope.version,
            "scope": scope.model_dump(mode="json"),
        },
        "budget": {
            "max_experiments": 5, "max_attempts": 8,
            "max_agent_calls": 20, "max_evaluator_calls": 10,
            "deadline_seconds": 3600, "plateau_experiments": 3,
        },
        "role_registry_digest": SHA,
        "evaluation": None,
        "parent": None,
    }


def _campaign_db(db_path: Path, spec) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        create_v5_graph_schema(conn)
        conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,spec_hash TEXT NOT NULL,status TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO campaigns(id,spec_json,spec_hash,status) VALUES(1,?,?,'PAUSED_NEEDS_INPUT')",
            (json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")), spec.approved_hash()),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    finally:
        conn.close()


def _execution_plan(child_payload, paths):
    child = _parse_v2_spec(child_payload)
    return PivotExecutionPlanV2.build(
        child_spec=child.model_dump(mode="json"),
        changed_paths=paths,
        resource_vector=_budget_caps(child),
    )


def _proposal_for_child(setup, child_payload):
    path = "src/optimizer/model/ranker.py"
    return PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=1,
        current_architecture="baseline optimizer", proposed_architecture="model variant",
        rationale="The current model limits search quality.",
        expected_effect="Improve candidate ranking within the approved source scope.",
        changed_scope=(path,),
        resources={"additional_agent_calls": 2, "additional_evaluator_calls": 1},
        execution_plan=_execution_plan(child_payload, (path,)),
    )


def _setup(tmp_path: Path, *, mode="FULL_AUTO", max_child_campaigns=2):
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = CampaignRegistry(repo)
    state_root = registry.root
    parent_entry, parent_dir, _ = registry.reserve("Parent research", "parent-seed")
    parent_scope = AutonomyScopeV2(
        version=1, allowed_paths=("src/optimizer",),
        max_child_depth=2, max_child_campaigns=max_child_campaigns,
    )
    parent_spec = parse_campaign_spec_document(_spec_payload(
        repo, mode=mode, scope=parent_scope, goal="Parent research",
    ))
    _campaign_db(parent_dir / "campaign.sqlite3", parent_spec)
    ledger = registry.budget_ledger()
    ledger.create_root(parent_entry.campaign_id, {
        "agent_calls": 100, "evaluator_calls": 50, "experiments": 20,
        "attempts": 40,
    })
    ledger.close()
    child_scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/optimizer/model",),
        max_child_depth=1, max_child_campaigns=1,
    )
    child_spec = _spec_payload(
        repo, mode=mode, scope=child_scope, goal="Refine optimizer model",
    )
    proposal = _proposal_for_child({"parent_spec": parent_spec}, child_spec)
    return {
        "repo": repo, "state_root": state_root, "registry": registry,
        "parent_id": parent_entry.campaign_id, "parent_db": parent_dir / "campaign.sqlite3",
        "parent_spec": parent_spec, "child_spec": child_spec, "proposal": proposal,
        "child_caps": {
            "agent_calls": 20, "evaluator_calls": 10, "experiments": 5, "attempts": 8,
        },
    }


def _saga(setup, fault_hook=None):
    return V2PivotSaga(
        repo=setup["repo"], state_root=setup["state_root"],
        parent_campaign_id=setup["parent_id"], fault_hook=fault_hook,
    )


def _seed_parent_protocol_approval(setup):
    parent_spec = setup["parent_spec"]
    protocol = EvaluatorProtocolV1(
        protocol_id="pivot-quality", version=1,
        source_snapshot_id=parent_spec.base_commit,
        evaluator_bundle_sha256="b" * 64, entrypoint=("python", "evaluate.py"),
        metric_id="answer_quality", metric_field="quality", direction="MAXIMIZE",
        aggregation="MEAN", tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="c" * 64, case_count=2),
            EvaluatorTierManifestV1(tier="validation", manifest_sha256="d" * 64, case_count=2),
        ), repeatability_runs=2,
    )
    payload = {
        "schema_version": 1, "campaign_id": setup["parent_id"],
        "spec_hash": parent_spec.approved_hash(), "source_snapshot_id": parent_spec.base_commit,
        "source_node_id": "source-node", "source_job_id": "research-job",
        "source_fence": 1, "source_request_hash": "e" * 64,
        "source_output_digest": "f" * 64, "source_event_cursor": 1,
        "proposer_role_id": "researcher", "proposer_role_instance_id": "agent-" + "1" * 24,
        "proposer_binding_digest": "2" * 64, "protocol": protocol.model_dump(mode="json"),
        "evidence_refs": ("artifact://sha256/" + "3" * 64,),
    }
    proposal = MetricDiscoveryProposalV1(
        proposal_digest=_protocol_digest(payload), **payload,
    )
    output = ProtocolVerifierOutputV1(
        proposal_digest=proposal.proposal_digest, decision="APPROVE",
        checks=("protocol_identity", "evaluator_bundle_review", "metric_contract",
                "tier_manifests", "frozen_case_scope", "no_hidden_data_dependency",
                "fair_comparison", "error_semantics"),
        rationale="Bounded test fixture approval.",
    )
    receipt = make_verifier_receipt(
        proposal, verifier_job_id="verifier-job", verifier_fence=1,
        verifier_role_id="independent_verifier", verifier_role_instance_id="agent-" + "4" * 24,
        verifier_binding_digest="5" * 64, verifier_request_hash="6" * 64,
        verifier_output_digest="7" * 64, review_material_digest="8" * 64,
        output=output,
    )
    conn = sqlite3.connect(setup["parent_db"], isolation_level=None)
    try:
        authority = EvaluatorProtocolAuthority(conn)
        authority._append_proposal(proposal)
        authority._append_verified_receipt(receipt)
    finally:
        conn.close()
    return protocol, proposal, receipt




def test_approved_exploration_protocol_is_handed_off_to_immutable_optimization_child(tmp_path):
    setup = _setup(tmp_path)
    protocol, proposal, receipt = _seed_parent_protocol_approval(setup)
    child_spec = dict(setup["child_spec"])
    child_spec["mode"] = "OPTIMIZATION"
    child_spec["evaluation"] = {
        "protocol_id": protocol.protocol_id, "version": protocol.version,
        "content_sha256": protocol.content_digest(), "approval_digest": receipt.approval_digest,
    }
    proposal = _proposal_for_child(setup, child_spec)
    parent_hash = setup["parent_spec"].approved_hash()
    result = _saga(setup).create_child(
        idempotency_key="approved-protocol-handoff", proposal=proposal,
        child_spec=child_spec,
    )
    assert result["state"] == "ACTIVE"
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        stored_parent = parse_campaign_spec_document(
            parent_conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0],
        )
        assert stored_parent.mode == "EXPLORATION"
        assert stored_parent.approved_hash() == parent_hash
    finally:
        parent_conn.close()
    child_dir, _ = setup["registry"].resolve(result["child_campaign_id"], activate=False)
    child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
    try:
        child_row = child_conn.execute(
            "SELECT spec_json,spec_hash FROM campaigns WHERE id=1",
        ).fetchone()
        child = parse_campaign_spec_document(child_row[0])
        assert child.mode == "OPTIMIZATION"
        assert child.evaluation.approval_digest == receipt.approval_digest
        assert child.parent.protocol_handoff_digest
        authority = EvaluatorProtocolAuthority(child_conn)
        accepted = authority.require_frozen_reference(
            child.evaluation.protocol_id, child.evaluation.version,
            child.evaluation.content_sha256, child.evaluation.approval_digest,
            campaign_id=result["child_campaign_id"], spec_hash=child_row[1],
        )
        assert accepted.receipt_digest == receipt.receipt_digest
        handoff = child_conn.execute(
            "SELECT child_spec_hash,parent_campaign_id,parent_spec_hash,allocation_id "
            "FROM evaluator_protocol_handoffs WHERE handoff_digest=?",
            (child.parent.protocol_handoff_digest,),
        ).fetchone()
        assert tuple(handoff) == (
            child_row[1], setup["parent_id"], parent_hash, result["allocation_id"],
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            child_conn.execute(
                "UPDATE evaluator_protocol_handoffs SET payload_json='{}' WHERE handoff_digest=?",
                (child.parent.protocol_handoff_digest,),
            )
    finally:
        child_conn.close()
    replay = _saga(setup).create_child(
        idempotency_key="approved-protocol-handoff", proposal=proposal,
        child_spec=child_spec,
    )
    assert replay["replayed"] is True


@pytest.mark.parametrize("corruption", ["missing", "tampered"])
def test_active_optimization_child_fails_closed_on_corrupt_handoff_record(tmp_path, corruption):
    setup = _setup(tmp_path)
    protocol, _, receipt = _seed_parent_protocol_approval(setup)
    child_spec = dict(setup["child_spec"])
    child_spec["mode"] = "OPTIMIZATION"
    child_spec["evaluation"] = {
        "protocol_id": protocol.protocol_id, "version": protocol.version,
        "content_sha256": protocol.content_digest(), "approval_digest": receipt.approval_digest,
    }
    proposal = _proposal_for_child(setup, child_spec)
    result = _saga(setup).create_child(
        idempotency_key="corrupt-protocol-handoff", proposal=proposal,
        child_spec=child_spec,
    )
    child_dir, _ = setup["registry"].resolve(result["child_campaign_id"], activate=False)
    child_conn = sqlite3.connect(child_dir / "campaign.sqlite3", isolation_level=None)
    try:
        if corruption == "missing":
            child_conn.execute("DROP TRIGGER evaluator_protocol_handoffs_no_delete")
            child_conn.execute(
                "DELETE FROM evaluator_protocol_handoffs WHERE handoff_digest=?",
                (parse_campaign_spec_document(
                    child_conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0],
                ).parent.protocol_handoff_digest,),
            )
        else:
            child_conn.execute("DROP TRIGGER evaluator_protocol_handoffs_no_update")
            child_conn.execute("UPDATE evaluator_protocol_handoffs SET payload_json='{}'")
    finally:
        child_conn.close()

    with pytest.raises(PivotSagaError, match="protocol handoff"):
        _saga(setup).create_child(
            idempotency_key="corrupt-protocol-handoff", proposal=proposal,
            child_spec=child_spec,
        )
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        assert parent_conn.execute(
            "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='corrupt-protocol-handoff'",
        ).fetchone()[0] == "ACTIVE"
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
        ).fetchone()[0] == 1
    finally:
        parent_conn.close()


def test_trusted_evaluator_accepts_adopted_child_protocol_and_rejects_missing_handoff(tmp_path):
    from types import SimpleNamespace

    setup = _setup(tmp_path)
    protocol, _, receipt = _seed_parent_protocol_approval(setup)
    child_spec = dict(setup["child_spec"])
    child_spec["mode"] = "OPTIMIZATION"
    child_spec["evaluation"] = {
        "protocol_id": protocol.protocol_id, "version": protocol.version,
        "content_sha256": protocol.content_digest(), "approval_digest": receipt.approval_digest,
    }
    proposal = _proposal_for_child(setup, child_spec)
    result = _saga(setup).create_child(
        idempotency_key="adapter-protocol-handoff", proposal=proposal,
        child_spec=child_spec,
    )
    child_dir, _ = setup["registry"].resolve(result["child_campaign_id"], activate=False)
    child_conn = sqlite3.connect(child_dir / "campaign.sqlite3", isolation_level=None)
    child = parse_campaign_spec_document(
        child_conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0],
    )

    class RecordingQueue:
        def __init__(self):
            self.requests = []

        def enqueue(self, **request):
            self.requests.append(request)

    queue = RecordingQueue()
    graph = SimpleNamespace(
        conn=child_conn, campaign_spec=lambda: child,
        bind_campaign_id=lambda _campaign_id: None,
    )
    adapter = TrustedEvaluatorAdapter(
        graph=graph, queue=queue, campaign_id=result["child_campaign_id"],
        state_root=tmp_path, runner=object(),
    )
    adapter._resolve_frozen_assets = lambda *_args: None
    adapter._snapshot_files = lambda *_args: None
    jobs = adapter.enqueue_repeatability(candidate_snapshot_id="9" * 40, tier="dev")
    assert len(jobs) == protocol.repeatability_runs
    assert len(queue.requests) == protocol.repeatability_runs
    assert all(item["campaign_id"] == result["child_campaign_id"] for item in queue.requests)
    assert all(item["request"]["approval_digest"] == receipt.approval_digest for item in queue.requests)

    child_conn.execute("DROP TRIGGER evaluator_protocol_handoffs_no_delete")
    child_conn.execute(
        "DELETE FROM evaluator_protocol_handoffs WHERE handoff_digest=?",
        (child.parent.protocol_handoff_digest,),
    )
    with pytest.raises(EvaluatorVerificationError, match="durable approved protocol handoff"):
        adapter.enqueue_repeatability(candidate_snapshot_id="9" * 40, tier="dev")
    child_conn.close()


def test_optimization_child_without_valid_parent_protocol_approval_fails_closed(tmp_path):
    setup = _setup(tmp_path)
    child_spec = dict(setup["child_spec"])
    child_spec["mode"] = "OPTIMIZATION"
    child_spec["evaluation"] = {
        "protocol_id": "pivot-quality", "version": 1,
        "content_sha256": "b" * 64, "approval_digest": "c" * 64,
    }
    proposal = _proposal_for_child(setup, child_spec)
    with pytest.raises(PivotSagaError, match="valid parent protocol approval"):
        _saga(setup).create_child(
            idempotency_key="missing-protocol-approval", proposal=proposal,
            child_spec=child_spec,
        )
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM v2_pivot_sagas",
        ).fetchone()[0] == 0
    finally:
        parent_conn.close()


@pytest.mark.parametrize("crash_at", [
    "AFTER_LEDGER_RESERVE",
    "AFTER_REGISTRY_RESERVE",
    "AFTER_ALLOCATION_BIND",
    "AFTER_CHILD_INITIALIZE",
    "AFTER_PARENT_ACTIVE",
])
def test_pivot_child_saga_reserves_before_create_and_resumes_once(tmp_path, crash_at):
    setup = _setup(tmp_path)
    fired = False

    def fail_once(step):
        nonlocal fired
        if step == crash_at and not fired:
            fired = True
            raise RuntimeError("injected process interruption")

    saga = _saga(setup, fail_once)
    with pytest.raises(RuntimeError, match="process interruption"):
        saga.create_child(
            idempotency_key="pivot-request-1", proposal=setup["proposal"],
            child_spec=setup["child_spec"],
        )

    conn = sqlite3.connect(setup["parent_db"])
    state = conn.execute(
        "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='pivot-request-1'"
    ).fetchone()[0]
    conn.close()
    expected_state = {
        "AFTER_LEDGER_RESERVE": "INTENT",
        "AFTER_REGISTRY_RESERVE": "ESCROWED",
        "AFTER_ALLOCATION_BIND": "CHILD_RESERVED",
        "AFTER_CHILD_INITIALIZE": "ALLOCATION_BOUND",
        "AFTER_PARENT_ACTIVE": "ACTIVE",
    }[crash_at]
    assert state == expected_state

    raw_ledger_conn = sqlite3.connect(setup["registry"].path, isolation_level=None)
    ledger = BudgetLedger.attach(raw_ledger_conn)
    allocations = ledger.conn.execute(
        "SELECT allocation_id,child_id,state FROM child_allocations"
    ).fetchall()
    assert len(allocations) == 1
    assert allocations[0]["state"] in {"RESERVED", "ACTIVE"}
    if crash_at == "AFTER_LEDGER_RESERVE":
        assert allocations[0]["state"] == "RESERVED"
        registry_conn = setup["registry"]._connect()
        try:
            assert registry_conn.execute(
                "SELECT COUNT(*) FROM campaign_index"
            ).fetchone()[0] == 1
        finally:
            registry_conn.close()
    if crash_at == "AFTER_PARENT_ACTIVE":
        assert allocations[0]["child_id"] is not None
        assert ledger.conn.execute(
            "SELECT state FROM child_allocation_gates WHERE allocation_id=?",
            (allocations[0]["allocation_id"],),
        ).fetchone()[0] == "BOUND"
        with pytest.raises(BudgetLedgerError, match="admission gate"):
            ledger.reserve_action(
                reservation_id="during-activation", campaign_id=allocations[0]["child_id"],
                action_id="act-during-activation", resource="agent_calls", amount=1,
                idempotency_key="reserve:during-activation",
                allocation_id=allocations[0]["allocation_id"],
            )
    raw_ledger_conn.close()

    result = _saga(setup).create_child(
        idempotency_key="pivot-request-1", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    assert result["state"] == "ACTIVE"
    if crash_at == "AFTER_PARENT_ACTIVE":
        ledger = setup["registry"].budget_ledger()
        try:
            assert ledger.conn.execute(
                "SELECT state FROM child_allocation_gates WHERE allocation_id=?",
                (result["allocation_id"],),
            ).fetchone()[0] == "ACTIVE"
        finally:
            ledger.close()
    assert result["child_spec_hash"]
    assert setup["registry"].resolve()[1] == setup["parent_id"]

    registry_conn = setup["registry"]._connect()
    try:
        active_campaign_before = registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0]
    finally:
        registry_conn.close()
    repeated = _saga(setup).create_child(
        idempotency_key="pivot-request-1", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    registry_conn = setup["registry"]._connect()
    try:
        assert registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0] == active_campaign_before
    finally:
        registry_conn.close()
    assert repeated["child_campaign_id"] == result["child_campaign_id"]
    assert repeated["allocation_id"] == result["allocation_id"]
    child_dir, _ = setup["registry"].resolve(result["child_campaign_id"], activate=False)
    child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
    try:
        assert child_conn.execute("PRAGMA user_version").fetchone()[0] == 5
        raw = child_conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0]
        child = parse_campaign_spec_document(raw)
        assert child.approved_hash() == result["child_spec_hash"]
        assert child.parent.parent_campaign_id == setup["parent_id"]
        assert child.parent.pivot_proposal_digest == setup["proposal"].digest
        assert child.parent.parent_spec_hash == setup["parent_spec"].approved_hash()
    finally:
        child_conn.close()
    child_status = CampaignApplication(
        setup["repo"], campaign_id=result["child_campaign_id"],
    ).status()
    assert child_status["status"] == "READY"
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 1
    ledger.close()
    conn = sqlite3.connect(setup["parent_db"])
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'"
    ).fetchone()[0] == 1
    conn.close()


def test_full_auto_child_quota_is_serialized_across_parallel_pivots(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    setup = _setup(tmp_path, max_child_campaigns=1)
    child_specs = []
    for i in (1, 2):
        child = dict(setup["child_spec"])
        child["goal"] = f"Refine optimizer model {i}"
        child_specs.append(child)
    proposals = [
        PivotProposal.build(
            spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=i,
            current_architecture="baseline", proposed_architecture=f"model-{i}",
            rationale=f"bounded in-scope proposal {i}", expected_effect="improve ranking",
            changed_scope=(f"src/optimizer/model/ranker{i}.py",),
            resources={"additional_agent_calls": 1},
            execution_plan=_execution_plan(child_specs[i - 1], (f"src/optimizer/model/ranker{i}.py",)),
        )
        for i in (1, 2)
    ]
    barrier = Barrier(2)

    def run(index):
        saga = _saga(setup)
        record = saga._record_intent

        def synchronized_record(conn, plan):
            barrier.wait(timeout=10)
            return record(conn, plan)

        saga._record_intent = synchronized_record
        return saga.create_child(
            idempotency_key=f"parallel-pivot-{index}",
            proposal=proposals[index - 1], child_spec=child_specs[index - 1],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, 1), pool.submit(run, 2)]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except PivotSagaError as exc:
                errors.append(exc)
    assert len(results) == 1
    assert len(errors) == 1
    assert "no remaining child capacity" in str(errors[0])
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 1
    ledger.close()


def test_out_of_scope_pivot_denies_before_budget_or_registry_mutation(tmp_path):
    setup = _setup(tmp_path)
    proposal = PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=1,
        current_architecture="baseline", proposed_architecture="secret change",
        rationale="outside the approved source scope",
        expected_effect="not permitted", changed_scope=("src/secrets/token.py",),
        resources={"additional_agent_calls": 1},
        execution_plan=_execution_plan(setup["child_spec"], ("src/secrets/token.py",)),
    )
    with pytest.raises(PivotSagaError, match="execution paths exceed|outside its versioned scope"):
        _saga(setup).create_child(
            idempotency_key="out-of-scope-pivot", proposal=proposal,
            child_spec=setup["child_spec"],
        )
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()
    conn = sqlite3.connect(setup["parent_db"])
    assert conn.execute("SELECT COUNT(*) FROM v2_pivot_sagas").fetchone()[0] == 0
    conn.close()


def test_supervised_pivot_keeps_explicit_approval_requirement_and_receipt(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    with pytest.raises(PivotSagaError, match="explicit approval"):
        _saga(setup).create_child(
            idempotency_key="supervised-pivot-1", proposal=setup["proposal"],
            child_spec=setup["child_spec"],
        )
    approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="approval-request-1",
        proposal_id=setup["proposal"].proposal_id, digest=setup["proposal"].digest,
        decision="approve",
    )
    saga = _saga(setup)
    with pytest.raises(PivotSagaError, match="durable approval receipt"):
        saga.create_child(
            idempotency_key="forged-supervised-pivot", proposal=setup["proposal"],
            child_spec=setup["child_spec"], approval=approval,
        )
    saga.record_pivot_proposal(setup["proposal"])
    saga.record_pivot_decision(approval)
    # Replaying the same idempotent operator decision preserves the receipt.
    saga.record_pivot_decision(approval)
    result = saga.create_child(
        idempotency_key="supervised-pivot-1", proposal=setup["proposal"],
        child_spec=setup["child_spec"], approval=approval,
    )
    child_dir, _ = setup["registry"].resolve(result["child_campaign_id"], activate=False)
    conn = sqlite3.connect(child_dir / "campaign.sqlite3")
    try:
        child = parse_campaign_spec_document(
            conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()[0]
        )
        assert child.parent.approval_request_id == "approval-request-1"
        assert child.parent.pivot_decision_id == "approval-request-1"
    finally:
        conn.close()


def test_supervised_approved_plan_can_expand_scope_capabilities_and_funded_budget(tmp_path):
    from bananloop.contracts import CapabilityPolicyV2

    setup = _setup(tmp_path, mode="SUPERVISED")
    expanded = dict(setup["child_spec"])
    expanded["goal"] = "Explore a separately approved subsystem"
    expanded["budget"] = dict(expanded["budget"], max_agent_calls=30)
    scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/new-area",),
        max_child_depth=1, max_child_campaigns=1,
    )
    expanded["autonomy"] = {
        "mode": "SUPERVISED", "scope_digest": scope.content_digest(),
        "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
        "capability_policy": CapabilityPolicyV2(
            tools=("read_file", "write_file"), read_paths=("src/new-area/**",),
            write_paths=("src/new-area/**",), backend="ISOLATED",
        ).model_dump(mode="json"),
    }
    path = "src/new-area/model.py"
    proposal = PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=2,
        current_architecture="existing subsystem", proposed_architecture="new subsystem",
        rationale="Supervised expansion is explicit in the plan.",
        expected_effect="Evaluate a separate implementation path.", changed_scope=(path,),
        resources={"additional_agent_calls": 10},
        execution_plan=_execution_plan(expanded, (path,)),
    )
    approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="expanded-approval",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    saga = _saga(setup)
    saga.record_pivot_proposal(proposal)
    saga.record_pivot_decision(approval)
    created = saga.create_child(
        idempotency_key="approved-expansion", proposal=proposal,
        child_spec=expanded, approval=approval,
    )
    assert created["state"] == "ACTIVE"
    child_spec = _parse_v2_spec(expanded)
    assert child_spec.budget.max_agent_calls == 30
    assert child_spec.autonomy.scope.allowed_paths == ("src/new-area",)
    # The expanded vector is escrowed from the live root account; the parent's
    # frozen spec remains unchanged by the child's supervised authorization.
    assert setup["parent_spec"].budget.max_agent_calls == 20


def test_full_auto_denies_the_same_explicit_scope_expansion_before_escrow(tmp_path):
    setup = _setup(tmp_path, mode="FULL_AUTO")
    expanded = dict(setup["child_spec"])
    expanded["goal"] = "Unauthorized Full Auto expansion"
    expanded["budget"] = dict(expanded["budget"], max_agent_calls=30)
    scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/new-area",),
        max_child_depth=1, max_child_campaigns=1,
    )
    expanded["autonomy"] = {
        "mode": "FULL_AUTO", "scope_digest": scope.content_digest(),
        "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
    }
    path = "src/new-area/model.py"
    proposal = PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=2,
        current_architecture="existing subsystem", proposed_architecture="expanded subsystem",
        rationale="The proposal binds these broader changes but Full Auto cannot approve them.",
        expected_effect="Evaluate a separate implementation path.", changed_scope=(path,),
        resources={"additional_agent_calls": 10},
        execution_plan=_execution_plan(expanded, (path,)),
    )
    with pytest.raises(PivotSagaError, match="Full Auto child autonomy exceeds"):
        _saga(setup).create_child(
            idempotency_key="auto-expansion-denied", proposal=proposal,
            child_spec=expanded,
        )
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()


def test_supervised_plan_cannot_exceed_live_budget_envelope(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    ledger = setup["registry"].budget_ledger()
    ledger.reserve_action(
        reservation_id="prior-parent-work", campaign_id=setup["parent_id"],
        action_id="prior-parent-action", resource="agent_calls", amount=80,
        idempotency_key="prior-parent-reservation",
    )
    ledger.settle_action(
        "prior-parent-work", actual=80, quality="actual", receipt_digest="a" * 64,
        fence=0,
    )
    ledger.close()
    expanded = dict(setup["child_spec"])
    expanded["budget"] = dict(expanded["budget"], max_agent_calls=30)
    path = "src/optimizer/model/ranker.py"
    proposal = PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=2,
        current_architecture="baseline", proposed_architecture="expanded budget",
        rationale="Approval cannot create new budget.", expected_effect="bounded execution",
        changed_scope=(path,), resources={"additional_agent_calls": 10},
        execution_plan=_execution_plan(expanded, (path,)),
    )
    approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="insufficient-budget-approval",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    saga = _saga(setup)
    saga.record_pivot_proposal(proposal)
    saga.record_pivot_decision(approval)
    with pytest.raises(PivotSagaError, match="budget|cap|envelope"):
        saga.create_child(
            idempotency_key="insufficient-budget", proposal=proposal,
            child_spec=expanded, approval=approval,
        )
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()


def test_supervised_nested_pivot_cannot_exceed_parent_allocation_even_with_root_funds(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")

    # Create a funded first-level campaign whose ledger allocation caps the
    # nested parent's agent calls at 20 while the project root still has 100.
    first_child = dict(setup["child_spec"])
    first_scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/optimizer/model",),
        max_child_depth=2, max_child_campaigns=1,
    )
    first_child["autonomy"] = {
        "mode": "SUPERVISED", "scope_digest": first_scope.content_digest(),
        "scope_version": first_scope.version, "scope": first_scope.model_dump(mode="json"),
    }
    first_proposal = _proposal_for_child(setup, first_child)
    first_approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="nested-parent-approval",
        proposal_id=first_proposal.proposal_id, digest=first_proposal.digest,
        decision="approve",
    )
    root_saga = _saga(setup)
    root_saga.record_pivot_proposal(first_proposal)
    root_saga.record_pivot_decision(first_approval)
    first_receipt = root_saga.create_child(
        idempotency_key="create-nested-parent", proposal=first_proposal,
        child_spec=first_child, approval=first_approval,
    )
    nested_parent_id = first_receipt["child_campaign_id"]
    nested_parent_dir, _ = setup["registry"].resolve(nested_parent_id, activate=False)
    nested_parent_db = nested_parent_dir / "campaign.sqlite3"
    with sqlite3.connect(nested_parent_db) as conn:
        conn.execute("UPDATE campaigns SET status='PAUSED_NEEDS_INPUT' WHERE id=1")

    with sqlite3.connect(nested_parent_db) as conn:
        nested_parent_payload = conn.execute(
            "SELECT spec_json FROM campaigns WHERE id=1",
        ).fetchone()[0]
    nested_parent_spec = _parse_v2_spec(nested_parent_payload)
    expanded = dict(setup["child_spec"])
    expanded["goal"] = "Nested child with explicitly approved larger budget"
    expanded["budget"] = dict(expanded["budget"], max_agent_calls=30)
    path = "src/optimizer/model/ranker.py"
    nested_setup = {
        **setup,
        "parent_id": nested_parent_id,
        "parent_db": nested_parent_db,
        "parent_spec": nested_parent_spec,
    }
    proposal = _proposal_for_child(nested_setup, expanded)
    approval = PivotDecisionCommand(
        campaign_id=nested_parent_id, request_id="nested-child-approval",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    nested_saga = V2PivotSaga(
        repo=setup["repo"], state_root=setup["state_root"],
        parent_campaign_id=nested_parent_id,
    )
    nested_saga.record_pivot_proposal(proposal)
    nested_saga.record_pivot_decision(approval)

    ledger = setup["registry"].budget_ledger()
    try:
        root_agent = ledger.snapshot(setup["parent_id"])["agent_calls"]
        assert root_agent["cap"] == 100
        assert root_agent["reserved"] == 20
        assert root_agent["spent"] == 0
        with pytest.raises(PivotSagaError, match="budget|cap|envelope"):
            nested_saga.create_child(
                idempotency_key="nested-expansion-over-parent-allocation",
                proposal=proposal, child_spec=expanded, approval=approval,
            )
        allocations = ledger.conn.execute(
            "SELECT COUNT(*) FROM child_allocations",
        ).fetchone()[0]
        assert allocations == 1
        root_agent = ledger.snapshot(setup["parent_id"])["agent_calls"]
        assert root_agent["cap"] == 100
        assert root_agent["reserved"] == 20
        assert root_agent["spent"] == 0
    finally:
        ledger.close()


def test_v2_human_steering_api_records_durable_proposal_and_decision(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    server = create_server(str(setup["repo"]), port=0, token="v2-api-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1/campaigns/{setup['parent_id']}"
    try:
        proposal_body = setup["proposal"].model_dump(mode="json")
        request = urllib.request.Request(
            base + "/v2/pivot-proposals",
            data=json.dumps(proposal_body).encode(),
            headers={
                "Authorization": "Bearer v2-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": setup["proposal"].proposal_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            proposal_receipt = json.loads(response.read())
        assert proposal_receipt["status"] == "PENDING"
        command = PivotDecisionCommand(
            campaign_id=setup["parent_id"], request_id="api-approval-1",
            proposal_id=setup["proposal"].proposal_id,
            digest=setup["proposal"].digest, decision="approve",
        )
        request = urllib.request.Request(
            base + "/v2/pivot-decisions",
            data=json.dumps(command.model_dump(mode="json")).encode(),
            headers={
                "Authorization": "Bearer v2-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": command.request_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            decision_receipt = json.loads(response.read())
        assert decision_receipt["status"] == "APPROVED"
        request = urllib.request.Request(
            base + "/v2/pivot-executions",
            data=json.dumps({
                "proposal_id": setup["proposal"].proposal_id,
                "digest": setup["proposal"].digest,
            }).encode(),
            headers={
                "Authorization": "Bearer v2-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": "api-approved-pivot",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            child = json.loads(response.read())
        assert child["state"] == "ACTIVE"
        replay = CampaignApplication(
            setup["repo"], campaign_id=setup["parent_id"],
        ).v2_execute_pivot(
            setup["proposal"].proposal_id, setup["proposal"].digest,
            "api-approved-pivot",
        )
        assert replay["child_campaign_id"] == child["child_campaign_id"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_v2_human_steering_cli_records_proposal_and_decision(tmp_path, capsys):
    import argparse
    from bananloop.cli import cmd_v2_pivot_decision, cmd_v2_pivot_execute, cmd_v2_pivot_proposal

    setup = _setup(tmp_path, mode="SUPERVISED")
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(
        json.dumps(setup["proposal"].model_dump(mode="json")), encoding="utf-8",
    )
    assert cmd_v2_pivot_proposal(argparse.Namespace(
        repo=str(setup["repo"]), campaign_id=setup["parent_id"],
        input=str(proposal_file),
    )) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PENDING"
    assert cmd_v2_pivot_decision(argparse.Namespace(
        repo=str(setup["repo"]), campaign_id=setup["parent_id"],
        request_id="cli-approval-1", proposal_id=setup["proposal"].proposal_id,
        digest=setup["proposal"].digest, decision="approve", on_reject=None,
    )) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "APPROVED"
    assert cmd_v2_pivot_execute(argparse.Namespace(
        repo=str(setup["repo"]), campaign_id=setup["parent_id"],
        proposal_id=setup["proposal"].proposal_id, digest=setup["proposal"].digest,
        idempotency_key="cli-approved-pivot",
    )) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ACTIVE"


def test_full_auto_application_and_cli_execute_persisted_plan(tmp_path, capsys):
    import argparse
    from bananloop.cli import cmd_v2_pivot_execute, cmd_v2_pivot_proposal

    app_root = tmp_path / "app"
    app_root.mkdir()
    app_setup = _setup(app_root, mode="FULL_AUTO")
    app = CampaignApplication(app_setup["repo"], campaign_id=app_setup["parent_id"])
    app.v2_record_pivot_proposal(app_setup["proposal"])
    app_receipt = app.v2_execute_pivot(
        app_setup["proposal"].proposal_id, app_setup["proposal"].digest,
        "full-auto-app-key",
    )
    assert app_receipt["state"] == "ACTIVE"

    cli_root = tmp_path / "cli"
    cli_root.mkdir()
    cli_setup = _setup(cli_root, mode="FULL_AUTO")
    proposal_file = tmp_path / "full-auto-proposal.json"
    proposal_file.write_text(
        json.dumps(cli_setup["proposal"].model_dump(mode="json")), encoding="utf-8",
    )
    assert cmd_v2_pivot_proposal(argparse.Namespace(
        repo=str(cli_setup["repo"]), campaign_id=cli_setup["parent_id"],
        input=str(proposal_file),
    )) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PENDING"
    assert cmd_v2_pivot_execute(argparse.Namespace(
        repo=str(cli_setup["repo"]), campaign_id=cli_setup["parent_id"],
        proposal_id=cli_setup["proposal"].proposal_id,
        digest=cli_setup["proposal"].digest,
        idempotency_key="full-auto-cli-key",
    )) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ACTIVE"


def test_application_execute_requires_persisted_approval_and_reloads_for_replay(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    app.v2_record_pivot_proposal(setup["proposal"])
    with pytest.raises(StoreError, match="no matching durable Supervised approval"):
        app.v2_execute_pivot(
            setup["proposal"].proposal_id, setup["proposal"].digest,
            "approval-required-before-execute",
        )

    approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="restart-approval",
        proposal_id=setup["proposal"].proposal_id,
        digest=setup["proposal"].digest, decision="approve",
    )
    app.v2_pivot_decision(approval)
    restarted_app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    first = restarted_app.v2_execute_pivot(
        setup["proposal"].proposal_id, setup["proposal"].digest,
        "restart-replay-key",
    )
    reloaded_app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    replay = reloaded_app.v2_execute_pivot(
        setup["proposal"].proposal_id, setup["proposal"].digest,
        "restart-replay-key",
    )
    assert first["child_campaign_id"] == replay["child_campaign_id"]
    assert first["allocation_id"] == replay["allocation_id"]


def test_application_rejected_or_stale_approval_and_wrong_digest_fail_before_escrow(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    app.v2_record_pivot_proposal(setup["proposal"])

    rejected = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="rejected-approval",
        proposal_id=setup["proposal"].proposal_id, digest=setup["proposal"].digest,
        decision="reject", rejection_action="continue_old_plan",
    )
    app.v2_pivot_decision(rejected)
    with pytest.raises(StoreError, match="no matching durable Supervised approval"):
        app.v2_execute_pivot(
            setup["proposal"].proposal_id, setup["proposal"].digest,
            "rejected-approval-execution",
        )
    with pytest.raises(StoreError, match="identity does not match"):
        app.v2_execute_pivot(
            setup["proposal"].proposal_id, "0" * 64,
            "wrong-digest-execution",
        )

    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()
    conn = sqlite3.connect(setup["parent_db"])
    assert conn.execute("SELECT COUNT(*) FROM v2_pivot_sagas").fetchone()[0] == 0
    conn.close()


def test_application_conflicting_idempotency_key_cannot_execute_another_plan(tmp_path):
    setup = _setup(tmp_path, mode="SUPERVISED")
    app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    app.v2_record_pivot_proposal(setup["proposal"])
    first_approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="first-plan-approval",
        proposal_id=setup["proposal"].proposal_id, digest=setup["proposal"].digest,
        decision="approve",
    )
    app.v2_pivot_decision(first_approval)
    first = app.v2_execute_pivot(
        setup["proposal"].proposal_id, setup["proposal"].digest, "reused-execute-key",
    )

    alternate_child = dict(setup["child_spec"])
    alternate_child["goal"] = "Different approved child plan"
    alternate = _proposal_for_child(setup, alternate_child)
    app.v2_record_pivot_proposal(alternate)
    second_approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="second-plan-approval",
        proposal_id=alternate.proposal_id, digest=alternate.digest, decision="approve",
    )
    app.v2_pivot_decision(second_approval)
    with pytest.raises(StoreError, match="reused with different content"):
        app.v2_execute_pivot(
            alternate.proposal_id, alternate.digest, "reused-execute-key",
        )

    ledger = setup["registry"].budget_ledger()
    try:
        assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 1
        assert first["child_campaign_id"]
    finally:
        ledger.close()


def test_application_concurrent_execute_replay_creates_one_child(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    setup = _setup(tmp_path, mode="SUPERVISED")
    app = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
    app.v2_record_pivot_proposal(setup["proposal"])
    approval = PivotDecisionCommand(
        campaign_id=setup["parent_id"], request_id="concurrent-approval",
        proposal_id=setup["proposal"].proposal_id, digest=setup["proposal"].digest,
        decision="approve",
    )
    app.v2_pivot_decision(approval)
    barrier = Barrier(2)

    def execute():
        instance = CampaignApplication(setup["repo"], campaign_id=setup["parent_id"])
        barrier.wait(timeout=10)
        return instance.v2_execute_pivot(
            setup["proposal"].proposal_id, setup["proposal"].digest,
            "concurrent-execute-key",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert results[0]["child_campaign_id"] == results[1]["child_campaign_id"]
    assert results[0]["allocation_id"] == results[1]["allocation_id"]
    ledger = setup["registry"].budget_ledger()
    try:
        assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 1
    finally:
        ledger.close()


def test_pivot_rejects_activation_control_ahead_of_durable_child_state(tmp_path):
    setup = _setup(tmp_path)

    def interrupt_after_child_init(step):
        if step == "AFTER_CHILD_INITIALIZE":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        _saga(setup, interrupt_after_child_init).create_child(
            idempotency_key="corrupt-control-state",
            proposal=setup["proposal"], child_spec=setup["child_spec"],
        )

    conn = sqlite3.connect(setup["parent_db"])
    conn.execute(
        "UPDATE v2_pivot_saga_control SET state='ACTIVATING', control_digest=? "
        "WHERE idempotency_key=?",
        ("0" * 64, "corrupt-control-state"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PivotSagaError, match="advanced before child initialization"):
        _saga(setup).create_child(
            idempotency_key="corrupt-control-state",
            proposal=setup["proposal"], child_spec=setup["child_spec"],
        )


def test_full_auto_api_executes_persisted_plan_without_approval_body(tmp_path):
    setup = _setup(tmp_path, mode="FULL_AUTO")
    server = create_server(str(setup["repo"]), port=0, token="full-auto-api-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1/campaigns/{setup['parent_id']}"
    try:
        request = urllib.request.Request(
            base + "/v2/pivot-proposals",
            data=json.dumps(setup["proposal"].model_dump(mode="json")).encode(),
            headers={
                "Authorization": "Bearer full-auto-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": setup["proposal"].proposal_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            proposal_receipt = json.loads(response.read())
        assert proposal_receipt["automatic_execution"] is False
        request = urllib.request.Request(
            base + "/v2/pivot-executions",
            data=json.dumps({
                "proposal_id": setup["proposal"].proposal_id,
                "digest": setup["proposal"].digest,
            }).encode(),
            headers={
                "Authorization": "Bearer full-auto-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": "full-auto-api-execution",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            child = json.loads(response.read())
        assert child["state"] == "ACTIVE"
        assert child["child_campaign_id"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_supervised_api_fails_closed_without_approval_and_replays_concurrently(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from urllib.error import HTTPError

    setup = _setup(tmp_path, mode="SUPERVISED")
    server = create_server(str(setup["repo"]), port=0, token="supervised-api-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1/campaigns/{setup['parent_id']}"

    def post(path, payload, key):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer supervised-api-token",
                "Content-Type": "application/json",
                "Idempotency-Key": key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    try:
        proposal = setup["proposal"].model_dump(mode="json")
        post("/v2/pivot-proposals", proposal, setup["proposal"].proposal_id)
        execute_payload = {
            "proposal_id": setup["proposal"].proposal_id,
            "digest": setup["proposal"].digest,
        }
        with pytest.raises(HTTPError) as rejected:
            post("/v2/pivot-executions", execute_payload, "missing-api-approval")
        assert rejected.value.code == 409
        with pytest.raises(HTTPError) as stale:
            post("/v2/pivot-executions", {
                **execute_payload, "digest": "0" * 64,
            }, "stale-api-digest")
        assert stale.value.code == 409

        approval = PivotDecisionCommand(
            campaign_id=setup["parent_id"], request_id="supervised-api-approval",
            proposal_id=setup["proposal"].proposal_id,
            digest=setup["proposal"].digest, decision="approve",
        )
        post("/v2/pivot-decisions", approval.model_dump(mode="json"), approval.request_id)
        barrier = Barrier(2)

        def execute():
            barrier.wait(timeout=5)
            return post("/v2/pivot-executions", execute_payload, "concurrent-api-key")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(), range(2)))
        assert results[0]["child_campaign_id"] == results[1]["child_campaign_id"]
        assert results[0]["allocation_id"] == results[1]["allocation_id"]
        ledger = setup["registry"].budget_ledger()
        try:
            assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 1
        finally:
            ledger.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_saga_idempotency_key_cannot_be_reused_for_different_child_spec(tmp_path):
    setup = _setup(tmp_path)
    _saga(setup).create_child(
        idempotency_key="same-pivot-key", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    changed = dict(setup["child_spec"])
    changed["goal"] = "different child goal"
    with pytest.raises(PivotSagaError, match="immutable approved execution plan"):
        _saga(setup).create_child(
            idempotency_key="same-pivot-key", proposal=setup["proposal"],
            child_spec=changed,
        )


def test_tampered_child_execution_plan_fails_before_saga_or_escrow(tmp_path):
    setup = _setup(tmp_path)
    changed = dict(setup["child_spec"])
    changed["goal"] = "Caller tries to replace the approved goal"
    with pytest.raises(PivotSagaError, match="immutable approved execution plan"):
        _saga(setup).create_child(
            idempotency_key="tampered-plan", proposal=setup["proposal"],
            child_spec=changed,
        )
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()
    conn = sqlite3.connect(setup["parent_db"])
    assert conn.execute("SELECT COUNT(*) FROM v2_pivot_sagas").fetchone()[0] == 0
    conn.close()


def test_legacy_pivot_proposal_digest_is_unchanged_but_cannot_execute(tmp_path):
    setup = _setup(tmp_path)
    legacy = PivotProposal.build(
        spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=2,
        current_architecture="baseline", proposed_architecture="legacy proposal",
        rationale="historical record", expected_effect="audit only",
        changed_scope=("src/optimizer/model/legacy.py",),
        resources={"additional_agent_calls": 1},
    )
    payload = legacy.model_dump(mode="json")
    payload.pop("execution_plan")
    payload.pop("proposal_id")
    payload.pop("digest")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert legacy.digest == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with pytest.raises(PivotSagaError, match="audit-only"):
        _saga(setup).create_child(
            idempotency_key="legacy-pivot", proposal=legacy,
            child_spec=setup["child_spec"],
        )
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute("SELECT COUNT(*) FROM child_allocations").fetchone()[0] == 0
    ledger.close()


@pytest.mark.parametrize("corruption", [
    "registry_missing",
    "registry_goal",
    "registry_origin",
    "registry_path",
    "allocation_binding",
    "allocation_idempotency",
    "allocation_parent",
    "allocation_spec_hash",
    "allocation_caps",
    "allocation_state",
    "child_db_missing",
    "child_spec",
    "child_lineage",
])
def test_active_pivot_replay_verifies_exact_registry_ledger_and_child_state(tmp_path, corruption):
    setup = _setup(tmp_path)
    result = _saga(setup).create_child(
        idempotency_key="verified-active-pivot", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    registry_conn = setup["registry"]._connect()
    try:
        if corruption == "registry_missing":
            registry_conn.execute(
                "DELETE FROM campaign_index WHERE campaign_id=?",
                (result["child_campaign_id"],),
            )
        elif corruption == "registry_goal":
            registry_conn.execute(
                "UPDATE campaign_index SET goal_fingerprint=? WHERE campaign_id=?",
                ("0" * 64, result["child_campaign_id"]),
            )
        elif corruption == "registry_origin":
            registry_conn.execute(
                "UPDATE campaign_index SET origin='legacy',state_path='.' WHERE campaign_id=?",
                (result["child_campaign_id"],),
            )
        elif corruption == "registry_path":
            registry_conn.execute(
                "UPDATE campaign_index SET state_path=? WHERE campaign_id=?",
                ("campaigns/c-" + "f" * 32, result["child_campaign_id"]),
            )
    finally:
        registry_conn.close()

    ledger = setup["registry"].budget_ledger()
    try:
        if corruption == "allocation_binding":
            ledger.conn.execute(
                "UPDATE child_allocations SET child_id=? WHERE allocation_id=?",
                ("c-" + "f" * 32, result["allocation_id"]),
            )
        elif corruption == "allocation_idempotency":
            ledger.conn.execute(
                "UPDATE child_allocations SET idempotency_key=? WHERE allocation_id=?",
                ("tampered-request-key", result["allocation_id"]),
            )
        elif corruption == "allocation_parent":
            ledger.conn.execute(
                "UPDATE child_allocations SET parent_id=? WHERE allocation_id=?",
                ("c-" + "e" * 32, result["allocation_id"]),
            )
        elif corruption == "allocation_spec_hash":
            ledger.conn.execute(
                "UPDATE child_allocations SET spec_hash=? WHERE allocation_id=?",
                ("f" * 64, result["allocation_id"]),
            )
        elif corruption == "allocation_caps":
            ledger.conn.execute(
                "UPDATE child_allocation_resources SET cap=cap+1 "
                "WHERE allocation_id=? AND resource='agent_calls'",
                (result["allocation_id"],),
            )
        elif corruption == "allocation_state":
            ledger.conn.execute(
                "UPDATE child_allocations SET state='CLOSED' WHERE allocation_id=?",
                (result["allocation_id"],),
            )
    finally:
        ledger.close()

    child_dir, _ = setup["registry"].resolve(
        result["child_campaign_id"], activate=False,
    ) if corruption not in {"registry_missing", "registry_goal", "registry_origin", "registry_path"} else (None, None)
    if corruption == "child_db_missing":
        (child_dir / "campaign.sqlite3").unlink()
    elif corruption in {"child_spec", "child_lineage"}:
        child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
        try:
            raw = child_conn.execute(
                "SELECT spec_json FROM campaigns WHERE id=1",
            ).fetchone()[0]
            document = json.loads(raw)
            if corruption == "child_spec":
                document["goal"] = "tampered child spec"
                new_hash = None
            else:
                document["parent"]["pivot_proposal_digest"] = "f" * 64
                changed_spec = parse_campaign_spec_document(document)
                new_hash = changed_spec.approved_hash()
            changed_json = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
            if new_hash is None:
                child_conn.execute(
                    "UPDATE campaigns SET spec_json=? WHERE id=1", (changed_json,),
                )
            else:
                child_conn.execute(
                    "UPDATE campaigns SET spec_json=?,spec_hash=? WHERE id=1",
                    (changed_json, new_hash),
                )
            child_conn.commit()
        finally:
            child_conn.close()

    registry_conn = setup["registry"]._connect()
    try:
        registry_count_before = registry_conn.execute(
            "SELECT COUNT(*) FROM campaign_index",
        ).fetchone()[0]
        active_campaign_before = registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0]
    finally:
        registry_conn.close()
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        active_events_before = parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
        ).fetchone()[0]
        assert parent_conn.execute(
            "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='verified-active-pivot'",
        ).fetchone()[0] == "ACTIVE"
    finally:
        parent_conn.close()

    with pytest.raises(PivotSagaError):
        _saga(setup).create_child(
            idempotency_key="verified-active-pivot", proposal=setup["proposal"],
            child_spec=setup["child_spec"],
        )

    registry_conn = setup["registry"]._connect()
    try:
        assert registry_conn.execute(
            "SELECT COUNT(*) FROM campaign_index",
        ).fetchone()[0] == registry_count_before
        assert registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0] == active_campaign_before
    finally:
        registry_conn.close()
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
        ).fetchone()[0] == active_events_before
        assert parent_conn.execute(
            "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='verified-active-pivot'",
        ).fetchone()[0] == "ACTIVE"
    finally:
        parent_conn.close()


def test_child_spec_is_verified_before_saga_transitions_to_active(tmp_path):
    setup = _setup(tmp_path)

    def corrupt_initialized_child(step):
        if step != "AFTER_CHILD_INITIALIZE":
            return
        entry = next(
            entry for entry, _ in setup["registry"].list()
            if entry.goal_summary == setup["child_spec"]["goal"]
        )
        child_dir, _ = setup["registry"].resolve(entry.campaign_id, activate=False)
        child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
        try:
            child_conn.execute(
                "UPDATE campaigns SET spec_hash=? WHERE id=1", ("0" * 64,),
            )
            child_conn.commit()
        finally:
            child_conn.close()

    with pytest.raises(PivotSagaError, match="spec or lineage"):
        _saga(setup, corrupt_initialized_child).create_child(
            idempotency_key="pre-active-verification", proposal=setup["proposal"],
            child_spec=setup["child_spec"],
        )
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        assert parent_conn.execute(
            "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='pre-active-verification'",
        ).fetchone()[0] == "CHILD_INITIALIZED"
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
        ).fetchone()[0] == 0
    finally:
        parent_conn.close()



def test_active_replay_accepts_fully_reconciled_closed_child_allocation(tmp_path):
    setup = _setup(tmp_path)
    result = _saga(setup).create_child(
        idempotency_key="terminal-pivot-replay", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    child_dir, _ = setup["registry"].resolve(
        result["child_campaign_id"], activate=False,
    )
    child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
    try:
        child_conn.execute(
            "UPDATE campaigns SET status='STOPPED' WHERE id=1",
        )
        child_conn.commit()
    finally:
        child_conn.close()

    ledger = setup["registry"].budget_ledger()
    ledger.close_child(result["allocation_id"], terminal_status="STOPPED")
    before = ledger.snapshot(setup["parent_id"])
    assert ledger.conn.execute(
        "SELECT state FROM child_allocations WHERE allocation_id=?",
        (result["allocation_id"],),
    ).fetchone()[0] == "CLOSED"
    ledger.close()

    registry_conn = setup["registry"]._connect()
    try:
        active_campaign_before = registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0]
    finally:
        registry_conn.close()
    replay = _saga(setup).create_child(
        idempotency_key="terminal-pivot-replay", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    assert replay["state"] == "ACTIVE"
    assert replay["child_campaign_id"] == result["child_campaign_id"]
    assert replay["allocation_id"] == result["allocation_id"]
    ledger = setup["registry"].budget_ledger()
    assert ledger.conn.execute(
        "SELECT state FROM child_allocations WHERE allocation_id=?",
        (result["allocation_id"],),
    ).fetchone()[0] == "CLOSED"
    assert ledger.snapshot(setup["parent_id"]) == before
    ledger.close()
    registry_conn = setup["registry"]._connect()
    try:
        assert registry_conn.execute(
            "SELECT active_campaign_id FROM project_meta WHERE id=1",
        ).fetchone()[0] == active_campaign_before
    finally:
        registry_conn.close()



def test_closed_allocation_cannot_activate_child_after_initialized_state_is_durable(tmp_path):
    setup = _setup(tmp_path)
    saga = _saga(setup)
    original_transition = saga._transition
    closed = False

    def transition_then_close(conn, idempotency_key, new_state, *, child_id=None):
        nonlocal closed
        original_transition(
            conn, idempotency_key, new_state, child_id=child_id,
        )
        if new_state != "CHILD_INITIALIZED" or closed:
            return
        closed = True
        saga_row = conn.execute(
            "SELECT child_id,allocation_id FROM v2_pivot_sagas WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        child_dir, _ = setup["registry"].resolve(saga_row["child_id"], activate=False)
        child_conn = sqlite3.connect(child_dir / "campaign.sqlite3")
        try:
            child_conn.execute("UPDATE campaigns SET status='STOPPED' WHERE id=1")
            child_conn.commit()
        finally:
            child_conn.close()
        ledger = setup["registry"].budget_ledger()
        try:
            # Inject a corrupted premature terminal allocation to prove the
            # activation verifier refuses to write PIVOT_CHILD_ACTIVE.
            ledger.conn.execute("BEGIN IMMEDIATE")
            ledger.conn.execute(
                "UPDATE child_allocations SET state='CLOSED' WHERE allocation_id=?",
                (saga_row["allocation_id"],),
            )
            ledger.conn.execute(
                "UPDATE child_allocation_gates SET state='CLOSED' WHERE allocation_id=?",
                (saga_row["allocation_id"],),
            )
            ledger.conn.commit()
        finally:
            ledger.close()

    saga._transition = transition_then_close
    with pytest.raises(PivotSagaError, match="budget allocation"):
        saga.create_child(
            idempotency_key="closed-before-active", proposal=setup["proposal"],
            child_spec=setup["child_spec"],
        )
    parent_conn = sqlite3.connect(setup["parent_db"])
    try:
        assert parent_conn.execute(
            "SELECT state FROM v2_pivot_sagas WHERE idempotency_key='closed-before-active'",
        ).fetchone()[0] == "CHILD_INITIALIZED"
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_CHILD_INITIALIZED'",
        ).fetchone()[0] == 1
        assert parent_conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
        ).fetchone()[0] == 0
    finally:
        parent_conn.close()
    ledger = setup["registry"].budget_ledger()
    try:
        assert ledger.conn.execute(
            "SELECT state FROM child_allocations WHERE parent_id=?",
            (setup["parent_id"],),
        ).fetchone()[0] == "CLOSED"
    finally:
        ledger.close()


@pytest.mark.parametrize("crash_at", [
    "AFTER_REGISTRY_RESERVE",
    "AFTER_ALLOCATION_BIND",
    "AFTER_CHILD_INITIALIZE",
])
def test_internal_cancel_proves_unused_child_and_tombstones_request(tmp_path, crash_at):
    setup = _setup(tmp_path)
    idem = "cancel-" + crash_at.lower().replace("_", "-")
    fired = False

    def fail_once(step):
        nonlocal fired
        if step == crash_at and not fired:
            fired = True
            raise RuntimeError("interrupted child creation")

    with pytest.raises(RuntimeError, match="interrupted"):
        _saga(setup, fail_once).create_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )
    request_id = "pivot-v2:" + hashlib.sha256(
        f"{setup['parent_id']}:{idem}:{setup['proposal'].digest}".encode()
    ).hexdigest()[:48]
    saga = _saga(setup)
    cancelled = saga._cancel_unactivated_child(
        idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
    )
    assert cancelled["state"] == "CANCELLED"
    replay = _saga(setup)._cancel_unactivated_child(
        idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
    )
    assert replay["state"] == "CANCELLED"

    ledger = setup["registry"].budget_ledger()
    try:
        allocation = ledger.conn.execute(
            "SELECT allocation_id,state FROM child_allocations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()
        assert allocation is not None and allocation["state"] == "CLOSED"
        gate = ledger.conn.execute(
            "SELECT state,cancellation_digest FROM child_allocation_gates WHERE allocation_id=?",
            (allocation["allocation_id"],),
        ).fetchone()
        assert gate["state"] == "CANCELLED"
        assert ledger.snapshot(setup["parent_id"])["agent_calls"]["reserved"] == 0
    finally:
        ledger.close()

    registry_conn = setup["registry"]._connect()
    try:
        assert registry_conn.execute(
            "SELECT COUNT(*) FROM campaign_request_tombstones WHERE allocation_id=?",
            (allocation["allocation_id"],),
        ).fetchone()[0] == 1
        parent_conn = sqlite3.connect(setup["parent_db"])
        try:
            assert parent_conn.execute(
                "SELECT state FROM v2_pivot_saga_control WHERE idempotency_key=?",
                (idem,),
            ).fetchone()[0] == "CANCELLED"
            assert parent_conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='PIVOT_CHILD_CANCELLED'",
            ).fetchone()[0] == 1
        finally:
            parent_conn.close()
    finally:
        registry_conn.close()

    with pytest.raises(RegistryError, match="tombstoned"):
        setup["registry"].reserve(setup["child_spec"]["goal"], request_id, activate=False)
    with pytest.raises(PivotSagaError, match="cancelled"):
        _saga(setup).create_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )


@pytest.mark.parametrize("crash_at", [
    "AFTER_CANCEL_CONTROL",
    "AFTER_CANCEL_LEDGER",
    "AFTER_CANCEL_TOMBSTONE",
])
def test_internal_cancel_replays_after_each_durable_boundary(tmp_path, crash_at):
    setup = _setup(tmp_path)
    idem = "cancel-replay-" + crash_at.lower().replace("_", "-")
    with pytest.raises(RuntimeError, match="process interruption"):
        _saga(setup, lambda step: (_ for _ in ()).throw(RuntimeError("process interruption"))
              if step == "AFTER_CHILD_INITIALIZE" else None).create_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )
    fired = False
    def fail_cancel_once(step):
        nonlocal fired
        if step == crash_at and not fired:
            fired = True
            raise RuntimeError("interrupted cancellation")
    with pytest.raises(RuntimeError, match="interrupted cancellation"):
        _saga(setup, fail_cancel_once)._cancel_unactivated_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )
    result = _saga(setup)._cancel_unactivated_child(
        idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
    )
    assert result["state"] == "CANCELLED"
    ledger = setup["registry"].budget_ledger()
    try:
        assert ledger.snapshot(setup["parent_id"])["agent_calls"]["reserved"] == 0
        assert ledger.conn.execute(
            "SELECT COUNT(*) FROM campaign_request_tombstones",
        ).fetchone()[0] == 1
    finally:
        ledger.close()


def test_activation_and_cancellation_share_one_way_control_cas(tmp_path):
    # Activation wins: cancellation is rejected after ACTIVATING.
    setup = _setup(tmp_path)
    idem = "cancel-race-activation-wins"
    with pytest.raises(RuntimeError):
        _saga(setup, lambda step: (_ for _ in ()).throw(RuntimeError())
              if step == "AFTER_CHILD_INITIALIZE" else None).create_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )
    conn = sqlite3.connect(setup["parent_db"], isolation_level=None)
    conn.row_factory = sqlite3.Row
    saga_row = conn.execute(
        "SELECT request_hash,allocation_id,child_id FROM v2_pivot_sagas WHERE idempotency_key=?",
        (idem,),
    ).fetchone()
    activation_digest = hashlib.sha256(json.dumps(
        {"key": idem, "request_hash": saga_row[0], "allocation_id": saga_row[1],
         "child_id": saga_row[2]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    _saga(setup)._begin_activation(conn, idem, activation_digest)
    conn.close()
    with pytest.raises(PivotSagaError, match="activation already won"):
        _saga(setup)._cancel_unactivated_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )

    # Cancellation wins: subsequent activation CAS cannot proceed.
    cancel_wins_root = tmp_path / "cancel-wins"
    cancel_wins_root.mkdir()
    setup = _setup(cancel_wins_root)
    idem = "cancel-race-cancellation-wins"
    with pytest.raises(RuntimeError):
        _saga(setup, lambda step: (_ for _ in ()).throw(RuntimeError())
              if step == "AFTER_CHILD_INITIALIZE" else None).create_child(
            idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
        )
    _saga(setup)._cancel_unactivated_child(
        idempotency_key=idem, proposal=setup["proposal"], child_spec=setup["child_spec"],
    )
    conn = sqlite3.connect(setup["parent_db"], isolation_level=None)
    conn.row_factory = sqlite3.Row
    with pytest.raises(PivotSagaError, match="blocked by cancellation"):
        _saga(setup)._begin_activation(conn, idem, "f" * 64)
    conn.close()
