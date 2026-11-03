from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bananloop.budget_ledger import BudgetLedger, BudgetLedgerError
from bananloop.contracts import (
    AutonomyScopeV2,
    PivotDecisionCommand,
    PivotProposal,
    RuntimePaths,
    parse_campaign_spec_document,
)
from bananloop.migrations import create_v5_graph_schema
from bananloop.registry import CampaignRegistry
from bananloop.v2_pivot_saga import PivotSagaError, V2PivotSaga
from bananloop.api import create_server
from bananloop.application import CampaignApplication
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
        conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,status TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO campaigns(id,spec_json,status) VALUES(1,?,'PAUSED_NEEDS_INPUT')",
            (json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    finally:
        conn.close()


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
    proposal = PivotProposal.build(
        spec_hash=parent_spec.approved_hash(), trigger_experiment_ordinal=1,
        current_architecture="baseline optimizer", proposed_architecture="model variant",
        rationale="The current model limits search quality.",
        expected_effect="Improve candidate ranking within the approved source scope.",
        changed_scope=("src/optimizer/model/ranker.py",),
        resources={"additional_agent_calls": 2, "additional_evaluator_calls": 1},
    )
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
    parent_hash = setup["parent_spec"].approved_hash()
    result = _saga(setup).create_child(
        idempotency_key="approved-protocol-handoff", proposal=setup["proposal"],
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
        idempotency_key="approved-protocol-handoff", proposal=setup["proposal"],
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
    result = _saga(setup).create_child(
        idempotency_key="corrupt-protocol-handoff", proposal=setup["proposal"],
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
            idempotency_key="corrupt-protocol-handoff", proposal=setup["proposal"],
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
    result = _saga(setup).create_child(
        idempotency_key="adapter-protocol-handoff", proposal=setup["proposal"],
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
    with pytest.raises(PivotSagaError, match="valid parent protocol approval"):
        _saga(setup).create_child(
            idempotency_key="missing-protocol-approval", proposal=setup["proposal"],
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
    proposals = [
        PivotProposal.build(
            spec_hash=setup["parent_spec"].approved_hash(), trigger_experiment_ordinal=i,
            current_architecture="baseline", proposed_architecture=f"model-{i}",
            rationale=f"bounded in-scope proposal {i}", expected_effect="improve ranking",
            changed_scope=(f"src/optimizer/model/ranker{i}.py",),
            resources={"additional_agent_calls": 1},
        )
        for i in (1, 2)
    ]
    child_specs = []
    for i in (1, 2):
        child = dict(setup["child_spec"])
        child["goal"] = f"Refine optimizer model {i}"
        child_specs.append(child)
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
    )
    with pytest.raises(PivotSagaError, match="outside its versioned scope"):
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
        child = _saga(setup).create_child(
            idempotency_key="api-approved-pivot",
            proposal=setup["proposal"], child_spec=setup["child_spec"],
            approval=command,
        )
        assert child["state"] == "ACTIVE"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_v2_human_steering_cli_records_proposal_and_decision(tmp_path, capsys):
    import argparse
    from bananloop.cli import cmd_v2_pivot_decision, cmd_v2_pivot_proposal

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


def test_saga_idempotency_key_cannot_be_reused_for_different_child_spec(tmp_path):
    setup = _setup(tmp_path)
    _saga(setup).create_child(
        idempotency_key="same-pivot-key", proposal=setup["proposal"],
        child_spec=setup["child_spec"],
    )
    changed = dict(setup["child_spec"])
    changed["goal"] = "different child goal"
    with pytest.raises(PivotSagaError, match="reused with different content"):
        _saga(setup).create_child(
            idempotency_key="same-pivot-key", proposal=setup["proposal"],
            child_spec=changed,
        )


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
            ledger.close_child(saga_row["allocation_id"], terminal_status="STOPPED")
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
