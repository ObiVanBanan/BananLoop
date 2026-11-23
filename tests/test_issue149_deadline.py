from __future__ import annotations

import time

from bananloop.engine import Engine
from bananloop.operator_control import OperatorCommand, submit_control
from bananloop.v2_campaign_cli import _campaign_deadline
from bananloop.v2_optimization_cli import _task_request_timeout
from bananloop.workspace import WorkspaceManager

from test_store_resources import make_store


def test_exploration_and_optimization_share_inherited_task_deadline(tmp_path):
    deadline = time.time() + 300
    state = tmp_path / "campaign-state"
    state.mkdir()

    # Exploration persists the parent task's absolute deadline once.
    assert _campaign_deadline(
        state, campaign_id="campaign-a", spec_hash="a" * 64,
        time_limit_seconds=3600, inherited_deadline_epoch=deadline,
    ) == deadline

    # Optimization receives that same epoch; its per-request budget shrinks
    # with remaining task time instead of starting a fresh 3600 second window.
    assert _task_request_timeout(deadline, 300, now=deadline - 12.8) == 12
    assert _campaign_deadline(
        state, campaign_id="campaign-a", spec_hash="a" * 64,
        time_limit_seconds=3600, inherited_deadline_epoch=deadline,
    ) == deadline


def test_user_stop_keeps_last_confirmed_champion(tmp_path):
    store = make_store(tmp_path)
    try:
        # This is a previously confirmed campaign state. A stop request must
        # only change terminal control state; it must not replace the champion.
        store.update_campaign(
            status="RUNNING", champion_json='{"quality":0.75}',
            champion_snapshot="confirmed-snapshot",
        )
        accepted = submit_control(
            store, "campaign-a", OperatorCommand("campaign-a", "stop-1", "end"),
        )
        assert accepted["status"] == "ACCEPTED"

        stopped = Engine(
            store, WorkspaceManager(tmp_path, tmp_path / "unused-workspace"),
        ).run()

        assert stopped["status"] == "STOPPED"
        assert stopped["stop_reason"] == "USER_STOPPED"
        assert stopped["champion_json"] == '{"quality":0.75}'
        assert stopped["champion_snapshot"] == "confirmed-snapshot"
        assert store.control_request("stop-1")["status"] == "COMPLETED"
    finally:
        store.close()
