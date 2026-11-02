"""Durable v2 worker queue colocated with the project budget ledger.

The queue and ledger share one registry SQLite file. Only trusted Core code
uses this class; worker processes receive payloads and fences, never this DB.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator
from contextlib import contextmanager

from .budget_ledger import BudgetLedger, BudgetLedgerError
from .contracts import utc_now


_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkerQueueError(RuntimeError):
    """Invalid worker state transition or stale lease/fence."""


class WorkerBudgetError(WorkerQueueError):
    """Worker action could not obtain or settle its budget reservation."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DurableWorkerQueue:
    """Project-wide queue with transactional claim, budget hold, and event log."""

    def __init__(self, registry_path: str | Path):
        self.path = Path(registry_path)
        bootstrap = BudgetLedger(self.path)
        bootstrap.close()
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS worker_jobs (
          job_id TEXT PRIMARY KEY,
          enqueue_key TEXT NOT NULL UNIQUE,
          campaign_id TEXT NOT NULL,
          action_id TEXT NOT NULL,
          resource TEXT NOT NULL,
          upper_bound INTEGER NOT NULL CHECK(upper_bound>0),
          request_hash TEXT NOT NULL,
          request_json TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('READY','LEASED','DISPATCHED','UNKNOWN','COMPLETED','CANCELLED')),
          fence INTEGER NOT NULL DEFAULT 0 CHECK(fence>=0),
          lease_owner TEXT,
          lease_until REAL,
          reservation_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS worker_jobs_ready_idx ON worker_jobs(state,created_at,job_id);
        CREATE TABLE IF NOT EXISTS worker_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL REFERENCES worker_jobs(job_id),
          event_type TEXT NOT NULL,
          fence INTEGER NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS worker_events_job_idx ON worker_events(job_id,event_id);
        CREATE TABLE IF NOT EXISTS worker_results (
          job_id TEXT PRIMARY KEY REFERENCES worker_jobs(job_id),
          fence INTEGER NOT NULL,
          output_digest TEXT NOT NULL,
          actual_amount INTEGER NOT NULL CHECK(actual_amount>=0),
          receipt_digest TEXT NOT NULL,
          response_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS team_role_call_jobs (
          job_id TEXT PRIMARY KEY REFERENCES worker_jobs(job_id),
          campaign_id TEXT NOT NULL,
          manifest_digest TEXT NOT NULL,
          role_instance_id TEXT NOT NULL,
          role_id TEXT NOT NULL,
          upper_bound INTEGER NOT NULL CHECK(upper_bound>0),
          request_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS team_role_call_jobs_scope_idx
          ON team_role_call_jobs(campaign_id,manifest_digest,role_instance_id);
        CREATE TABLE IF NOT EXISTS team_role_token_jobs (
          job_id TEXT PRIMARY KEY REFERENCES worker_jobs(job_id),
          campaign_id TEXT NOT NULL,
          manifest_digest TEXT NOT NULL,
          role_instance_id TEXT NOT NULL,
          role_id TEXT NOT NULL,
          upper_bound INTEGER NOT NULL CHECK(upper_bound>0),
          request_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS team_role_token_jobs_scope_idx
          ON team_role_token_jobs(campaign_id,manifest_digest,role_instance_id);
        CREATE TABLE IF NOT EXISTS team_role_allocation_releases (
          job_id TEXT PRIMARY KEY REFERENCES worker_jobs(job_id),
          reason TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS team_role_call_jobs_no_update
          BEFORE UPDATE ON team_role_call_jobs BEGIN SELECT RAISE(ABORT,'team role call jobs are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_call_jobs_no_delete
          BEFORE DELETE ON team_role_call_jobs BEGIN SELECT RAISE(ABORT,'team role call jobs are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_token_jobs_no_update
          BEFORE UPDATE ON team_role_token_jobs BEGIN SELECT RAISE(ABORT,'team role token jobs are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_token_jobs_no_delete
          BEFORE DELETE ON team_role_token_jobs BEGIN SELECT RAISE(ABORT,'team role token jobs are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_allocation_releases_no_update
          BEFORE UPDATE ON team_role_allocation_releases BEGIN SELECT RAISE(ABORT,'team allocation releases are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS team_role_allocation_releases_no_delete
          BEFORE DELETE ON team_role_allocation_releases BEGIN SELECT RAISE(ABORT,'team allocation releases are immutable'); END;
        """)
        self.ledger = BudgetLedger.attach(self.conn)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def enqueue(
        self, *, job_id: str, enqueue_key: str, campaign_id: str, action_id: str,
        resource: str, upper_bound: int, request: dict[str, Any],
        role_allocation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist immutable job input; an identical enqueue key replays safely."""
        for value, label, pattern in (
            (job_id, "job_id", _JOB_ID), (enqueue_key, "enqueue_key", _JOB_ID),
            (campaign_id, "campaign_id", _JOB_ID), (action_id, "action_id", _JOB_ID),
        ):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise WorkerQueueError(f"invalid {label}")
        if not isinstance(request, dict):
            raise WorkerQueueError("request must be a JSON object")
        try:
            request_json = _canonical(request)
        except (TypeError, ValueError) as exc:
            raise WorkerQueueError(f"request is not canonical JSON: {exc}") from exc
        request_hash = _digest(request_json)
        if role_allocation is not None:
            if resource != "agent_calls":
                raise WorkerQueueError("role allocations can only bind agent-call jobs")
            if not isinstance(role_allocation, dict) or set(role_allocation) != {
                    "manifest_digest", "role_instance_id", "role_id", "calls_limit",
                    "tokens_limit", "token_upper_bound"}:
                raise WorkerQueueError("role_allocation must provide immutable call and token ceilings")
            if (not _SHA256.fullmatch(str(role_allocation["manifest_digest"]))
                    or not isinstance(role_allocation["role_instance_id"], str)
                    or not _JOB_ID.fullmatch(role_allocation["role_instance_id"])
                    or not isinstance(role_allocation["role_id"], str)
                    or not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", role_allocation["role_id"])
                    or isinstance(role_allocation["calls_limit"], bool)
                    or not isinstance(role_allocation["calls_limit"], int)
                    or role_allocation["calls_limit"] < 1
                    or isinstance(role_allocation["tokens_limit"], bool)
                    or not isinstance(role_allocation["tokens_limit"], int)
                    or role_allocation["tokens_limit"] < 1
                    or isinstance(role_allocation["token_upper_bound"], bool)
                    or not isinstance(role_allocation["token_upper_bound"], int)
                    or role_allocation["token_upper_bound"] < 1
                    or role_allocation["token_upper_bound"] > role_allocation["tokens_limit"]):
                raise WorkerQueueError("role_allocation contains invalid values")
            binding = request.get("role_binding")
            if not isinstance(binding, dict) or (
                    binding.get("team_manifest_digest") != role_allocation["manifest_digest"]
                    or binding.get("role_instance_id") != role_allocation["role_instance_id"]
                    or binding.get("role_id") != role_allocation["role_id"]
                    or binding.get("allocated_agent_calls") != role_allocation["calls_limit"]
                    or binding.get("allocated_model_tokens") != role_allocation["tokens_limit"]
                    or binding.get("per_call_model_token_limit") != role_allocation["token_upper_bound"]):
                raise WorkerQueueError("immutable job request is not bound to its role allocation")
        if resource not in {"agent_calls", "evaluator_calls", "model_tokens", "evaluation_units", "experiments", "attempts"}:
            raise WorkerQueueError(f"unsupported resource: {resource}")
        if isinstance(upper_bound, bool) or not isinstance(upper_bound, int) or upper_bound <= 0:
            raise WorkerQueueError("upper_bound must be a positive integer")
        now = utc_now()
        with self._transaction():
            replay = self.conn.execute("SELECT * FROM worker_jobs WHERE enqueue_key=?", (enqueue_key,)).fetchone()
            if replay:
                expected = (job_id, campaign_id, action_id, resource, upper_bound, request_hash)
                actual = (replay["job_id"], replay["campaign_id"], replay["action_id"],
                          replay["resource"], int(replay["upper_bound"]), replay["request_hash"])
                if actual != expected:
                    raise WorkerQueueError("enqueue key was reused with different job content")
                allocation = self.conn.execute(
                    "SELECT * FROM team_role_call_jobs WHERE job_id=?", (job_id,),
                ).fetchone()
                token_allocation = self.conn.execute(
                    "SELECT * FROM team_role_token_jobs WHERE job_id=?", (job_id,),
                ).fetchone()
                if role_allocation is None and allocation is not None:
                    raise WorkerQueueError("replayed role-bound job omitted its allocation")
                if role_allocation is None and token_allocation is not None:
                    raise WorkerQueueError("replayed role-bound job omitted its token allocation")
                if role_allocation is not None:
                    wanted = (campaign_id, role_allocation["manifest_digest"],
                              role_allocation["role_instance_id"], role_allocation["role_id"],
                              upper_bound, request_hash)
                    actual_allocation = None if allocation is None else (
                        allocation["campaign_id"], allocation["manifest_digest"],
                        allocation["role_instance_id"], allocation["role_id"],
                        int(allocation["upper_bound"]), allocation["request_hash"],
                    )
                    if actual_allocation != wanted:
                        raise WorkerQueueError("replayed role-bound job has a different allocation")
                    wanted_token = (campaign_id, role_allocation["manifest_digest"],
                                    role_allocation["role_instance_id"], role_allocation["role_id"],
                                    role_allocation["token_upper_bound"] * upper_bound, request_hash)
                    actual_token = None if token_allocation is None else (
                        token_allocation["campaign_id"], token_allocation["manifest_digest"],
                        token_allocation["role_instance_id"], token_allocation["role_id"],
                        int(token_allocation["upper_bound"]), token_allocation["request_hash"],
                    )
                    if actual_token != wanted_token:
                        raise WorkerQueueError("replayed role-bound job has a different token allocation")
                return self._job_view(replay)
            if role_allocation is not None:
                pinned = self.conn.execute(
                    "SELECT calls_limit FROM team_role_call_allocations WHERE campaign_id=? AND manifest_digest=? AND role_instance_id=? AND role_id=?",
                    (campaign_id, role_allocation["manifest_digest"],
                     role_allocation["role_instance_id"], role_allocation["role_id"]),
                ).fetchone()
                if pinned is None or int(pinned["calls_limit"]) != role_allocation["calls_limit"]:
                    raise WorkerQueueError("role call allocation is not present in the pinned team manifest")
                used = int(self.conn.execute(
                    "SELECT COALESCE(SUM(upper_bound),0) FROM team_role_call_jobs WHERE campaign_id=? AND manifest_digest=? AND role_instance_id=? AND job_id NOT IN (SELECT job_id FROM team_role_allocation_releases)",
                    (campaign_id, role_allocation["manifest_digest"], role_allocation["role_instance_id"]),
                ).fetchone()[0])
                if used + upper_bound > int(pinned["calls_limit"]):
                    raise WorkerBudgetError("role instance agent-call allocation exhausted")
                pinned_tokens = self.conn.execute(
                    "SELECT tokens_limit FROM team_role_token_allocations WHERE campaign_id=? AND manifest_digest=? AND role_instance_id=? AND role_id=?",
                    (campaign_id, role_allocation["manifest_digest"],
                     role_allocation["role_instance_id"], role_allocation["role_id"]),
                ).fetchone()
                if pinned_tokens is None or int(pinned_tokens["tokens_limit"]) != role_allocation["tokens_limit"]:
                    raise WorkerQueueError("role token allocation is not present in the pinned team manifest")
                token_used = int(self.conn.execute(
                    "SELECT COALESCE(SUM(upper_bound),0) FROM team_role_token_jobs WHERE campaign_id=? AND manifest_digest=? AND role_instance_id=? AND job_id NOT IN (SELECT job_id FROM team_role_allocation_releases)",
                    (campaign_id, role_allocation["manifest_digest"], role_allocation["role_instance_id"]),
                ).fetchone()[0])
                token_cost = role_allocation["token_upper_bound"] * upper_bound
                if token_used + token_cost > int(pinned_tokens["tokens_limit"]):
                    raise WorkerBudgetError("role instance model-token allocation exhausted")
            self.conn.execute(
                "INSERT INTO worker_jobs(job_id,enqueue_key,campaign_id,action_id,resource,upper_bound,request_hash,request_json,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,'READY',?,?)",
                (job_id, enqueue_key, campaign_id, action_id, resource, upper_bound, request_hash, request_json, now, now),
            )
            if role_allocation is not None:
                self.conn.execute(
                    "INSERT INTO team_role_call_jobs(job_id,campaign_id,manifest_digest,role_instance_id,role_id,upper_bound,request_hash) VALUES(?,?,?,?,?,?,?)",
                    (job_id, campaign_id, role_allocation["manifest_digest"],
                     role_allocation["role_instance_id"], role_allocation["role_id"], upper_bound, request_hash),
                )
                self.conn.execute(
                    "INSERT INTO team_role_token_jobs(job_id,campaign_id,manifest_digest,role_instance_id,role_id,upper_bound,request_hash) VALUES(?,?,?,?,?,?,?)",
                    (job_id, campaign_id, role_allocation["manifest_digest"],
                     role_allocation["role_instance_id"], role_allocation["role_id"],
                     role_allocation["token_upper_bound"] * upper_bound, request_hash),
                )
            self._event(job_id, "JOB_ENQUEUED", 0, {"request_hash": request_hash})
            return self._job_view(self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone())

    def claim(self, job_id: str, worker_id: str, *, lease_seconds: int, now: float | None = None,
              fail_before_commit: bool = False) -> dict[str, Any]:
        """Atomically acquire a lease, reserve budget, and append JOB_LEASED."""
        self._validate_job_id(job_id)
        if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
            raise WorkerQueueError("invalid worker_id")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise WorkerQueueError("lease_seconds must be a positive integer")
        effective_now = time.time() if now is None else now
        if isinstance(effective_now, bool) or not isinstance(effective_now, (int, float)) or not math.isfinite(effective_now):
            raise WorkerQueueError("now must be a finite timestamp")
        with self._transaction():
            job = self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None or job["state"] != "READY":
                raise WorkerQueueError("job is missing or not READY")
            fence = int(job["fence"]) + 1
            action_id = f"{job['action_id']}:{fence}"
            reservation_id = f"job:{job_id}:f{fence}:{job['resource']}"
            enqueue_alloc = self.conn.execute(
                "SELECT allocation_id,state FROM child_allocations WHERE child_id=?",
                (job["campaign_id"],),
            ).fetchone()
            if enqueue_alloc is not None:
                if enqueue_alloc["state"] != "ACTIVE":
                    raise WorkerQueueError("child campaign allocation is not active")
                allocation_id = enqueue_alloc["allocation_id"]
            else:
                allocation_id = None
            try:
                self.ledger.reserve_action(
                    reservation_id=reservation_id,
                    campaign_id=job["campaign_id"],
                    action_id=action_id,
                    resource=job["resource"],
                    amount=int(job["upper_bound"]),
                    idempotency_key=reservation_id,
                    fence=fence,
                    allocation_id=allocation_id,
                )
            except BudgetLedgerError as exc:
                raise WorkerBudgetError(str(exc)) from exc
            lease_until = effective_now + lease_seconds
            changed = self.conn.execute(
                "UPDATE worker_jobs SET state='LEASED',fence=?,lease_owner=?,lease_until=?,reservation_id=?,updated_at=? "
                "WHERE job_id=? AND state='READY'",
                (fence, worker_id, lease_until, reservation_id, utc_now(), job_id),
            ).rowcount
            if changed != 1:
                raise WorkerQueueError("job state changed during claim")
            self._event(job_id, "JOB_LEASED", fence,
                        {"worker_id": worker_id, "reservation_id": reservation_id, "lease_until": lease_until})
            if fail_before_commit:
                raise RuntimeError("injected failure before atomic claim commit")
            return {"job_id": job_id, "worker_id": worker_id, "fence": fence,
                    "lease_until": lease_until, "reservation_id": reservation_id,
                    "request_hash": job["request_hash"], "request": json.loads(job["request_json"])}

    def mark_dispatched(self, job_id: str, worker_id: str, fence: int, *, now: float | None = None) -> None:
        """Commit the dispatch boundary before calling any external worker/provider."""
        effective_now = time.time() if now is None else now
        if isinstance(effective_now, bool) or not isinstance(effective_now, (int, float)) or not math.isfinite(effective_now):
            raise WorkerQueueError("now must be a finite timestamp")
        with self._transaction():
            job = self._owned_job(job_id, worker_id, fence, state="LEASED", now=effective_now)
            changed = self.conn.execute(
                "UPDATE worker_jobs SET state='DISPATCHED',updated_at=? WHERE job_id=? AND state='LEASED' AND fence=? AND lease_until>?",
                (utc_now(), job_id, fence, effective_now),
            ).rowcount
            if changed != 1:
                raise WorkerQueueError("lease expired before dispatch")
            try:
                self.ledger.mark_dispatched(job["reservation_id"], fence=fence)
            except BudgetLedgerError as exc:
                raise WorkerBudgetError(str(exc)) from exc
            self._event(job_id, "JOB_DISPATCHED", fence, {"reservation_id": job["reservation_id"]})

    def renew(self, job_id: str, worker_id: str, fence: int, *, lease_seconds: int,
              now: float | None = None) -> float:
        """Extend only the current, still-live lease generation."""
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise WorkerQueueError("lease_seconds must be a positive integer")
        effective_now = time.time() if now is None else now
        if isinstance(effective_now, bool) or not isinstance(effective_now, (int, float)) or not math.isfinite(effective_now):
            raise WorkerQueueError("now must be a finite timestamp")
        with self._transaction():
            job = self._owned_job(job_id, worker_id, fence, states={"LEASED", "DISPATCHED"}, now=effective_now)
            lease_until = max(effective_now + lease_seconds, float(job["lease_until"]))
            changed = self.conn.execute(
                "UPDATE worker_jobs SET lease_until=?,updated_at=? WHERE job_id=? AND lease_owner=? AND fence=? AND state IN ('LEASED','DISPATCHED') AND lease_until>?",
                (lease_until, utc_now(), job_id, worker_id, fence, effective_now),
            ).rowcount
            if changed != 1:
                raise WorkerQueueError("lease renewal lost its fence race")
            self._event(job_id, "LEASE_RENEWED", fence, {"lease_until": lease_until})
            return lease_until

    def complete(
        self, job_id: str, worker_id: str, fence: int, *, output_digest: str,
        actual_amount: int, receipt_digest: str, now: float | None = None,
        result_payload: dict[str, Any] | None = None, fail_before_commit: bool = False,
    ) -> dict[str, Any]:
        """Commit a result and settle its reservation under the active fence.

        The trusted Core must verify the receipt and output before calling this.
        """
        self._validate_job_id(job_id)
        if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
            raise WorkerQueueError("invalid worker_id")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise WorkerQueueError("fence must be a nonnegative integer")
        self._validate_digest(output_digest, "output_digest")
        self._validate_digest(receipt_digest, "receipt_digest")
        if isinstance(actual_amount, bool) or not isinstance(actual_amount, int) or actual_amount < 0:
            raise WorkerQueueError("actual_amount must be a nonnegative integer")
        result_json = None
        if result_payload is not None:
            if not isinstance(result_payload, dict):
                raise WorkerQueueError("result_payload must be a JSON object")
            try:
                result_json = _canonical(result_payload)
            except (TypeError, ValueError) as exc:
                raise WorkerQueueError(f"result_payload is not canonical JSON: {exc}") from exc
            if len(result_json.encode("utf-8")) > 2_000_000:
                raise WorkerQueueError("result_payload exceeds the 2 MB durable-result limit")
            if _digest(result_json) != output_digest:
                raise WorkerQueueError("output_digest does not match the durable result payload")
        effective_now = time.time() if now is None else now
        if isinstance(effective_now, bool) or not isinstance(effective_now, (int, float)) or not math.isfinite(effective_now):
            raise WorkerQueueError("now must be a finite timestamp")
        with self._transaction():
            prior = self.conn.execute("SELECT * FROM worker_results WHERE job_id=?", (job_id,)).fetchone()
            if prior is not None:
                expected = (fence, output_digest, actual_amount, receipt_digest)
                actual = (int(prior["fence"]), prior["output_digest"],
                          int(prior["actual_amount"]), prior["receipt_digest"])
                if expected != actual:
                    raise WorkerQueueError("job already has a different committed result")
                return json.loads(prior["response_json"])
            job = self._owned_job(job_id, worker_id, fence, state="DISPATCHED", now=effective_now)
            try:
                self.ledger.settle_action(job["reservation_id"], actual=actual_amount,
                                          quality="actual", receipt_digest=receipt_digest,
                                          fence=fence)
            except BudgetLedgerError as exc:
                raise WorkerBudgetError(str(exc)) from exc
            budget = self.ledger.snapshot(job["campaign_id"])[job["resource"]]
            budget_breached = budget["spent"] + budget["reserved"] + budget["unknown"] > budget["cap"]
            response = {"job_id": job_id, "fence": fence, "output_digest": output_digest,
                        "actual_amount": actual_amount, "receipt_digest": receipt_digest,
                        "budget_breached": budget_breached}
            if result_payload is not None:
                response["result"] = result_payload
            self.conn.execute("UPDATE worker_jobs SET state='COMPLETED',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                              (utc_now(), job_id))
            self.conn.execute("INSERT INTO worker_results VALUES(?,?,?,?,?,?,?)",
                              (job_id, fence, output_digest, actual_amount, receipt_digest, _canonical(response), utc_now()))
            self._event(job_id, "JOB_COMPLETED", fence, response)
            if fail_before_commit:
                raise RuntimeError("injected failure before atomic result commit")
            return response

    def cancel(self, job_id: str, *, reason: str) -> str:
        """Cancel READY/LEASED jobs; dispatched cancellation becomes UNKNOWN."""
        self._validate_job_id(job_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise WorkerQueueError("cancellation reason must be nonempty and bounded")
        with self._transaction():
            job = self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise WorkerQueueError("unknown job")
            if job["state"] == "CANCELLED":
                return "CANCELLED"
            if job["state"] == "COMPLETED":
                raise WorkerQueueError("completed job cannot be cancelled")
            if job["state"] == "UNKNOWN":
                return "UNKNOWN"
            if job["state"] == "LEASED":
                try:
                    self.ledger.release_action(job["reservation_id"], fence=int(job["fence"]),
                                               dispatch_proven_absent=True)
                except BudgetLedgerError as exc:
                    raise WorkerBudgetError(str(exc)) from exc
                state = "CANCELLED"
            elif job["state"] == "DISPATCHED":
                state = "UNKNOWN"
                self._mark_unknown(job)
            else:
                state = "CANCELLED"
            self.conn.execute("UPDATE worker_jobs SET state=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                              (state, utc_now(), job_id))
            if state == "CANCELLED" and self.conn.execute(
                    "SELECT 1 FROM team_role_call_jobs WHERE job_id=?", (job_id,)).fetchone():
                self.conn.execute(
                    "INSERT OR IGNORE INTO team_role_allocation_releases(job_id,reason,created_at) VALUES(?,?,?)",
                    (job_id, "dispatch_proven_absent", utc_now()),
                )
            self._event(job_id, "JOB_CANCELLED" if state == "CANCELLED" else "JOB_UNKNOWN",
                        int(job["fence"]), {"reason": reason})
            return state

    def expire(self, *, now: float | None = None) -> dict[str, int]:
        """Release expired pre-dispatch leases; retain dispatched calls as UNKNOWN."""
        effective_now = time.time() if now is None else now
        if isinstance(effective_now, bool) or not isinstance(effective_now, (int, float)) or not math.isfinite(effective_now):
            raise WorkerQueueError("now must be a finite timestamp")
        released = unknown = 0
        with self._transaction():
            rows = self.conn.execute(
                "SELECT * FROM worker_jobs WHERE state IN ('LEASED','DISPATCHED') AND lease_until<=? ORDER BY job_id",
                (effective_now,),
            ).fetchall()
            for job in rows:
                if job["state"] == "LEASED":
                    try:
                        self.ledger.release_action(job["reservation_id"], fence=int(job["fence"]),
                                                   dispatch_proven_absent=True)
                    except BudgetLedgerError as exc:
                        raise WorkerBudgetError(str(exc)) from exc
                    self.conn.execute("UPDATE worker_jobs SET state='READY',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=? AND fence=?",
                                      (utc_now(), job["job_id"], job["fence"]))
                    self._event(job["job_id"], "LEASE_EXPIRED_PRE_DISPATCH", int(job["fence"]), {})
                    released += 1
                else:
                    self._mark_unknown(job)
                    self.conn.execute("UPDATE worker_jobs SET state='UNKNOWN',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=? AND fence=?",
                                      (utc_now(), job["job_id"], job["fence"]))
                    self._event(job["job_id"], "JOB_UNKNOWN", int(job["fence"]), {"reason": "dispatched lease expired"})
                    unknown += 1
        return {"released": released, "unknown": unknown}

    def reconcile_unknown(
        self, job_id: str, *, actual_amount: int | None,
        receipt_digest: str, output_digest: str,
    ) -> dict[str, Any]:
        """Resolve UNKNOWN only after a trusted provider adapter verifies a receipt."""
        self._validate_job_id(job_id)
        self._validate_digest(receipt_digest, "receipt_digest")
        self._validate_digest(output_digest, "output_digest")
        if actual_amount is not None and (isinstance(actual_amount, bool) or not isinstance(actual_amount, int) or actual_amount < 0):
            raise WorkerQueueError("actual_amount must be a nonnegative integer or None")
        with self._transaction():
            job = self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
            prior = self.conn.execute("SELECT * FROM worker_results WHERE job_id=?", (job_id,)).fetchone()
            charged = int(job["upper_bound"]) if actual_amount is None and job is not None else actual_amount
            expected = (int(job["fence"]), output_digest, charged, receipt_digest) if job is not None else None
            if prior is not None:
                actual = (int(prior["fence"]), prior["output_digest"], int(prior["actual_amount"]), prior["receipt_digest"])
                if expected == actual:
                    return json.loads(prior["response_json"])
                raise WorkerQueueError("unknown job was already reconciled with different evidence")
            if job is None or job["state"] != "UNKNOWN":
                raise WorkerQueueError("job is not UNKNOWN")
            quality = "actual" if actual_amount is not None else "upper_bound"
            try:
                self.ledger.settle_action(job["reservation_id"], actual=actual_amount,
                                          quality=quality, receipt_digest=receipt_digest,
                                          fence=int(job["fence"]), authoritative=True)
            except BudgetLedgerError as exc:
                raise WorkerBudgetError(str(exc)) from exc
            charged = int(job["upper_bound"]) if actual_amount is None else actual_amount
            budget = self.ledger.snapshot(job["campaign_id"])[job["resource"]]
            budget_breached = budget["spent"] + budget["reserved"] + budget["unknown"] > budget["cap"]
            response = {"job_id": job_id, "fence": int(job["fence"]), "output_digest": output_digest,
                        "actual_amount": charged, "receipt_digest": receipt_digest,
                        "budget_breached": budget_breached}
            self.conn.execute("UPDATE worker_jobs SET state='COMPLETED',updated_at=? WHERE job_id=?",
                              (utc_now(), job_id))
            self.conn.execute("INSERT INTO worker_results VALUES(?,?,?,?,?,?,?)",
                              (job_id, int(job["fence"]), output_digest, charged, receipt_digest, _canonical(response), utc_now()))
            self._event(job_id, "UNKNOWN_RECONCILED", int(job["fence"]), response)
            return response

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise WorkerQueueError("unknown job")
        return self._job_view(row)

    def get_result(self, job_id: str) -> dict[str, Any]:
        """Return the durable result payload for crash-safe downstream projection."""
        self._validate_job_id(job_id)
        row = self.conn.execute("SELECT * FROM worker_results WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise WorkerQueueError("job has no committed result")
        response = json.loads(row["response_json"])
        return {**dict(row), "response": response,
                "result": response.get("result")}

    def events(self, job_id: str) -> list[dict[str, Any]]:
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in self.conn.execute(
            "SELECT * FROM worker_events WHERE job_id=? ORDER BY event_id", (job_id,)
        )]

    def events_after(self, cursor: int, *, limit: int = 200) -> list[dict[str, Any]]:
        """Read the authoritative event stream for replayable projections."""
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise WorkerQueueError("event cursor must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise WorkerQueueError("event page size must be between 1 and 1000")
        rows = self.conn.execute(
            "SELECT * FROM worker_events WHERE event_id>? ORDER BY event_id LIMIT ?",
            (cursor, limit),
        ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def _owned_job(self, job_id: str, worker_id: str, fence: int, *,
                   state: str | None = None, states: set[str] | None = None,
                   now: float) -> sqlite3.Row:
        self._validate_job_id(job_id)
        if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
            raise WorkerQueueError("invalid worker_id")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise WorkerQueueError("fence must be a nonnegative integer")
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise WorkerQueueError("now must be a finite timestamp")
        row = self.conn.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
        valid_states = states if states is not None else ({state} if state is not None else set())
        if (row is None or row["state"] not in valid_states or row["lease_owner"] != worker_id
                or int(row["fence"]) != fence or row["lease_until"] is None
                or float(row["lease_until"]) <= now):
            raise WorkerQueueError("worker does not own a live lease at the current fence")
        return row

    def _mark_unknown(self, job: sqlite3.Row) -> None:
        try:
            self.ledger.settle_action(job["reservation_id"], actual=None, quality="unknown",
                                      receipt_digest=None, fence=int(job["fence"]))
        except BudgetLedgerError as exc:
            raise WorkerBudgetError(str(exc)) from exc

    def _event(self, job_id: str, event_type: str, fence: int, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO worker_events(job_id,event_type,fence,payload_json,created_at) VALUES(?,?,?,?,?)",
            (job_id, event_type, fence, _canonical(payload), utc_now()),
        )

    @staticmethod
    def _job_view(row: sqlite3.Row) -> dict[str, Any]:
        return {"job_id": row["job_id"], "campaign_id": row["campaign_id"],
                "action_id": row["action_id"], "resource": row["resource"],
                "upper_bound": int(row["upper_bound"]), "request_hash": row["request_hash"],
                "request": json.loads(row["request_json"]), "state": row["state"],
                "fence": int(row["fence"]), "lease_owner": row["lease_owner"],
                "lease_until": row["lease_until"], "reservation_id": row["reservation_id"]}

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise WorkerQueueError("invalid job_id")

    @staticmethod
    def _validate_digest(value: str, label: str) -> None:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise WorkerQueueError(f"{label} must be a lowercase SHA-256 digest")
