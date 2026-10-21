from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path

from bananloop.contracts import AgentConfig, CampaignSpec, Direction, MetricSpec
from bananloop.store import CampaignStore, StoreError


def make_store(tmp_path: Path) -> CampaignStore:
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    spec = CampaignSpec(
        goal="test",
        source_repo=str(tmp_path),
        base_commit="abc",
        evaluation_command=["python", "evaluator.py"],
        primary_metric=MetricSpec(
            name="quality",
            direction=Direction.MAXIMIZE,
            target=1.0,
            min_improvement=0.1,
        ),
        agent=AgentConfig(kind="codex"),
    )
    store.initialize(spec)
    return store


def test_action_call_is_reserved_before_and_settled_after_completion(tmp_path: Path):
    store = make_store(tmp_path)
    try:
        store.start_action("a1", None, "PROPOSE", "agent", "hash")
        rows = store.reservations()
        assert len(rows) == 1
        assert rows[0]["resource"] == "agent_call"
        assert rows[0]["status"] == "RESERVED"
        assert store.campaign()["agent_calls"] == 1

        store.finish_action("a1", {"ok": True})
        row = store.reservations()[0]
        assert row["status"] == "CONSUMED"
        assert row["settled_at"]
        assert row["duration_ms"] >= 0

        summary = store.resource_summary()
        assert summary["reservation_count"] == 1
        assert summary["open_reservations"] == 0
        assert summary["unknown_outcomes"] == 0
    finally:
        store.close()


def test_cancelled_action_settles_reservation_and_retry_reserves_again(tmp_path: Path):
    store = make_store(tmp_path)
    try:
        store.start_action("a1", None, "PROPOSE", "agent", "hash")
        store.cancel_action("a1", "user interrupt")
        assert store.reservations()[0]["status"] == "CANCELLED"

        store.restart_action("a1")
        rows = store.reservations()
        assert [row["status"] for row in rows] == ["CANCELLED", "RESERVED"]
        assert store.campaign()["agent_calls"] == 2

        store.finish_action("a1", {"ok": True})
        assert [row["status"] for row in store.reservations()] == ["CANCELLED", "CONSUMED"]
    finally:
        store.close()


def test_orphaned_running_reservation_becomes_unknown_on_retry(tmp_path: Path):
    store = make_store(tmp_path)
    try:
        store.start_action("a1", None, "PROPOSE", "agent", "hash")
        store.restart_action("a1")

        rows = store.reservations()
        assert [row["status"] for row in rows] == ["UNKNOWN", "RESERVED"]
        assert store.resource_summary()["unknown_outcomes"] == 1
    finally:
        store.close()



def test_existing_state_db_migrates_evaluations_to_dev_tier(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          experiment_id INTEGER,
          candidate_snapshot TEXT NOT NULL,
          result_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO evaluations(experiment_id,candidate_snapshot,result_json,created_at) VALUES(?,?,?,?)",
        (
            None,
            "abc",
            json.dumps({"status": "ok", "metrics": {"quality": 0}}),
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    store = CampaignStore(path)
    try:
        rows = store.evaluations()
        assert len(rows) == 1
        assert rows[0]["candidate_snapshot"] == "abc"
        assert rows[0]["tier"] == "dev"
    finally:
        store.close()


def test_failed_work_reservation_rolls_back_action_and_retry(tmp_path):
    store = make_store(tmp_path)
    try:
        spec = store.spec()
        spec.budget.max_evaluation_units = 1
        spec.budget.evaluation_units_per_call = 1
        store.update_campaign(spec_json=spec.model_dump_json(), spec_hash=spec.approved_hash())
        store.start_action("spent", None, "BASELINE", "evaluator", "hash")
        store.fail_action("spent", "known failure")
        for retry in (False, True):
            with pytest.raises(StoreError, match="work budget exhausted"):
                if retry:
                    store.restart_action("spent")
                else:
                    store.start_action("extra", None, "BASELINE", "evaluator", "hash")
            assert store.action("extra") is None
            assert store.action("spent")["status"] == "FAILED"
            assert store.action("spent")["error"] == "known failure"
            assert store.campaign()["evaluator_calls"] == 1
            assert store.evaluation_work_reserved() == 1
    finally:
        store.close()
