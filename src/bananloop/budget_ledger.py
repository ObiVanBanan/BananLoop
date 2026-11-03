"""Project-wide, dimensional budget ledger for v2 campaigns.

The campaign registry database is shared by every campaign in a project, so it
is the only place that can serialize reservations against a parent envelope.
This module deliberately does not alter v1 CampaignStore counters.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Mapping

from .contracts import utc_now


RESOURCES = (
    "agent_calls", "evaluator_calls", "model_tokens", "evaluation_units",
    "experiments", "attempts",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class BudgetLedgerError(RuntimeError):
    """A budget operation was invalid or could not be safely completed."""


class BudgetExceeded(BudgetLedgerError):
    """The requested vector would exceed one or more independent resource caps."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _vector(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value:
        raise BudgetLedgerError("budget vector must be a nonempty resource mapping")
    unknown = set(value) - set(RESOURCES)
    if unknown:
        raise BudgetLedgerError(f"unknown budget resources: {', '.join(sorted(unknown))}")
    result: dict[str, int] = {}
    for resource, amount in value.items():
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise BudgetLedgerError(f"{resource} must be a positive integer")
        result[resource] = amount
    return {key: result[key] for key in RESOURCES if key in result}


class BudgetLedger:
    """Transactional root/child budget authority stored alongside the registry."""

    def __init__(self, registry_path: str | Path):
        self.path = Path(registry_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from .registry import assert_safe_local_path

        assert_safe_local_path(self.path, "campaign budget ledger")
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._owns_connection = True
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS budget_roots (
          campaign_id TEXT PRIMARY KEY, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_accounts (
          campaign_id TEXT NOT NULL, resource TEXT NOT NULL,
          cap INTEGER NOT NULL CHECK(cap>=0), spent INTEGER NOT NULL DEFAULT 0 CHECK(spent>=0),
          reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0), unknown INTEGER NOT NULL DEFAULT 0 CHECK(unknown>=0),
          revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
          PRIMARY KEY(campaign_id,resource)
        );
        CREATE TABLE IF NOT EXISTS child_allocations (
          allocation_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
          parent_id TEXT NOT NULL, child_id TEXT, spec_hash TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('RESERVED','ACTIVE','CLOSED')),
          created_at TEXT NOT NULL, closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS child_allocation_resources (
          allocation_id TEXT NOT NULL REFERENCES child_allocations(allocation_id),
          resource TEXT NOT NULL, cap INTEGER NOT NULL CHECK(cap>=0),
          spent INTEGER NOT NULL DEFAULT 0 CHECK(spent>=0),
          reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0),
          unknown INTEGER NOT NULL DEFAULT 0 CHECK(unknown>=0),
          revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
          PRIMARY KEY(allocation_id,resource)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS child_allocation_campaign_unique
          ON child_allocations(child_id) WHERE child_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS budget_reservations (
          reservation_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, allocation_id TEXT,
          action_id TEXT NOT NULL, resource TEXT NOT NULL, upper_bound INTEGER NOT NULL CHECK(upper_bound>0),
          actual INTEGER, state TEXT NOT NULL CHECK(state IN ('RESERVED','UNKNOWN','SETTLED','RELEASED')),
          quality TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, fence INTEGER NOT NULL DEFAULT 0,
          receipt_digest TEXT, created_at TEXT NOT NULL, settled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS budget_reservations_action_idx ON budget_reservations(campaign_id,action_id);
        CREATE UNIQUE INDEX IF NOT EXISTS budget_reservations_one_resource_per_action
          ON budget_reservations(campaign_id,action_id,resource);
        CREATE INDEX IF NOT EXISTS budget_reservations_allocation_idx ON budget_reservations(allocation_id,state);
        """)
        # Registry-local schema changes are additive because this database is
        # shared with CampaignRegistry and may already contain RPDI-03 tables.
        self._ensure_dispatch_column()
        self._ensure_allocation_gates()

    def _ensure_allocation_gates(self) -> None:
        """Add an admission gate without changing legacy allocation columns."""
        self.conn.execute("""CREATE TABLE IF NOT EXISTS child_allocation_gates (
            allocation_id TEXT PRIMARY KEY REFERENCES child_allocations(allocation_id),
            state TEXT NOT NULL CHECK(state IN ('RESERVED','BOUND','ACTIVE','CANCELLED','CLOSED')),
            activation_digest TEXT,
            cancellation_digest TEXT,
            updated_at TEXT NOT NULL
        )""")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO child_allocation_gates "
                "(allocation_id,state,activation_digest,cancellation_digest,updated_at) "
                "SELECT allocation_id,CASE "
                "WHEN state='RESERVED' AND child_id IS NULL THEN 'RESERVED' "
                "WHEN state='RESERVED' AND child_id IS NOT NULL THEN 'BOUND' "
                "WHEN state='ACTIVE' THEN 'ACTIVE' ELSE 'CLOSED' END,"
                "NULL,NULL,created_at FROM child_allocations"
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_dispatch_column(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(budget_reservations)")}
        if "dispatch_state" in columns:
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(budget_reservations)")}
            if "dispatch_state" not in columns:
                self.conn.execute(
                    "ALTER TABLE budget_reservations ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'PRE_DISPATCH' "
                    "CHECK(dispatch_state IN ('PRE_DISPATCH','DISPATCHED'))"
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @classmethod
    def attach(cls, connection: sqlite3.Connection) -> "BudgetLedger":
        """Attach ledger operations to an existing transaction-owning connection."""
        ledger = cls.__new__(cls)
        ledger.path = None
        ledger.conn = connection
        ledger._owns_connection = False
        ledger.conn.row_factory = sqlite3.Row
        ledger.conn.execute("PRAGMA foreign_keys=ON")
        return ledger

    def close(self) -> None:
        if self._owns_connection:
            self.conn.close()

    def create_root(self, campaign_id: str, caps: Mapping[str, int]) -> None:
        """Create a root envelope once; replay is allowed only for the same vector."""
        self._validate_id(campaign_id, "campaign_id")
        vector = _vector(caps)
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            is_root = self.conn.execute(
                "SELECT 1 FROM budget_roots WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            existing = self._accounts(campaign_id)
            if is_root:
                if {k: int(v["cap"]) for k, v in existing.items()} != vector:
                    raise BudgetLedgerError("root budget already exists with a different immutable cap")
                if owns_transaction:
                    self.conn.commit()
                return
            if existing or self.conn.execute(
                "SELECT 1 FROM child_allocations WHERE child_id=?", (campaign_id,)
            ).fetchone():
                raise BudgetLedgerError("a campaign with an existing budget identity cannot become a root")
            self.conn.execute("INSERT INTO budget_roots(campaign_id,created_at) VALUES(?,?)", (campaign_id, utc_now()))
            self.conn.executemany(
                "INSERT INTO budget_accounts(campaign_id,resource,cap) VALUES(?,?,?)",
                [(campaign_id, resource, amount) for resource, amount in vector.items()],
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def reserve_child(
        self, *, allocation_id: str, idempotency_key: str, parent_id: str,
        spec_hash: str, caps: Mapping[str, int],
    ) -> dict[str, object]:
        """Atomically escrow a per-resource vector before registry/DB child creation."""
        for value, label in ((allocation_id, "allocation_id"), (idempotency_key, "idempotency_key"),
                             (parent_id, "parent_id")):
            self._validate_id(value, label)
        if not re.fullmatch(r"[0-9a-f]{64}", spec_hash):
            raise BudgetLedgerError("spec_hash must be a lowercase SHA-256 digest")
        vector = _vector(caps)
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            replay = self.conn.execute(
                "SELECT * FROM child_allocations WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if replay:
                rows = self.conn.execute(
                    "SELECT resource,cap FROM child_allocation_resources WHERE allocation_id=?",
                    (replay["allocation_id"],),
                ).fetchall()
                old = {r["resource"]: int(r["cap"]) for r in rows}
                if (replay["allocation_id"] != allocation_id or replay["parent_id"] != parent_id
                        or replay["spec_hash"] != spec_hash or old != vector):
                    raise BudgetLedgerError("child allocation idempotency key was reused with different content")
                gate = self.conn.execute("SELECT state FROM child_allocation_gates WHERE allocation_id=?", (replay["allocation_id"],)).fetchone()
                if gate is None or gate["state"] in {"CANCELLED", "CLOSED"}:
                    raise BudgetLedgerError("cancelled or closed allocation cannot be replayed")
                if owns_transaction:
                    self.conn.commit()
                return dict(replay)
            if self.conn.execute("SELECT 1 FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone():
                raise BudgetLedgerError("allocation_id is already in use")
            parent_binding = self.conn.execute(
                "SELECT a.state,g.state AS gate_state FROM child_allocations a "
                "JOIN child_allocation_gates g USING(allocation_id) WHERE a.child_id=?", (parent_id,)
            ).fetchone()
            if parent_binding is not None and (parent_binding["state"] != "ACTIVE" or parent_binding["gate_state"] != "ACTIVE"):
                raise BudgetLedgerError("parent child campaign allocation is not active")
            is_root = self.conn.execute(
                "SELECT 1 FROM budget_roots WHERE campaign_id=?", (parent_id,)
            ).fetchone()
            if not is_root and parent_binding is None:
                raise BudgetLedgerError("parent campaign has no active budget authority")
            parent = self._accounts(parent_id)
            parent_allocation = self.conn.execute(
                "SELECT a.allocation_id FROM child_allocations a JOIN child_allocation_gates g USING(allocation_id) "
                "WHERE a.child_id=? AND a.state='ACTIVE' AND g.state='ACTIVE'",
                (parent_id,),
            ).fetchone()
            for resource, amount in vector.items():
                account = parent.get(resource)
                if account is None:
                    raise BudgetExceeded(f"root has no {resource} envelope")
                if int(account["spent"]) + int(account["reserved"]) + int(account["unknown"]) + amount > int(account["cap"]):
                    raise BudgetExceeded(f"child allocation exceeds root {resource} cap")
                if parent_allocation is not None:
                    ancestor = self.conn.execute(
                        "SELECT * FROM child_allocation_resources WHERE allocation_id=? AND resource=?",
                        (parent_allocation["allocation_id"], resource),
                    ).fetchone()
                    if ancestor is None or int(ancestor["spent"]) + int(ancestor["reserved"]) + int(ancestor["unknown"]) + amount > int(ancestor["cap"]):
                        raise BudgetExceeded(f"nested child allocation exceeds parent {resource} envelope")
            now = utc_now()
            self.conn.execute(
                "INSERT INTO child_allocations VALUES(?,?,?,?,?,'RESERVED',?,NULL)",
                (allocation_id, idempotency_key, parent_id, None, spec_hash, now),
            )
            self.conn.execute(
                "INSERT INTO child_allocation_gates(allocation_id,state,updated_at) VALUES(?,'RESERVED',?)",
                (allocation_id, now),
            )
            for resource, amount in vector.items():
                self.conn.execute(
                    "INSERT INTO child_allocation_resources(allocation_id,resource,cap) VALUES(?,?,?)",
                    (allocation_id, resource, amount),
                )
                self.conn.execute(
                    "UPDATE budget_accounts SET reserved=reserved+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                    (amount, parent_id, resource),
                )
                if parent_allocation is not None:
                    self.conn.execute(
                        "UPDATE child_allocation_resources SET reserved=reserved+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                        (amount, parent_allocation["allocation_id"], resource),
                    )
            if owns_transaction:
                self.conn.commit()
            return {"allocation_id": allocation_id, "idempotency_key": idempotency_key,
                    "parent_id": parent_id, "child_id": None, "spec_hash": spec_hash,
                    "state": "RESERVED", "created_at": now}
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def bind_child(self, allocation_id: str, child_id: str, spec_hash: str) -> None:
        """Bind a reserved escrow to exactly one registry identity and confirmed spec."""
        self._validate_id(child_id, "child_id")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
            if row is None or row["spec_hash"] != spec_hash:
                raise BudgetLedgerError("allocation is missing or bound to a different child spec")
            if row["child_id"] not in (None, child_id):
                raise BudgetLedgerError("allocation is already bound to another child")
            gate = self.conn.execute("SELECT state FROM child_allocation_gates WHERE allocation_id=?", (allocation_id,)).fetchone()
            if row["state"] == "CLOSED" or gate is None or gate["state"] in {"CANCELLED", "CLOSED"}:
                raise BudgetLedgerError("closed allocation cannot be rebound")
            if row["child_id"] is None and self.conn.execute(
                "SELECT 1 FROM budget_roots WHERE campaign_id=?", (child_id,)
            ).fetchone():
                raise BudgetLedgerError("an existing root campaign cannot become a child")
            self.conn.execute(
                "UPDATE child_allocations SET child_id=?,state='ACTIVE' WHERE allocation_id=?",
                (child_id, allocation_id),
            )
            self.conn.execute(
                "UPDATE child_allocation_gates SET state='BOUND',updated_at=? "
                "WHERE allocation_id=? AND state IN ('RESERVED','BOUND')",
                (utc_now(), allocation_id),
            )
            caps = self.conn.execute(
                "SELECT resource,cap FROM child_allocation_resources WHERE allocation_id=?", (allocation_id,)
            ).fetchall()
            for item in caps:
                self.conn.execute(
                    "INSERT OR IGNORE INTO budget_accounts(campaign_id,resource,cap) VALUES(?,?,?)",
                    (child_id, item["resource"], item["cap"]),
                )
                actual = self.conn.execute(
                    "SELECT cap FROM budget_accounts WHERE campaign_id=? AND resource=?",
                    (child_id, item["resource"]),
                ).fetchone()
                if int(actual["cap"]) != int(item["cap"]):
                    raise BudgetLedgerError("child account exists with a different allocation cap")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def activate_child(self, allocation_id: str, child_id: str, spec_hash: str, activation_digest: str) -> None:
        """Open child action admission after the parent saga has durably activated."""
        if not re.fullmatch(r"[0-9a-f]{64}", activation_digest):
            raise BudgetLedgerError("activation_digest must be a lowercase SHA-256 digest")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
            gate = self.conn.execute("SELECT * FROM child_allocation_gates WHERE allocation_id=?", (allocation_id,)).fetchone()
            if row is None or gate is None or row["child_id"] != child_id or row["spec_hash"] != spec_hash:
                raise BudgetLedgerError("activation does not match the bound child")
            if gate["state"] == "ACTIVE":
                if gate["activation_digest"] != activation_digest:
                    raise BudgetLedgerError("activation replay digest mismatch")
                self.conn.commit()
                return
            if gate["state"] != "BOUND":
                raise BudgetLedgerError("only a bound allocation can be activated")
            self.conn.execute("UPDATE child_allocation_gates SET state='ACTIVE',activation_digest=?,updated_at=? WHERE allocation_id=?",
                              (activation_digest, utc_now(), allocation_id))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def reserve_action(
        self, *, reservation_id: str, campaign_id: str, action_id: str,
        resource: str, amount: int, idempotency_key: str, fence: int = 0,
        allocation_id: str | None = None,
    ) -> dict[str, object]:
        """Reserve one resource before dispatch; retries return the same reservation."""
        if resource not in RESOURCES:
            raise BudgetLedgerError(f"unknown budget resource: {resource}")
        for value, label in ((reservation_id, "reservation_id"), (campaign_id, "campaign_id"),
                             (action_id, "action_id"), (idempotency_key, "idempotency_key")):
            self._validate_id(value, label)
        if (isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
                or isinstance(fence, bool) or not isinstance(fence, int) or fence < 0):
            raise BudgetLedgerError("amount must be positive and fence nonnegative")
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            replay = self.conn.execute("SELECT * FROM budget_reservations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if replay:
                if (replay["campaign_id"], replay["action_id"], replay["resource"],
                    int(replay["upper_bound"]), replay["allocation_id"]) != (
                    campaign_id, action_id, resource, amount, allocation_id
                ):
                    raise BudgetLedgerError("reservation idempotency key was reused with different content")
                if owns_transaction:
                    self.conn.commit()
                return dict(replay)
            account = self.conn.execute(
                "SELECT * FROM budget_accounts WHERE campaign_id=? AND resource=?", (campaign_id, resource)
            ).fetchone()
            if account is None:
                raise BudgetExceeded(f"campaign has no {resource} account")
            child_binding = self.conn.execute(
                "SELECT allocation_id,state FROM child_allocations WHERE child_id=?",
                (campaign_id,),
            ).fetchone()
            if child_binding is not None and child_binding["state"] != "ACTIVE":
                raise BudgetLedgerError("child campaign allocation is not active")
            gate = None
            if child_binding is not None:
                gate = self.conn.execute("SELECT state FROM child_allocation_gates WHERE allocation_id=?",
                                         (child_binding["allocation_id"],)).fetchone()
                if gate is None or gate["state"] != "ACTIVE":
                    raise BudgetLedgerError("child allocation admission gate is not active")
            if child_binding is not None and allocation_id != child_binding["allocation_id"]:
                raise BudgetLedgerError("child action must be charged to its active parent allocation")
            if child_binding is None and allocation_id is not None:
                raise BudgetLedgerError("allocation does not identify this active child campaign")
            if child_binding is None and self.conn.execute(
                "SELECT 1 FROM budget_roots WHERE campaign_id=?", (campaign_id,)
            ).fetchone() is None:
                raise BudgetLedgerError("campaign has no active root or child budget identity")
            if int(account["spent"]) + int(account["reserved"]) + int(account["unknown"]) + amount > int(account["cap"]):
                raise BudgetExceeded(f"campaign {resource} cap exceeded")
            if allocation_id is not None:
                allocation = self.conn.execute(
                    "SELECT child_id,state FROM child_allocations WHERE allocation_id=?", (allocation_id,)
                ).fetchone()
                gate = self.conn.execute("SELECT state FROM child_allocation_gates WHERE allocation_id=?", (allocation_id,)).fetchone()
                if allocation is None or allocation["child_id"] != campaign_id or allocation["state"] != "ACTIVE" or gate is None or gate["state"] != "ACTIVE":
                    raise BudgetLedgerError("child allocation is not active for this campaign")
                child_resource = self.conn.execute(
                    "SELECT * FROM child_allocation_resources WHERE allocation_id=? AND resource=?",
                    (allocation_id, resource),
                ).fetchone()
                if child_resource is None or int(child_resource["spent"]) + int(child_resource["reserved"]) + int(child_resource["unknown"]) + amount > int(child_resource["cap"]):
                    raise BudgetExceeded(f"child allocation {resource} cap exceeded")
                self.conn.execute(
                    "UPDATE child_allocation_resources SET reserved=reserved+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                    (amount, allocation_id, resource),
                )
            now = utc_now()
            self.conn.execute(
                "INSERT INTO budget_reservations(reservation_id,campaign_id,allocation_id,action_id,resource,upper_bound,actual,state,quality,idempotency_key,fence,receipt_digest,created_at,settled_at) "
                "VALUES(?,?,?,?,?,?,NULL,'RESERVED','upper_bound',?,?,NULL,?,NULL)",
                (reservation_id, campaign_id, allocation_id, action_id, resource, amount, idempotency_key, fence, now),
            )
            self.conn.execute(
                "UPDATE budget_accounts SET reserved=reserved+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                (amount, campaign_id, resource),
            )
            if owns_transaction:
                self.conn.commit()
            return dict(self.conn.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone())
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def settle_action(
        self, reservation_id: str, *, actual: int | None, quality: str,
        receipt_digest: str | None, fence: int, authoritative: bool = False,
    ) -> None:
        """Settle known usage or retain it as UNKNOWN; only receipts reconcile UNKNOWN."""
        if actual is not None and (isinstance(actual, bool) or not isinstance(actual, int) or actual < 0):
            raise BudgetLedgerError("actual usage must be a nonnegative integer")
        if quality not in {"actual", "upper_bound", "unknown"}:
            raise BudgetLedgerError("invalid usage quality")
        if actual is not None and quality == "unknown":
            raise BudgetLedgerError("known usage cannot be labeled unknown")
        if authoritative and actual is None and quality != "upper_bound":
            raise BudgetLedgerError("an upper-bound receipt must be labeled upper_bound")
        if receipt_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", receipt_digest):
            raise BudgetLedgerError("receipt_digest must be a lowercase SHA-256 digest")
        if actual is not None and receipt_digest is None:
            raise BudgetLedgerError("known usage requires an immutable receipt digest")
        if authoritative and receipt_digest is None:
            raise BudgetLedgerError("authoritative reconciliation requires a receipt digest")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise BudgetLedgerError("fence must be a nonnegative integer")
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise BudgetLedgerError("unknown reservation")
            if int(row["fence"]) != fence:
                raise BudgetLedgerError("stale worker fence cannot settle reservation")
            if row["state"] == "SETTLED":
                same_usage = row["actual"] == actual or (
                    actual is None and row["quality"] == "upper_bound"
                    and int(row["actual"]) == int(row["upper_bound"])
                )
                if same_usage and row["receipt_digest"] == receipt_digest:
                    if owns_transaction:
                        self.conn.commit()
                    return
                raise BudgetLedgerError("settled reservation cannot be changed")
            if row["state"] == "RELEASED":
                raise BudgetLedgerError("released reservation cannot be settled")
            if row["state"] == "UNKNOWN" and not authoritative:
                raise BudgetLedgerError("UNKNOWN usage requires provider-authoritative receipt reconciliation")
            if row["state"] in {"RESERVED", "DISPATCHED"} and actual is None and not authoritative:
                self.conn.execute("UPDATE budget_reservations SET state='UNKNOWN',quality='unknown' WHERE reservation_id=?", (reservation_id,))
                self.conn.execute("UPDATE budget_accounts SET reserved=reserved-?,unknown=unknown+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                                  (row["upper_bound"], row["upper_bound"], row["campaign_id"], row["resource"]))
                if row["allocation_id"]:
                    self.conn.execute("UPDATE child_allocation_resources SET reserved=reserved-?,unknown=unknown+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                                      (row["upper_bound"], row["upper_bound"], row["allocation_id"], row["resource"]))
                if owns_transaction:
                    self.conn.commit()
                return
            usage = int(row["upper_bound"]) if actual is None else actual
            if row["state"] == "UNKNOWN":
                old = int(row["upper_bound"])
                self.conn.execute("UPDATE budget_accounts SET unknown=unknown-?,spent=spent+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                                  (old, usage, row["campaign_id"], row["resource"]))
                if row["allocation_id"]:
                    self.conn.execute("UPDATE child_allocation_resources SET unknown=unknown-?,spent=spent+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                                      (old, usage, row["allocation_id"], row["resource"]))
            else:
                self.conn.execute("UPDATE budget_accounts SET reserved=reserved-?,spent=spent+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                                  (row["upper_bound"], usage, row["campaign_id"], row["resource"]))
                if row["allocation_id"]:
                    self.conn.execute("UPDATE child_allocation_resources SET reserved=reserved-?,spent=spent+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                                      (row["upper_bound"], usage, row["allocation_id"], row["resource"]))
            now = utc_now()
            self.conn.execute("UPDATE budget_reservations SET actual=?,state='SETTLED',quality=?,receipt_digest=?,settled_at=? WHERE reservation_id=?",
                              (usage, quality, receipt_digest, now, reservation_id))
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def advance_fence(self, reservation_id: str, *, expected_fence: int, new_fence: int) -> None:
        """Invalidate prior workers before a recovered action may be settled."""
        if (isinstance(expected_fence, bool) or not isinstance(expected_fence, int)
                or isinstance(new_fence, bool) or not isinstance(new_fence, int)
                or new_fence <= expected_fence):
            raise BudgetLedgerError("new worker fence must be an integer greater than the current fence")
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            changed = self.conn.execute(
                "UPDATE budget_reservations SET fence=? WHERE reservation_id=? AND fence=? AND "
                "(state='UNKNOWN' OR (state='RESERVED' AND dispatch_state='PRE_DISPATCH'))",
                (new_fence, reservation_id, expected_fence),
            ).rowcount
            if changed != 1:
                raise BudgetLedgerError("reservation is missing, settled, or already has a newer fence")
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def release_action(self, reservation_id: str, *, fence: int, dispatch_proven_absent: bool) -> None:
        """Release a reservation only with durable evidence that dispatch never occurred."""
        if not dispatch_proven_absent:
            raise BudgetLedgerError("a reservation cannot be released after possible dispatch")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise BudgetLedgerError("fence must be a nonnegative integer")
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise BudgetLedgerError("unknown reservation")
            if int(row["fence"]) != fence:
                raise BudgetLedgerError("stale worker fence cannot release reservation")
            if row["state"] == "RELEASED":
                if owns_transaction:
                    self.conn.commit()
                return
            if row["state"] != "RESERVED" or row["dispatch_state"] != "PRE_DISPATCH":
                raise BudgetLedgerError("only undispatched RESERVED work can be released")
            amount = int(row["upper_bound"])
            self.conn.execute("UPDATE budget_accounts SET reserved=reserved-?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                              (amount, row["campaign_id"], row["resource"]))
            if row["allocation_id"]:
                self.conn.execute("UPDATE child_allocation_resources SET reserved=reserved-?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                                  (amount, row["allocation_id"], row["resource"]))
            self.conn.execute("UPDATE budget_reservations SET state='RELEASED',settled_at=? WHERE reservation_id=?",
                              (utc_now(), reservation_id))
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def mark_dispatched(self, reservation_id: str, *, fence: int) -> None:
        """Persist the irreversible external dispatch boundary before invocation."""
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise BudgetLedgerError("fence must be a nonnegative integer")
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            changed = self.conn.execute(
                "UPDATE budget_reservations SET dispatch_state='DISPATCHED' "
                "WHERE reservation_id=? AND fence=? AND state='RESERVED' AND dispatch_state='PRE_DISPATCH'",
                (reservation_id, fence),
            ).rowcount
            if changed != 1:
                row = self.conn.execute(
                    "SELECT state,dispatch_state,fence FROM budget_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None or int(row["fence"]) != fence or row["state"] != "RESERVED" or row["dispatch_state"] != "DISPATCHED":
                    raise BudgetLedgerError("reservation is missing, stale, or not dispatchable")
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def close_child(self, allocation_id: str, *, terminal_status: str) -> None:
        """Return unused escrow once, only after all child actions are reconciled."""
        if terminal_status not in {"GOAL_MET", "STOPPED"}:
            raise BudgetLedgerError("child allocation can close only after a terminal campaign status")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            allocation = self.conn.execute("SELECT * FROM child_allocations WHERE allocation_id=?", (allocation_id,)).fetchone()
            if allocation is None:
                raise BudgetLedgerError("unknown child allocation")
            if allocation["state"] == "CLOSED":
                self.conn.commit()
                return
            if allocation["child_id"] is None:
                raise BudgetLedgerError("unbound allocation cannot be closed; reconcile the creation saga first")
            open_work = self.conn.execute(
                "SELECT 1 FROM budget_reservations WHERE allocation_id=? AND state IN ('RESERVED','DISPATCHED','UNKNOWN') LIMIT 1",
                (allocation_id,),
            ).fetchone()
            if open_work:
                raise BudgetLedgerError("child has reserved or UNKNOWN work that requires reconciliation")
            nested = self.conn.execute(
                "SELECT 1 FROM child_allocations WHERE parent_id=? AND state IN ('RESERVED','ACTIVE') LIMIT 1",
                (allocation["child_id"],),
            ).fetchone()
            if nested:
                raise BudgetLedgerError("child has open nested allocations that must be reconciled first")
            rows = self.conn.execute("SELECT * FROM child_allocation_resources WHERE allocation_id=?", (allocation_id,)).fetchall()
            for item in rows:
                used = int(item["spent"])
                cap = int(item["cap"])
                if int(item["reserved"]) or int(item["unknown"]):
                    raise BudgetLedgerError("child allocation still holds reserved or UNKNOWN resources")
                parent = self.conn.execute(
                    "SELECT * FROM budget_accounts WHERE campaign_id=? AND resource=?",
                    (allocation["parent_id"], item["resource"]),
                ).fetchone()
                if parent is None or int(parent["reserved"]) < cap:
                    raise BudgetLedgerError("root escrow is inconsistent; refusing child closure")
                ancestor = self.conn.execute(
                    "SELECT allocation_id FROM child_allocations WHERE child_id=? AND state='ACTIVE'",
                    (allocation["parent_id"],),
                ).fetchone()
                if ancestor is not None:
                    upstream = self.conn.execute(
                        "SELECT * FROM child_allocation_resources WHERE allocation_id=? AND resource=?",
                        (ancestor["allocation_id"], item["resource"]),
                    ).fetchone()
                    if upstream is None or int(upstream["reserved"]) < cap:
                        raise BudgetLedgerError("ancestor escrow is inconsistent; refusing nested child closure")
                    self.conn.execute(
                        "UPDATE child_allocation_resources SET reserved=reserved-?,spent=spent+?,revision=revision+1 WHERE allocation_id=? AND resource=?",
                        (cap, used, ancestor["allocation_id"], item["resource"]),
                    )
                self.conn.execute(
                    "UPDATE budget_accounts SET reserved=reserved-?,spent=spent+?,revision=revision+1 WHERE campaign_id=? AND resource=?",
                    (cap, used, allocation["parent_id"], item["resource"]),
                )
            self.conn.execute("UPDATE child_allocations SET state='CLOSED',closed_at=? WHERE allocation_id=?", (utc_now(), allocation_id))
            self.conn.execute("UPDATE child_allocation_gates SET state='CLOSED',updated_at=? WHERE allocation_id=?", (utc_now(), allocation_id))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def snapshot(self, campaign_id: str) -> dict[str, dict[str, int]]:
        return {row["resource"]: {key: int(row[key]) for key in ("cap", "spent", "reserved", "unknown", "revision")}
                for row in self._accounts(campaign_id).values()}

    def _accounts(self, campaign_id: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM budget_accounts WHERE campaign_id=?", (campaign_id,)).fetchall()
        return {row["resource"]: row for row in rows}

    @staticmethod
    def _validate_id(value: str, label: str) -> None:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise BudgetLedgerError(f"invalid {label}")
