from __future__ import annotations

import json
import sqlite3
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
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER,
  candidate_snapshot TEXT NOT NULL,
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

    def start_action(self, action_id: str, experiment_id: int | None, stage: str, kind: str, request_hash: str) -> None:
        now = utc_now()
        self.conn.execute(
            "INSERT INTO actions(id,experiment_id,stage,kind,status,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (action_id, experiment_id, stage, kind, "RUNNING", request_hash, now, now),
        )
        counter = "agent_calls" if kind == "agent" else "evaluator_calls"
        self.conn.execute(f"UPDATE campaigns SET {counter}={counter}+1,updated_at=? WHERE id=1", (now,))
        self.event("ACTION_STARTED", {"action_id": action_id, "kind": kind, "stage": stage})
        self.conn.commit()

    def finish_action(self, action_id: str, response: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE actions SET status='COMPLETED',response_json=?,updated_at=? WHERE id=?",
            (json.dumps(response, sort_keys=True), utc_now(), action_id),
        )
        self.event("ACTION_COMPLETED", {"action_id": action_id})
        self.conn.commit()

    def fail_action(self, action_id: str, error: str) -> None:
        self.conn.execute("UPDATE actions SET status='FAILED',error=?,updated_at=? WHERE id=?", (error, utc_now(), action_id))
        self.event("ACTION_FAILED", {"action_id": action_id, "error": error[:500]})
        self.conn.commit()

    def record_evaluation(self, experiment_id: int | None, snapshot: str, result: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO evaluations(experiment_id,candidate_snapshot,result_json,created_at) VALUES(?,?,?,?)",
            (experiment_id, snapshot, json.dumps(result, sort_keys=True), utc_now()),
        )
        self.conn.commit()

    def record_decision(self, experiment_id: int, snapshot: str, decision: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO decisions(experiment_id,candidate_snapshot,decision_json,created_at) VALUES(?,?,?,?)",
            (experiment_id, snapshot, json.dumps(decision, sort_keys=True), utc_now()),
        )
        self.conn.commit()

    def experiments(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM experiments ORDER BY id")]

    def events(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM events ORDER BY seq")]
