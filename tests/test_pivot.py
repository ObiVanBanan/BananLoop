from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from bananloop.agents.base import AgentError
from bananloop.agents.codex import PIVOT_ADVICE_SCHEMA, CodexAdapter
from bananloop.agents.pi import PiAdapter
from bananloop.contracts import (
    CampaignSpec, PivotDecisionCommand, PivotProposal, PivotResourceRequest,
    Direction, MetricSpec, PortfolioRecord, RuntimePaths, SpecLineage,
)
from bananloop.engine import Engine, EngineError
from bananloop.operator_control import submit_pivot_decision
from bananloop.registry import CampaignRegistry
from bananloop.store import CampaignStore, StoreError
from test_store_resources import make_store
from test_engine import make_engine


def _approved_child_fixture(tmp_path, *, decision="approve"):
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = CampaignRegistry(repo, RuntimePaths.for_repo(repo, tmp_path / "runtime"))
    parent_entry, parent_dir, _ = registry.reserve("parent goal", "parent-request")
    parent = CampaignStore(parent_dir / "campaign.sqlite3")
    parent_spec = CampaignSpec(
        goal="parent goal", source_repo=str(repo), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(
            name="quality", direction=Direction.MAXIMIZE,
            target=1.0, min_improvement=0.1,
        ),
    )
    parent.initialize(parent_spec)
    parent.update_campaign(
        status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT",
        baseline_json='{"metrics":{"quality":0.5}}', baseline_snapshot="baseline-snapshot",
        champion_json='{"metrics":{"quality":0.7}}', champion_snapshot="champion-snapshot",
        evaluator_calls=3, experiment_count=2,
    )
    proposal = PivotProposal.build(
        spec_hash=parent_spec.approved_hash(), trigger_experiment_ordinal=1,
        current_architecture="single stage", proposed_architecture="bounded two stage",
        rationale="plateau", expected_effect="improve quality",
        changed_scope=["src/new.py"],
        resources=PivotResourceRequest(
            additional_agent_calls=1, additional_evaluator_calls=1,
            notes="one bounded iteration",
        ),
    )
    parent.save_pivot_proposal(proposal, "operator approval required")
    command = PivotDecisionCommand(
        campaign_id=parent_entry.campaign_id, request_id="pivot-decision-1",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision=decision,
    )
    decision_row = parent.apply_pivot_decision(parent_entry.campaign_id, command)
    child = parent_spec.model_copy(update={
        "goal": "parent goal with approved retrieval architecture",
        "editable_scope": ["src/new.py"],
    })
    lineage = SpecLineage(
        parent_campaign_id=parent_entry.campaign_id,
        parent_spec_hash=parent_spec.approved_hash(),
        proposal_id=proposal.proposal_id, proposal_digest=proposal.digest,
        decision_request_id=decision_row["request_id"],
    )
    confirmed_hash = child.model_copy(update={"spec_lineage": lineage}).approved_hash()
    return parent, parent_entry, parent_spec, proposal, child, confirmed_hash, registry


def test_approved_pivot_creates_child_with_immutable_parent_lineage(tmp_path):
    parent, entry, parent_spec, proposal, child, digest, registry = _approved_child_fixture(tmp_path)
    try:
        before = parent.campaign()
        before_events = parent.events()
        child_id, paths, approved = Engine.create_approved_pivot_child(
            parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
            child, digest, registry=registry,
        )
        child_store = CampaignStore(paths.db)
        try:
            linked = child_store.spec()
            assert linked.approved_hash() == digest
            assert linked.spec_lineage.parent_campaign_id == entry.campaign_id
            assert linked.spec_lineage.parent_spec_hash == parent_spec.approved_hash()
            assert linked.spec_lineage.proposal_id == proposal.proposal_id
            assert linked.spec_lineage.proposal_digest == proposal.digest
            assert linked.spec_lineage.decision_request_id == "pivot-decision-1"
            assert child_store.campaign()["status"] == "READY"
        finally:
            child_store.close()
        # Exact replay is idempotent and returns the same initialized child.
        replay_id, replay_paths, _ = Engine.create_approved_pivot_child(
            parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
            child, digest, registry=registry,
        )
        assert replay_id == child_id
        assert replay_paths.db == paths.db
        after = parent.campaign()
        assert after["spec_hash"] == before["spec_hash"] == parent_spec.approved_hash()
        assert after["champion_snapshot"] == before["champion_snapshot"]
        assert parent.events() == before_events
        assert approved.spec_lineage is not None
    finally:
        parent.close()


def test_pivot_child_requires_exact_confirmed_child_hash(tmp_path):
    parent, entry, _, proposal, child, digest, registry = _approved_child_fixture(tmp_path)
    try:
        with pytest.raises(EngineError, match="confirmed child spec digest"):
            Engine.create_approved_pivot_child(
                parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
                child, "0" * 64, registry=registry,
            )
        assert list((registry.root / "campaigns").glob("*/campaign.sqlite3")) == [parent.path]
    finally:
        parent.close()


def test_orphaned_child_reservation_is_bound_to_confirmed_spec_hash(tmp_path):
    parent, entry, parent_spec, proposal, child, digest, registry = _approved_child_fixture(tmp_path)
    try:
        lineage = SpecLineage(
            parent_campaign_id=entry.campaign_id,
            parent_spec_hash=parent_spec.approved_hash(),
            proposal_id=proposal.proposal_id,
            proposal_digest=proposal.digest,
            decision_request_id="pivot-decision-1",
        )
        orphan_hash = child.model_copy(update={"spec_lineage": lineage}).approved_hash()
        orphan_request = (
            f"pivot-child:{entry.campaign_id}:{proposal.proposal_id}:"
            f"{proposal.digest}:{orphan_hash}"
        )
        orphan_entry, orphan_dir, created = registry.reserve(child.goal, orphan_request)
        assert created is True
        assert not (orphan_dir / "campaign.sqlite3").exists()

        changed_child = child.model_copy(update={"goal": "different operator confirmed child goal"})
        changed_hash = changed_child.model_copy(update={"spec_lineage": lineage}).approved_hash()
        child_id, child_paths, _ = Engine.create_approved_pivot_child(
            parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
            changed_child, changed_hash, registry=registry,
        )
        assert child_id != orphan_entry.campaign_id
        assert child_paths.db.exists()
        assert not (orphan_dir / "campaign.sqlite3").exists()
    finally:
        parent.close()


def test_rejected_pivot_cannot_create_child_or_lineage(tmp_path):
    parent, entry, _, proposal, child, digest, registry = _approved_child_fixture(tmp_path, decision="reject")
    try:
        with pytest.raises(StoreError, match="approval is missing"):
            Engine.create_approved_pivot_child(
                parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
                child, digest, registry=registry,
            )
        assert list((registry.root / "campaigns").glob("*/campaign.sqlite3")) == [parent.path]
        assert child.spec_lineage is None
    finally:
        parent.close()


def test_tampered_approved_proposal_cannot_create_child(tmp_path):
    parent, entry, _, proposal, child, digest, registry = _approved_child_fixture(tmp_path)
    try:
        parent.conn.execute(
            "UPDATE pivot_proposals SET proposal_json=? WHERE proposal_id=?",
            ("{}", proposal.proposal_id),
        )
        parent.conn.commit()
        with pytest.raises(StoreError, match="approval is missing"):
            Engine.create_approved_pivot_child(
                parent, entry.campaign_id, proposal.proposal_id, proposal.digest,
                child, digest, registry=registry,
            )
        assert list((registry.root / "campaigns").glob("*/campaign.sqlite3")) == [parent.path]
    finally:
        parent.close()


def _prepare_plateau(store, engine):
    evaluation = json.dumps({
        "status": "ok",
        "metrics": {"quality": {"value": 0.0}, "regressions": {"value": 0.0}},
        "checks": {"tests": {"passed": True}},
    })
    store.update_campaign(
        status="RUNNING",
        baseline_json=evaluation,
        baseline_snapshot="snapshot-baseline",
        champion_json=evaluation,
        champion_snapshot="snapshot-baseline",
    )
    experiment_id, attempt_id = store.create_attempt(store.campaign()["champion_snapshot"])
    store.update_experiment(
        experiment_id,
        status="REJECTED",
        evaluation_json=json.dumps({"metrics": {"quality": 0.0}}),
        decision_json=json.dumps({"accepted": False, "reasons": ["DEV_REJECTED"]}),
    )
    store.finish_attempt(
        attempt_id,
        experiment_id,
        status="REJECTED",
        scientific=True,
        improved=False,
    )
    store.event("PORTFOLIO_DECISION", PortfolioRecord(
        experiment_ordinal=1,
        action_id="exp:1:propose",
        strategy="reframe",
        selected_candidate_id="reframe-candidate",
    ).model_dump(mode="json"))
    store.update_campaign(non_improve_streak=engine.spec.budget.plateau_experiments)


def _fake_pivot_advice(_task, **_kwargs):
    return {
        "proposed_architecture": "Use a bounded two-stage retrieval and reranking pipeline.",
        "rationale": "The current single-stage retrieval path has plateaued on DEV.",
        "expected_effect": "Improve the primary DEV metric while preserving the current guardrails.",
        "changed_scope": ["src/retrieval.py", "tests/test_retrieval.py"],
        "resources": {
            "additional_agent_calls": 2,
            "additional_evaluator_calls": 2,
            "additional_evaluation_units": None,
            "notes": "Estimate assumes two implementation trials.",
        },
    }


def test_plateau_persists_typed_pivot_and_pauses_without_evaluation(tmp_path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    called = []
    try:
        _prepare_plateau(store, engine)
        champion = store.campaign()["champion_snapshot"]
        evaluator_calls = store.campaign()["evaluator_calls"]
        monkeypatch.setattr(
            engine,
            "_invoke_agent",
            lambda task, **kwargs: (called.append(task) or _fake_pivot_advice(task)),
        )

        assert engine._post_iteration_stop() is True

        campaign = store.campaign()
        row = store.pending_pivot_proposal()
        assert campaign["status"] == "PAUSED_NEEDS_INPUT"
        assert campaign["stop_reason"] == "PAUSED_NEEDS_INPUT"
        assert len(called) == 1
        assert called[0]["role"] == "pivot_advice"
        assert called[0]["pivot_context"]["approved_spec_hash"] == campaign["spec_hash"]
        assert campaign["agent_calls"] == 1
        assert campaign["evaluator_calls"] == evaluator_calls
        assert campaign["champion_snapshot"] == champion
        assert row is not None and row["status"] == "PENDING"
        proposal = PivotProposal.model_validate_json(row["proposal_json"])
        assert proposal.proposal_id == row["proposal_id"]
        assert proposal.digest == row["digest"]
        assert proposal.current_architecture
        assert proposal.proposed_architecture == _fake_pivot_advice(None)["proposed_architecture"]
        assert proposal.changed_scope == ("src/retrieval.py", "tests/test_retrieval.py")
        assert proposal.resources.additional_agent_calls == 2
        assert proposal.resources.additional_evaluator_calls == 2
        assert "pending" in row["pause_reason"].lower()

        # A resumed/replayed plateau remains paused on the same durable proposal.
        assert engine._post_iteration_stop() is True
        assert len(called) == 1
        assert store.campaign()["agent_calls"] == 1
        assert store.pending_pivot_proposal()["proposal_id"] == row["proposal_id"]
    finally:
        store.close()


@pytest.mark.parametrize("gate", ["agent_budget", "deadline"])
def test_plateau_budget_gate_pauses_without_starting_pivot_call(tmp_path, monkeypatch, gate):
    store, engine, _, _ = make_engine(tmp_path)
    called = []
    try:
        _prepare_plateau(store, engine)
        if gate == "agent_budget":
            store.update_campaign(agent_calls=engine.spec.budget.max_agent_calls)
        else:
            old = (datetime.now(timezone.utc) - timedelta(seconds=engine.spec.budget.deadline_seconds + 5)).isoformat()
            store.update_campaign(created_at=old)
        monkeypatch.setattr(
            engine,
            "_invoke_agent",
            lambda *args, **kwargs: called.append(args),
        )

        assert engine._post_iteration_stop() is True
        campaign = store.campaign()
        assert campaign["status"] == "PAUSED_NEEDS_INPUT"
        assert called == []
        assert store.pending_pivot_proposal() is None
        events = [item for item in store.events() if item["event_type"] == "PIVOT_PROPOSAL_BLOCKED"]
        assert events
        payload = json.loads(events[-1]["payload_json"])
        assert payload["agent_call_started"] is False
        assert payload["reason"] == ("BUDGET_EXHAUSTED" if gate == "agent_budget" else "DEADLINE_REACHED")
        if gate == "agent_budget":
            assert campaign["agent_calls"] == engine.spec.budget.max_agent_calls
    finally:
        store.close()


def test_pivot_digest_covers_all_operator_review_fields():
    proposal = PivotProposal.build(
        spec_hash="a" * 64,
        trigger_experiment_ordinal=3,
        current_architecture="Current system",
        proposed_architecture="Different system",
        rationale="A different mechanism may address the plateau.",
        expected_effect="Improve DEV metric.",
        changed_scope=["src/a.py"],
        resources=PivotResourceRequest(additional_agent_calls=2, additional_evaluator_calls=1),
    )
    data = proposal.model_dump(mode="json")
    data["changed_scope"] = ["src/b.py"]
    with pytest.raises(ValidationError, match="digest does not match"):
        PivotProposal.model_validate(data)


def test_pivot_advice_has_a_dedicated_read_only_agent_contract():
    required = PIVOT_ADVICE_SCHEMA["required"]
    assert required == [
        "proposed_architecture", "rationale", "expected_effect", "changed_scope", "resources",
    ]
    assert PIVOT_ADVICE_SCHEMA["additionalProperties"] is False
    task = {
        "role": "pivot_advice",
        "pivot_context": {
            "current_architecture": "Current design",
            "approved_goal": "Improve quality",
            "approved_editable_scope": ["src/**"],
        },
    }
    prompt = PiAdapter._prompt(task)
    repair_prompt = PiAdapter._repair_prompt(task)
    assert "Do not change files" in prompt
    assert "proposed_architecture" in prompt
    assert "proposed_architecture" in repair_prompt


def test_codex_pivot_advice_uses_its_strict_schema_and_read_only_sandbox(tmp_path, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "_resolved_command", lambda: ["codex"])
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    captured = {}

    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    response = {
        "proposed_architecture": "A separated retrieval and reranking flow.",
        "rationale": "The current retrieval flow has plateaued.",
        "expected_effect": "Higher DEV quality.",
        "changed_scope": ["src/search.py"],
        "resources": {
            "additional_agent_calls": 1,
            "additional_evaluator_calls": 1,
            "additional_evaluation_units": None,
            "notes": "one bounded trial",
        },
    }

    def fake_run_process(argv, *, cwd, timeout, check=True, input_text=None, **kwargs):
        captured["schema"] = json.loads(
            Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8")
        )
        captured["sandbox"] = argv[argv.index("--sandbox") + 1]
        captured["prompt"] = input_text
        Path(argv[argv.index("-o") + 1]).write_text(json.dumps(response), encoding="utf-8")
        return Result()

    monkeypatch.setattr("bananloop.agents.codex.run_process", fake_run_process)
    result = adapter.execute({"role": "pivot_advice", "pivot_context": {"current_architecture": "current"}}, tmp_path, timeout=30)
    assert result == response
    assert captured["schema"] == PIVOT_ADVICE_SCHEMA
    assert captured["sandbox"] == "read-only"
    assert "human review" in captured["prompt"]


def test_codex_rejects_unknown_role_before_process_or_schema_dispatch(tmp_path, monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "_resolved_command", lambda: pytest.fail("unknown role resolved a command"))
    monkeypatch.setattr(
        "bananloop.agents.codex.run_process",
        lambda *args, **kwargs: pytest.fail("unknown role launched Codex"),
    )
    with pytest.raises(AgentError, match="unsupported Codex role"):
        adapter.execute({"role": "unknown"}, tmp_path, timeout=30)


def test_pi_rejects_unknown_role_before_spawn_prompt_or_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bananloop.agents.pi.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("unknown role launched Pi"),
    )
    with pytest.raises(AgentError, match="unsupported Pi role"):
        PiAdapter().execute({"role": "unknown"}, tmp_path, timeout=30)
    with pytest.raises(AgentError, match="unsupported Pi role"):
        PiAdapter._prompt({"role": "unknown"})
    with pytest.raises(AgentError, match="unsupported Pi role"):
        PiAdapter._repair_prompt({"role": "unknown"})


def _make_pending_pivot(tmp_path):
    store = make_store(tmp_path)
    proposal = PivotProposal.build(
        spec_hash=store.campaign()["spec_hash"],
        trigger_experiment_ordinal=1,
        current_architecture="Current design",
        proposed_architecture="Alternative design",
        rationale="A different architecture may address the measured plateau.",
        expected_effect="Improve DEV quality.",
        changed_scope=["src/example.py"],
        resources=PivotResourceRequest(additional_agent_calls=2, additional_evaluator_calls=2),
    )
    store.save_pivot_proposal(proposal, "Awaiting explicit operator review.")
    store.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT")
    return store, proposal


def test_pivot_decision_requires_exact_identity_and_is_idempotent(tmp_path):
    store, proposal = _make_pending_pivot(tmp_path)
    command = PivotDecisionCommand(
        campaign_id="campaign-a", request_id="pivot-decision-1",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    try:
        first = submit_pivot_decision(store, "campaign-a", command)
        repeated = submit_pivot_decision(store, "campaign-a", command)
        assert first == repeated
        assert first["status"] == "APPROVED"
        assert store.pivot_proposal(proposal.proposal_id)["status"] == "APPROVED"
        assert store.campaign()["status"] == "PAUSED_NEEDS_INPUT"
        assert len([e for e in store.events() if e["event_type"] == "PIVOT_DECISION_RECORDED"]) == 1
        assert len(store.conn.execute("SELECT request_id FROM pivot_decisions").fetchall()) == 1

        with pytest.raises(StoreError, match="reused with different command"):
            submit_pivot_decision(store, "campaign-a", command.model_copy(update={"decision": "reject"}))
        with pytest.raises(StoreError, match="already pending|no longer pending"):
            submit_pivot_decision(store, "campaign-a", command.model_copy(update={
                "request_id": "pivot-decision-2",
                "decision": "reject",
            }))
    finally:
        store.close()


def test_pivot_decision_rejects_campaign_and_digest_mismatch_without_state_change(tmp_path):
    store, proposal = _make_pending_pivot(tmp_path)
    command = PivotDecisionCommand(
        campaign_id="campaign-a", request_id="pivot-decision-1",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    try:
        with pytest.raises(StoreError, match="different campaign"):
            submit_pivot_decision(store, "campaign-b", command)
        with pytest.raises(StoreError, match="digest mismatch"):
            submit_pivot_decision(store, "campaign-a", command.model_copy(update={"digest": "b" * 64}))
        with pytest.raises(StoreError, match="proposal not found"):
            submit_pivot_decision(store, "campaign-a", command.model_copy(update={
                "proposal_id": "pivot-" + "0" * 24,
                "request_id": "pivot-decision-unknown",
            }))
        assert store.pivot_proposal(proposal.proposal_id)["status"] == "PENDING"
        assert store.pivot_decision(command.request_id) is None
    finally:
        store.close()


def test_explicit_pivot_rejection_is_durable_and_does_not_resume_campaign(tmp_path):
    store, proposal = _make_pending_pivot(tmp_path)
    command = PivotDecisionCommand(
        campaign_id="campaign-a", request_id="pivot-reject-1",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="reject",
    )
    try:
        receipt = submit_pivot_decision(store, "campaign-a", command)
        assert receipt["status"] == "REJECTED"
        assert store.pivot_proposal(proposal.proposal_id)["status"] == "REJECTED"
        assert store.campaign()["status"] == "PAUSED_NEEDS_INPUT"
        assert submit_pivot_decision(store, "campaign-a", command) == receipt
    finally:
        store.close()


def test_modified_pivot_body_marks_proposal_stale_and_never_approves(tmp_path):
    store, proposal = _make_pending_pivot(tmp_path)
    command = PivotDecisionCommand(
        campaign_id="campaign-a", request_id="pivot-decision-stale",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    try:
        row = store.pivot_proposal(proposal.proposal_id)
        altered = json.loads(row["proposal_json"])
        altered["proposed_architecture"] = "Changed after display"
        store.conn.execute(
            "UPDATE pivot_proposals SET proposal_json=? WHERE proposal_id=?",
            (json.dumps(altered, sort_keys=True, separators=(",", ":"), ensure_ascii=False), proposal.proposal_id),
        )
        store.conn.commit()

        receipt = submit_pivot_decision(store, "campaign-a", command)
        assert receipt["status"] == "STALE"
        assert store.pivot_proposal(proposal.proposal_id)["status"] == "STALE"
        assert store.campaign()["status"] == "PAUSED_NEEDS_INPUT"
        assert store.pivot_decision(command.request_id)["decision"] == "approve"
    finally:
        store.close()


def test_mutating_an_approved_pivot_invalidates_its_saved_decision_on_replay(tmp_path):
    store, proposal = _make_pending_pivot(tmp_path)
    command = PivotDecisionCommand(
        campaign_id="campaign-a", request_id="pivot-decision-approved",
        proposal_id=proposal.proposal_id, digest=proposal.digest, decision="approve",
    )
    try:
        approved = submit_pivot_decision(store, "campaign-a", command)
        assert approved["status"] == "APPROVED"
        row = store.pivot_proposal(proposal.proposal_id)
        altered = json.loads(row["proposal_json"])
        altered["rationale"] = "Altered after approval"
        store.conn.execute(
            "UPDATE pivot_proposals SET proposal_json=? WHERE proposal_id=?",
            (json.dumps(altered, sort_keys=True, separators=(",", ":"), ensure_ascii=False), proposal.proposal_id),
        )
        store.conn.commit()

        replayed = submit_pivot_decision(store, "campaign-a", command)
        assert replayed["status"] == "STALE"
        assert store.pivot_proposal(proposal.proposal_id)["status"] == "STALE"
        assert store.pivot_decision(command.request_id)["status"] == "STALE"
        assert len([e for e in store.events() if e["event_type"] == "PIVOT_DECISION_INVALIDATED"]) == 1
    finally:
        store.close()
