from __future__ import annotations

import multiprocessing
import sqlite3

import pytest

from bananloop.budget_ledger import BudgetExceeded, BudgetLedger, BudgetLedgerError


def _bind_child(ledger: BudgetLedger, *, allocation: str, parent: str, child: str,
                caps: dict[str, int], spec_hash: str = "a" * 64) -> None:
    ledger.reserve_child(
        allocation_id=allocation,
        idempotency_key=f"allocate:{allocation}",
        parent_id=parent,
        spec_hash=spec_hash,
        caps=caps,
    )
    ledger.bind_child(allocation, child, spec_hash)
    digest = _record_parent_activation(ledger, parent=parent, allocation=allocation,
                                       child=child, spec_hash=spec_hash)
    ledger.activate_child(allocation, child, spec_hash, digest)



def _record_parent_activation(ledger: BudgetLedger, *, parent: str, allocation: str,
                             child: str, spec_hash: str, gate_state="ACTIVE") -> str:
    import hashlib
    import json
    from pathlib import Path
    root = Path(ledger.path).parent
    ledger.conn.execute("""CREATE TABLE IF NOT EXISTS campaign_index (
        campaign_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, goal_fingerprint TEXT NOT NULL,
        goal_summary TEXT NOT NULL, state_path TEXT NOT NULL, origin TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    now = "2026-01-01T00:00:00Z"
    ledger.conn.execute(
        "INSERT OR REPLACE INTO campaign_index VALUES(?,?,?,?,?,?,?,?)",
        (parent, "request:" + parent, "f" * 64, parent, f"campaigns/{parent}", "managed", now, now),
    )
    parent_db = root / "campaigns" / parent / "campaign.sqlite3"
    parent_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(parent_db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events(event_type TEXT,payload_json TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS v2_pivot_sagas(
            idempotency_key TEXT PRIMARY KEY,request_hash TEXT,parent_campaign_id TEXT,
            allocation_id TEXT,child_id TEXT,child_spec_hash TEXT,state TEXT,proposal_json TEXT
        );
        CREATE TABLE IF NOT EXISTS v2_pivot_saga_control(
            idempotency_key TEXT PRIMARY KEY,state TEXT,control_digest TEXT,updated_at TEXT
        );
    """)
    key, request_hash = "pivot:" + allocation, "e" * 64
    digest = hashlib.sha256(json.dumps(
        {"key": key, "request_hash": request_hash, "allocation_id": allocation, "child_id": child},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    conn.execute(
        "INSERT OR REPLACE INTO v2_pivot_sagas VALUES(?,?,?,?,?,?,?,?)",
        (key, request_hash, parent, allocation, child, spec_hash, "ACTIVE",
         json.dumps({"digest": "f" * 64}, sort_keys=True, separators=(",", ":"))),
    )
    if gate_state == "ACTIVE":
        conn.execute(
            "INSERT OR REPLACE INTO v2_pivot_saga_control VALUES(?,?,?,?)",
            (key, "ACTIVE", digest, now),
        )
    elif gate_state == "LEGACY_ACTIVE":
        conn.execute(
            "INSERT OR REPLACE INTO events VALUES('PIVOT_CHILD_ACTIVE',?,?)",
            (json.dumps({"allocation_id": allocation, "campaign_id": parent,
                         "child_id": child, "child_spec_hash": spec_hash}), now),
        )
    if gate_state == "LEGACY_ACTIVE":
        conn.execute("DROP TABLE v2_pivot_saga_control")
    conn.execute("""CREATE TABLE IF NOT EXISTS campaigns(
        id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,spec_hash TEXT NOT NULL,status TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()
    child_dir = root / "campaigns" / child
    child_dir.mkdir(parents=True, exist_ok=True)
    child_db = child_dir / "campaign.sqlite3"
    child_conn = sqlite3.connect(child_db)
    child_conn.execute("""CREATE TABLE IF NOT EXISTS campaigns(
        id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,spec_hash TEXT NOT NULL,status TEXT NOT NULL
    )""")
    child_conn.execute(
        "INSERT OR REPLACE INTO campaigns VALUES(1,?,?, 'READY')",
        (json.dumps({"parent":{"parent_campaign_id":parent,"allocation_id":allocation}},
                    sort_keys=True, separators=(",", ":")), spec_hash),
    )
    child_conn.commit()
    child_conn.close()
    ledger.conn.execute(
        "INSERT OR REPLACE INTO campaign_index VALUES(?,?,?,?,?,?,?,?)",
        (child, "request:child:" + child, "a" * 64, child, f"campaigns/{child}", "managed", now, now),
    )
    ledger.conn.commit()
    return digest



def _record_parent_cancellation(ledger: BudgetLedger, *, parent: str, allocation: str,
                               child: str, spec_hash: str) -> str:
    import hashlib
    import json
    from pathlib import Path
    root = Path(ledger.path).parent
    key, request_hash, proposal_digest = "pivot:" + allocation, "e" * 64, "f" * 64
    registry_request_id = "pivot-v2:" + hashlib.sha256(
        f"{parent}:{key}:{proposal_digest}".encode()
    ).hexdigest()[:48]
    digest = hashlib.sha256(json.dumps({
        "operation": "cancel-unactivated-pivot",
        "idempotency_key": key, "request_hash": request_hash,
        "allocation_id": allocation, "registry_request_id": registry_request_id,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    conn = sqlite3.connect(root / "campaigns" / parent / "campaign.sqlite3")
    conn.execute(
        "UPDATE v2_pivot_sagas SET state='CHILD_RESERVED',proposal_json=? WHERE allocation_id=?",
        (json.dumps({"digest": proposal_digest}, sort_keys=True, separators=(",", ":")), allocation),
    )
    conn.execute(
        "UPDATE v2_pivot_saga_control SET state='CANCEL_REQUESTED',control_digest=? "
        "WHERE idempotency_key=?",
        (digest, key),
    )
    conn.commit()
    conn.close()
    return digest

def _set_child_status(ledger: BudgetLedger, child: str, status: str) -> None:
    from pathlib import Path
    root = Path(ledger.path).parent
    db = root / "campaigns" / child / "campaign.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE campaigns SET status=? WHERE id=1", (status,))
    conn.commit()
    conn.close()

def _reserve_from_process(path, index: int, start_event, result_queue) -> None:
    ledger = BudgetLedger(path)
    start_event.wait(10)
    try:
        ledger.reserve_child(allocation_id=f"alloc-{index}", idempotency_key=f"allocate:{index}",
                             parent_id="root", spec_hash=f"{index:064x}",
                             caps={"agent_calls": 6})
        result_queue.put("reserved")
    except BudgetExceeded:
        result_queue.put("rejected")
    finally:
        ledger.close()


def test_child_resource_caps_are_independent_and_settle_once(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 5, "model_tokens": 100})
    _bind_child(ledger, allocation="alloc-1", parent="root", child="child-1",
                caps={"agent_calls": 3, "model_tokens": 100})

    ledger.reserve_action(reservation_id="res-call", campaign_id="child-1", action_id="act-call",
                         resource="agent_calls", amount=3, idempotency_key="reserve:call",
                         allocation_id="alloc-1")
    ledger.reserve_action(reservation_id="res-token", campaign_id="child-1", action_id="act-token",
                         resource="model_tokens", amount=100, idempotency_key="reserve:token",
                         allocation_id="alloc-1")
    with pytest.raises(BudgetExceeded):
        ledger.reserve_action(reservation_id="res-call-2", campaign_id="child-1", action_id="act-call-2",
                             resource="agent_calls", amount=1, idempotency_key="reserve:call-2",
                             allocation_id="alloc-1")
    with pytest.raises(BudgetLedgerError):
        ledger.reserve_action(reservation_id="res-bypass", campaign_id="child-1", action_id="act-bypass",
                             resource="agent_calls", amount=1, idempotency_key="reserve:bypass")

    ledger.settle_action("res-call", actual=2, quality="actual", receipt_digest="b" * 64, fence=0)
    ledger.settle_action("res-token", actual=70, quality="actual", receipt_digest="c" * 64, fence=0)
    _set_child_status(ledger, "child-1", "STOPPED")
    ledger.close_child("alloc-1", terminal_status="STOPPED")
    ledger.close_child("alloc-1", terminal_status="STOPPED")
    with pytest.raises(BudgetLedgerError):
        ledger.reserve_action(reservation_id="res-after-close", campaign_id="child-1",
                             action_id="act-after-close", resource="agent_calls", amount=1,
                             idempotency_key="reserve:after-close")

    root = ledger.snapshot("root")
    assert root["agent_calls"]["spent"] == 2
    assert root["agent_calls"]["reserved"] == 0
    assert root["model_tokens"]["spent"] == 70
    assert root["model_tokens"]["reserved"] == 0
    ledger.close()


def test_unknown_usage_requires_authoritative_receipt_and_current_fence(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 6})
    _bind_child(ledger, allocation="alloc-1", parent="root", child="child-1",
                caps={"agent_calls": 6})
    ledger.reserve_action(reservation_id="res-1", campaign_id="child-1", action_id="act-1",
                         resource="agent_calls", amount=6, idempotency_key="reserve:1",
                         allocation_id="alloc-1")
    ledger.settle_action("res-1", actual=None, quality="unknown", receipt_digest=None,
                         fence=0)
    _set_child_status(ledger, "child-1", "STOPPED")
    with pytest.raises(BudgetLedgerError):
        ledger.close_child("alloc-1", terminal_status="STOPPED")
    ledger.advance_fence("res-1", expected_fence=0, new_fence=1)
    with pytest.raises(BudgetLedgerError):
        ledger.settle_action("res-1", actual=4, quality="actual", receipt_digest="d" * 64,
                             fence=0, authoritative=True)
    ledger.settle_action("res-1", actual=4, quality="actual", receipt_digest="d" * 64,
                         fence=1, authoritative=True)
    ledger.settle_action("res-1", actual=4, quality="actual", receipt_digest="d" * 64,
                         fence=1, authoritative=True)
    ledger.reserve_action(reservation_id="res-upper", campaign_id="child-1", action_id="act-upper",
                         resource="agent_calls", amount=2, idempotency_key="reserve:upper",
                         allocation_id="alloc-1", fence=1)
    ledger.settle_action("res-upper", actual=None, quality="upper_bound",
                         receipt_digest="e" * 64, fence=1, authoritative=True)
    ledger.close_child("alloc-1", terminal_status="STOPPED")
    assert ledger.snapshot("root")["agent_calls"]["spent"] == 6
    ledger.close()


def test_parent_escrow_waits_for_nested_child_and_propagates_spend(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 10})
    _bind_child(ledger, allocation="alloc-parent", parent="root", child="parent",
                caps={"agent_calls": 8})
    _bind_child(ledger, allocation="alloc-grandchild", parent="parent", child="grandchild",
                caps={"agent_calls": 4}, spec_hash="e" * 64)

    _set_child_status(ledger, "parent", "STOPPED")
    with pytest.raises(BudgetLedgerError):
        ledger.close_child("alloc-parent", terminal_status="STOPPED")
    ledger.reserve_action(reservation_id="res-grandchild", campaign_id="grandchild",
                         action_id="act-grandchild", resource="agent_calls", amount=3,
                         idempotency_key="reserve:grandchild", allocation_id="alloc-grandchild")
    ledger.settle_action("res-grandchild", actual=2, quality="actual", receipt_digest="f" * 64,
                         fence=0)
    _set_child_status(ledger, "grandchild", "GOAL_MET")
    ledger.close_child("alloc-grandchild", terminal_status="GOAL_MET")
    assert ledger.snapshot("parent")["agent_calls"]["spent"] == 2
    ledger.close_child("alloc-parent", terminal_status="STOPPED")
    root = ledger.snapshot("root")["agent_calls"]
    assert root["spent"] == 2
    assert root["reserved"] == 0
    with pytest.raises(BudgetLedgerError):
        ledger.reserve_child(allocation_id="alloc-after-parent-close",
                             idempotency_key="allocate:after-parent-close", parent_id="parent",
                             spec_hash="9" * 64, caps={"agent_calls": 1})
    ledger.close()


def test_concurrent_child_escrow_cannot_exceed_root_cap(tmp_path):
    path = tmp_path / "campaign-registry.sqlite3"
    setup = BudgetLedger(path)
    setup.create_root("root", {"agent_calls": 10})
    setup.close()
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    workers = [context.Process(target=_reserve_from_process,
                               args=(path, index, start_event, result_queue))
               for index in range(2)]
    for worker in workers:
        worker.start()
    start_event.set()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    results = [result_queue.get(timeout=2) for _ in workers]
    assert sorted(results) == ["rejected", "reserved"]
    check = BudgetLedger(path)
    assert check.snapshot("root")["agent_calls"]["reserved"] == 6
    check.close()


def test_bound_child_rejects_actions_until_activation_gate_opens(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 5})
    ledger.reserve_child(allocation_id="alloc-bound", idempotency_key="allocate:bound",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 4})
    ledger.bind_child("alloc-bound", "child-bound", "a" * 64)
    with pytest.raises(BudgetLedgerError, match="durable parent saga"):
        ledger.activate_child("alloc-bound", "child-bound", "a" * 64, "d" * 64)
    with pytest.raises(BudgetLedgerError, match="admission gate"):
        ledger.reserve_action(
            reservation_id="res-bound", campaign_id="child-bound", action_id="act-bound",
            resource="agent_calls", amount=1, idempotency_key="reserve:bound",
            allocation_id="alloc-bound",
        )
    digest = _record_parent_activation(ledger, parent="root", allocation="alloc-bound",
                                       child="child-bound", spec_hash="a" * 64)
    ledger.activate_child("alloc-bound", "child-bound", "a" * 64, digest)
    ledger.activate_child("alloc-bound", "child-bound", "a" * 64, digest)
    ledger.reserve_action(
        reservation_id="res-active", campaign_id="child-bound", action_id="act-active",
        resource="agent_calls", amount=1, idempotency_key="reserve:active",
        allocation_id="alloc-bound",
    )
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='alloc-bound'",
    ).fetchone()[0] == "ACTIVE"
    ledger.close()


@pytest.mark.parametrize("evidence", ["none", "legacy_event", "control"])
def test_legacy_active_allocation_backfill_requires_durable_parent_activation(tmp_path, evidence):
    path = tmp_path / "campaign-registry.sqlite3"
    ledger = BudgetLedger(path)
    ledger.create_root("root", {"agent_calls": 5})
    ledger.reserve_child(allocation_id="legacy-alloc", idempotency_key="allocate:legacy",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 4})
    ledger.bind_child("legacy-alloc", "legacy-child", "a" * 64)
    ledger.conn.execute(
        "UPDATE child_allocations SET state='ACTIVE' WHERE allocation_id='legacy-alloc'",
    )
    if evidence != "none":
        digest = _record_parent_activation(
            ledger, parent="root", allocation="legacy-alloc", child="legacy-child",
            spec_hash="a" * 64,
            gate_state="LEGACY_ACTIVE" if evidence == "legacy_event" else "ACTIVE",
        )
    ledger.conn.execute("DROP TABLE child_allocation_gates")
    ledger.close()

    upgraded = BudgetLedger(path)
    gate = upgraded.conn.execute(
        "SELECT state,activation_digest FROM child_allocation_gates WHERE allocation_id='legacy-alloc'",
    ).fetchone()
    if evidence != "none":
        assert gate["state"] == "ACTIVE"
        assert gate["activation_digest"] == digest
    else:
        assert gate["state"] == "BOUND"
        with pytest.raises(BudgetLedgerError, match="admission gate"):
            upgraded.reserve_action(
                reservation_id="legacy-res", campaign_id="legacy-child", action_id="legacy-act",
                resource="agent_calls", amount=1, idempotency_key="legacy:reserve",
                allocation_id="legacy-alloc",
            )
    upgraded.close()


def test_activation_rejects_symlinked_managed_parent_path(tmp_path):
    from pathlib import Path

    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 5})
    ledger.reserve_child(allocation_id="symlink-alloc", idempotency_key="allocate:symlink",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 4})
    ledger.bind_child("symlink-alloc", "symlink-child", "a" * 64)
    digest = _record_parent_activation(
        ledger, parent="root", allocation="symlink-alloc", child="symlink-child",
        spec_hash="a" * 64,
    )
    campaign_dir = Path(ledger.path).parent / "campaigns" / "root"
    outside = Path(ledger.path).parent / "root-moved"
    campaign_dir.rename(outside)
    try:
        campaign_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        outside.rename(campaign_dir)
        ledger.close()
        pytest.skip("directory symlinks are unavailable on this platform")
    with pytest.raises(BudgetLedgerError, match="durable parent saga"):
        ledger.activate_child("symlink-alloc", "symlink-child", "a" * 64, digest)
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='symlink-alloc'",
    ).fetchone()[0] == "BOUND"
    ledger.close()
    campaign_dir.unlink()
    outside.rename(campaign_dir)


def test_activation_rejects_symlink_above_project_state_root(tmp_path):
    from pathlib import Path

    real_root = tmp_path / "real-project"
    real_root.mkdir()
    linked_root = tmp_path / "linked-project"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")
    ledger = BudgetLedger(linked_root / "state" / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 5})
    ledger.reserve_child(allocation_id="ancestor-alloc", idempotency_key="allocate:ancestor",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 4})
    ledger.bind_child("ancestor-alloc", "ancestor-child", "a" * 64)
    digest = _record_parent_activation(
        ledger, parent="root", allocation="ancestor-alloc", child="ancestor-child",
        spec_hash="a" * 64,
    )
    with pytest.raises(BudgetLedgerError, match="durable parent saga"):
        ledger.activate_child("ancestor-alloc", "ancestor-child", "a" * 64, digest)
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='ancestor-alloc'",
    ).fetchone()[0] == "BOUND"
    ledger.close()


def test_close_child_uses_registered_terminal_status_not_caller_claim(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 5})
    _bind_child(ledger, allocation="close-proof", parent="root", child="close-child",
                caps={"agent_calls": 4})
    _set_child_status(ledger, "close-child", "STOPPED")
    with pytest.raises(BudgetLedgerError, match="caller terminal status"):
        ledger.close_child("close-proof", terminal_status="GOAL_MET")
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='close-proof'",
    ).fetchone()[0] == "ACTIVE"
    ledger.close_child("close-proof", terminal_status="STOPPED")
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='close-proof'",
    ).fetchone()[0] == "CLOSED"
    ledger.close()


def test_pre_activation_cancel_refunds_escrow_once_with_same_digest(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 10})
    ledger.reserve_child(allocation_id="cancel-once", idempotency_key="allocate:cancel-once",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 6})
    ledger.bind_child("cancel-once", "cancel-child", "a" * 64)
    _record_parent_activation(ledger, parent="root", allocation="cancel-once",
                              child="cancel-child", spec_hash="a" * 64)
    digest = _record_parent_cancellation(
        ledger, parent="root", allocation="cancel-once", child="cancel-child",
        spec_hash="a" * 64,
    )
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 6
    ledger.cancel_child("cancel-once", cancellation_digest=digest)
    ledger.cancel_child("cancel-once", cancellation_digest=digest)
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 0
    with pytest.raises(BudgetLedgerError, match="parent saga control"):
        ledger.cancel_child("cancel-once", cancellation_digest="e" * 64)
    with pytest.raises(BudgetLedgerError):
        ledger.reserve_action(
            reservation_id="after-cancel", campaign_id="cancel-child", action_id="act",
            resource="agent_calls", amount=1, idempotency_key="after-cancel",
            allocation_id="cancel-once",
        )
    ledger.close()


@pytest.mark.parametrize("history", ["DISPATCHED", "UNKNOWN", "SETTLED"])
def test_cancel_retains_dispatched_unknown_or_settled_usage(tmp_path, history):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 10})
    _bind_child(ledger, allocation="history-alloc", parent="root", child="history-child",
                caps={"agent_calls": 6})
    ledger.reserve_action(
        reservation_id="history-res", campaign_id="history-child", action_id="history-act",
        resource="agent_calls", amount=2, idempotency_key="history-res",
        allocation_id="history-alloc",
    )
    if history == "DISPATCHED":
        ledger.mark_dispatched("history-res", fence=0)
    elif history == "UNKNOWN":
        ledger.settle_action("history-res", actual=None, quality="unknown",
                             receipt_digest=None, fence=0)
    else:
        ledger.settle_action("history-res", actual=1, quality="actual",
                             receipt_digest="b" * 64, fence=0)
    # Model a legacy pre-activation bind that carried action history.
    ledger.conn.execute(
        "UPDATE child_allocation_gates SET state='BOUND' WHERE allocation_id='history-alloc'",
    )
    digest = _record_parent_cancellation(
        ledger, parent="root", allocation="history-alloc", child="history-child",
        spec_hash="a" * 64,
    )
    with pytest.raises(BudgetLedgerError, match="history|resources"):
        ledger.cancel_child("history-alloc", cancellation_digest=digest)
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='history-alloc'",
    ).fetchone()[0] == "BOUND"
    ledger.close()


def test_cancel_blocks_open_nested_allocation(tmp_path):
    ledger = BudgetLedger(tmp_path / "campaign-registry.sqlite3")
    ledger.create_root("root", {"agent_calls": 20})
    ledger.reserve_child(allocation_id="parent-cancel", idempotency_key="allocate:parent-cancel",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 12})
    ledger.bind_child("parent-cancel", "cancel-parent", "a" * 64)
    digest = _record_parent_activation(ledger, parent="root", allocation="parent-cancel",
                                       child="cancel-parent", spec_hash="a" * 64)
    ledger.activate_child("parent-cancel", "cancel-parent", "a" * 64, digest)
    ledger.reserve_child(allocation_id="nested-cancel", idempotency_key="allocate:nested-cancel",
                         parent_id="cancel-parent", spec_hash="b" * 64,
                         caps={"agent_calls": 4})
    ledger.bind_child("nested-cancel", "cancel-grandchild", "b" * 64)
    ledger.conn.execute(
        "UPDATE child_allocation_gates SET state='BOUND' WHERE allocation_id='parent-cancel'",
    )
    digest = _record_parent_cancellation(
        ledger, parent="root", allocation="parent-cancel", child="cancel-parent",
        spec_hash="a" * 64,
    )
    with pytest.raises(BudgetLedgerError):
        ledger.cancel_child("parent-cancel", cancellation_digest=digest)
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='parent-cancel'",
    ).fetchone()[0] == "BOUND"
    ledger.close()
