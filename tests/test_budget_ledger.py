from __future__ import annotations

import multiprocessing

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
    ledger.activate_child(allocation, child, spec_hash, "c" * 64)


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

    with pytest.raises(BudgetLedgerError):
        ledger.close_child("alloc-parent", terminal_status="STOPPED")
    ledger.reserve_action(reservation_id="res-grandchild", campaign_id="grandchild",
                         action_id="act-grandchild", resource="agent_calls", amount=3,
                         idempotency_key="reserve:grandchild", allocation_id="alloc-grandchild")
    ledger.settle_action("res-grandchild", actual=2, quality="actual", receipt_digest="f" * 64,
                         fence=0)
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
    with pytest.raises(BudgetLedgerError, match="admission gate"):
        ledger.reserve_action(
            reservation_id="res-bound", campaign_id="child-bound", action_id="act-bound",
            resource="agent_calls", amount=1, idempotency_key="reserve:bound",
            allocation_id="alloc-bound",
        )
    ledger.activate_child("alloc-bound", "child-bound", "a" * 64, "d" * 64)
    ledger.activate_child("alloc-bound", "child-bound", "a" * 64, "d" * 64)
    ledger.reserve_action(
        reservation_id="res-active", campaign_id="child-bound", action_id="act-active",
        resource="agent_calls", amount=1, idempotency_key="reserve:active",
        allocation_id="alloc-bound",
    )
    assert ledger.conn.execute(
        "SELECT state FROM child_allocation_gates WHERE allocation_id='alloc-bound'",
    ).fetchone()[0] == "ACTIVE"
    ledger.close()
