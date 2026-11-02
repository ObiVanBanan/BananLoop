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


def _ensure_child_lineage_triggers(db: sqlite3.Connection) -> None:
    _ensure_immutable_triggers(db, "campaign_lineage", "campaign_lineage")


def _ensure_immutable_triggers(db: sqlite3.Connection, table: str, prefix: str,
                              error_code: str = "CHILD_CONFLICT") -> None:
    label = table.replace("_", " ")
    expected = {
        f"{prefix}_no_update": f"CREATE TRIGGER {prefix}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'{label} is immutable'); END",
        f"{prefix}_no_delete": f"CREATE TRIGGER {prefix}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'{label} is immutable'); END",
    }
    for name, sql in expected.items():
        row = db.execute("SELECT tbl_name,sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if row:
            if row["tbl_name"] != table or " ".join(row["sql"].lower().split()) != " ".join(sql.lower().split()):
                raise ContractError(error_code, f"database has an incompatible {name} trigger")
        else:
            db.execute(sql)


def init_campaign(path: str | Path, budget_cap: int = 100, *,
                  campaign_lineage: dict[str, Any] | None = None,
                  fail_before_commit: bool = False) -> None:
    if budget_cap < 0:
        raise ContractError("INVALID_BUDGET", "budget cap must be non-negative")
    db = connect(path)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        existing_tables = set()
        if campaign_lineage:
            existing_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if existing_tables:
                if "campaign_lineage" not in existing_tables or "budget" not in existing_tables:
                    raise ContractError("CHILD_CONFLICT", "refusing to attach lineage to a populated database")
                lineage_count = db.execute("SELECT COUNT(*) FROM campaign_lineage").fetchone()[0]
                if lineage_count != 1:
                    raise ContractError("CHILD_CONFLICT", "existing child database must contain exactly one lineage record")
                budget = db.execute("SELECT cap FROM budget WHERE id=1").fetchone()
                if not budget or budget["cap"] != budget_cap:
                    raise ContractError("CHILD_CONFLICT", "existing child database has a different budget allocation")
                saved = db.execute("SELECT child_id,parent_campaign_id,amount,approved_scope_digest,approved_scope_json,child_spec_json,child_spec_digest FROM campaign_lineage WHERE allocation_id=?",
                                   (campaign_lineage["allocation_id"],)).fetchone()
                expected = (campaign_lineage["child_id"], campaign_lineage["parent_campaign_id"], budget_cap,
                            campaign_lineage["approved_scope_digest"], campaign_lineage["approved_scope_json"],
                            campaign_lineage["child_spec_json"], campaign_lineage["child_spec_digest"])
                if not saved or tuple(saved) != expected:
                    raise ContractError("CHILD_CONFLICT", "existing child database has no matching immutable lineage")
        statements = (
            "CREATE TABLE IF NOT EXISTS graph_meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)",
            "INSERT OR IGNORE INTO graph_meta VALUES(1, 0)",
            "CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY, payload TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'EXPLORATION', status TEXT NOT NULL DEFAULT 'READY', revision INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS edges(src TEXT NOT NULL REFERENCES nodes(node_id), dst TEXT NOT NULL REFERENCES nodes(node_id), kind TEXT NOT NULL CHECK(kind IN ('REQUIRES','DERIVED_FROM','RELATED_TO')), PRIMARY KEY(src,dst,kind))",
            "CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, response TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0), spent INTEGER NOT NULL DEFAULT 0 CHECK(spent>=0), unknown INTEGER NOT NULL DEFAULT 0 CHECK(unknown>=0))",
            "CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, status TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0, lease_until REAL, request_hash TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS reservations(action_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id), fence INTEGER NOT NULL, amount INTEGER NOT NULL, state TEXT NOT NULL, UNIQUE(job_id,fence))",
            "CREATE TABLE IF NOT EXISTS job_results(job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), fence INTEGER NOT NULL, output_digest TEXT NOT NULL, actual_amount INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS exploration_completions(completion_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, node_id TEXT NOT NULL UNIQUE REFERENCES nodes(node_id), source_graph_revision INTEGER NOT NULL, graph_revision INTEGER NOT NULL, package_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS completion_evidence(completion_id TEXT NOT NULL REFERENCES exploration_completions(completion_id), evidence_ref TEXT NOT NULL REFERENCES job_results(job_id), artifact_digest TEXT NOT NULL, source_request_hash TEXT NOT NULL, fence INTEGER NOT NULL, PRIMARY KEY(completion_id,evidence_ref))",
        )
        for statement in statements:
            db.execute(statement)
        # The initial cap is immutable through initialization. Reopening a
        # campaign must never silently raise or lower its spending ceiling.
        db.execute("INSERT OR IGNORE INTO budget(id,cap) VALUES(1,?)", (budget_cap,))
        node_columns = {row["name"] for row in db.execute("PRAGMA table_info(nodes)")}
        for column, declaration in (("phase", "TEXT NOT NULL DEFAULT 'EXPLORATION'"),
                                    ("status", "TEXT NOT NULL DEFAULT 'READY'"),
                                    ("revision", "INTEGER NOT NULL DEFAULT 0")):
            if column not in node_columns:
                db.execute(f"ALTER TABLE nodes ADD COLUMN {column} {declaration}")
        _ensure_immutable_triggers(db, "exploration_completions", "exploration_completion", "COMPLETION_CONFLICT")
        _ensure_immutable_triggers(db, "completion_evidence", "completion_evidence", "COMPLETION_CONFLICT")
        if campaign_lineage:
            db.execute("""CREATE TABLE IF NOT EXISTS campaign_lineage(
              allocation_id TEXT PRIMARY KEY, child_id TEXT NOT NULL, parent_campaign_id TEXT NOT NULL,
              amount INTEGER NOT NULL CHECK(amount>0), approved_scope_digest TEXT NOT NULL,
              approved_scope_json TEXT NOT NULL, child_spec_json TEXT NOT NULL, child_spec_digest TEXT NOT NULL
            )""")
            _ensure_child_lineage_triggers(db)
            expected = (campaign_lineage["child_id"], campaign_lineage["parent_campaign_id"], budget_cap,
                        campaign_lineage["approved_scope_digest"], campaign_lineage["approved_scope_json"],
                        campaign_lineage["child_spec_json"], campaign_lineage["child_spec_digest"])
            row = db.execute("SELECT child_id,parent_campaign_id,amount,approved_scope_digest,approved_scope_json,child_spec_json,child_spec_digest FROM campaign_lineage WHERE allocation_id=?",
                             (campaign_lineage["allocation_id"],)).fetchone()
            if row and tuple(row) != expected:
                raise ContractError("CHILD_CONFLICT", "child lineage or immutable scope/spec does not match the allocation")
            if not row:
                if existing_tables:
                    raise ContractError("CHILD_CONFLICT", "refusing to add lineage to an existing campaign database")
                db.execute("INSERT INTO campaign_lineage VALUES(?,?,?,?,?,?,?,?)",
                           (campaign_lineage["allocation_id"], *expected))
        if fail_before_commit:
            raise RuntimeError("injected child saga crash during child DB initialization")
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
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
                 expected_revision: int, changes: list[dict[str, Any]],
                 evidence_refs: list[str] | None = None) -> dict[str, Any]:
    refs = [] if evidence_refs is None else evidence_refs
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ContractError("INVALID_EVIDENCE", "evidence refs must be non-empty strings")
    refs = sorted(set(refs))
    payload = {"expected_revision": expected_revision, "changes": changes, "evidence_refs": refs}
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
                db.execute("INSERT INTO nodes(node_id,payload) VALUES(?,?)", (change["node_id"], json.dumps(change.get("payload", {}), sort_keys=True)))
            elif change["type"] == "AddEdge":
                if change["kind"] not in {"REQUIRES", "DERIVED_FROM", "RELATED_TO"}:
                    raise ContractError("INVALID_GRAPH", "unknown edge kind")
                db.execute("INSERT INTO edges VALUES(?,?,?)", (change["src"], change["dst"], change["kind"]))
            else:
                raise ContractError("INVALID_GRAPH", "unknown change type")
        if _execution_cycle(db):
            raise ContractError("INVALID_GRAPH", "REQUIRES edges must be acyclic")
        new_revision = expected_revision + 1
        cursor = db.execute("UPDATE graph_meta SET revision=? WHERE id=1 AND revision=?", (new_revision, expected_revision))
        if cursor.rowcount != 1:
            raise ContractError("REVISION_CONFLICT", "graph revision compare-and-swap failed")
        response = {"command_id": command_id, "graph_revision": new_revision, "evidence_refs": refs}
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
              *, now: float | None = None, fail_before_commit: bool = False) -> dict[str, Any]:
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
        lease_until = (time.time() if now is None else now) + lease_seconds
        action_id = f"act_{job_id}_{fence}"
        changed = db.execute("UPDATE budget SET reserved=reserved+? WHERE id=1 AND spent+reserved+unknown+?<=cap", (reserve, reserve)).rowcount
        if changed != 1:
            raise ContractError("BUDGET_EXHAUSTED", "reservation exceeds campaign cap")
        db.execute("INSERT INTO reservations VALUES(?,?,?,?,'RESERVED')", (action_id, job_id, fence, reserve))
        changed = db.execute("UPDATE jobs SET status='LEASED',fence=?,lease_until=? WHERE job_id=? AND status='READY'", (fence, lease_until, job_id)).rowcount
        if changed != 1:
            raise ContractError("JOB_NOT_READY", "job changed before lease commit")
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


def mark_dispatched(path: str | Path, job_id: str, fence: int, *, now: float | None = None) -> None:
    """Persist dispatch boundary; crash after this point must be reconciled as UNKNOWN."""
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        effective_now = time.time() if now is None else now
        changed = db.execute("UPDATE jobs SET status='DISPATCHED' WHERE job_id=? AND fence=? AND status='LEASED' AND lease_until>?", (job_id, fence, effective_now)).rowcount
        if changed != 1:
            raise ContractError("STALE_LEASE", "lease fence is stale or expired")
        reserved = db.execute("UPDATE reservations SET state='DISPATCHED' WHERE job_id=? AND fence=? AND state='RESERVED'", (job_id, fence)).rowcount
        if reserved != 1:
            raise ContractError("RESERVATION_MISSING", "current lease has no open reservation")
        db.execute("INSERT INTO events(kind,payload) VALUES('JOB_DISPATCHED',?)", (json.dumps({"job_id": job_id, "fence": fence}),))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _mark_unknown_in_transaction(db: sqlite3.Connection, job_id: str, fence: int) -> None:
    job = db.execute("SELECT status FROM jobs WHERE job_id=? AND fence=?", (job_id, fence)).fetchone()
    reservation = db.execute("SELECT amount,state FROM reservations WHERE job_id=? AND fence=?", (job_id, fence)).fetchone()
    if not job or job["status"] != "DISPATCHED" or not reservation or reservation["state"] != "DISPATCHED":
        raise ContractError("STALE_LEASE", "only dispatched current lease can become unknown")
    changed = db.execute("UPDATE budget SET reserved=reserved-?, unknown=unknown+? WHERE id=1 AND reserved>=?", (reservation["amount"], reservation["amount"], reservation["amount"])).rowcount
    if changed != 1:
        raise ContractError("BUDGET_INVARIANT", "reserved balance cannot cover dispatched reservation")
    changed = db.execute("UPDATE jobs SET status='UNKNOWN' WHERE job_id=? AND fence=? AND status='DISPATCHED'", (job_id, fence)).rowcount
    if changed != 1:
        raise ContractError("STALE_LEASE", "job changed before UNKNOWN commit")
    changed = db.execute("UPDATE reservations SET state='UNKNOWN' WHERE job_id=? AND fence=? AND state='DISPATCHED'", (job_id, fence)).rowcount
    if changed != 1:
        raise ContractError("RESERVATION_INVARIANT", "reservation changed before UNKNOWN commit")
    db.execute("INSERT INTO events(kind,payload) VALUES('JOB_UNKNOWN',?)", (json.dumps({"job_id": job_id, "fence": fence}),))


def record_unknown(path: str | Path, job_id: str, fence: int) -> None:
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        _mark_unknown_in_transaction(db, job_id, fence)
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def recover_expired_jobs(path: str | Path, *, now: float) -> dict[str, int]:
    """Release expired pre-dispatch leases; retain dispatched calls as UNKNOWN."""
    db = connect(path)
    reclaimed = unknown = 0
    try:
        db.execute("BEGIN IMMEDIATE")
        expired = db.execute(
            "SELECT job_id,fence,status FROM jobs WHERE status IN ('LEASED','DISPATCHED') AND lease_until<=? ORDER BY job_id",
            (now,),
        ).fetchall()
        for job in expired:
            if job["status"] == "LEASED":
                reservation = db.execute(
                    "SELECT amount FROM reservations WHERE job_id=? AND fence=? AND state='RESERVED'",
                    (job["job_id"], job["fence"]),
                ).fetchone()
                if not reservation:
                    raise ContractError("RESERVATION_MISSING", "expired lease has no open reservation")
                changed = db.execute("UPDATE budget SET reserved=reserved-? WHERE id=1 AND reserved>=?", (reservation["amount"], reservation["amount"])).rowcount
                if changed != 1:
                    raise ContractError("BUDGET_INVARIANT", "reserved balance cannot cover expired lease")
                changed = db.execute("UPDATE reservations SET state='RELEASED' WHERE job_id=? AND fence=? AND state='RESERVED'", (job["job_id"], job["fence"])).rowcount
                if changed != 1:
                    raise ContractError("RESERVATION_INVARIANT", "expired lease reservation changed")
                changed = db.execute("UPDATE jobs SET status='READY',fence=fence+1,lease_until=NULL WHERE job_id=? AND fence=? AND status='LEASED'", (job["job_id"], job["fence"])).rowcount
                if changed != 1:
                    raise ContractError("STALE_LEASE", "expired lease changed before reclaim")
                db.execute("INSERT INTO events(kind,payload) VALUES('JOB_LEASE_EXPIRED',?)", (json.dumps({"job_id": job["job_id"], "invalidated_fence": job["fence"], "next_fence": job["fence"] + 2}),))
                reclaimed += 1
            else:
                _mark_unknown_in_transaction(db, job["job_id"], job["fence"])
                db.execute("UPDATE jobs SET lease_until=NULL WHERE job_id=? AND fence=?", (job["job_id"], job["fence"]))
                unknown += 1
        db.execute("COMMIT")
        return {"reclaimed": reclaimed, "unknown": unknown}
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def complete_job(path: str | Path, job_id: str, fence: int, *, output_digest: str,
                 actual_amount: int, now: float | None = None) -> dict[str, Any]:
    """Persist an on-time result and settle its reservation under the current fence."""
    if not output_digest or actual_amount < 0:
        raise ContractError("INVALID_RESULT", "output digest and non-negative actual amount are required")
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT fence,output_digest,actual_amount FROM job_results WHERE job_id=?", (job_id,)).fetchone()
        if prior:
            if (prior["fence"], prior["output_digest"], prior["actual_amount"]) == (fence, output_digest, actual_amount):
                db.execute("COMMIT")
                return {"job_id": job_id, "fence": fence, "output_digest": output_digest, "actual_amount": actual_amount}
            raise ContractError("RESULT_CONFLICT", "job already has a different committed result")
        effective_now = time.time() if now is None else now
        job = db.execute("SELECT status,fence,lease_until FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        reservation = db.execute("SELECT amount,state FROM reservations WHERE job_id=? AND fence=?", (job_id, fence)).fetchone()
        if (not job or job["status"] != "DISPATCHED" or job["fence"] != fence or
                job["lease_until"] is None or job["lease_until"] <= effective_now or
                not reservation or reservation["state"] != "DISPATCHED"):
            raise ContractError("STALE_LEASE", "result does not own an unexpired dispatched lease")
        if actual_amount > reservation["amount"]:
            raise ContractError("RESERVATION_EXCEEDED", "actual amount exceeds the reservation")
        changed = db.execute(
            "UPDATE jobs SET status='COMPLETED' WHERE job_id=? AND status='DISPATCHED' AND fence=? AND lease_until>?",
            (job_id, fence, effective_now),
        ).rowcount
        if changed != 1:
            raise ContractError("STALE_LEASE", "job lease changed before result commit")
        changed = db.execute("UPDATE budget SET reserved=reserved-?, spent=spent+? WHERE id=1 AND reserved>=?", (reservation["amount"], actual_amount, reservation["amount"])).rowcount
        if changed != 1:
            raise ContractError("BUDGET_INVARIANT", "reserved balance cannot cover result settlement")
        changed = db.execute("UPDATE reservations SET state='SETTLED' WHERE job_id=? AND fence=? AND state='DISPATCHED'", (job_id, fence)).rowcount
        if changed != 1:
            raise ContractError("RESERVATION_INVARIANT", "dispatched reservation changed before settlement")
        db.execute("INSERT INTO job_results VALUES(?,?,?,?)", (job_id, fence, output_digest, actual_amount))
        response = {"job_id": job_id, "fence": fence, "output_digest": output_digest, "actual_amount": actual_amount}
        db.execute("INSERT INTO events(kind,payload) VALUES('JOB_COMPLETED',?)", (json.dumps(response, sort_keys=True),))
        db.execute("COMMIT")
        return response
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def persist_exploration_completion(path: str | Path, spec: dict[str, Any], *,
                                   completion_id: str, idempotency_key: str,
                                   expected_graph_revision: int, node_id: str,
                                   evidence_refs: list[str], artifact_digests: list[str],
                                   limitations: list[str], next_experiment: str,
                                   fail_before_commit: bool = False) -> dict[str, Any]:
    """Persist an evidence-only completion bound to fenced job results and a graph revision."""
    if not completion_id or not idempotency_key or not node_id:
        raise ContractError("INVALID_COMPLETION", "completion ID, idempotency key, and node ID are required")
    if (not isinstance(expected_graph_revision, int) or isinstance(expected_graph_revision, bool) or
            expected_graph_revision < 0):
        raise ContractError("INVALID_COMPLETION", "expected graph revision must be a non-negative integer")
    if not isinstance(evidence_refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
        raise ContractError("INVALID_EVIDENCE", "evidence refs must be non-empty strings")
    if not isinstance(artifact_digests, list) or any(not isinstance(digest, str) or not digest.strip() for digest in artifact_digests):
        raise ContractError("INVALID_EVIDENCE", "artifact digests must be non-empty strings")
    if not isinstance(limitations, list) or any(not isinstance(item, str) for item in limitations):
        raise ContractError("INVALID_COMPLETION", "limitations must be a list of strings")
    refs = sorted(set(evidence_refs))
    digests = sorted(set(artifact_digests))
    if not refs or len(refs) != len(evidence_refs):
        raise ContractError("INVALID_EVIDENCE", "evidence refs must be unique, non-empty strings")
    if not digests or len(digests) != len(artifact_digests):
        raise ContractError("INVALID_EVIDENCE", "artifact digests must be unique, non-empty strings")
    payload = {"type": "CompleteExploration", "node_id": node_id,
               "expected_graph_revision": expected_graph_revision, "evidence_refs": refs,
               "artifact_digests": digests, "limitations": limitations,
               "next_experiment": next_experiment, "spec_hash": canonical_hash(spec)}
    payload_hash = canonical_hash(payload)
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT payload_hash,response FROM commands WHERE idem_key=?", (idempotency_key,)).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise ContractError("IDEMPOTENCY_CONFLICT", "completion key reused with different payload")
            db.execute("COMMIT")
            return json.loads(prior["response"])
        prior_id = db.execute("SELECT payload_hash,response FROM commands WHERE command_id=?", (completion_id,)).fetchone()
        if prior_id:
            if prior_id["payload_hash"] != payload_hash:
                raise ContractError("IDEMPOTENCY_CONFLICT", "completion ID reused with different payload")
            db.execute("COMMIT")
            return json.loads(prior_id["response"])
        meta = db.execute("SELECT revision FROM graph_meta WHERE id=1").fetchone()
        if not meta or meta["revision"] != expected_graph_revision:
            raise ContractError("REVISION_CONFLICT", "completion graph revision is stale")
        node = db.execute("SELECT phase,status FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        if not node:
            raise ContractError("INVALID_COMPLETION", "completion node does not exist")
        if (node["phase"], node["status"]) != ("EXPLORATION", "READY"):
            raise ContractError("NODE_NOT_READY", "node is not in the evidence-only completion state")
        evidence = []
        for ref in refs:
            result = db.execute("""SELECT j.node_id,j.status,j.fence,j.request_hash,
                                      r.fence AS result_fence,r.output_digest
                               FROM jobs AS j JOIN job_results AS r ON r.job_id=j.job_id
                               WHERE j.job_id=?""", (ref,)).fetchone()
            if (not result or result["node_id"] != node_id or result["status"] != "COMPLETED" or
                    result["fence"] != result["result_fence"]):
                raise ContractError("INVALID_EVIDENCE", f"evidence ref {ref} is not a current completed job for this node")
            evidence.append({"evidence_ref": ref, "artifact_digest": result["output_digest"],
                             "source_request_hash": result["request_hash"], "fence": result["result_fence"]})
        if {item["artifact_digest"] for item in evidence} != set(digests):
            raise ContractError("INVALID_EVIDENCE", "artifact digests must match the referenced job results")
        package = complete_exploration(spec, node_id=node_id, evidence_refs=refs,
                                       artifact_digests=digests, limitations=limitations,
                                       next_experiment=next_experiment)
        new_revision = expected_graph_revision + 1
        changed = db.execute("UPDATE graph_meta SET revision=? WHERE id=1 AND revision=?",
                             (new_revision, expected_graph_revision)).rowcount
        if changed != 1:
            raise ContractError("REVISION_CONFLICT", "completion revision compare-and-swap failed")
        changed = db.execute("""UPDATE nodes SET phase='CONSOLIDATION',status='COMPLETED',revision=revision+1
                               WHERE node_id=? AND phase='EXPLORATION' AND status='READY'""", (node_id,)).rowcount
        if changed != 1:
            raise ContractError("NODE_NOT_READY", "node changed before completion commit")
        package.update({"completion_id": completion_id,
                        "source_graph_revision": expected_graph_revision,
                        "graph_revision": new_revision})
        package_json = json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        db.execute("INSERT INTO exploration_completions VALUES(?,?,?,?,?,?,?)",
                   (completion_id, idempotency_key, payload_hash, node_id, expected_graph_revision,
                    new_revision, package_json))
        for item in evidence:
            db.execute("INSERT INTO completion_evidence VALUES(?,?,?,?,?)",
                       (completion_id, item["evidence_ref"], item["artifact_digest"],
                        item["source_request_hash"], item["fence"]))
        response = package
        db.execute("INSERT INTO commands VALUES(?,?,?,?)",
                   (completion_id, idempotency_key, payload_hash, json.dumps(response, sort_keys=True)))
        db.execute("INSERT INTO events(kind,payload) VALUES('EXPLORATION_COMPLETED',?)",
                   (json.dumps(response, sort_keys=True),))
        if fail_before_commit:
            raise RuntimeError("injected completion crash before commit")
        db.execute("COMMIT")
        return response
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
        CREATE TABLE IF NOT EXISTS root_budget(id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0), spent INTEGER NOT NULL DEFAULT 0 CHECK(spent>=0), escrowed INTEGER NOT NULL DEFAULT 0 CHECK(escrowed>=0));
        CREATE TABLE IF NOT EXISTS child_allocations(
          allocation_id TEXT PRIMARY KEY, child_id TEXT UNIQUE NOT NULL, parent_campaign_id TEXT NOT NULL,
          child_db_path TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), approved_scope_digest TEXT NOT NULL,
          approved_scope_json TEXT NOT NULL, child_spec_json TEXT NOT NULL, child_spec_digest TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('RESERVED','REGISTRY_INITIALIZING','CHILD_READY','ACTIVE','RECONCILIATION_REQUIRED'))
        );
        CREATE TABLE IF NOT EXISTS child_approvals(
          allocation_id TEXT PRIMARY KEY, child_id TEXT UNIQUE NOT NULL, parent_campaign_id TEXT NOT NULL,
          child_db_path TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), approved_scope_digest TEXT NOT NULL,
          approved_scope_json TEXT NOT NULL, child_spec_json TEXT NOT NULL, child_spec_digest TEXT NOT NULL,
          approved_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS child_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, allocation_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO root_budget(id,cap) VALUES(1,?)", (cap,))
        _ensure_immutable_triggers(db, "child_approvals", "child_approval")
    finally:
        db.close()


def init_registry(path: str | Path) -> None:
    db = connect(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS children(child_id TEXT PRIMARY KEY, allocation_id TEXT UNIQUE NOT NULL, parent_campaign_id TEXT NOT NULL, db_path TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL, approved_scope_digest TEXT NOT NULL, child_spec_digest TEXT NOT NULL, state TEXT NOT NULL)")
    finally:
        db.close()


def _child_spec_payload(parent_campaign_id: str, child_id: str, child_path: str, amount: int,
                        approved_scope: dict[str, Any], child_spec: dict[str, Any]) -> dict[str, Any]:
    if (amount <= 0 or not parent_campaign_id or not child_id or not isinstance(approved_scope, dict) or
        not approved_scope or not isinstance(child_spec, dict) or not child_spec):
        raise ContractError("INVALID_CHILD_SPEC", "child identity, positive amount, approved scope and child spec are required")
    autonomy = child_spec.get("autonomy")
    scope_digest = canonical_hash(approved_scope)
    if (not isinstance(autonomy, dict) or autonomy.get("scope_digest") != scope_digest or
        not isinstance(autonomy.get("scope_version"), int) or isinstance(autonomy.get("scope_version"), bool) or
        autonomy["scope_version"] < 1 or autonomy.get("mode") not in {"FULL_AUTO", "SUPERVISED"}):
        raise ContractError("SCOPE_SPEC_MISMATCH", "child spec autonomy scope must match the approved scope digest")
    scope_json = json.dumps(approved_scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    child_spec_json = json.dumps(child_spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    child_spec_digest = hashlib.sha256(child_spec_json.encode("utf-8")).hexdigest()
    return {"parent_campaign_id": parent_campaign_id, "child_id": child_id,
            "child_db_path": str(Path(child_path).resolve()), "amount": amount,
            "approved_scope_digest": scope_digest, "approved_scope_json": scope_json,
            "child_spec_json": child_spec_json, "child_spec_digest": child_spec_digest}


def approve_child_creation(parent_path: str | Path, allocation_id: str, *, parent_campaign_id: str,
                           child_id: str, child_path: str | Path, amount: int,
                           approved_scope: dict[str, Any], child_spec: dict[str, Any],
                           approved_by: str) -> str:
    """Record the trusted parent's approval before the saga may reserve escrow."""
    if not allocation_id or not approved_by:
        raise ContractError("INVALID_APPROVAL", "allocation ID and approving authority are required")
    record = _child_spec_payload(parent_campaign_id, child_id, str(child_path), amount, approved_scope, child_spec)
    db = connect(parent_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        _ensure_immutable_triggers(db, "child_approvals", "child_approval")
        existing = db.execute("SELECT * FROM child_approvals WHERE allocation_id=?", (allocation_id,)).fetchone()
        keys = ("parent_campaign_id", "child_id", "child_db_path", "amount", "approved_scope_digest",
                "approved_scope_json", "child_spec_json", "child_spec_digest", "approved_by")
        if existing:
            if tuple(existing[key] for key in keys) != tuple(record.get(key, approved_by) for key in keys):
                raise ContractError("APPROVAL_CONFLICT", "allocation ID already has a different parent approval")
            db.execute("COMMIT")
            return record["approved_scope_digest"]
        prior_child = db.execute("SELECT allocation_id FROM child_approvals WHERE child_id=?", (child_id,)).fetchone()
        if prior_child:
            raise ContractError("APPROVAL_CONFLICT", "child ID already has a parent approval")
        prior_path = db.execute("SELECT allocation_id FROM child_approvals WHERE child_db_path=?", (record["child_db_path"],)).fetchone()
        if prior_path:
            raise ContractError("APPROVAL_CONFLICT", "child database path already has a parent approval")
        db.execute("INSERT INTO child_approvals VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (allocation_id, child_id, parent_campaign_id, record["child_db_path"], amount,
                    record["approved_scope_digest"], record["approved_scope_json"], record["child_spec_json"],
                    record["child_spec_digest"], approved_by))
        db.execute("INSERT INTO child_events(allocation_id,kind,payload) VALUES(?,?,?)",
                   (allocation_id, "CHILD_SPEC_APPROVED", json.dumps({"approved_by": approved_by,
                    "scope_digest": record["approved_scope_digest"],
                    "child_spec_digest": record["child_spec_digest"]}, sort_keys=True)))
        db.execute("COMMIT")
        return record["approved_scope_digest"]
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _set_child_allocation_state(parent_path: str | Path, allocation_id: str, state: str) -> None:
    db = connect(parent_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
        if not row:
            raise ContractError("ALLOCATION_MISSING", "parent allocation does not exist")
        current = row["state"]
        if current == "RECONCILIATION_REQUIRED" and state != current:
            raise ContractError("RECONCILIATION_REQUIRED", "allocation requires operator reconciliation")
        rank = {"RESERVED": 0, "REGISTRY_INITIALIZING": 1, "CHILD_READY": 2, "ACTIVE": 3,
                "RECONCILIATION_REQUIRED": 4}
        if rank[state] < rank[current]:
            db.execute("COMMIT")
            return
        if current != state:
            db.execute("UPDATE child_allocations SET state=? WHERE allocation_id=?", (state, allocation_id))
            db.execute("INSERT INTO child_events(allocation_id,kind,payload) VALUES(?,?,?)",
                       (allocation_id, "CHILD_STATE_CHANGED", json.dumps({"from": row["state"], "to": state}, sort_keys=True)))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _mark_child_reconciliation_required(parent_path: str | Path, allocation_id: str) -> None:
    _set_child_allocation_state(parent_path, allocation_id, "RECONCILIATION_REQUIRED")


def create_child_saga(parent_path: str | Path, registry_path: str | Path, child_path: str | Path,
                      allocation_id: str, child_id: str, amount: int, *,
                      parent_campaign_id: str, approved_scope_digest: str,
                      approved_scope: dict[str, Any], child_spec: dict[str, Any],
                      crash_at: str | None = None) -> None:
    if amount <= 0:
        raise ContractError("INVALID_ALLOCATION", "child allocation must be positive")
    if not allocation_id or not approved_scope_digest:
        raise ContractError("INVALID_CHILD_SPEC", "allocation ID and approved scope digest are required")
    record = _child_spec_payload(parent_campaign_id, child_id, str(child_path), amount, approved_scope, child_spec)
    if record["approved_scope_digest"] != approved_scope_digest:
        raise ContractError("SCOPE_DIGEST_MISMATCH", "approved scope digest does not match canonical scope")
    if crash_at not in {None, "after_reserve", "after_registry", "during_child_init", "after_campaign_init", "after_child_init",
                        "after_registry_active", "after_parent_active"}:
        raise ContractError("INVALID_CRASH_POINT", "unknown child saga crash point")
    stable_child_path = record["child_db_path"]
    scope_json = record["approved_scope_json"]
    child_spec_json = record["child_spec_json"]
    child_spec_digest = record["child_spec_digest"]
    payload_hash = canonical_hash({"parent_campaign_id": parent_campaign_id, "child_id": child_id,
                                   "child_path": stable_child_path, "amount": amount,
                                   "approved_scope_digest": approved_scope_digest,
                                   "child_spec_digest": child_spec_digest})
    p = connect(parent_path)
    try:
        p.execute("BEGIN IMMEDIATE")
        approval = p.execute("SELECT * FROM child_approvals WHERE allocation_id=?", (allocation_id,)).fetchone()
        approval_keys = ("parent_campaign_id", "child_id", "child_db_path", "amount", "approved_scope_digest",
                         "approved_scope_json", "child_spec_json", "child_spec_digest")
        if not approval:
            raise ContractError("APPROVAL_REQUIRED", "child creation has no durable parent approval")
        if tuple(approval[key] for key in approval_keys) != tuple(record[key] for key in approval_keys):
            raise ContractError("APPROVAL_CONFLICT", "child creation request does not match durable parent approval")
        existing = p.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
        if existing:
            if existing["state"] == "RECONCILIATION_REQUIRED":
                raise ContractError("RECONCILIATION_REQUIRED", "allocation requires operator reconciliation")
            if (existing["child_id"], existing["parent_campaign_id"], existing["child_db_path"], existing["amount"],
                existing["approved_scope_digest"], existing["approved_scope_json"], existing["child_spec_json"],
                existing["child_spec_digest"]) != (
                child_id, parent_campaign_id, stable_child_path, amount, approved_scope_digest, scope_json,
                child_spec_json, child_spec_digest):
                raise ContractError("ALLOCATION_CONFLICT", "allocation ID reused with different payload")
        else:
            prior_child = p.execute("SELECT allocation_id FROM child_allocations WHERE child_id=?", (child_id,)).fetchone()
            if prior_child:
                raise ContractError("ALLOCATION_CONFLICT", "child ID already has a parent allocation")
            root = p.execute("SELECT * FROM root_budget WHERE id=1").fetchone()
            if root["spent"] + root["escrowed"] + amount > root["cap"]:
                raise ContractError("BUDGET_EXHAUSTED", "child escrow exceeds root cap")
            p.execute("INSERT INTO child_allocations VALUES(?,?,?,?,?,?,?,?,?,'RESERVED')",
                      (allocation_id, child_id, parent_campaign_id, stable_child_path, amount,
                       approved_scope_digest, scope_json, child_spec_json, child_spec_digest))
            changed = p.execute("UPDATE root_budget SET escrowed=escrowed+? WHERE id=1 AND spent+escrowed+?<=cap", (amount, amount)).rowcount
            if changed != 1:
                raise ContractError("BUDGET_EXHAUSTED", "child escrow exceeds root cap")
            p.execute("INSERT INTO child_events(allocation_id,kind,payload) VALUES(?,?,?)",
                      (allocation_id, "CHILD_ESCROW_RESERVED", json.dumps({"payload_hash": payload_hash, "amount": amount})))
        p.execute("COMMIT")
    except Exception:
        if p.in_transaction: p.execute("ROLLBACK")
        raise
    finally:
        p.close()
    if crash_at == "after_reserve":
        raise RuntimeError("injected child saga crash after reserve")
    r = connect(registry_path)
    try:
        r.execute("BEGIN IMMEDIATE")
        prior = r.execute("SELECT * FROM children WHERE child_id=? OR allocation_id=? OR db_path=?",
                          (child_id, allocation_id, stable_child_path)).fetchone()
        if prior:
            if (prior["child_id"], prior["allocation_id"], prior["parent_campaign_id"], prior["db_path"],
                prior["amount"], prior["approved_scope_digest"], prior["child_spec_digest"]) != (
                child_id, allocation_id, parent_campaign_id, stable_child_path, amount,
                approved_scope_digest, child_spec_digest):
                raise ContractError("REGISTRY_CONFLICT", "child or allocation already maps to different immutable identity")
        else:
            r.execute("INSERT INTO children VALUES(?,?,?,?,?,?,?,'INITIALIZING')",
                      (child_id, allocation_id, parent_campaign_id, stable_child_path, amount,
                       approved_scope_digest, child_spec_digest))
        r.execute("COMMIT")
    except ContractError as exc:
        if r.in_transaction:
            r.execute("ROLLBACK")
        if exc.code == "REGISTRY_CONFLICT":
            _mark_child_reconciliation_required(parent_path, allocation_id)
        raise
    except Exception:
        if r.in_transaction:
            r.execute("ROLLBACK")
        raise
    finally:
        r.close()
    _set_child_allocation_state(parent_path, allocation_id, "REGISTRY_INITIALIZING")
    if crash_at == "after_registry":
        raise RuntimeError("injected child saga crash after registry insert")
    lineage = {"allocation_id": allocation_id, "child_id": child_id,
               "parent_campaign_id": parent_campaign_id, "approved_scope_digest": approved_scope_digest,
               "approved_scope_json": scope_json, "child_spec_json": child_spec_json,
               "child_spec_digest": child_spec_digest}
    try:
        init_campaign(stable_child_path, budget_cap=amount, campaign_lineage=lineage,
                      fail_before_commit=crash_at == "during_child_init")
    except ContractError as exc:
        if exc.code == "CHILD_CONFLICT":
            _mark_child_reconciliation_required(parent_path, allocation_id)
        raise
    except sqlite3.DatabaseError as exc:
        _mark_child_reconciliation_required(parent_path, allocation_id)
        raise ContractError("CHILD_CONFLICT", "existing child database is unreadable") from exc
    if crash_at == "during_child_init":
        raise RuntimeError("injected child saga crash during child DB initialization")
    if crash_at == "after_campaign_init":
        raise RuntimeError("injected child saga crash after campaign DB initialization")
    if crash_at == "after_child_init":
        raise RuntimeError("injected child saga crash after child DB initialization")
    _set_child_allocation_state(parent_path, allocation_id, "CHILD_READY")
    r = connect(registry_path)
    try:
        r.execute("BEGIN IMMEDIATE")
        changed = r.execute("UPDATE children SET state='ACTIVE' WHERE allocation_id=? AND child_id=? AND db_path=? AND child_spec_digest=? AND state IN ('INITIALIZING','ACTIVE')",
                            (allocation_id, child_id, stable_child_path, child_spec_digest)).rowcount
        if changed != 1:
            raise ContractError("REGISTRY_CONFLICT", "registry identity changed before activation")
        r.execute("COMMIT")
    except Exception:
        if r.in_transaction:
            r.execute("ROLLBACK")
        _mark_child_reconciliation_required(parent_path, allocation_id)
        raise
    finally:
        r.close()
    if crash_at == "after_registry_active":
        raise RuntimeError("injected child saga crash after registry activation")
    _set_child_allocation_state(parent_path, allocation_id, "ACTIVE")
    if crash_at == "after_parent_active":
        raise RuntimeError("injected child saga crash after parent activation")


def reconcile_child_saga(parent_path: str | Path, registry_path: str | Path, child_path: str | Path,
                         allocation_id: str, child_id: str, amount: int, *, parent_campaign_id: str,
                         approved_scope_digest: str, approved_scope: dict[str, Any],
                         child_spec: dict[str, Any]) -> None:
    create_child_saga(parent_path, registry_path, child_path, allocation_id, child_id, amount,
                      parent_campaign_id=parent_campaign_id, approved_scope_digest=approved_scope_digest,
                      approved_scope=approved_scope,
                      child_spec=child_spec)


def child_admission_ready(parent_path: str | Path, registry_path: str | Path, child_path: str | Path,
                          allocation_id: str) -> bool:
    """Require parent, registry, and child DB to agree before a child can run."""
    p = connect(parent_path)
    r = connect(registry_path)
    child_path = str(Path(child_path).resolve())
    try:
        parent = p.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
        if not parent or parent["state"] != "ACTIVE":
            return False
        registry = r.execute("SELECT * FROM children WHERE allocation_id=?", (allocation_id,)).fetchone()
        if (not registry or registry["state"] != "ACTIVE" or registry["child_id"] != parent["child_id"] or
            registry["parent_campaign_id"] != parent["parent_campaign_id"] or
            parent["child_db_path"] != child_path or registry["db_path"] != child_path or
            registry["amount"] != parent["amount"] or registry["approved_scope_digest"] != parent["approved_scope_digest"] or
            registry["child_spec_digest"] != parent["child_spec_digest"]):
            return False
    finally:
        p.close()
        r.close()
    c = connect(child_path)
    try:
        if c.execute("SELECT COUNT(*) FROM campaign_lineage").fetchone()[0] != 1:
            return False
        lineage = c.execute("SELECT * FROM campaign_lineage WHERE allocation_id=?", (allocation_id,)).fetchone()
        if not lineage:
            return False
        return (
            lineage["child_id"] == parent["child_id"] and
            lineage["parent_campaign_id"] == parent["parent_campaign_id"] and
            lineage["amount"] == parent["amount"] and
            lineage["approved_scope_digest"] == parent["approved_scope_digest"] and
            lineage["approved_scope_json"] == parent["approved_scope_json"] and
            lineage["child_spec_json"] == parent["child_spec_json"] and
            lineage["child_spec_digest"] == parent["child_spec_digest"] and
            canonical_hash(json.loads(lineage["approved_scope_json"])) == parent["approved_scope_digest"] and
            hashlib.sha256(lineage["child_spec_json"].encode("utf-8")).hexdigest() == parent["child_spec_digest"]
        )
    except sqlite3.DatabaseError:
        return False
    finally:
        c.close()
