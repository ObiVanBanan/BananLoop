from __future__ import annotations

import multiprocessing

import pytest

from bananloop.budget_ledger import BudgetExceeded, BudgetLedger, BudgetLedgerError
from bananloop.worker_queue import DurableWorkerQueue, WorkerBudgetError, WorkerQueueError


def _queue(tmp_path, *, cap: int = 20):
    path = tmp_path / "campaign-registry.sqlite3"
    ledger = BudgetLedger(path)
    ledger.create_root("root", {"agent_calls": cap})
    ledger.close()
    return path, DurableWorkerQueue(path)


def _enqueue(queue: DurableWorkerQueue, job_id: str, *, amount: int = 5):
    return queue.enqueue(job_id=job_id, enqueue_key=f"enqueue:{job_id}", campaign_id="root",
                         action_id=f"action:{job_id}", resource="agent_calls", upper_bound=amount,
                         request={"operation": "agent", "job": job_id})


def _claim_process(path, worker_id: str, start_event, result_queue) -> None:
    queue = DurableWorkerQueue(path)
    start_event.wait(10)
    try:
        result_queue.put(("claimed", queue.claim("job-race", worker_id, lease_seconds=30)["fence"]))
    except WorkerQueueError:
        result_queue.put(("rejected", None))
    finally:
        queue.close()


def test_claim_reservation_event_commit_or_rollback_as_one(tmp_path):
    path, queue = _queue(tmp_path, cap=5)
    _enqueue(queue, "job-1")
    with pytest.raises(RuntimeError, match="injected failure"):
        queue.claim("job-1", "worker-a", lease_seconds=30, now=100, fail_before_commit=True)

    assert queue.get_job("job-1")["state"] == "READY"
    assert len(queue.events("job-1")) == 1
    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 0
    ledger.close()

    claim = queue.claim("job-1", "worker-a", lease_seconds=30, now=100)
    assert claim["fence"] == 1
    with pytest.raises(WorkerQueueError):
        queue.mark_dispatched("job-1", "worker-a", True, now=101)
    with pytest.raises(WorkerQueueError):
        queue.claim("job-1", "worker-b", lease_seconds=30, now=101)
    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 5
    ledger.close()
    assert [event["event_type"] for event in queue.events("job-1")] == ["JOB_ENQUEUED", "JOB_LEASED"]
    assert [event["event_type"] for event in queue.events_after(0)] == ["JOB_ENQUEUED", "JOB_LEASED"]
    queue.close()


def test_concurrent_claim_has_one_worker_and_one_budget_hold(tmp_path):
    path, queue = _queue(tmp_path, cap=5)
    _enqueue(queue, "job-race")
    queue.close()
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    workers = [context.Process(target=_claim_process, args=(path, f"worker-{index}", start_event, result_queue))
               for index in range(2)]
    for worker in workers:
        worker.start()
    start_event.set()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    outcomes = [result_queue.get(timeout=2) for _ in workers]
    assert sum(outcome[0] == "claimed" for outcome in outcomes) == 1
    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 5
    ledger.close()


def test_child_worker_claim_charges_its_parent_allocation(tmp_path):
    path, queue = _queue(tmp_path, cap=10)
    ledger = BudgetLedger(path)
    ledger.reserve_child(allocation_id="alloc-child", idempotency_key="allocate:child",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 6})
    ledger.bind_child("alloc-child", "child", "a" * 64)
    ledger.close()
    queue.enqueue(job_id="job-child", enqueue_key="enqueue:child", campaign_id="child",
                  action_id="action:child", resource="agent_calls", upper_bound=4,
                  request={"operation": "agent"})
    claim = queue.claim("job-child", "worker-child", lease_seconds=30, now=100)
    queue.mark_dispatched("job-child", "worker-child", claim["fence"], now=101)
    queue.complete("job-child", "worker-child", claim["fence"], output_digest="f" * 64,
                   actual_amount=3, receipt_digest="e" * 64, now=102)
    ledger = BudgetLedger(path)
    ledger.close_child("alloc-child", terminal_status="GOAL_MET")
    assert ledger.snapshot("root")["agent_calls"]["spent"] == 3
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 0
    ledger.close()
    queue.close()


def test_predispatch_expiry_releases_and_advances_fence(tmp_path):
    path, queue = _queue(tmp_path, cap=10)
    _enqueue(queue, "job-expire", amount=4)
    first = queue.claim("job-expire", "worker-a", lease_seconds=5, now=100)
    assert queue.expire(now=106) == {"released": 1, "unknown": 0}
    assert queue.get_job("job-expire")["state"] == "READY"
    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["reserved"] == 0
    ledger.close()

    second = queue.claim("job-expire", "worker-b", lease_seconds=30, now=110)
    assert second["fence"] == first["fence"] + 1
    with pytest.raises(WorkerQueueError):
        queue.mark_dispatched("job-expire", "worker-a", first["fence"], now=111)
    assert queue.cancel("job-expire", reason="operator cancelled") == "CANCELLED"
    queue.close()


def test_dispatched_expiry_is_unknown_until_receipt_reconciliation(tmp_path):
    path, queue = _queue(tmp_path, cap=5)
    _enqueue(queue, "job-unknown")
    claim = queue.claim("job-unknown", "worker-a", lease_seconds=5, now=100)
    queue.mark_dispatched("job-unknown", "worker-a", claim["fence"], now=101)
    ledger = BudgetLedger(path)
    with pytest.raises(BudgetLedgerError):
        ledger.release_action(claim["reservation_id"], fence=claim["fence"],
                              dispatch_proven_absent=True)
    with pytest.raises(BudgetLedgerError):
        ledger.advance_fence(claim["reservation_id"], expected_fence=claim["fence"],
                             new_fence=claim["fence"] + 1)
    ledger.close()
    assert queue.expire(now=106) == {"released": 0, "unknown": 1}
    assert queue.get_job("job-unknown")["state"] == "UNKNOWN"
    ledger = BudgetLedger(path)
    budget = ledger.snapshot("root")["agent_calls"]
    assert budget["reserved"] == 0
    assert budget["unknown"] == 5
    ledger.close()
    with pytest.raises(WorkerQueueError):
        queue.complete("job-unknown", "worker-a", claim["fence"], output_digest="a" * 64,
                       actual_amount=3, receipt_digest="b" * 64, now=107)

    result = queue.reconcile_unknown("job-unknown", actual_amount=3,
                                     receipt_digest="c" * 64, output_digest="a" * 64)
    replay = queue.reconcile_unknown("job-unknown", actual_amount=3,
                                     receipt_digest="c" * 64, output_digest="a" * 64)
    assert replay == result
    ledger = BudgetLedger(path)
    budget = ledger.snapshot("root")["agent_calls"]
    assert budget["spent"] == 3
    assert budget["unknown"] == 0
    queue.close()
    ledger.close()


def test_lease_renewal_and_dispatch_result_are_fenced(tmp_path):
    _, queue = _queue(tmp_path, cap=8)
    _enqueue(queue, "job-renew", amount=4)
    claim = queue.claim("job-renew", "worker-a", lease_seconds=5, now=100)
    renewed_until = queue.renew("job-renew", "worker-a", claim["fence"],
                                lease_seconds=20, now=103)
    assert renewed_until == 123
    assert queue.renew("job-renew", "worker-a", claim["fence"],
                       lease_seconds=5, now=104) == 123
    assert queue.expire(now=110) == {"released": 0, "unknown": 0}
    queue.mark_dispatched("job-renew", "worker-a", claim["fence"], now=111)
    with pytest.raises(WorkerQueueError):
        queue.renew("job-renew", "worker-b", claim["fence"], lease_seconds=10, now=112)
    with pytest.raises(RuntimeError, match="injected failure"):
        queue.complete("job-renew", "worker-a", claim["fence"],
                       output_digest="d" * 64, actual_amount=2,
                       receipt_digest="e" * 64, now=113, fail_before_commit=True)
    assert queue.get_job("job-renew")["state"] == "DISPATCHED"
    result = queue.complete("job-renew", "worker-a", claim["fence"],
                            output_digest="d" * 64, actual_amount=2,
                            receipt_digest="e" * 64, now=113)
    assert result["actual_amount"] == 2
    assert queue.get_job("job-renew")["state"] == "COMPLETED"
    queue.close()


def test_postdispatch_cancel_retains_unknown_charge(tmp_path):
    path, queue = _queue(tmp_path, cap=5)
    _enqueue(queue, "job-cancel")
    claim = queue.claim("job-cancel", "worker-a", lease_seconds=30, now=100)
    queue.mark_dispatched("job-cancel", "worker-a", claim["fence"], now=101)
    assert queue.cancel("job-cancel", reason="stop requested") == "UNKNOWN"
    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["unknown"] == 5
    ledger.close()
    queue.close()


def test_actual_provider_overrun_is_recorded_and_blocks_future_claims(tmp_path):
    path, queue = _queue(tmp_path, cap=5)
    _enqueue(queue, "job-overrun", amount=5)
    claim = queue.claim("job-overrun", "worker-a", lease_seconds=30, now=100)
    queue.mark_dispatched("job-overrun", "worker-a", claim["fence"], now=101)
    result = queue.complete("job-overrun", "worker-a", claim["fence"], output_digest="1" * 64,
                            actual_amount=7, receipt_digest="2" * 64, now=102)
    assert result["budget_breached"] is True

    ledger = BudgetLedger(path)
    assert ledger.snapshot("root")["agent_calls"]["spent"] == 7
    ledger.close()
    _enqueue(queue, "job-after-overrun", amount=1)
    with pytest.raises(WorkerBudgetError):
        queue.claim("job-after-overrun", "worker-b", lease_seconds=30, now=103)
    assert queue.get_job("job-after-overrun")["state"] == "READY"
    queue.close()


def test_nested_child_overrun_propagates_to_root_before_next_allocation(tmp_path):
    path, queue = _queue(tmp_path, cap=10)
    ledger = BudgetLedger(path)
    ledger.reserve_child(allocation_id="alloc-parent", idempotency_key="allocate:parent",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 8})
    ledger.bind_child("alloc-parent", "parent", "a" * 64)
    ledger.reserve_child(allocation_id="alloc-grand", idempotency_key="allocate:grand",
                         parent_id="parent", spec_hash="b" * 64,
                         caps={"agent_calls": 5})
    ledger.bind_child("alloc-grand", "grandchild", "b" * 64)
    ledger.close()

    queue.enqueue(job_id="job-grand", enqueue_key="enqueue:grand", campaign_id="grandchild",
                  action_id="action:grand", resource="agent_calls", upper_bound=5,
                  request={"operation": "agent"})
    claim = queue.claim("job-grand", "worker-a", lease_seconds=30, now=100)
    queue.mark_dispatched("job-grand", "worker-a", claim["fence"], now=101)
    queue.complete("job-grand", "worker-a", claim["fence"], output_digest="3" * 64,
                   actual_amount=7, receipt_digest="4" * 64, now=102)

    ledger = BudgetLedger(path)
    ledger.close_child("alloc-grand", terminal_status="STOPPED")
    ledger.close_child("alloc-parent", terminal_status="STOPPED")
    assert ledger.snapshot("root")["agent_calls"]["spent"] == 7
    with pytest.raises(BudgetExceeded):
        ledger.reserve_child(allocation_id="alloc-after-overrun",
                             idempotency_key="allocate:after-overrun", parent_id="root",
                             spec_hash="5" * 64, caps={"agent_calls": 4})
    ledger.close()
    queue.close()
