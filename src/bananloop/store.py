from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import CampaignSpec, PivotDecisionCommand, PivotProposal, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  spec_json TEXT NOT NULL,
  spec_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  stop_reason TEXT,
  stop_requested INTEGER NOT NULL DEFAULT 0,
  pause_requested INTEGER NOT NULL DEFAULT 0,
  stage TEXT,
  baseline_json TEXT,
  baseline_snapshot TEXT,
  champion_json TEXT,
  champion_snapshot TEXT,
  research_state_json TEXT,
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
  dev_decision_json TEXT,
  failure_analysis_json TEXT,
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
  request_json TEXT,
  response_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluator_bindings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  binding_id TEXT NOT NULL UNIQUE,
  binding_hash TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_receipts (
  action_id TEXT PRIMARY KEY,
  receipt_hash TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  created_at TEXT NOT NULL
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


def campaign_db_initialized(path: Path) -> bool:
    """Inspect campaign state without creating or migrating the SQLite file."""
    if not path.exists():
        return False
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
            ).fetchone()
            if table is None:
                return False
            return conn.execute(
                "SELECT 1 FROM campaigns WHERE id=1"
            ).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise StoreError(
            f"campaign database is unreadable at {path}; preserve it for diagnosis"
        ) from exc


class CampaignStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        existed = path.exists() and path.stat().st_size > 0
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version > 4:
            self.conn.close()
            raise StoreError(f"campaign database schema {version} is newer than this BananLoop version")
        if existed and version < 1:
            backup = path.with_name(path.name + ".pre-operator-v1.bak")
            if not backup.exists():
                with sqlite3.connect(backup) as destination:
                    self.conn.backup(destination)
        if existed and version == 1:
            backup = path.with_name(path.name + ".pre-composite-v2.bak")
            suffix = 0
            while True:
                candidate = backup if suffix == 0 else backup.with_name(backup.name + f".{suffix}")
                try:
                    with candidate.open("xb"):
                        pass
                    backup = candidate
                    break
                except FileExistsError:
                    suffix += 1
            try:
                with sqlite3.connect(backup) as destination:
                    self.conn.backup(destination)
            except Exception:
                backup.unlink(missing_ok=True)
                raise
        if existed and version == 2:
            backup = path.with_name(path.name + ".pre-pivot-v3.bak")
            suffix = 0
            while True:
                candidate = backup if suffix == 0 else backup.with_name(backup.name + f".{suffix}")
                try:
                    with candidate.open("xb"):
                        pass
                    backup = candidate
                    break
                except FileExistsError:
                    suffix += 1
            try:
                with sqlite3.connect(backup) as destination:
                    self.conn.backup(destination)
            except Exception:
                backup.unlink(missing_ok=True)
                raise
        if existed and version == 3:
            backup = path.with_name(path.name + ".pre-pivot-decision-v4.bak")
            suffix = 0
            while True:
                candidate = backup if suffix == 0 else backup.with_name(backup.name + f".{suffix}")
                try:
                    with candidate.open("xb"):
                        pass
                    backup = candidate
                    break
                except FileExistsError:
                    suffix += 1
            try:
                with sqlite3.connect(backup) as destination:
                    self.conn.backup(destination)
            except Exception:
                backup.unlink(missing_ok=True)
                raise
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

        campaign_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(campaigns)")
        }
        if "research_state_json" not in campaign_columns:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN research_state_json TEXT")
        if "stop_requested" not in campaign_columns:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0")
        if "pause_requested" not in campaign_columns:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0")

        action_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(actions)")}
        if "request_json" not in action_columns:
            self.conn.execute("ALTER TABLE actions ADD COLUMN request_json TEXT")

        experiment_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(experiments)")
        }
        if "dev_decision_json" not in experiment_columns:
            self.conn.execute("ALTER TABLE experiments ADD COLUMN dev_decision_json TEXT")
        if "failure_analysis_json" not in experiment_columns:
            self.conn.execute("ALTER TABLE experiments ADD COLUMN failure_analysis_json TEXT")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS operator_requests (
                request_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('pause','resume','end')),
                status TEXT NOT NULL CHECK(status IN ('ACCEPTED','COMPLETED','REJECTED','UNKNOWN')),
                payload_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        composite_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(experiments)")
        }
        if "composite_id" not in composite_columns:
            self.conn.execute("ALTER TABLE experiments ADD COLUMN composite_id TEXT")
        if "composite_step" not in composite_columns:
            self.conn.execute("ALTER TABLE experiments ADD COLUMN composite_step INTEGER")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS composite_sequences (
                id TEXT PRIMARY KEY,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                base_snapshot TEXT NOT NULL,
                tip_snapshot TEXT NOT NULL,
                max_steps INTEGER NOT NULL CHECK(max_steps BETWEEN 2 AND 4),
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','COMPLETED','REJECTED','ABANDONED')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '[]',
                current_step INTEGER NOT NULL DEFAULT 0,
                final_experiment_id INTEGER
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS composite_steps (
                composite_id TEXT NOT NULL REFERENCES composite_sequences(id),
                step_order INTEGER NOT NULL,
                experiment_id INTEGER NOT NULL UNIQUE REFERENCES experiments(id),
                parent_snapshot TEXT NOT NULL,
                candidate_snapshot TEXT NOT NULL,
                dev_decision_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(composite_id,step_order)
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS composite_steps_experiment ON composite_steps(experiment_id)")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS pivot_proposals (
                proposal_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL UNIQUE,
                spec_hash TEXT NOT NULL,
                trigger_experiment_ordinal INTEGER NOT NULL,
                proposal_json TEXT NOT NULL,
                pause_reason TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','STALE')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS pivot_proposals_status ON pivot_proposals(status, created_at)")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS pivot_decisions (
                request_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL REFERENCES pivot_proposals(proposal_id),
                digest TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
                status TEXT NOT NULL CHECK(status IN ('APPROVED','REJECTED','STALE')),
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS pivot_decisions_proposal ON pivot_decisions(proposal_id, created_at)")
        self.conn.execute("PRAGMA user_version=4")
        self.conn.commit()

    def save_pivot_proposal(self, proposal: PivotProposal, pause_reason: str) -> dict[str, Any]:
        """Persist an immutable proposal; replay is idempotent by ID and digest."""
        if not pause_reason or len(pause_reason) > 1000:
            raise StoreError("pivot proposal pause reason must be nonempty and bounded")
        payload = proposal.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self.conn:
            existing = self.pivot_proposal(proposal.proposal_id)
            if existing is not None:
                if existing["digest"] != proposal.digest or existing["proposal_json"] != encoded:
                    raise StoreError("pivot proposal id reused with different content")
                return existing
            self.conn.execute(
                "INSERT INTO pivot_proposals VALUES(?,?,?,?,?,?, 'PENDING',?,?)",
                (
                    proposal.proposal_id, proposal.digest, proposal.spec_hash,
                    proposal.trigger_experiment_ordinal, encoded, pause_reason,
                    utc_now(), utc_now(),
                ),
            )
            self.event("PIVOT_PROPOSAL_CREATED", {
                "proposal_id": proposal.proposal_id,
                "digest": proposal.digest,
                "trigger_experiment_ordinal": proposal.trigger_experiment_ordinal,
                "pause_reason": pause_reason,
            })
            return self.pivot_proposal(proposal.proposal_id)

    def pivot_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pivot_proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def pending_pivot_proposal(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pivot_proposals WHERE status='PENDING' ORDER BY created_at LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def pivot_decision(self, request_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pivot_decisions WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def approved_pivot_for_child(
        self, proposal_id: str, digest: str, campaign_id: str,
    ) -> tuple[PivotProposal, dict[str, Any]]:
        """Return only an intact, approved pivot bound to this paused parent."""
        row = self.pivot_proposal(proposal_id)
        campaign = self.campaign()
        decision_row = self.conn.execute(
            "SELECT * FROM pivot_decisions WHERE campaign_id=? AND proposal_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (campaign_id, proposal_id),
        ).fetchone()
        if (
            campaign["status"] != "PAUSED_NEEDS_INPUT"
            or row is None
            or decision_row is None
            or decision_row["status"] != "APPROVED"
            or decision_row["decision"] != "approve"
            or decision_row["digest"] != digest
            or row["status"] != "APPROVED"
            or not self._pivot_proposal_is_intact(row, digest, require_paused=True)
        ):
            raise StoreError("pivot approval is missing, stale, or bound to a different parent")
        proposal = PivotProposal.model_validate_json(row["proposal_json"])
        if proposal.proposal_id != proposal_id or proposal.spec_hash != campaign["spec_hash"]:
            raise StoreError("approved pivot proposal no longer matches the parent spec")
        return proposal, dict(decision_row)

    def _pivot_proposal_is_intact(
        self, proposal_row: dict[str, Any] | None, expected_digest: str,
        *, require_paused: bool,
    ) -> bool:
        if proposal_row is None:
            return False
        try:
            proposal = PivotProposal.model_validate_json(proposal_row["proposal_json"])
            canonical = json.dumps(
                proposal.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            campaign = self.campaign()
        except (ValueError, TypeError, json.JSONDecodeError, StoreError):
            return False
        return bool(
            proposal.proposal_id == proposal_row["proposal_id"]
            and proposal.digest == expected_digest == proposal_row["digest"]
            and proposal.spec_hash == campaign["spec_hash"] == proposal_row["spec_hash"]
            and canonical == proposal_row["proposal_json"]
            and (not require_paused or campaign["status"] == "PAUSED_NEEDS_INPUT")
        )

    def apply_pivot_decision(
        self, selected_campaign_id: str, command: PivotDecisionCommand,
    ) -> dict[str, Any]:
        """Atomically validate proposal identity/digest and record one decision."""
        if not selected_campaign_id or command.campaign_id != selected_campaign_id:
            raise StoreError("pivot decision targets a different campaign")
        if command.decision == "approve" and command.rejection_action is not None:
            raise StoreError("approval cannot include a rejection action")
        if command.decision == "reject" and command.rejection_action not in {
            None, "continue_old_plan", "change_conditions", "end_campaign",
        }:
            raise StoreError("invalid pivot rejection action")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self.pivot_decision(command.request_id)
            if previous is not None:
                if any(previous[key] != value for key, value in {
                    "campaign_id": command.campaign_id,
                    "proposal_id": command.proposal_id,
                    "digest": command.digest,
                    "decision": command.decision,
                }.items()):
                    raise StoreError("pivot decision request id reused with different command")
                try:
                    previous_action = json.loads(previous["result_json"]).get("rejection_action")
                except (ValueError, TypeError, AttributeError):
                    previous_action = None
                if previous_action != command.rejection_action:
                    raise StoreError("pivot decision request id reused with different command")
                if previous["status"] != "STALE":
                    proposal_row = self.pivot_proposal(command.proposal_id)
                    if (
                        not self._pivot_proposal_is_intact(
                            proposal_row, command.digest, require_paused=False,
                        )
                        or proposal_row["status"] != previous["status"]
                    ):
                        result = json.dumps(
                            {"reason": "approved proposal content is stale"},
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        now = utc_now()
                        if proposal_row is not None:
                            self.conn.execute(
                                "UPDATE pivot_proposals SET status='STALE',updated_at=? WHERE proposal_id=?",
                                (now, command.proposal_id),
                            )
                        self.conn.execute(
                            "UPDATE pivot_decisions SET status='STALE',result_json=? WHERE request_id=?",
                            (result, command.request_id),
                        )
                        self.event("PIVOT_DECISION_INVALIDATED", {
                            "request_id": command.request_id,
                            "proposal_id": command.proposal_id,
                            "digest": command.digest,
                            "reason": "proposal content no longer matches the approved digest",
                        })
                        previous = self.pivot_decision(command.request_id)
                self.conn.commit()
                return previous

            proposal_row = self.pivot_proposal(command.proposal_id)
            if proposal_row is None:
                raise StoreError("pivot proposal not found")
            if command.digest != proposal_row["digest"]:
                raise StoreError("pivot proposal digest mismatch")
            if proposal_row["status"] != "PENDING":
                raise StoreError("pivot proposal is no longer pending")

            result: dict[str, Any] = {}
            status: str
            try:
                if not self._pivot_proposal_is_intact(
                    proposal_row, command.digest, require_paused=True,
                ):
                    raise ValueError("proposal no longer matches its displayed identity or campaign")
                status = "APPROVED" if command.decision == "approve" else "REJECTED"
                result = {"proposal_id": command.proposal_id, "digest": command.digest}
                if command.decision == "reject" and command.rejection_action is not None:
                    result["rejection_action"] = command.rejection_action
            except (ValueError, TypeError, json.JSONDecodeError):
                status = "STALE"
                result = {"reason": "proposal identity or immutable content is stale"}

            now = utc_now()
            self.conn.execute(
                "UPDATE pivot_proposals SET status=?,updated_at=? WHERE proposal_id=? AND status='PENDING'",
                (status, now, command.proposal_id),
            )
            self.conn.execute(
                "INSERT INTO pivot_decisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    command.request_id, command.campaign_id, command.proposal_id,
                    command.digest, command.decision, status,
                    json.dumps(result, sort_keys=True, separators=(",", ":")), now,
                ),
            )
            self.event("PIVOT_DECISION_RECORDED", {
                "request_id": command.request_id,
                "proposal_id": command.proposal_id,
                "digest": command.digest,
                "decision": command.decision,
                "status": status,
            })
            decision = self.pivot_decision(command.request_id)
            self.conn.commit()
            return decision
        except Exception:
            self.conn.rollback()
            raise

    def control_request(self, request_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM operator_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def pending_control_requests(self, kind: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM operator_requests WHERE kind=? AND status='ACCEPTED' ORDER BY created_at",
            (kind,),
        )]

    def record_control_request(self, request_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not request_id or len(request_id) > 200:
            raise StoreError("invalid operator request id")
        if kind not in {"pause", "resume", "end"}:
            raise StoreError("invalid operator request kind")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.conn:
            existing = self.control_request(request_id)
            if existing is not None:
                if existing["kind"] != kind or existing["payload_json"] != encoded:
                    raise StoreError("operator request id reused with different command")
                return existing
            now = utc_now()
            self.conn.execute(
                "INSERT INTO operator_requests VALUES(?,?,?,?,?,?,?)",
                (request_id, kind, "ACCEPTED", encoded, None, now, now),
            )
            self.event("OPERATOR_REQUEST_ACCEPTED", {"request_id": request_id, "kind": kind})
            return self.control_request(request_id)

    def finish_control_request(self, request_id: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        if status not in {"COMPLETED", "REJECTED", "UNKNOWN"}:
            raise StoreError("invalid operator request outcome")
        with self.conn:
            existing = self.control_request(request_id)
            if existing is None:
                raise StoreError("operator request missing")
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
            if existing["status"] != "ACCEPTED":
                if existing["status"] != status or existing["result_json"] != encoded:
                    raise StoreError("operator request outcome already recorded")
                return existing
            self.conn.execute(
                "UPDATE operator_requests SET status=?,result_json=?,updated_at=? WHERE request_id=?",
                (status, encoded, utc_now(), request_id),
            )
            self.event("OPERATOR_REQUEST_FINISHED", {"request_id": request_id, "status": status})
            return self.control_request(request_id)

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


    def research_state(self) -> dict[str, Any] | None:
        payload = self.campaign().get("research_state_json")
        return json.loads(payload) if payload else None

    def set_research_state(self, state: dict[str, Any]) -> None:
        self.update_campaign(research_state_json=json.dumps(state, sort_keys=True))

    def update_campaign(self, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        sql = ",".join(f"{key}=?" for key in fields)
        self.conn.execute(f"UPDATE campaigns SET {sql} WHERE id=1", tuple(fields.values()))
        self.conn.commit()

    def request_stop(self) -> bool:
        """Signal the owner; never overwrite a stage while its process is running."""
        with self.conn:
            changed = self.conn.execute(
                "UPDATE campaigns SET stop_requested=1,updated_at=? WHERE id=1 "
                "AND status NOT IN ('STOPPED','GOAL_MET') AND stop_requested=0",
                (utc_now(),),
            ).rowcount
            if changed:
                self.event("STOP_REQUESTED", {})
        return bool(changed)

    def request_pause(self) -> bool:
        """Request a resumable halt without changing the live stage."""
        with self.conn:
            changed = self.conn.execute(
                "UPDATE campaigns SET pause_requested=1,updated_at=? WHERE id=1 "
                "AND status IN ('READY','RUNNING') AND pause_requested=0 AND stop_requested=0",
                (utc_now(),),
            ).rowcount
            if changed:
                self.event("PAUSE_REQUESTED", {})
        return bool(changed)

    def clear_pause_request(self) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE campaigns SET pause_requested=0,updated_at=? WHERE id=1",
                (utc_now(),),
            )
            self.event("PAUSE_CLEARED", {})

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
        row = self.conn.execute("SELECT COALESCE(MAX(ordinal), 0) AS max_ordinal FROM experiments").fetchone()
        ordinal = int(row["max_ordinal"]) + 1
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

    def create_composite_attempt(self, sequence_id: str) -> tuple[int, int]:
        """Create the next composite experiment, attempt and link atomically."""
        now = utc_now()
        with self.conn:
            campaign = self.campaign()
            sequence = self.conn.execute(
                "SELECT * FROM composite_sequences WHERE id=?", (sequence_id,),
            ).fetchone()
            if sequence is None or sequence["status"] != "ACTIVE":
                raise StoreError("composite is not active")
            if campaign["active_attempt_id"] is not None:
                raise StoreError("cannot create a composite attempt while another attempt is active")
            step_order = int(sequence["current_step"]) + 1
            if step_order > int(sequence["max_steps"]):
                raise StoreError("composite has no remaining steps")
            if campaign["champion_snapshot"] != sequence["base_snapshot"]:
                raise StoreError("composite champion changed before its next step")
            row = self.conn.execute(
                "SELECT COALESCE(MAX(ordinal), 0) AS max_ordinal FROM experiments"
            ).fetchone()
            ordinal = int(row["max_ordinal"]) + 1
            parent_snapshot = sequence["tip_snapshot"]
            cur = self.conn.execute(
                "INSERT INTO experiments(ordinal,parent_snapshot,status,composite_id,composite_step,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (ordinal, parent_snapshot, "ACTIVE", sequence_id, step_order, now, now),
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
            self.event("ATTEMPT_STARTED", {
                "experiment_id": experiment_id, "attempt_id": attempt_id,
                "composite_id": sequence_id, "composite_step": step_order,
            })
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

    def finish_attempt(
        self, attempt_id: int, experiment_id: int, *, status: str,
        scientific: bool, improved: bool, dev_progress: bool = False,
    ) -> None:
        attempt = self.attempt(attempt_id)
        if attempt["experiment_id"] != experiment_id:
            raise StoreError("attempt/experiment mismatch")
        if attempt["status"] == "COMPLETED":
            return
        if self.campaign()["active_attempt_id"] != attempt_id:
            raise StoreError("cannot finish an inactive attempt")
        now = utc_now()
        self.conn.execute("UPDATE attempts SET status='COMPLETED',updated_at=? WHERE id=?", (now, attempt_id))
        self.conn.execute("UPDATE experiments SET status=?,updated_at=? WHERE id=?", (status, now, experiment_id))
        if scientific:
            self.conn.execute("UPDATE campaigns SET experiment_count=experiment_count+1 WHERE id=1")
            if improved or dev_progress:
                self.conn.execute("UPDATE campaigns SET non_improve_streak=0 WHERE id=1")
            else:
                self.conn.execute("UPDATE campaigns SET non_improve_streak=non_improve_streak+1 WHERE id=1")
        self.conn.execute("UPDATE campaigns SET active_experiment_id=NULL,active_attempt_id=NULL,stage=NULL,updated_at=? WHERE id=1", (now,))
        self.event("ATTEMPT_COMPLETED", {"attempt_id": attempt_id, "experiment_id": experiment_id, "status": status})
        self.conn.commit()

    def create_composite(
        self, sequence_id: str, base_snapshot: str, *, plan: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not sequence_id or len(sequence_id) > 80 or not sequence_id.replace("-", "").isalnum():
            raise StoreError("composite ID must be a stable alphanumeric slug")
        if not 2 <= len(plan) <= 4:
            raise StoreError("composite plan must contain between 2 and 4 steps")
        now = utc_now()
        with self.conn:
            campaign = self.campaign()
            if campaign["champion_snapshot"] != base_snapshot:
                raise StoreError("composite base must be the current champion snapshot")
            self.conn.execute(
                "INSERT INTO composite_sequences(id,campaign_id,base_snapshot,tip_snapshot,max_steps,status,created_at,updated_at,plan_json,current_step) VALUES(?,?,?,?,?,'ACTIVE',?,?,?,0)",
                (sequence_id, 1, base_snapshot, base_snapshot, len(plan), now, now,
                 json.dumps(plan, sort_keys=True)),
            )
            self.event("COMPOSITE_STARTED", {"composite_id": sequence_id, "max_steps": len(plan)})
        return self.composite(sequence_id)

    def composite(self, sequence_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM composite_sequences WHERE id=?", (sequence_id,)).fetchone()
        if row is None:
            raise StoreError(f"composite {sequence_id} missing")
        result = dict(row)
        result["steps"] = [dict(item) for item in self.conn.execute(
            "SELECT * FROM composite_steps WHERE composite_id=? ORDER BY step_order", (sequence_id,)
        )]
        return result

    def active_composite(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id FROM composite_sequences WHERE status='ACTIVE' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return self.composite(row["id"]) if row else None

    def attach_composite_attempt(self, sequence_id: str, experiment_id: int, step_order: int) -> None:
        sequence = self.composite(sequence_id)
        if sequence["status"] != "ACTIVE":
            raise StoreError("composite is not active")
        plan = json.loads(sequence["plan_json"])
        if not 1 <= step_order <= len(plan) or int(sequence["current_step"]) + 1 != step_order:
            raise StoreError("composite step is not next in saved plan")
        with self.conn:
            self.conn.execute(
                "UPDATE experiments SET composite_id=?,composite_step=? WHERE id=?",
                (sequence_id, step_order, experiment_id),
            )

    def start_composite_for_attempt(
        self, sequence_id: str, base_snapshot: str, *, plan: list[dict[str, Any]],
        experiment_id: int,
    ) -> dict[str, Any]:
        """Atomically persist a proposal sequence and link its first attempt.

        Replays accept only the same durable plan/base and the same active
        experiment, so a crash after this transaction cannot create a second
        sequence or attach a different proposal to the saved ID.
        """
        if not sequence_id or len(sequence_id) > 80 or not sequence_id.replace("-", "").isalnum():
            raise StoreError("composite ID must be a stable alphanumeric slug")
        if not 2 <= len(plan) <= 4:
            raise StoreError("composite plan must contain between 2 and 4 steps")
        plan_json = json.dumps(plan, sort_keys=True)
        now = utc_now()
        with self.conn:
            campaign = self.campaign()
            experiment = self.experiment(experiment_id)
            if campaign["champion_snapshot"] != base_snapshot:
                raise StoreError("composite base must be the current champion snapshot")
            if campaign["active_experiment_id"] != experiment_id:
                raise StoreError("composite must attach to the active experiment")
            if experiment["parent_snapshot"] != base_snapshot:
                raise StoreError("composite base must match the proposal parent snapshot")
            sequence = self.conn.execute(
                "SELECT * FROM composite_sequences WHERE id=?", (sequence_id,),
            ).fetchone()
            if sequence is None:
                self.conn.execute(
                    "INSERT INTO composite_sequences(id,campaign_id,base_snapshot,tip_snapshot,max_steps,status,created_at,updated_at,plan_json,current_step) VALUES(?,?,?,?,?,'ACTIVE',?,?,?,0)",
                    (sequence_id, 1, base_snapshot, base_snapshot, len(plan), now, now, plan_json),
                )
                self.event("COMPOSITE_STARTED", {
                    "composite_id": sequence_id, "max_steps": len(plan),
                })
            else:
                if (sequence["campaign_id"] != 1
                        or sequence["base_snapshot"] != base_snapshot
                        or sequence["max_steps"] != len(plan)
                        or sequence["plan_json"] != plan_json
                        or sequence["status"] != "ACTIVE"
                        or int(sequence["current_step"]) != 0):
                    raise StoreError("composite replay conflicts with saved proposal identity")
            if experiment.get("composite_id") is None:
                self.conn.execute(
                    "UPDATE experiments SET composite_id=?,composite_step=1 WHERE id=?",
                    (sequence_id, experiment_id),
                )
            elif experiment["composite_id"] != sequence_id or int(experiment["composite_step"] or 0) != 1:
                raise StoreError("composite replay conflicts with active experiment link")
        return self.composite(sequence_id)

    def complete_composite_step(
        self, sequence_id: str, experiment_id: int, *, parent_snapshot: str,
        candidate_snapshot: str, dev_decision: dict[str, Any], status: str,
    ) -> int:
        experiment = self.experiment(experiment_id)
        expected_order = int(experiment.get("composite_step") or 0)
        decision_json = json.dumps(dev_decision, sort_keys=True)
        now = utc_now()
        with self.conn:
            sequence = self.conn.execute(
                "SELECT * FROM composite_sequences WHERE id=?", (sequence_id,)
            ).fetchone()
            if sequence is None:
                raise StoreError("composite is missing")
            existing = self.conn.execute(
                "SELECT * FROM composite_steps WHERE composite_id=? AND experiment_id=?",
                (sequence_id, experiment_id),
            ).fetchone()
            if existing:
                order = int(existing["step_order"])
                if (order != expected_order or existing["parent_snapshot"] != parent_snapshot
                        or existing["candidate_snapshot"] != candidate_snapshot
                        or existing["dev_decision_json"] != decision_json or existing["status"] != status):
                    raise StoreError("composite retry conflicts with recorded step")
                expected_tip = parent_snapshot if status == "FAILED" else candidate_snapshot
                if int(sequence["current_step"]) == order - 1 and sequence["tip_snapshot"] == expected_tip:
                    self.conn.execute(
                        "UPDATE composite_sequences SET current_step=?,updated_at=? WHERE id=?",
                        (order, now, sequence_id),
                    )
                elif int(sequence["current_step"]) != order or sequence["tip_snapshot"] != expected_tip:
                    raise StoreError("composite retry conflicts with durable sequence state")
                return order
            if sequence["status"] != "ACTIVE":
                raise StoreError("composite is not active")
            order = int(sequence["current_step"]) + 1
            if expected_order != order:
                raise StoreError("composite experiment step order mismatch")
            if parent_snapshot != sequence["tip_snapshot"]:
                raise StoreError("composite parent must equal durable sequence tip")
            if order > int(sequence["max_steps"]):
                raise StoreError("composite step limit exceeded")
            self.conn.execute("INSERT INTO composite_steps VALUES(?,?,?,?,?,?,?,?,?)", (
                sequence_id, order, experiment_id, parent_snapshot, candidate_snapshot,
                decision_json, status, now, now,
            ))
            tip_snapshot = parent_snapshot if status == "FAILED" else candidate_snapshot
            self.conn.execute(
                "UPDATE composite_sequences SET tip_snapshot=?,current_step=?,updated_at=? WHERE id=?",
                (tip_snapshot, order, now, sequence_id),
            )
            self.event("COMPOSITE_STEP_RECORDED", {"composite_id": sequence_id, "step": order,
                "experiment_id": experiment_id, "snapshot": candidate_snapshot, "status": status})
        return order

    def finish_composite(self, sequence_id: str, *, status: str, final_experiment_id: int | None = None) -> None:
        if status not in {"COMPLETED", "REJECTED", "ABANDONED"}:
            raise StoreError("invalid composite terminal status")
        with self.conn:
            row = self.conn.execute("SELECT status,final_experiment_id FROM composite_sequences WHERE id=?", (sequence_id,)).fetchone()
            if row is None:
                raise StoreError("composite is missing")
            if row["status"] != "ACTIVE":
                if row["status"] == status and row["final_experiment_id"] == final_experiment_id:
                    return
                raise StoreError("composite already has a different terminal outcome")
            self.conn.execute(
                "UPDATE composite_sequences SET status=?,final_experiment_id=?,updated_at=? WHERE id=?",
                (status, final_experiment_id, utc_now(), sequence_id),
            )
            self.event("COMPOSITE_FINISHED", {"composite_id": sequence_id, "status": status,
                "final_experiment_id": final_experiment_id})

    def promote_attempt(self, attempt_id: int, experiment_id: int) -> None:
        """Commit champion, promotion receipt and next stage as one transaction."""
        attempt = self.attempt(attempt_id)
        experiment = self.experiment(experiment_id)
        campaign = self.campaign()
        if attempt["experiment_id"] != experiment_id:
            raise StoreError("attempt/experiment mismatch")
        if attempt["stage"] == "RECORD" and campaign["champion_snapshot"] == experiment["candidate_snapshot"]:
            return
        if attempt["stage"] != "PROMOTE" or campaign["active_attempt_id"] != attempt_id:
            raise StoreError("promotion requires the active PROMOTE stage")
        if not json.loads(experiment["decision_json"] or "{}").get("accepted"):
            raise StoreError("cannot promote a rejected candidate")
        if experiment.get("composite_id"):
            sequence = self.composite(experiment["composite_id"])
            final_order = int(experiment.get("composite_step") or 0)
            plan_length = len(json.loads(sequence["plan_json"]))
            composite_final_is_current = (
                sequence["status"] == "ACTIVE"
                and final_order == plan_length
                and int(sequence["current_step"]) == final_order - 1
                and sequence["tip_snapshot"] == experiment["parent_snapshot"]
                and sequence["base_snapshot"] == campaign["champion_snapshot"]
            )
            if not composite_final_is_current:
                raise StoreError("composite promotion is not the current final step")
        elif campaign["champion_snapshot"] not in {experiment["parent_snapshot"], experiment["candidate_snapshot"]}:
            raise StoreError("champion changed before promotion")
        now = utc_now()
        with self.conn:
            self.conn.execute(
                "UPDATE campaigns SET champion_snapshot=?,champion_json=?,stage='RECORD',updated_at=? WHERE id=1",
                (experiment["candidate_snapshot"], experiment["evaluation_json"], now),
            )
            self.conn.execute("UPDATE attempts SET stage='RECORD',updated_at=? WHERE id=?", (now, attempt_id))
            self.event("CHAMPION_PROMOTED", {"experiment_id": experiment_id, "snapshot": experiment["candidate_snapshot"]})
            self.event("STAGE_ENTERED", {"attempt_id": attempt_id, "stage": "RECORD"})

    def abandon_active_attempt(self, reason: str) -> None:
        campaign = self.campaign()
        if campaign["active_attempt_id"] is None:
            return
        now = utc_now()
        with self.conn:
            self.conn.execute(
                "UPDATE attempts SET status='CANCELLED',updated_at=? WHERE id=?",
                (now, campaign["active_attempt_id"]),
            )
            self.conn.execute(
                "UPDATE experiments SET status='CANCELLED',failure_category=?,lesson=?,updated_at=? WHERE id=?",
                (reason, "Campaign ended before this attempt completed", now, campaign["active_experiment_id"]),
            )
            self.conn.execute("UPDATE campaigns SET active_attempt_id=NULL,active_experiment_id=NULL WHERE id=1")
            self.event("ATTEMPT_CANCELLED", {"attempt_id": campaign["active_attempt_id"], "reason": reason})

    def action(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _resource_for_kind(kind: str) -> str:
        return "agent_call" if kind == "agent" else "evaluator_call"

    def evaluation_work_reserved(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM reservations WHERE resource='evaluation_unit'").fetchone()[0])

    def _reserve_action(self, action_id: str, kind: str, now: str) -> None:
        if kind != "agent":
            units = self.spec().budget.evaluation_units_per_call
            if units is not None:
                # Never refund failed, cancelled or unknown work: it may have run.
                if self.evaluation_work_reserved() + units > self.spec().budget.max_evaluation_units:
                    raise StoreError("evaluation work budget exhausted")
                self.conn.execute(
                    "INSERT INTO reservations(action_id,resource,amount,status,quality,reserved_at) VALUES(?,?,?,?,?,?)",
                    (action_id, "evaluation_unit", units, "RESERVED", "upper_bound", now),
                )
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
            "UPDATE reservations SET status=?,settled_at=?,duration_ms=CASE WHEN resource='evaluation_unit' THEN NULL ELSE ? END WHERE action_id=? AND status='RESERVED'",
            (status, now, duration_ms, action_id),
        )

    def start_action(self, action_id: str, experiment_id: int | None, stage: str, kind: str, request_hash: str, request: dict[str, Any] | None = None) -> None:
        now = utc_now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO actions(id,experiment_id,stage,kind,status,request_hash,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (action_id, experiment_id, stage, kind, "RUNNING", request_hash, json.dumps(request, sort_keys=True) if request is not None else None, now, now),
            )
            counter = "agent_calls" if kind == "agent" else "evaluator_calls"
            self._reserve_action(action_id, kind, now)
            self.conn.execute(f"UPDATE campaigns SET {counter}={counter}+1,updated_at=? WHERE id=1", (now,))
            self.event("ACTION_STARTED", {"action_id": action_id, "kind": kind, "stage": stage})

    def finish_action(self, action_id: str, response: dict[str, Any]) -> None:
        now = utc_now()
        receipt = response.pop("_evaluation_receipt", None)
        with self.conn:
            self.conn.execute(
                "UPDATE actions SET status='COMPLETED',response_json=?,updated_at=? WHERE id=?",
                (json.dumps(response, sort_keys=True), now, action_id),
            )
            if receipt is not None:
                prior = self.conn.execute(
                    "SELECT receipt_hash,receipt_json FROM evaluation_receipts WHERE action_id=?",
                    (action_id,),
                ).fetchone()
                encoded_receipt = json.dumps(receipt, sort_keys=True)
                if prior and (prior["receipt_hash"] != receipt["receipt_hash"] or prior["receipt_json"] != encoded_receipt):
                    raise StoreError(f"conflicting evaluation receipt for action {action_id}")
                if not prior:
                    self.conn.execute(
                        "INSERT INTO evaluation_receipts(action_id,receipt_hash,receipt_json,created_at) VALUES(?,?,?,?)",
                        (action_id, receipt["receipt_hash"], encoded_receipt, now),
                    )
            self._settle_reservation(action_id, "CONSUMED", now)
            self.event("ACTION_COMPLETED", {"action_id": action_id})

    def evaluator_binding(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM evaluator_bindings WHERE id=1").fetchone()
        return dict(row) if row else None

    def actions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM actions ORDER BY created_at,id")]

    def save_evaluator_binding(self, binding_id: str, binding_hash: str, binding_json: str) -> None:
        existing = self.evaluator_binding()
        if existing:
            if existing["binding_id"] != binding_id or existing["binding_hash"] != binding_hash:
                raise StoreError("immutable evaluator binding mismatch")
            return
        with self.conn:
            self.conn.execute(
                "INSERT INTO evaluator_bindings(id,binding_id,binding_hash,binding_json,created_at) VALUES(1,?,?,?,?)",
                (binding_id, binding_hash, binding_json, utc_now()),
            )

    def evaluation_receipt(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM evaluation_receipts WHERE action_id=?", (action_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["receipt"] = json.loads(result.pop("receipt_json"))
        return result

    def evaluation_receipts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM evaluation_receipts ORDER BY action_id")
        result = []
        for row in rows:
            item = dict(row)
            item["receipt"] = json.loads(item.pop("receipt_json"))
            result.append(item)
        return result

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
        with self.conn:
            self._settle_reservation(action_id, "UNKNOWN", now)
            self.conn.execute(
                "UPDATE actions SET status='RUNNING',response_json=NULL,error=NULL,updated_at=? WHERE id=?",
                (now, action_id),
            )
            self._reserve_action(action_id, action["kind"], now)
            self.conn.execute(f"UPDATE campaigns SET {counter}={counter}+1,updated_at=? WHERE id=1", (now,))
            self.event("ACTION_RETRIED", {"action_id": action_id, "kind": action["kind"], "stage": action["stage"]})

    def record_evaluation(
        self,
        experiment_id: int | None,
        snapshot: str,
        result: dict[str, Any],
        *,
        tier: str = "dev",
    ) -> None:
        existing = self.conn.execute(
            "SELECT id FROM evaluations WHERE experiment_id IS ? AND candidate_snapshot=? AND tier=?",
            (experiment_id, snapshot, tier),
        ).fetchone()
        if existing:
            return
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
            "evaluation_units_reserved": self.evaluation_work_reserved(),
            "evaluation_units_limit": self.spec().budget.max_evaluation_units,
            "evaluation_units_reported": sum(
                json.loads(row["response_json"]).get("resource_units") or 0
                for row in self.conn.execute("SELECT response_json FROM actions WHERE kind!='agent' AND status='COMPLETED'")
            ),
            "wall_time_seconds": wall_ms / 1000.0,
            "open_reservations": sum(1 for row in rows if row["status"] == "RESERVED"),
            "unknown_outcomes": sum(1 for row in rows if row["status"] == "UNKNOWN"),
            "reservation_count": len(rows),
        }

    def events(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM events ORDER BY seq")]
