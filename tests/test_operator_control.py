from __future__ import annotations

import pytest

from bananloop.contracts import PivotDecisionCommand, PivotProposal, PivotResourceRequest
from bananloop.operator_control import OperatorCommand, reconcile_control, submit_control, submit_pivot_decision
from bananloop.store import StoreError
from test_store_resources import make_store


def test_typed_pause_is_idempotent_and_separate_from_completion(tmp_path):
    store = make_store(tmp_path)
    try:
        command = OperatorCommand("campaign-a", "pause-1", "pause")
        first = submit_control(store, "campaign-a", command)
        assert first["status"] == "ACCEPTED"
        assert store.campaign()["pause_requested"] == 1
        assert reconcile_control(store, "pause-1")["status"] == "ACCEPTED"
        assert submit_control(store, "campaign-a", command) == first
        assert [x["event_type"] for x in store.events()].count("PAUSE_REQUESTED") == 1
        store.update_campaign(status="PAUSED_USER")
        assert reconcile_control(store, "pause-1")["status"] == "COMPLETED"
        assert submit_control(store, "campaign-a", command)["status"] == "COMPLETED"
    finally:
        store.close()


def test_operator_control_rejects_wrong_campaign_and_invalid_commands(tmp_path):
    store = make_store(tmp_path)
    try:
        with pytest.raises(StoreError, match="different campaign"):
            submit_control(store, "campaign-a", OperatorCommand("campaign-b", "pause-1", "pause"))
        with pytest.raises(StoreError, match="invalid operator request id"):
            submit_control(store, "campaign-a", OperatorCommand("campaign-a", "../bad", "pause"))
        with pytest.raises(StoreError, match="invalid operator control kind"):
            submit_control(store, "campaign-a", OperatorCommand("campaign-a", "bad-kind", "shell"))
        assert store.campaign()["pause_requested"] == 0
        assert [x["event_type"] for x in store.events()] == ["CAMPAIGN_CREATED"]
    finally:
        store.close()


def test_operator_request_id_cannot_change_meaning(tmp_path):
    store = make_store(tmp_path)
    try:
        submit_control(store, "campaign-a", OperatorCommand("campaign-a", "request-1", "pause"))
        with pytest.raises(StoreError, match="different command"):
            submit_control(store, "campaign-a", OperatorCommand("campaign-a", "request-1", "end"))
        with pytest.raises(StoreError, match="different command"):
            submit_control(store, "campaign-b", OperatorCommand("campaign-b", "request-1", "pause"))
        assert store.campaign()["stop_requested"] == 0
    finally:
        store.close()


def test_competing_terminal_stop_rejects_unfulfilled_pause(tmp_path):
    store = make_store(tmp_path)
    try:
        submit_control(store, "campaign-a", OperatorCommand("campaign-a", "pause-1", "pause"))
        store.update_campaign(status="STOPPED", stop_reason="USER_STOPPED")
        receipt = reconcile_control(store, "pause-1")
        assert receipt["status"] == "REJECTED"
        assert "STOPPED" in receipt["result_json"]
    finally:
        store.close()


def test_pivot_rejection_requires_stable_explicit_next_step(tmp_path):
    store = make_store(tmp_path)
    try:
        proposal = PivotProposal.build(
            spec_hash=store.campaign()["spec_hash"], trigger_experiment_ordinal=2,
            current_architecture="current", proposed_architecture="proposed",
            rationale="rationale", expected_effect="effect", changed_scope=["src/new.py"],
            resources=PivotResourceRequest(additional_agent_calls=1, additional_evaluator_calls=1, notes="bounded"),
        )
        store.save_pivot_proposal(proposal, "awaiting operator")
        store.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT")
        base = dict(
            campaign_id="campaign-a", request_id="reject-choice-1",
            proposal_id=proposal.proposal_id, digest=proposal.digest, decision="reject",
        )
        command = PivotDecisionCommand(**base, rejection_action="continue_old_plan")
        first = submit_pivot_decision(store, "campaign-a", command)
        assert first["status"] == "REJECTED"
        assert '"rejection_action":"continue_old_plan"' in first["result_json"]
        assert submit_pivot_decision(store, "campaign-a", command) == first
        with pytest.raises(StoreError, match="different command"):
            submit_pivot_decision(
                store, "campaign-a",
                command.model_copy(update={"rejection_action": "change_conditions"}),
            )
        assert store.campaign()["status"] == "PAUSED_NEEDS_INPUT"
        assert store.campaign()["stop_requested"] == 0
    finally:
        store.close()
