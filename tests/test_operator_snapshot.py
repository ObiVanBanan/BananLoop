from __future__ import annotations

import json
import argparse

from bananloop.cli import cmd_operator_events, cmd_operator_snapshot
from bananloop.contracts import Hypothesis, PivotProposal, PivotResourceRequest
from bananloop.operator_snapshot import read_events_after, read_snapshot
from bananloop.store import CampaignStore
from test_store_resources import make_store


def _result(value: float) -> str:
    return json.dumps({"status": "ok", "metrics": {"quality": {"value": value}}, "checks": {}})


def test_snapshot_reports_missing_values_without_inventing_progress(tmp_path):
    store = make_store(tmp_path)
    try:
        view = read_snapshot(store, "campaign-a")
        assert view["schema_version"] == 1
        assert view["campaign_id"] == "campaign-a"
        assert view["goal"] == "test"
        assert view["metric"]["baseline"] is None
        assert view["metric"]["champion"] is None
        assert view["display_stage"] is None
        assert view["completed_epochs"] == 0
        assert view["hypotheses"] == []
        assert view["dev_points"] == []
        assert set(view["missing"]) == {"baseline_score", "champion_score", "display_stage"}
    finally:
        store.close()


def test_snapshot_projects_complete_immutable_pending_pivot(tmp_path):
    store = make_store(tmp_path)
    try:
        spec_hash = store.campaign()["spec_hash"]
        proposal = PivotProposal.build(
            spec_hash=spec_hash, trigger_experiment_ordinal=4,
            current_architecture="single-stage lookup", proposed_architecture="bounded two-stage retrieval",
            rationale="The current candidate pool limits recall.",
            expected_effect="Improve DEV recall without exceeding the evaluation budget.",
            changed_scope=["src/retrieval.py", "tests/test_retrieval.py"],
            resources=PivotResourceRequest(
                additional_agent_calls=2, additional_evaluator_calls=1,
                additional_evaluation_units=3, notes="one bounded test iteration",
            ),
        )
        store.save_pivot_proposal(proposal, "Awaiting operator decision")
        store.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="PAUSED_NEEDS_INPUT")
        view = read_snapshot(store, "campaign-a")
        pivot = view["pivot_review"]
        assert pivot["integrity_valid"] is True
        assert pivot["status"] == "PENDING"
        assert pivot["proposal"]["proposal_id"] == proposal.proposal_id
        assert pivot["proposal"]["digest"] == proposal.digest
        assert pivot["proposal"]["current_architecture"] == proposal.current_architecture
        assert pivot["proposal"]["proposed_architecture"] == proposal.proposed_architecture
        assert pivot["proposal"]["rationale"] == proposal.rationale
        assert pivot["proposal"]["expected_effect"] == proposal.expected_effect
        assert pivot["proposal"]["changed_scope"] == list(proposal.changed_scope)
        assert pivot["proposal"]["resources"]["additional_evaluator_calls"] == 1
    finally:
        store.close()


def test_snapshot_marks_tampered_pivot_invalid_without_projecting_body(tmp_path):
    store = make_store(tmp_path)
    try:
        proposal = PivotProposal.build(
            spec_hash=store.campaign()["spec_hash"], trigger_experiment_ordinal=1,
            current_architecture="old", proposed_architecture="new", rationale="why",
            expected_effect="effect", changed_scope=["src/new.py"],
            resources=PivotResourceRequest(additional_agent_calls=1, additional_evaluator_calls=1, notes="one"),
        )
        store.save_pivot_proposal(proposal, "Awaiting operator decision")
        store.conn.execute("UPDATE pivot_proposals SET proposal_json='{}' WHERE proposal_id=?", (proposal.proposal_id,))
        store.conn.commit()
        view = read_snapshot(store, "campaign-a")
        assert view["pivot_review"]["integrity_valid"] is False
        assert view["pivot_review"]["proposal"] is None
    finally:
        store.close()


def test_snapshot_uses_dev_only_and_keeps_exact_stage(tmp_path):
    store = make_store(tmp_path)
    try:
        store.update_campaign(baseline_json=_result(0.2), champion_json=_result(0.4), status="RUNNING")
        experiment_id, attempt_id = store.create_attempt("base")
        store.update_experiment(
            experiment_id,
            hypothesis_json=json.dumps({"hypothesis": "try a better candidate"}),
            evaluation_json=_result(0.5),
            candidate_snapshot="candidate-a",
        )
        store.record_evaluation(experiment_id, "candidate-a", json.loads(_result(0.5)), tier="dev")
        store.record_evaluation(experiment_id, "candidate-a", json.loads(_result(0.99)), tier="validation")
        store.record_evaluation(experiment_id, "candidate-a", json.loads(_result(1.0)), tier="holdout")
        store.set_stage(attempt_id, "VERIFY")
        view = read_snapshot(store, "campaign-a")
        assert view["engine_stage"] == "VERIFY"
        assert view["display_stage"] is None
        assert view["missing"]["display_stage"].startswith("engine stage VERIFY")
        assert view["metric"]["baseline"] == 0.2
        assert view["metric"]["champion"] == 0.4
        assert [x["score"] for x in view["dev_points"]] == [0.5]
        assert view["hypotheses"][0]["dev_score"] == 0.5
        assert view["completed_epochs"] == 0
        assert view["attempt_count"] == 1
        assert view["active_experiment_id"] == experiment_id
    finally:
        store.close()


def test_snapshot_projects_composite_steps_and_dev_scores(tmp_path):
    store = make_store(tmp_path)
    try:
        base = "base-snapshot"
        store.update_campaign(champion_snapshot=base)
        plan = [
            Hypothesis(
                hypothesis=f"step {order}", rationale="bounded", expected_effect="quality up",
                files_or_area=["solution.py"], falsification_condition="no gain",
                mechanism_key=f"step-{order}",
            ).model_dump(mode="json")
            for order in (1, 2)
        ]
        sequence = store.create_composite("snapshot-composite", base, plan=plan)
        experiment_id, _attempt_id = store.create_attempt(base)
        store.attach_composite_attempt(sequence["id"], experiment_id, 1)
        store.update_experiment(
            experiment_id,
            hypothesis_json=json.dumps(plan[0]),
            evaluation_json=_result(0.7),
        )
        store.complete_composite_step(
            sequence["id"], experiment_id,
            parent_snapshot=base, candidate_snapshot="tip-snapshot",
            dev_decision={"accepted": True}, status="INTERMEDIATE",
        )
        view = read_snapshot(store, "campaign-a")
        assert view["composites"] == [{
            "id": "snapshot-composite", "status": "ACTIVE",
            "base_snapshot": "base-snapshot", "tip_snapshot": "tip-snapshot",
            "current_step": 1, "max_steps": 2,
            "steps": [{
                "step_order": 1, "experiment_id": experiment_id,
                "title": "step 1", "status": "INTERMEDIATE", "dev_score": 0.7,
            }],
            "final_experiment_id": None,
        }]
    finally:
        store.close()


def test_event_cursor_reconnect_has_no_duplicates(tmp_path):
    store = make_store(tmp_path)
    try:
        store.event("ONE", {"n": 1})
        store.event("TWO", {"n": 2})
        store.conn.commit()
        first = read_events_after(store, 0, limit=2)
        assert [x["seq"] for x in first["events"]] == [1, 2]
        assert first["has_more"] is True
        second_store = CampaignStore(store.path)
        try:
            second_store.event("THREE", {"n": 3})
            second_store.conn.commit()
        finally:
            second_store.close()
        second = read_events_after(store, first["cursor"], limit=2)
        assert [x["seq"] for x in second["events"]] == [3, 4]
        assert second["cursor"] == 4
        assert second["has_more"] is False
        assert read_events_after(store, second["cursor"])["events"] == []
    finally:
        store.close()


def test_campaign_state_change_advances_snapshot_and_event_revision(tmp_path):
    store = make_store(tmp_path)
    try:
        before = read_snapshot(store, "campaign-a")
        store.update_campaign(status="PAUSED_NEEDS_INPUT", stop_reason="OPERATOR_REQUIRED")
        after = read_snapshot(store, "campaign-a")

        assert after["revision"] > before["revision"]
        page = read_events_after(store, before["revision"])
        assert page["cursor"] == after["revision"]
        assert page["events"][-1]["seq"] == after["revision"]
        assert page["events"][-1]["type"] == "CAMPAIGN_STATE_UPDATED"
        assert page["events"][-1]["payload"] == {}
    finally:
        store.close()


def test_campaign_counter_change_advances_snapshot_and_event_revision(tmp_path):
    store = make_store(tmp_path)
    try:
        before = read_snapshot(store, "campaign-a")
        store.increment("agent_calls")
        after = read_snapshot(store, "campaign-a")

        assert after["revision"] > before["revision"]
        assert after["budget"]["agent_calls"] == 1
        page = read_events_after(store, before["revision"])
        assert page["cursor"] == after["revision"]
        assert page["events"][-1]["type"] == "CAMPAIGN_STATE_UPDATED"
        assert page["events"][-1]["payload"] == {}
    finally:
        store.close()


def test_operator_cli_json_reads_selected_campaign(tmp_path, capsys):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    store = make_store(state_dir)
    try:
        store.event("PING", {"ok": True})
        store.conn.commit()
    finally:
        store.close()
    args = argparse.Namespace(repo=str(tmp_path), state_dir=str(state_dir), campaign_id=None)
    assert cmd_operator_snapshot(args) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["campaign_id"].startswith("state-")
    assert snapshot["goal"] == "test"
    args.after = 0
    args.limit = 10
    assert cmd_operator_events(args) == 0
    page = json.loads(capsys.readouterr().out)
    assert [event["type"] for event in page["events"]] == ["CAMPAIGN_CREATED", "PING"]


def test_event_projection_redacts_private_evaluator_and_error_text(tmp_path):
    store = make_store(tmp_path)
    try:
        secret = "PRIVATE_HOLDOUT_CANARY"
        store.event("FINAL_HOLDOUT_FAILED", {"error": secret, "private_result": 0.99})
        store.event("ACTION_FAILED", {"error": secret})
        store.event("CAMPAIGN_STOPPED", {"status": "STOPPED", "reason": "PLATEAU", "final_holdout_target_met": secret})
        store.event("STAGE_ENTERED", {"stage": "PROPOSE", "attempt_id": 2, "private": secret})
        store.conn.commit()
        page = read_events_after(store, 0)
        assert secret not in json.dumps(page)
        assert page["events"][-1]["payload"] == {"stage": "PROPOSE", "attempt_id": 2}
        assert page["events"][-2]["payload"] == {"status": "STOPPED", "reason": "PLATEAU"}
    finally:
        store.close()
