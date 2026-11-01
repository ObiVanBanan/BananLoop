"""Executable, stdlib-only contract spike for BananLoop v0.2 RPDI-00.

This is a disposable design prototype, not production code or a v1 migration.
Each SQLite connection belongs to the trusted Core; workers receive job data only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ContractError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_spec(spec: dict[str, Any]) -> None:
    """Minimal discriminated-spec contract; deliberately leaves v1 bytes untouched."""
    if spec.get("schema_version") != 2:
        raise ContractError("UNSUPPORTED_SPEC", "spike accepts only schema_version=2")
    if spec.get("mode") not in {"EXPLORATION", "OPTIMIZATION"}:
        raise ContractError("INVALID_MODE", "mode must be EXPLORATION or OPTIMIZATION")
    evaluator = spec.get("evaluation")
    if spec["mode"] == "OPTIMIZATION" and not isinstance(evaluator, dict):
        raise ContractError("EVALUATOR_REQUIRED", "optimization requires a frozen evaluator reference")
    if spec["mode"] == "EXPLORATION" and evaluator is not None:
        raise ContractError("EXPLORATION_EVALUATOR", "exploration does not accept an implicit score")
    if not spec.get("goal"):
        raise ContractError("GOAL_REQUIRED", "goal is required")


def complete_exploration(spec: dict[str, Any], *, node_id: str, evidence_refs: list[str],
                         artifact_digests: list[str], limitations: list[str], next_experiment: str) -> dict[str, Any]:
    """Evidence-only terminal package skeleton; deliberately has no reward field."""
    validate_spec(spec)
    if spec["mode"] != "EXPLORATION":
        raise ContractError("WRONG_MODE", "evidence-only package requires an exploration spec")
    if not evidence_refs or not artifact_digests or not next_experiment:
        raise ContractError("INCOMPLETE_PACKAGE", "evidence, artifact digests and next experiment are required")
    return {"node_id": node_id, "status": "COMPLETED", "phase": "CONSOLIDATION",
            "evidence_refs": list(evidence_refs), "artifact_digests": list(artifact_digests),
            "limitations": list(limitations), "next_experiment": next_experiment,
            "protocol_id": None}


def connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_campaign(path: str | Path, budget_cap: int = 100) -> None:
    if budget_cap < 0:
        raise ContractError("INVALID_BUDGET", "budget cap must be non-negative")
    db = connect(path)
    try:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS graph_meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO graph_meta VALUES(1, 0);
        CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS edges(src TEXT NOT NULL REFERENCES nodes(node_id), dst TEXT NOT NULL REFERENCES nodes(node_id), kind TEXT NOT NULL CHECK(kind IN ('REQUIRES','DERIVED_FROM','RELATED_TO')), PRIMARY KEY(src,dst,kind));
        CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL, reserved INTEGER NOT NULL DEFAULT 0, spent INTEGER NOT NULL DEFAULT 0, unknown INTEGER NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO budget(id,cap) VALUES(1, 100);
        CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, status TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0, lease_until REAL, request_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reservations(action_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(job_id), amount INTEGER NOT NULL, state TEXT NOT NULL);
        """)
        db.execute("UPDATE budget SET cap=? WHERE id=1", (budget_cap,))
    finally:
        db.close()


def _execution_cycle(db: sqlite3.Connection) -> bool:
    graph: dict[str, list[str]] = {}
    for row in db.execute("SELECT src,dst FROM edges WHERE kind='REQUIRES'"):
        graph.setdefault(row["src"], []).append(row["dst"])
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(n: str) -> bool:
        if n in visiting:
            return True
        if n in done:
            return False
        visiting.add(n)
        if any(visit(child) for child in graph.get(n, [])):
            return True
        visiting.remove(n)
        done.add(n)
        return False

    return any(visit(n) for n in graph)


def mutate_graph(path: str | Path, *, command_id: str, idempotency_key: str,
                 expected_revision: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {"expected_revision": expected_revision, "changes": changes}
    digest = canonical_hash(payload)
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT payload_hash,response FROM commands WHERE idem_key=?", (idempotency_key,)).fetchone()
        if old:
            if old["payload_hash"] != digest:
                raise ContractError("IDEMPOTENCY_CONFLICT", "key reused with different canonical payload")
            db.execute("COMMIT")
            return json.loads(old["response"])
        old_command = db.execute("SELECT payload_hash,response FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if old_command:
            if old_command["payload_hash"] == digest:
                db.execute("COMMIT")
                return json.loads(old_command["response"])
            raise ContractError("IDEMPOTENCY_CONFLICT", "command ID reused with different canonical payload")
        meta = db.execute("SELECT revision FROM graph_meta WHERE id=1").fetchone()
        if meta["revision"] != expected_revision:
            raise ContractError("REVISION_CONFLICT", f"current revision is {meta['revision']}")
        for change in changes:
            if change["type"] == "AddNode":
                db.execute("INSERT INTO nodes VALUES(?,?)", (change["node_id"], json.dumps(change.get("payload", {}), sort_keys=True)))
            elif change["type"] == "AddEdge":
                if change["kind"] not in {"REQUIRES", "DERIVED_FROM", "RELATED_TO"}:
                    raise ContractError("INVALID_GRAPH", "unknown edge kind")
                db.execute("INSERT INTO edges VALUES(?,?,?)", (change["src"], change["dst"], change["kind"]))
            else:
                raise ContractError("INVALID_GRAPH", "unknown change type")
        if _execution_cycle(db):
            raise ContractError("INVALID_GRAPH", "REQUIRES edges must be acyclic")
        new_revision = expected_revision + 1
        db.execute("UPDATE graph_meta SET revision=? WHERE id=1 AND revision=?", (new_revision, expected_revision))
        response = {"command_id": command_id, "graph_revision": new_revision}
        db.execute("INSERT INTO commands VALUES(?,?,?,?)", (command_id, idempotency_key, digest, json.dumps(response, sort_keys=True)))
        db.execute("INSERT INTO events(kind,payload) VALUES('GRAPH_MUTATED',?)", (json.dumps(response, sort_keys=True),))
        db.execute("COMMIT")
        return response
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def enqueue_job(path: str | Path, job_id: str, node_id: str, request: dict[str, Any]) -> None:
    db = connect(path)
    try:
        db.execute("INSERT INTO jobs(job_id,node_id,status,request_hash) VALUES(?,?,'READY',?)",
                   (job_id, node_id, canonical_hash(request)))
    finally:
        db.close()


def claim_job(path: str | Path, job_id: str, reserve: int, lease_seconds: int = 30,
              *, fail_before_commit: bool = False) -> dict[str, Any]:
    if reserve <= 0 or lease_seconds <= 0:
        raise ContractError("INVALID_RESERVATION", "reserve and lease duration must be positive")
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job or job["status"] != "READY":
            raise ContractError("JOB_NOT_READY", "job is not claimable")
        budget = db.execute("SELECT * FROM budget WHERE id=1").fetchone()
        if budget["spent"] + budget["reserved"] + budget["unknown"] + reserve > budget["cap"]:
            raise ContractError("BUDGET_EXHAUSTED", "reservation exceeds campaign cap")
        fence = job["fence"] + 1
        lease_until = time.time() + lease_seconds
        action_id = "act_" + job_id
        db.execute("UPDATE budget SET reserved=reserved+? WHERE id=1", (reserve,))
        db.execute("INSERT INTO reservations VALUES(?,?,?,'RESERVED')", (action_id, job_id, reserve))
        db.execute("UPDATE jobs SET status='LEASED',fence=?,lease_until=? WHERE job_id=? AND status='READY'", (fence, lease_until, job_id))
        db.execute("INSERT INTO events(kind,payload) VALUES('JOB_LEASED',?)", (json.dumps({"job_id": job_id, "fence": fence, "action_id": action_id}),))
        if fail_before_commit:
            raise RuntimeError("injected crash before claim commit")
        db.execute("COMMIT")
        return {"job_id": job_id, "fence": fence, "action_id": action_id, "lease_until": lease_until,
                "request_hash": job["request_hash"]}
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def mark_dispatched(path: str | Path, job_id: str, fence: int) -> None:
    """Persist dispatch boundary; crash after this point must be reconciled as UNKNOWN."""
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute("UPDATE jobs SET status='DISPATCHED' WHERE job_id=? AND fence=? AND status='LEASED'", (job_id, fence)).rowcount
        if changed != 1:
            raise ContractError("STALE_LEASE", "lease fence no longer owns this job")
        db.execute("UPDATE reservations SET state='DISPATCHED' WHERE job_id=?", (job_id,))
        db.execute("INSERT INTO events(kind,payload) VALUES('JOB_DISPATCHED',?)", (json.dumps({"job_id": job_id, "fence": fence}),))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def record_unknown(path: str | Path, job_id: str, fence: int) -> None:
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status FROM jobs WHERE job_id=? AND fence=?", (job_id, fence)).fetchone()
        if not job or job["status"] != "DISPATCHED":
            raise ContractError("STALE_LEASE", "only dispatched current lease can become unknown")
        db.execute("UPDATE jobs SET status='UNKNOWN' WHERE job_id=?", (job_id,))
        db.execute("UPDATE reservations SET state='UNKNOWN' WHERE job_id=?", (job_id,))
        db.execute("UPDATE budget SET reserved=reserved-(SELECT amount FROM reservations WHERE job_id=?), unknown=unknown+(SELECT amount FROM reservations WHERE job_id=?) WHERE id=1", (job_id, job_id))
        db.execute("INSERT INTO events(kind,payload) VALUES('JOB_UNKNOWN',?)", (json.dumps({"job_id": job_id, "fence": fence}),))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


# Cross-file escrow prototype. Registry and child DB are intentionally separate
# resources; recovery is idempotent and never creates a second allocation.
def init_parent(path: str | Path, cap: int = 100) -> None:
    if cap < 0:
        raise ContractError("INVALID_BUDGET", "root budget cap must be non-negative")
    db = connect(path)
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS root_budget(id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL, spent INTEGER NOT NULL DEFAULT 0, escrowed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS child_allocations(allocation_id TEXT PRIMARY KEY, child_id TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL, state TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO root_budget(id,cap) VALUES(1,?)", (cap,))
    finally:
        db.close()


def init_registry(path: str | Path) -> None:
    db = connect(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS children(child_id TEXT PRIMARY KEY, allocation_id TEXT UNIQUE NOT NULL, db_path TEXT NOT NULL, state TEXT NOT NULL)")
    finally:
        db.close()


def create_child_saga(parent_path: str | Path, registry_path: str | Path, child_path: str | Path,
                      allocation_id: str, child_id: str, amount: int, *, crash_after_child_init: bool = False) -> None:
    if amount <= 0:
        raise ContractError("INVALID_ALLOCATION", "child allocation must be positive")
    p = connect(parent_path)
    try:
        p.execute("BEGIN IMMEDIATE")
        existing = p.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
        if existing:
            if existing["child_id"] != child_id or existing["amount"] != amount:
                raise ContractError("ALLOCATION_CONFLICT", "allocation ID reused with different payload")
        else:
            root = p.execute("SELECT * FROM root_budget WHERE id=1").fetchone()
            if root["spent"] + root["escrowed"] + amount > root["cap"]:
                raise ContractError("BUDGET_EXHAUSTED", "child escrow exceeds root cap")
            p.execute("INSERT INTO child_allocations VALUES(?,?,?,'RESERVED')", (allocation_id, child_id, amount))
            p.execute("UPDATE root_budget SET escrowed=escrowed+? WHERE id=1", (amount,))
        p.execute("COMMIT")
    except Exception:
        if p.in_transaction: p.execute("ROLLBACK")
        raise
    finally:
        p.close()
    r = connect(registry_path)
    try:
        prior = r.execute("SELECT * FROM children WHERE child_id=? OR allocation_id=?", (child_id, allocation_id)).fetchone()
        if prior:
            if (prior["child_id"], prior["allocation_id"], prior["db_path"]) != (child_id, allocation_id, str(child_path)):
                raise ContractError("REGISTRY_CONFLICT", "child or allocation already maps to a different identity/path")
        else:
            r.execute("INSERT INTO children VALUES(?,?,?,'INITIALIZING')", (child_id, allocation_id, str(child_path)))
    finally:
        r.close()
    child_exists = Path(child_path).exists()
    if child_exists:
        child = connect(child_path)
        try:
            row = child.execute("SELECT cap FROM budget WHERE id=1").fetchone()
            if row is not None and row["cap"] != amount:
                raise ContractError("CHILD_CONFLICT", "existing child database has a different budget allocation")
        finally:
            child.close()
    init_campaign(child_path, budget_cap=amount)
    if crash_after_child_init:
        raise RuntimeError("injected crash after child DB initialization")
    p = connect(parent_path)
    try:
        p.execute("UPDATE child_allocations SET state='ACTIVE' WHERE allocation_id=?", (allocation_id,))
    finally:
        p.close()
    r = connect(registry_path)
    try:
        r.execute("UPDATE children SET state='ACTIVE' WHERE allocation_id=?", (allocation_id,))
    finally:
        r.close()


def reconcile_child_saga(parent_path: str | Path, registry_path: str | Path, child_path: str | Path,
                         allocation_id: str, child_id: str, amount: int) -> None:
    create_child_saga(parent_path, registry_path, child_path, allocation_id, child_id, amount)
