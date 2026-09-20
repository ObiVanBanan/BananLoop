from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import CampaignSpec, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  spec_json TEXT NOT NULL,
  spec_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  stop_reason TEXT,
  stage TEXT,
  baseline_json TEXT,
  baseline_snapshot TEXT,
  champion_json TEXT,
  champion_snapshot TEXT,
  active_experiment_id INTEGER,
  active_attempt_id INTEGER,
  experiment_count INTEGER NOT NULL DEFAULT 0,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  agent_calls INTEGER NOT NULL DEFAULT 0,
  evaluator_calls INTEGER NOT NULL DEFAULT 0,
  non_improve_streak INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ordinal INTEGER NOT NULL,
  hypothesis_json TEXT,
  parent_snapshot TEXT NOT NULL,
  candidate_snapshot TEXT,
  evaluation_json TEXT,
  decision_json TEXT,
  status TEXT NOT NULL,
  failure_category TEXT,
  lesson TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY,
  experiment_id INTEGER,
  stage TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id TEXT NOT NULL,
  resource TEXT NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL,
  quality TEXT NOT NULL,
  reserved_at TEXT NOT NULL,
  settled_at TEXT,
  duration_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_reservations_action ON reservations(action_id, id);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER,
  candidate_snapshot TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'dev',
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL UNIQUE,
  candidate_snapshot TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    pass


class CampaignStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply additive migrations so existing campaign state remains usable."""
        evaluation_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(evaluations)")
        }
        if "tier" not in evaluation_columns:
            self.conn.execute(
                "ALTER TABLE evaluations ADD COLUMN tier TEXT NOT NULL DEFAULT 'dev'"
            )
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def initialize(self, spec: CampaignSpec) -> None:
        if self.conn.execute("SELECT 1 FROM campaigns WHERE id=1").fetchone():
            raise StoreError("campaign already exists for this state directory")
        now = utc_now()
        self.conn.execute(
            "INSERT INTO campaigns(id,spec_json,spec_hash,status,created_at,updated_at) VALUES(1,?,?,?,?,?)",
            (spec.model_dump_json(), spec.approved_hash(), "READY", now, now),
        )
        self.event("CAMPAIGN_CREATED", {"spec_hash": spec.approved_hash()})
        self.conn.commit()

    def campaign(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM campaigns WHERE id=1").fetchone()
        if not row:
            raise StoreError("campaign is not initialized; run `bananloop init` first")
        return dict(row)

    def spec(self) -> CampaignSpec:
        return CampaignSpec.model_validate_json(self.campaign()["spec_json"])

    def update_campaign(self, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        sql = ",".join(f"{key}=?" for key in fields)
        self.conn.execute(f"UPDATE campaigns SET {sql} WHERE id=1", tuple(fields.values()))
        self.conn.commit()

    def increment(self, field: str, amount: int = 1) -> None:
        allowed = {"experiment_count", "attempt_count", "agent_calls", "evaluator_calls", "non_improve_streak"}
        if field not in allowed:
            raise StoreError(f"invalid counter: {field}")
        self.conn.execute(f"UPDATE campaigns SET {field}={field}+?, updated_at=? WHERE id=1", (amount, utc_now()))
        self.conn.commit()

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
            (event_type, json.dumps(payload, sort_keys=True), utc_now()),
        )

    def create_attempt(self, parent_snapshot: str) -> tuple[int, int]:
        campaign = self.campaign()
        ordinal = campaign["experiment_count"] + 1
        now = utc_now()
        cur = self.conn.execute(
            "INSERT INTO experiments(ordinal,parent_snapshot,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (ordinal, parent_snapshot, "ACTIVE", now, now),
        )
        experiment_id = int(cur.lastrowid)
        cur = self.conn.execute(
            "INSERT INTO attempts(experiment_id,stage,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (experiment_id, "PROPOSE", "ACTIVE", now, now),
        )
        attempt_id = int(cur.lastrowid)
        self.conn.execute(
            "UPDATE campaigns SET active_experiment_id=?,active_attempt_id=?,attempt_count=attempt_count+1,stage='PROPOSE',updated_at=? WHERE id=1",
            (experiment_id, attempt_id, now),
        )
        self.event("ATTEMPT_STARTED", {"experiment_id": experiment_id, "attempt_id": attempt_id})
        self.conn.commit()
        return experiment_id, attempt_id

    def attempt(self, attempt_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise StoreError(f"attempt {attempt_id} missing")
        return dict(row)

    def experiment(self, experiment_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row:
            raise StoreError(f"experiment {experiment_id} missing")
        return dict(row)

    def set_stage(self, attempt_id: int, stage: str) -> None:
        now = utc_now()
        self.conn.execute("UPDATE attempts SET stage=?,updated_at=? WHERE id=?", (stage, now, attempt_id))
        self.conn.execute("UPDATE campaigns SET stage=?,updated_at=? WHERE id=1", (stage, now))
        self.event("STAGE_ENTERED", {"attempt_id": attempt_id, "stage": stage})
        self.conn.commit()

    def update_experiment(self, experiment_id: int, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        sql = ",".join(f"{key}=?" for key in fields)
        self.conn.execute(f"UPDATE experiments SET {sql} WHERE id=?", tuple(fields.values()) + (experiment_id,))
        self.conn.commit()

    def finish_attempt(self, attempt_id: int, experiment_id: int, *, status: str, scientific: bool, improved: bool) -> None:
        now = utc_now()
        self.conn.execute("UPDATE attempts SET status='COMPLETED',updated_at=? WHERE id=?", (now, attempt_id))
        self.conn.execute("UPDATE experiments SET status=?,updated_at=? WHERE id=?", (status, now, experiment_id))
        if scientific:
            self.conn.execute("UPDATE campaigns SET experiment_count=experiment_count+1 WHERE id=1")
            if improved:
                self.conn.execute("UPDATE campaigns SET non_improve_streak=0 WHERE id=1")
            else:
                self.conn.execute("UPDATE campaigns SET non_improve_streak=non_improve_streak+1 WHERE id=1")
        self.conn.execute("UPDATE campaigns SET active_experiment_id=NULL,active_attempt_id=NULL,stage=NULL,updated_at=? WHERE id=1", (now,))
        self.event("ATTEMPT_COMPLETED", {"attempt_id": attempt_id, "experiment_id": experiment_id, "status": status})
        self.conn.commit()

    def action(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _resource_for_kind(kind: str) -> str:
        return "agent_call" if kind == "agent" else "evaluator_call"

    def _reserve_action(self, action_id: str, kind: str, now: str) -> None:
        self.conn.execute(
            "INSERT INTO reservations(action_id,resource,amount,status,quality,reserved_at) VALUES(?,?,?,?,?,?)",
            (action_id, self._resource_for_kind(kind), 1.0, "RESERVED", "actual", now),
        )

    def _settle_reservation(self, action_id: str, status: str, now: str) -> None:
        row = self.conn.execute(
            "SELECT id,reserved_at FROM reservations WHERE action_id=? AND status='RESERVED' ORDER BY id DESC LIMIT 1",
            (action_id,),
        ).fetchone()
        if not row:
            return
        started = datetime.fromisoformat(row["reserved_at"])
        ended = datetime.fromisoformat(now)
        duration_ms = max(0.0, (ended - started).total_seconds() * 1000.0)
        self.conn.execute(
            "UPDATE reservations SET status=?,settled_at=?,duration_ms=? WHERE id=?",
            (status, now, duration_ms, row["id"]),
        )

    def start_action(self, action_id: str, experiment_id: int | None, stage: str, kind: str, request_hash: str) -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT INTO actions(id,experiment_id,stage,kind,status,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (action_id, experiment_id, stage, kind, "RUNNING", request_hash, now, now),
        )
        counter = "agent_calls" if kind == "agent" else "evaluator_calls"
        self._reserve_action(action_id, kind, now)
        self.conn.execute(f"UPDATE campaigns SET {counter}={counter}+1,updated_at=? WHERE id=1", (now,))
        self.event("ACTION_STARTED", {"action_id": action_id, "kind": kind, "stage": stage})
        self.conn.commit()

    def finish_action(self, action_id: str, response: dict[str, Any]) -> None:
        now = utc_now()
        self.conn.execute(
            "UPDATE actions SET status='COMPLETED',response_json=?,updated_at=? WHERE id=?",
            (json.dumps(response, sort_keys=True), now, action_id),
        )
        self._settle_reservation(action_id, "CONSUMED", now)
        self.event("ACTION_COMPLETED", {"action_id": action_id})
        self.conn.commit()

    def fail_action(self, action_id: str, error: str) -> None:
        now = utc_now()
        self.conn.execute("UPDATE actions SET status='FAILED',error=?,updated_at=? WHERE id=?", (error, now, action_id))
        self._settle_reservation(action_id, "FAILED", now)
        self.event("ACTION_FAILED", {"action_id": action_id, "error": error[:500]})
        self.conn.commit()

    def cancel_action(self, action_id: str, reason: str = "user interrupt") -> None:
        now = utc_now()
        self.conn.execute(
            "UPDATE actions SET status='CANCELLED',error=?,updated_at=? WHERE id=?",
            (reason, now, action_id),
        )
        self._settle_reservation(action_id, "CANCELLED", now)
        self.event("ACTION_CANCELLED", {"action_id": action_id, "reason": reason[:500]})
        self.conn.commit()

    def restart_action(self, action_id: str) -> None:
        action = self.action(action_id)
        if not action:
            raise StoreError(f"action {action_id} missing")
        now = utc_now()
        counter = "agent_calls" if action["kind"] == "agent" else "evaluator_calls"
        self._settle_reservation(action_id, "UNKNOWN", now)
        self.conn.execute(
            "UPDATE actions SET status='RUNNING',response_json=NULL,error=NULL,updated_at=? WHERE id=?",
            (now, action_id),
        )
        self._reserve_action(action_id, action["kind"], now)
        self.conn.execute(f"UPDATE campaigns SET {counter}={counter}+1,updated_at=? WHERE id=1", (now,))
        self.event("ACTION_RETRIED", {"action_id": action_id, "kind": action["kind"], "stage": action["stage"]})
        self.conn.commit()

    def record_evaluation(
        self,
        experiment_id: int | None,
        snapshot: str,
        result: dict[str, Any],
        *,
        tier: str = "dev",
    ) -> None:
        self.conn.execute(
            "INSERT INTO evaluations(experiment_id,candidate_snapshot,tier,result_json,created_at) VALUES(?,?,?,?,?)",
            (experiment_id, snapshot, tier, json.dumps(result, sort_keys=True), utc_now()),
        )
        self.conn.commit()

    def evaluations(self, tier: str | None = None) -> list[dict[str, Any]]:
        if tier is None:
            rows = self.conn.execute("SELECT * FROM evaluations ORDER BY id")
        else:
            rows = self.conn.execute(
                "SELECT * FROM evaluations WHERE tier=? ORDER BY id",
                (tier,),
            )
        return [dict(row) for row in rows]

    def latest_evaluation(self, snapshot: str, tier: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM evaluations WHERE candidate_snapshot=? AND tier=? ORDER BY id DESC LIMIT 1",
            (snapshot, tier),
        ).fetchone()
        return dict(row) if row else None

    def record_decision(self, experiment_id: int, snapshot: str, decision: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO decisions(experiment_id,candidate_snapshot,decision_json,created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(experiment_id) DO UPDATE SET
              candidate_snapshot=excluded.candidate_snapshot,
              decision_json=excluded.decision_json,
              created_at=excluded.created_at
            """,
            (experiment_id, snapshot, json.dumps(decision, sort_keys=True), utc_now()),
        )
        self.conn.commit()

    def experiments(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM experiments ORDER BY id")]

    def reservations(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM reservations ORDER BY id")]

    def resource_summary(self) -> dict[str, Any]:
        rows = self.reservations()
        wall_ms = sum(float(row["duration_ms"] or 0.0) for row in rows)
        return {
            "wall_time_seconds": wall_ms / 1000.0,
            "open_reservations": sum(1 for row in rows if row["status"] == "RESERVED"),
            "unknown_outcomes": sum(1 for row in rows if row["status"] == "UNKNOWN"),
            "reservation_count": len(rows),
        }

    def events(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM events ORDER BY seq")]
