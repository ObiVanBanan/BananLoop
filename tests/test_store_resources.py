from __future__ import annotations

from pathlib import Path

from bananloop.contracts import AgentConfig, CampaignSpec, Direction, MetricSpec
from bananloop.store import CampaignStore


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
