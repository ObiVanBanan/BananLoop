from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from bananloop.store import CampaignStore, StoreError


def test_operator_requests_are_durable_and_idempotent(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    store = CampaignStore(path)
    try:
        first = store.record_control_request("request-1", "pause", {"campaign_id": "c1"})
        repeated = store.record_control_request("request-1", "pause", {"campaign_id": "c1"})
        assert first == repeated
        assert first["status"] == "ACCEPTED"
        with pytest.raises(StoreError, match="different command"):
            store.record_control_request("request-1", "end", {"campaign_id": "c1"})
        finished = store.finish_control_request("request-1", "COMPLETED", {"status": "PAUSED_USER"})
        assert json.loads(finished["result_json"]) == {"status": "PAUSED_USER"}
        assert store.finish_control_request("request-1", "COMPLETED", {"status": "PAUSED_USER"}) == finished
        with pytest.raises(StoreError, match="already recorded"):
            store.finish_control_request("request-1", "REJECTED", {"reason": "late"})
        assert [item["event_type"] for item in store.events()] == [
            "OPERATOR_REQUEST_ACCEPTED", "OPERATOR_REQUEST_FINISHED"
        ]
    finally:
        store.close()
    reopened = CampaignStore(path)
    try:
        assert reopened.control_request("request-1")["status"] == "COMPLETED"
        assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        reopened.close()


def test_legacy_database_backup_can_be_restored(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sentinel(value) VALUES('original')")
    migrated = CampaignStore(path)
    try:
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert migrated.conn.execute("PRAGMA table_info(campaigns)").fetchall()
        assert migrated.conn.execute("SELECT value FROM sentinel").fetchone()[0] == "original"
    finally:
        migrated.close()
    backup = path.with_name(path.name + ".pre-operator-v1.bak")
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "original"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='operator_requests'").fetchone() is None
    restored_path = tmp_path / "restored.sqlite3"
    shutil.copy2(backup, restored_path)
    with sqlite3.connect(restored_path) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "original"


def test_future_schema_is_rejected_without_modification(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=5")
    with pytest.raises(StoreError, match="newer"):
        CampaignStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v1_database_migrates_to_v4_without_rewriting_campaign_data(tmp_path):
    path = tmp_path / "v1.sqlite3"
    store = CampaignStore(path)
    from bananloop.contracts import CampaignSpec, Direction, MetricSpec

    campaign_spec = CampaignSpec(
        goal="preserve legacy state", source_repo=str(tmp_path), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE,
                                  target=1, min_improvement=0.1),
    )
    store.initialize(campaign_spec)
    store.update_campaign(
        status="PAUSED_INFRA", stop_reason="AGENT_ERROR", champion_json='{"sentinel":true}',
        champion_snapshot="snapshot-legacy",
    )
    legacy_evaluation = {"status": "ok", "metrics": {"quality": {"value": 0.5}}}
    store.record_evaluation(None, "snapshot-legacy", legacy_evaluation)
    original_hash = store.campaign()["spec_hash"]
    store.close()

    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS composite_steps")
        conn.execute("DROP TABLE IF EXISTS composite_sequences")
        conn.execute("DROP TABLE IF EXISTS pivot_proposals")
        conn.execute("ALTER TABLE evaluations DROP COLUMN tier")
        conn.execute("ALTER TABLE experiments DROP COLUMN composite_step")
        conn.execute("ALTER TABLE experiments DROP COLUMN composite_id")
        conn.execute("PRAGMA user_version=1")

    migrated = CampaignStore(path)
    try:
        campaign = migrated.campaign()
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert campaign["spec_hash"] == original_hash
        assert campaign["status"] == "PAUSED_INFRA"
        assert campaign["stop_reason"] == "AGENT_ERROR"
        assert campaign["champion_json"] == '{"sentinel":true}'
        assert campaign["champion_snapshot"] == "snapshot-legacy"
        assert migrated.conn.execute("SELECT COUNT(*) FROM pivot_proposals").fetchone()[0] == 0
        assert migrated.conn.execute("SELECT COUNT(*) FROM composite_sequences").fetchone()[0] == 0
        rows = migrated.evaluations()
        assert len(rows) == 1
        assert rows[0]["candidate_snapshot"] == "snapshot-legacy"
        assert rows[0]["tier"] == "dev"
        assert json.loads(rows[0]["result_json"]) == legacy_evaluation
    finally:
        migrated.close()

    backup = path.with_name(path.name + ".pre-composite-v2.bak")
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        old_campaign = conn.execute(
            "SELECT spec_hash,status,stop_reason,champion_json,champion_snapshot FROM campaigns WHERE id=1"
        ).fetchone()
        assert old_campaign == (
            original_hash, "PAUSED_INFRA", "AGENT_ERROR", '{"sentinel":true}', "snapshot-legacy"
        )
        assert conn.execute("SELECT candidate_snapshot,result_json FROM evaluations").fetchone() == (
            "snapshot-legacy", json.dumps(legacy_evaluation, sort_keys=True)
        )


def test_v2_to_v4_migration_preserves_campaign_and_uses_collision_safe_backup(tmp_path):
    from bananloop.contracts import CampaignSpec, Direction, MetricSpec

    path = tmp_path / "v2.sqlite3"
    store = CampaignStore(path)
    campaign_spec = CampaignSpec(
        goal="preserve v2 state", source_repo=str(tmp_path), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE,
                                  target=1, min_improvement=0.1),
    )
    store.initialize(campaign_spec)
    store.update_campaign(
        status="PAUSED_INFRA", stop_reason="AGENT_ERROR", champion_json='{"v2":true}',
        champion_snapshot="snapshot-v2",
    )
    evaluation = {"status": "ok", "metrics": {"quality": {"value": 0.75}}}
    store.record_evaluation(None, "snapshot-v2", evaluation)
    original_hash = store.campaign()["spec_hash"]
    store.close()

    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE pivot_proposals")
        conn.execute("PRAGMA user_version=2")
    backup = path.with_name(path.name + ".pre-pivot-v3.bak")
    with sqlite3.connect(backup) as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel(value) VALUES('keep-existing-backup')")

    migrated = CampaignStore(path)
    try:
        campaign = migrated.campaign()
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert campaign["spec_hash"] == original_hash
        assert campaign["status"] == "PAUSED_INFRA"
        assert campaign["stop_reason"] == "AGENT_ERROR"
        assert campaign["champion_json"] == '{"v2":true}'
        assert campaign["champion_snapshot"] == "snapshot-v2"
        assert migrated.conn.execute("SELECT COUNT(*) FROM pivot_proposals").fetchone()[0] == 0
        rows = migrated.evaluations()
        assert len(rows) == 1
        assert rows[0]["candidate_snapshot"] == "snapshot-v2"
        assert rows[0]["tier"] == "dev"
        assert json.loads(rows[0]["result_json"]) == evaluation
    finally:
        migrated.close()

    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "keep-existing-backup"
    migrated_backup = path.with_name(path.name + ".pre-pivot-v3.bak.1")
    assert migrated_backup.exists()
    with sqlite3.connect(migrated_backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        old_campaign = conn.execute(
            "SELECT spec_hash,status,stop_reason,champion_json,champion_snapshot FROM campaigns WHERE id=1"
        ).fetchone()
        assert old_campaign == (
            original_hash, "PAUSED_INFRA", "AGENT_ERROR", '{"v2":true}', "snapshot-v2"
        )
        assert conn.execute("SELECT candidate_snapshot,tier,result_json FROM evaluations").fetchone() == (
            "snapshot-v2", "dev", json.dumps(evaluation, sort_keys=True)
        )


def test_v3_to_v4_migration_preserves_pending_pivot_and_backs_up_v3(tmp_path):
    from bananloop.contracts import CampaignSpec, Direction, MetricSpec, PivotProposal, PivotResourceRequest

    path = tmp_path / "v3.sqlite3"
    store = CampaignStore(path)
    campaign_spec = CampaignSpec(
        goal="preserve pending pivot", source_repo=str(tmp_path), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE,
                                  target=1, min_improvement=0.1),
    )
    store.initialize(campaign_spec)
    proposal = PivotProposal.build(
        spec_hash=store.campaign()["spec_hash"],
        trigger_experiment_ordinal=3,
        current_architecture="Current system",
        proposed_architecture="Alternative system",
        rationale="A different approach may help.",
        expected_effect="Better DEV quality.",
        changed_scope=["src/module.py"],
        resources=PivotResourceRequest(additional_agent_calls=1),
    )
    store.save_pivot_proposal(proposal, "Awaiting operator review.")
    store.close()

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=3")
        conn.execute("DROP TABLE pivot_decisions")

    migrated = CampaignStore(path)
    try:
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert migrated.pivot_proposal(proposal.proposal_id)["digest"] == proposal.digest
        assert migrated.pending_pivot_proposal()["status"] == "PENDING"
        assert migrated.conn.execute("SELECT COUNT(*) FROM pivot_decisions").fetchone()[0] == 0
    finally:
        migrated.close()

    backup = path.with_name(path.name + ".pre-pivot-decision-v4.bak")
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute(
            "SELECT digest,status FROM pivot_proposals WHERE proposal_id=?",
            (proposal.proposal_id,),
        ).fetchone() == (proposal.digest, "PENDING")
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='pivot_decisions'").fetchone() is None
