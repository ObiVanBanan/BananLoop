from __future__ import annotations

import multiprocessing
import hashlib
import json
import sqlite3

import pytest

from bananloop.budget_ledger import BudgetExceeded, BudgetLedger, BudgetLedgerError
from bananloop.worker_queue import DurableWorkerQueue, WorkerBudgetError, WorkerQueueError



def _activate_parent_proof(ledger, *, parent, allocation, child, spec_hash):
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
    db = root / "campaigns" / parent / "campaign.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events(event_type TEXT,payload_json TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS v2_pivot_sagas(
            idempotency_key TEXT PRIMARY KEY,request_hash TEXT,parent_campaign_id TEXT,
            allocation_id TEXT,child_id TEXT,child_spec_hash TEXT,state TEXT
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
    conn.execute("INSERT OR REPLACE INTO v2_pivot_sagas VALUES(?,?,?,?,?,?,?)",
                 (key, request_hash, parent, allocation, child, spec_hash, "ACTIVE"))
    conn.execute("INSERT OR REPLACE INTO v2_pivot_saga_control VALUES(?,?,?,?)",
                 (key, "ACTIVE", digest, now))
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


def _set_child_status(ledger, child: str, status: str):
    from pathlib import Path
    db = Path(ledger.path).parent / "campaigns" / child / "campaign.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE campaigns SET status=? WHERE id=1", (status,))
    conn.commit()
    conn.close()

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


def test_complete_persists_canonical_result_for_projection_replay(tmp_path):
    import hashlib
    import json

    _, queue = _queue(tmp_path, cap=3)
    _enqueue(queue, "job-result", amount=1)
    claim = queue.claim("job-result", "worker-result", lease_seconds=30, now=100)
    queue.mark_dispatched("job-result", "worker-result", claim["fence"], now=101)
    result = {"node_id": "n1", "evidence_refs": [], "artifacts": [],
              "limitations": [], "next_experiment": "try again"}
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    queue.complete("job-result", "worker-result", claim["fence"], output_digest=digest,
                   actual_amount=1, receipt_digest="f" * 64, now=102, result_payload=result)
    assert queue.get_result("job-result")["result"] == result
    with pytest.raises(WorkerQueueError, match="does not match"):
        queue.complete("job-result", "worker-result", claim["fence"], output_digest="a" * 64,
                       actual_amount=1, receipt_digest="f" * 64, now=103, result_payload=result)
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
    digest = _activate_parent_proof(ledger, parent="root", allocation="alloc-child",
                                    child="child", spec_hash="a" * 64)
    ledger.activate_child("alloc-child", "child", "a" * 64, digest)
    ledger.close()
    queue.enqueue(job_id="job-child", enqueue_key="enqueue:child", campaign_id="child",
                  action_id="action:child", resource="agent_calls", upper_bound=4,
                  request={"operation": "agent"})
    claim = queue.claim("job-child", "worker-child", lease_seconds=30, now=100)
    queue.mark_dispatched("job-child", "worker-child", claim["fence"], now=101)
    queue.complete("job-child", "worker-child", claim["fence"], output_digest="f" * 64,
                   actual_amount=3, receipt_digest="e" * 64, now=102)
    ledger = BudgetLedger(path)
    _set_child_status(ledger, "child", "GOAL_MET")
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
    digest = _activate_parent_proof(ledger, parent="root", allocation="alloc-parent",
                                    child="parent", spec_hash="a" * 64)
    ledger.activate_child("alloc-parent", "parent", "a" * 64, digest)
    ledger.reserve_child(allocation_id="alloc-grand", idempotency_key="allocate:grand",
                         parent_id="parent", spec_hash="b" * 64,
                         caps={"agent_calls": 5})
    ledger.bind_child("alloc-grand", "grandchild", "b" * 64)
    digest = _activate_parent_proof(ledger, parent="parent", allocation="alloc-grand",
                                    child="grandchild", spec_hash="b" * 64)
    ledger.activate_child("alloc-grand", "grandchild", "b" * 64, digest)
    ledger.close()

    queue.enqueue(job_id="job-grand", enqueue_key="enqueue:grand", campaign_id="grandchild",
                  action_id="action:grand", resource="agent_calls", upper_bound=5,
                  request={"operation": "agent"})
    claim = queue.claim("job-grand", "worker-a", lease_seconds=30, now=100)
    queue.mark_dispatched("job-grand", "worker-a", claim["fence"], now=101)
    queue.complete("job-grand", "worker-a", claim["fence"], output_digest="3" * 64,
                   actual_amount=7, receipt_digest="4" * 64, now=102)

    ledger = BudgetLedger(path)
    _set_child_status(ledger, "grandchild", "STOPPED")
    ledger.close_child("alloc-grand", terminal_status="STOPPED")
    _set_child_status(ledger, "parent", "STOPPED")
    ledger.close_child("alloc-parent", terminal_status="STOPPED")
    assert ledger.snapshot("root")["agent_calls"]["spent"] == 7
    with pytest.raises(BudgetExceeded):
        ledger.reserve_child(allocation_id="alloc-after-overrun",
                             idempotency_key="allocate:after-overrun", parent_id="root",
                             spec_hash="5" * 64, caps={"agent_calls": 4})
    ledger.close()
    queue.close()


def _vector_queue(tmp_path, *, evaluator_calls=10, evaluation_units=100):
    path = tmp_path / "vector-registry.sqlite3"
    ledger = BudgetLedger(path)
    ledger.create_root("root", {
        "evaluator_calls": evaluator_calls,
        "evaluation_units": evaluation_units,
    })
    ledger.close()
    return path, DurableWorkerQueue(path)


def _enqueue_vector(queue, job_id, *, calls=1, units=10, campaign_id="root", action_id=None):
    return queue.enqueue(
        job_id=job_id, enqueue_key=f"enqueue:{job_id}", campaign_id=campaign_id,
        action_id=action_id or f"action:{job_id}", resource="evaluator_calls",
        upper_bound=calls, resource_bounds={"evaluator_calls": calls, "evaluation_units": units},
        request={"operation": "evaluate", "job": job_id},
    )


def test_vector_claim_is_atomic_when_one_dimension_exceeds_its_cap(tmp_path):
    path, queue = _vector_queue(tmp_path, evaluator_calls=5, evaluation_units=2)
    _enqueue_vector(queue, "job-vector-limit", calls=1, units=3)
    with pytest.raises(WorkerBudgetError):
        queue.claim("job-vector-limit", "worker-vector", lease_seconds=30, now=100)
    assert queue.get_job("job-vector-limit")["state"] == "READY"
    assert queue.conn.execute(
        "SELECT COUNT(*) FROM worker_job_reservations WHERE job_id='job-vector-limit'"
    ).fetchone()[0] == 0
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["reserved"] == 0
    assert snapshot["evaluation_units"]["reserved"] == 0
    ledger.close()
    queue.close()


def test_vector_enqueue_replay_and_complete_bind_all_resource_amounts(tmp_path):
    path, queue = _vector_queue(tmp_path)
    _enqueue_vector(queue, "job-vector-complete", calls=1, units=20)
    replay = _enqueue_vector(queue, "job-vector-complete", calls=1, units=20)
    assert replay["resource_bounds"] == {"evaluator_calls": 1, "evaluation_units": 20}
    with pytest.raises(WorkerQueueError, match="different resource bounds"):
        _enqueue_vector(queue, "job-vector-complete", calls=1, units=21)
    claim = queue.claim("job-vector-complete", "worker-vector", lease_seconds=30, now=100)
    assert claim["reservations"] == {
        "evaluator_calls": claim["reservation_id"],
        "evaluation_units": "job:job-vector-complete:f1:evaluation_units",
    }
    queue.mark_dispatched("job-vector-complete", "worker-vector", claim["fence"], now=101)
    with pytest.raises(WorkerQueueError, match="every reserved resource"):
        queue.complete(
            "job-vector-complete", "worker-vector", claim["fence"], output_digest="a" * 64,
            actual_usage={"evaluator_calls": 1}, receipt_digest="b" * 64, now=101,
        )
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["reserved"] == 1
    assert snapshot["evaluation_units"]["reserved"] == 20
    reservations = ledger.conn.execute(
        "SELECT resource,dispatch_state FROM budget_reservations WHERE action_id='action:job-vector-complete:1' ORDER BY resource"
    ).fetchall()
    assert [(row["resource"], row["dispatch_state"]) for row in reservations] == [
        ("evaluation_units", "DISPATCHED"), ("evaluator_calls", "DISPATCHED"),
    ]
    ledger.close()
    with pytest.raises(RuntimeError, match="injected failure"):
        queue.complete(
            "job-vector-complete", "worker-vector", claim["fence"], output_digest="a" * 64,
            actual_usage={"evaluator_calls": 1, "evaluation_units": 7},
            receipt_digest="b" * 64, now=102, fail_before_commit=True,
        )
    assert queue.get_job("job-vector-complete")["state"] == "DISPATCHED"
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["reserved"] == 1
    assert snapshot["evaluation_units"]["reserved"] == 20
    assert snapshot["evaluator_calls"]["spent"] == 0
    assert snapshot["evaluation_units"]["spent"] == 0
    ledger.close()
    result = queue.complete(
        "job-vector-complete", "worker-vector", claim["fence"], output_digest="a" * 64,
        actual_usage={"evaluator_calls": 1, "evaluation_units": 7},
        receipt_digest="b" * 64, now=102,
    )
    assert result["actual_amount"] == 1
    assert result["actual_usage"] == {"evaluator_calls": 1, "evaluation_units": 7}
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["spent"] == 1
    assert snapshot["evaluation_units"]["spent"] == 7
    assert snapshot["evaluation_units"]["reserved"] == 0
    rows = ledger.conn.execute(
        "SELECT resource,receipt_digest FROM budget_reservations WHERE action_id='action:job-vector-complete:1' ORDER BY resource"
    ).fetchall()
    assert [(row["resource"], row["receipt_digest"]) for row in rows] == [
        ("evaluation_units", "b" * 64), ("evaluator_calls", "b" * 64),
    ]
    ledger.close()
    with pytest.raises(WorkerQueueError, match="different committed result"):
        queue.complete(
            "job-vector-complete", "worker-vector", claim["fence"], output_digest="a" * 64,
            actual_usage={"evaluator_calls": 1, "evaluation_units": 8},
            receipt_digest="b" * 64, now=103,
        )
    queue.close()


def test_vector_cancel_expiry_and_unknown_reconciliation_cover_every_dimension(tmp_path):
    path, queue = _vector_queue(tmp_path, evaluator_calls=5, evaluation_units=50)
    _enqueue_vector(queue, "job-vector-expire", calls=2, units=20)
    first = queue.claim("job-vector-expire", "worker-one", lease_seconds=5, now=100)
    assert queue.expire(now=106) == {"released": 1, "unknown": 0}
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["reserved"] == 0
    assert snapshot["evaluation_units"]["reserved"] == 0
    ledger.close()

    second = queue.claim("job-vector-expire", "worker-two", lease_seconds=30, now=110)
    assert second["fence"] == first["fence"] + 1
    assert queue.cancel("job-vector-expire", reason="cancel before dispatch") == "CANCELLED"
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["reserved"] == 0
    assert snapshot["evaluation_units"]["reserved"] == 0
    ledger.close()
    _enqueue_vector(queue, "job-vector-unknown", calls=2, units=20)
    third = queue.claim("job-vector-unknown", "worker-three", lease_seconds=30, now=120)
    queue.mark_dispatched("job-vector-unknown", "worker-three", third["fence"], now=121)
    assert queue.cancel("job-vector-unknown", reason="test ambiguous dispatch") == "UNKNOWN"
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["unknown"] == 2
    assert snapshot["evaluation_units"]["unknown"] == 20
    ledger.close()

    result = queue.reconcile_unknown(
        "job-vector-unknown", actual_usage={"evaluator_calls": 1, "evaluation_units": 6},
        receipt_digest="c" * 64, output_digest="d" * 64,
    )
    assert result["actual_usage"] == {"evaluator_calls": 1, "evaluation_units": 6}
    assert queue.reconcile_unknown(
        "job-vector-unknown", actual_usage={"evaluator_calls": 1, "evaluation_units": 6},
        receipt_digest="c" * 64, output_digest="d" * 64,
    ) == result
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["spent"] == 1
    assert snapshot["evaluation_units"]["spent"] == 6
    assert snapshot["evaluator_calls"]["unknown"] == 0
    assert snapshot["evaluation_units"]["unknown"] == 0
    ledger.close()

    _enqueue_vector(queue, "job-vector-expired-unknown", calls=1, units=5)
    fourth = queue.claim("job-vector-expired-unknown", "worker-four", lease_seconds=5, now=200)
    queue.mark_dispatched("job-vector-expired-unknown", "worker-four", fourth["fence"], now=201)
    assert queue.expire(now=206) == {"released": 0, "unknown": 1}
    ledger = BudgetLedger(path)
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["unknown"] == 1
    assert snapshot["evaluation_units"]["unknown"] == 5
    ledger.close()
    queue.close()


def test_vector_child_allocation_settles_each_independent_resource(tmp_path):
    path, queue = _vector_queue(tmp_path, evaluator_calls=10, evaluation_units=100)
    ledger = BudgetLedger(path)
    ledger.reserve_child(
        allocation_id="alloc-vector-child", idempotency_key="allocate:vector-child",
        parent_id="root", spec_hash="a" * 64,
        caps={"evaluator_calls": 4, "evaluation_units": 40},
    )
    ledger.bind_child("alloc-vector-child", "child-vector", "a" * 64)
    digest = _activate_parent_proof(ledger, parent="root", allocation="alloc-vector-child",
                                    child="child-vector", spec_hash="a" * 64)
    ledger.activate_child("alloc-vector-child", "child-vector", "a" * 64, digest)
    ledger.close()
    _enqueue_vector(queue, "job-child-vector", calls=2, units=20,
                    campaign_id="child-vector", action_id="evaluate:child")
    claim = queue.claim("job-child-vector", "worker-child", lease_seconds=30, now=100)
    queue.mark_dispatched("job-child-vector", "worker-child", claim["fence"], now=101)
    queue.complete(
        "job-child-vector", "worker-child", claim["fence"], output_digest="e" * 64,
        actual_usage={"evaluator_calls": 1, "evaluation_units": 8},
        receipt_digest="f" * 64, now=102,
    )
    ledger = BudgetLedger(path)
    _set_child_status(ledger, "child-vector", "GOAL_MET")
    ledger.close_child("alloc-vector-child", terminal_status="GOAL_MET")
    snapshot = ledger.snapshot("root")
    assert snapshot["evaluator_calls"]["spent"] == 1
    assert snapshot["evaluation_units"]["spent"] == 8
    assert snapshot["evaluator_calls"]["reserved"] == 0
    assert snapshot["evaluation_units"]["reserved"] == 0
    ledger.close()
    queue.close()


def test_child_worker_claim_rolls_back_while_allocation_gate_is_bound(tmp_path):
    path, queue = _queue(tmp_path, cap=10)
    queue.enqueue(job_id="job-bound", enqueue_key="enqueue:bound", campaign_id="child-bound",
                  action_id="action:bound", resource="agent_calls", upper_bound=4,
                  request={"operation": "agent"})
    ledger = BudgetLedger(path)
    ledger.reserve_child(allocation_id="alloc-bound", idempotency_key="allocate:bound",
                         parent_id="root", spec_hash="a" * 64,
                         caps={"agent_calls": 6})
    ledger.bind_child("alloc-bound", "child-bound", "a" * 64)
    ledger.close()
    with pytest.raises(WorkerQueueError, match="admission gate"):
        queue.enqueue(job_id="job-bound-late", enqueue_key="enqueue:bound-late",
                      campaign_id="child-bound", action_id="action:late",
                      resource="agent_calls", upper_bound=1,
                      request={"operation": "agent"})
    with pytest.raises(WorkerQueueError):
        queue.claim("job-bound", "worker-bound", lease_seconds=30, now=100)
    assert queue.get_job("job-bound")["state"] == "READY"
    ledger = BudgetLedger(path)
    try:
        assert ledger.conn.execute(
            "SELECT state FROM child_allocation_gates WHERE allocation_id='alloc-bound'",
        ).fetchone()[0] == "BOUND"
        assert ledger.conn.execute(
            "SELECT COUNT(*) FROM budget_reservations WHERE allocation_id='alloc-bound'",
        ).fetchone()[0] == 0
    finally:
        ledger.close()
        queue.close()
