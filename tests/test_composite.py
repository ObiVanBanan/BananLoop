from __future__ import annotations

import json
import sqlite3

import pytest

from bananloop.contracts import Hypothesis, StopReason
from bananloop.engine import ControlledInterruption
from bananloop.store import CampaignStore, StoreError
from test_engine import git, make_engine


def _plan():
    return [
        Hypothesis(
            hypothesis=f"bounded step {index}", rationale="test sequence",
            expected_effect="quality changes", files_or_area=["value.txt"],
            falsification_condition="quality does not change", mechanism_key=f"step-{index}",
        ).model_dump(mode="json")
        for index in (1, 2)
    ]


def _composite_engine(tmp_path, *, final_candidate_is_safe=False, validation=False):
    store, engine, repo, base = make_engine(tmp_path)
    agent = repo / "agent.py"
    source = agent.read_text(encoding="utf-8")
    marker = "  return {'candidate_id':cid,'hypothesis':hyp,'evidence_refs':refs,"
    composite = (
        "  steps=[dict(hypothesis='ordered step one',rationale='fixture',expected_effect='quality up',"
        "files_or_area=['value.txt'],falsification_condition='no gain',mechanism_key='step-1'),"
        "dict(hypothesis='ordered step two',rationale='fixture',expected_effect='quality up',"
        "files_or_area=['value.txt'],falsification_condition='guard regression',mechanism_key='step-2')]\n"
        "  return {'candidate_id':cid,'hypothesis':hyp,'composite_steps':steps,'evidence_refs':refs,"
    )
    assert marker in source
    source = source.replace(marker, composite)
    if final_candidate_is_safe:
        source = source.replace(
            "elif k=='step-2': Path('value.txt').write_text('2\\n'); Path('guard.txt').write_text('1\\n')",
            "elif k=='step-2': Path('value.txt').write_text('3\\n'); Path('guard.txt').write_text('0\\n')",
        )
    agent.write_text(source, encoding="utf-8")
    git(["add", "agent.py"], repo)
    git(["commit", "-m", "fixture composite candidate"], repo)
    engine.spec.base_commit = git(["rev-parse", "HEAD"], repo)
    base = engine.spec.base_commit
    if validation:
        engine.spec.validation_command = [engine.spec.agent.command[0], "validation.py"]
    store.conn.execute(
        "UPDATE campaigns SET spec_json=?,spec_hash=? WHERE id=1",
        (engine.spec.model_dump_json(), engine.spec.approved_hash()),
    )
    store.conn.commit()
    if validation:
        from bananloop.engine import Engine
        engine = Engine(store, engine.workspace)
    return store, engine, repo, base


def _downgrade_to_v1(store):
    store.conn.execute("DROP TABLE composite_steps")
    store.conn.execute("DROP TABLE composite_sequences")
    store.conn.execute("ALTER TABLE experiments DROP COLUMN composite_step")
    store.conn.execute("ALTER TABLE experiments DROP COLUMN composite_id")
    store.conn.execute("PRAGMA user_version=1")
    store.conn.commit()


def test_composite_step_write_and_retry_are_atomic_and_durable(tmp_path):
    store, _engine, _repo, base = make_engine(tmp_path)
    try:
        store.update_campaign(champion_snapshot=base)
        sequence = store.create_composite("composite-one", base, plan=_plan())
        first, attempt = store.create_attempt(base)
        store.attach_composite_attempt(sequence["id"], first, 1)
        decision = {"accepted": False, "delta": 0.0}
        store.update_experiment(first, candidate_snapshot="step-one")

        assert store.complete_composite_step(
            sequence["id"], first, parent_snapshot=base,
            candidate_snapshot="step-one", dev_decision=decision, status="INTERMEDIATE",
        ) == 1
        # Re-entering RECORD after a process interruption must not duplicate the step.
        assert store.complete_composite_step(
            sequence["id"], first, parent_snapshot=base,
            candidate_snapshot="step-one", dev_decision=decision, status="INTERMEDIATE",
        ) == 1
        current = store.composite(sequence["id"])
        assert current["current_step"] == 1
        assert current["tip_snapshot"] == "step-one"
        assert len(current["steps"]) == 1
        assert store.experiment(first)["composite_step"] == 1

        store.finish_attempt(attempt, first, status="REJECTED", scientific=True, improved=False)
        second, _second_attempt = store.create_attempt(current["tip_snapshot"])
        store.attach_composite_attempt(sequence["id"], second, 2)
        assert store.experiment(second)["parent_snapshot"] == "step-one"
        assert store.experiment(second)["composite_step"] == 2
    finally:
        store.close()


def test_composite_bounds_and_future_schema_migration(tmp_path):
    store, _engine, _repo, base = make_engine(tmp_path)
    try:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        with pytest.raises(StoreError, match="2 and 4"):
            store.create_composite("too-short", base, plan=_plan()[:1])
        store.update_campaign(champion_snapshot=base)
        saved = store.create_composite("bounded-sequence", base, plan=_plan())
        assert saved["max_steps"] == 2
        assert json.loads(saved["plan_json"])[0]["mechanism_key"] == "step-1"
    finally:
        store.close()

    path = tmp_path / "state" / "campaign.sqlite3"
    reopened = CampaignStore(path)
    try:
        assert reopened.composite("bounded-sequence")["status"] == "ACTIVE"
        assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        reopened.close()


def test_v1_to_v2_migration_preserves_data_and_creates_restorable_backup(tmp_path):
    store, _engine, _repo, _base = make_engine(tmp_path)
    path = store.path
    try:
        store.event("MIGRATION_SENTINEL", {"campaign": "preserved"})
        store.start_action(
            "migration-action", None, "PROPOSE", "agent", "a" * 64,
            {"request": "preserved"},
        )
        store.conn.commit()
        _downgrade_to_v1(store)
    finally:
        store.close()

    migrated = CampaignStore(path)
    backup = path.with_name(path.name + ".pre-composite-v2.bak")
    try:
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert migrated.campaign()["spec_hash"]
        assert migrated.action("migration-action")["request_json"] == '{"request": "preserved"}'
        assert any(event["event_type"] == "MIGRATION_SENTINEL" for event in migrated.events())
        assert backup.is_file()
    finally:
        migrated.close()

    with sqlite3.connect(backup) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
        assert restored.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0]
        assert restored.execute("SELECT request_json FROM actions WHERE id='migration-action'").fetchone()[0] == '{"request": "preserved"}'
        assert restored.execute("SELECT event_type FROM events WHERE event_type='MIGRATION_SENTINEL'").fetchone()


def test_v1_migration_preserves_existing_backup_and_writes_unique_backup(tmp_path):
    store, _engine, _repo, _base = make_engine(tmp_path)
    path = store.path
    _downgrade_to_v1(store)
    store.close()
    stale_backup = path.with_name(path.name + ".pre-composite-v2.bak")
    stale_backup.write_bytes(b"preserve this older backup")

    migrated = CampaignStore(path)
    try:
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert stale_backup.read_bytes() == b"preserve this older backup"
        unique_backup = path.with_name(path.name + ".pre-composite-v2.bak.1")
        assert unique_backup.is_file()
        with sqlite3.connect(unique_backup) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        migrated.close()


@pytest.mark.parametrize(
    "checkpoint_stage", ["COMPOSITE_ATTACHED", "RECORD", "COMPOSITE_ATTEMPT_CREATED"],
)
def test_composite_recovery_after_intermediate_dev_gate(tmp_path, monkeypatch, checkpoint_stage):
    store, engine, _repo, base = _composite_engine(tmp_path)
    try:
        monkeypatch.setenv("BANANLOOP_TEST_STOP_AFTER_STAGE", checkpoint_stage)
        with pytest.raises(ControlledInterruption):
            engine.run()
        sequence = store.active_composite()
        assert sequence is not None
        # The first DEV result is durable, while promotion has not changed the champion.
        expected_current_step = 1 if checkpoint_stage == "COMPOSITE_ATTEMPT_CREATED" else 0
        assert sequence["current_step"] == expected_current_step
        assert store.campaign()["champion_snapshot"] == base
        assert store.attempt(store.campaign()["active_attempt_id"])["stage"] == {
            "COMPOSITE_ATTACHED": "PROPOSE",
            "RECORD": "RECORD",
            "COMPOSITE_ATTEMPT_CREATED": "PROPOSE",
        }[checkpoint_stage]
        if checkpoint_stage == "COMPOSITE_ATTEMPT_CREATED":
            active_exp = store.experiment(store.campaign()["active_experiment_id"])
            assert active_exp["composite_id"] == sequence["id"]
            assert active_exp["composite_step"] == 2
            assert active_exp["parent_snapshot"] == sequence["tip_snapshot"]

        monkeypatch.delenv("BANANLOOP_TEST_STOP_AFTER_STAGE")
        result = engine.run(resume=True)
        sequence = store.composite(sequence["id"])
        assert sequence["status"] == "REJECTED"
        assert sequence["current_step"] == 2
        assert [step["status"] for step in sequence["steps"]] == ["INTERMEDIATE", "FINAL_REJECTED"]
        assert store.experiment(sequence["steps"][1]["experiment_id"])["parent_snapshot"] == sequence["steps"][0]["candidate_snapshot"]
        assert store.campaign()["champion_snapshot"] == base
    finally:
        store.close()


@pytest.mark.parametrize("failure_category", ["SCOPE_VIOLATION", "LEAKAGE_GUARD"])
def test_unsafe_composite_step_does_not_advance_tip(tmp_path, failure_category):
    store, engine, _repo, base = make_engine(tmp_path)
    try:
        store.update_campaign(champion_snapshot=base)
        sequence = store.create_composite("unsafe-step", base, plan=_plan())
        experiment_id, attempt_id = store.create_attempt(base)
        store.attach_composite_attempt(sequence["id"], experiment_id, 1)
        store.set_stage(attempt_id, "RECORD")
        decision = {
            "accepted": False, "primary_value": None, "champion_value": None,
            "delta": None, "predicates": {"safe": False}, "reasons": [failure_category],
        }
        store.update_experiment(
            experiment_id,
            candidate_snapshot="unsafe-candidate-snapshot",
            failure_category=failure_category,
            decision_json=json.dumps(decision),
            dev_decision_json=json.dumps(decision),
            evaluation_json=None,
        )

        engine._stage_record(attempt_id, experiment_id)
        saved = store.composite(sequence["id"])
        assert saved["status"] == "REJECTED"
        assert saved["tip_snapshot"] == base
        assert saved["current_step"] == 1
        assert saved["steps"][0]["status"] == "FAILED"
        assert saved["steps"][0]["candidate_snapshot"] == "unsafe-candidate-snapshot"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("reason", "expected_status", "still_active"),
    [
        (StopReason.USER_PAUSED, "ACTIVE", True),
        (StopReason.USER_STOPPED, "ABANDONED", False),
    ],
)
def test_composite_pause_preserves_and_stop_abandons_sequence(
    tmp_path, reason, expected_status, still_active,
):
    store, engine, _repo, base = make_engine(tmp_path)
    try:
        store.update_campaign(champion_snapshot=base)
        store.create_composite("operator-controlled", base, plan=_plan())
        engine._stop(reason)
        saved = store.composite("operator-controlled")
        assert saved["status"] == expected_status
        assert (store.active_composite() is not None) is still_active
    finally:
        store.close()


def test_composite_final_acceptance_validates_and_promotes(tmp_path):
    store, engine, _repo, base = _composite_engine(
        tmp_path, final_candidate_is_safe=True, validation=True,
    )
    try:
        engine.run()
        sequence = store.composite("composite-1")
        assert sequence["status"] == "COMPLETED"
        assert [step["status"] for step in sequence["steps"]] == [
            "INTERMEDIATE", "FINAL_ACCEPTED",
        ]
        final_snapshot = sequence["steps"][-1]["candidate_snapshot"]
        assert store.campaign()["champion_snapshot"] == final_snapshot
        assert any(
            row["experiment_id"] == sequence["final_experiment_id"]
            and row["tier"] == "validation"
            for row in store.conn.execute("SELECT experiment_id,tier FROM evaluations")
        )
    finally:
        store.close()


def test_composite_final_rejection_preserves_champion(tmp_path):
    store, engine, _repo, base = _composite_engine(tmp_path)
    try:
        engine.run()
        sequence = store.composite("composite-1")
        assert sequence["status"] == "REJECTED"
        assert sequence["steps"][-1]["status"] == "FINAL_REJECTED"
        assert store.campaign()["champion_snapshot"] == base
    finally:
        store.close()
