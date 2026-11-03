"""Recoverable v2 pivot-to-child campaign saga.

The project registry and each campaign use separate SQLite files. This module
records intent and progress in the parent DB, escrows budget before taking a
registry child slot, and resumes idempotently after any process interruption.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .budget_ledger import BudgetLedger, BudgetLedgerError
from .contracts import (
    ExplorationCampaignSpecV2,
    OptimizationCampaignSpecV2,
    ParentCampaignRefV2,
    PivotDecisionCommand,
    PivotProposal,
    RuntimePaths,
    parse_campaign_spec_document,
    utc_now,
)
from .migrations import create_v5_graph_schema
from .registry import (
    CampaignRegistry,
    RegistryError,
    assert_safe_campaign_db,
)
from .store import StoreError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_STATES = ("INTENT", "ESCROWED", "CHILD_RESERVED", "ALLOCATION_BOUND",
           "CHILD_INITIALIZED", "ACTIVE")
_SPEC_TYPES = (ExplorationCampaignSpecV2, OptimizationCampaignSpecV2)


class PivotSagaError(StoreError):
    """A v2 pivot cannot safely authorize or recover its child campaign."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _spec_json(spec: Any) -> str:
    return _canonical(spec.model_dump(mode="json"))


def _parse_v2_spec(value: dict[str, Any] | str):
    try:
        spec = parse_campaign_spec_document(value)
    except Exception as exc:
        raise PivotSagaError("pivot child must contain a valid frozen v2 campaign spec") from exc
    if not isinstance(spec, _SPEC_TYPES):
        raise PivotSagaError("pivot child must use schema_version=2")
    return spec


def _budget_caps(spec: Any) -> dict[str, int]:
    budget = spec.budget
    caps = {
        "agent_calls": budget.max_agent_calls,
        "evaluator_calls": budget.max_evaluator_calls,
        "experiments": budget.max_experiments,
        "attempts": budget.max_attempts,
    }
    if budget.max_model_tokens is not None:
        caps["model_tokens"] = budget.max_model_tokens
    if budget.max_evaluation_units is not None:
        caps["evaluation_units"] = budget.max_evaluation_units
    return caps


def _validate_budget_inheritance(parent: Any, child: Any) -> None:
    fields = (
        "max_experiments", "max_attempts", "max_agent_calls",
        "max_evaluator_calls", "max_model_tokens", "max_evaluation_units",
    )
    for name in fields:
        parent_cap, child_cap = getattr(parent.budget, name), getattr(child.budget, name)
        if parent_cap is not None and (child_cap is None or child_cap > parent_cap):
            raise PivotSagaError(f"child budget expands the parent {name} envelope")


def _create_parent_saga_schema(conn: sqlite3.Connection) -> None:
    required = {"seq", "event_type", "payload_json", "created_at"}
    event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if not required <= event_columns:
        raise PivotSagaError("parent v2 campaign has no compatible durable event log")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS v2_pivot_proposals (
            proposal_id TEXT PRIMARY KEY,
            digest TEXT NOT NULL CHECK(length(digest)=64),
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            proposal_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','STALE')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS v2_pivot_decisions (
            request_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL REFERENCES v2_pivot_proposals(proposal_id),
            digest TEXT NOT NULL CHECK(length(digest)=64),
            decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
            rejection_action TEXT,
            status TEXT NOT NULL CHECK(status IN ('APPROVED','REJECTED','STALE')),
            created_at TEXT NOT NULL
        )"""
    )
    decision_columns = {row["name"] for row in conn.execute("PRAGMA table_info(v2_pivot_decisions)")}
    if "rejection_action" not in decision_columns:
        conn.execute("ALTER TABLE v2_pivot_decisions ADD COLUMN rejection_action TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS v2_pivot_sagas (
            idempotency_key TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
            parent_campaign_id TEXT NOT NULL,
            parent_spec_hash TEXT NOT NULL CHECK(length(parent_spec_hash)=64),
            proposal_json TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            child_id TEXT,
            allocation_id TEXT NOT NULL UNIQUE,
            child_spec_json TEXT NOT NULL,
            child_spec_hash TEXT NOT NULL CHECK(length(child_spec_hash)=64),
            budget_caps_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN (
                'INTENT','ESCROWED','CHILD_RESERVED','ALLOCATION_BOUND',
                'CHILD_INITIALIZED','ACTIVE'
            )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )


def _append_event(conn: sqlite3.Connection, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
        (event_type, _canonical(payload), utc_now()),
    )


class V2PivotSaga:
    """Authorize and create a budget-bounded child without cross-file atomicity claims."""

    def __init__(
        self, *, repo: str | Path, parent_campaign_id: str,
        state_root: str | Path | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.parent_campaign_id = parent_campaign_id
        self.paths = RuntimePaths.for_repo(
            self.repo, Path(state_root) if state_root is not None else None,
        )
        self.registry = CampaignRegistry(self.repo, self.paths)
        self.fault_hook = fault_hook

    def _checkpoint(self, name: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(name)

    def _parent_location(self) -> tuple[Path, Path]:
        try:
            parent_dir, selected = self.registry.resolve(
                self.parent_campaign_id, activate=False,
            )
        except RegistryError as exc:
            raise PivotSagaError("parent campaign is not registered") from exc
        if selected != self.parent_campaign_id:
            raise PivotSagaError("parent campaign registry identity changed")
        parent_db = parent_dir / "campaign.sqlite3"
        assert_safe_campaign_db(parent_db)
        return parent_dir, parent_db

    def _load_parent_spec(self, parent_db: Path):
        conn = sqlite3.connect(parent_db, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()
            if row is None:
                raise PivotSagaError("parent campaign has no frozen spec")
            spec = _parse_v2_spec(row["spec_json"])
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 5:
                raise PivotSagaError("pivot saga requires the v2 SQLite v5 graph schema")
            return spec
        finally:
            conn.close()

    def _campaign_depth(self, campaign_id: str, spec: Any) -> int:
        depth, seen = 0, {campaign_id}
        lineage = spec.parent
        while lineage is not None:
            parent_id = lineage.parent_campaign_id
            if parent_id in seen:
                raise PivotSagaError("campaign lineage contains a cycle")
            seen.add(parent_id)
            try:
                parent_dir, selected = self.registry.resolve(parent_id, activate=False)
            except RegistryError as exc:
                raise PivotSagaError("campaign lineage parent is missing from the registry") from exc
            if selected != parent_id:
                raise PivotSagaError("campaign lineage identity changed")
            db = parent_dir / "campaign.sqlite3"
            assert_safe_campaign_db(db)
            conn = sqlite3.connect(db, timeout=10)
            try:
                row = conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()
                if row is None:
                    raise PivotSagaError("campaign lineage parent has no frozen spec")
                parent_spec = _parse_v2_spec(row[0])
            finally:
                conn.close()
            depth += 1
            if depth > 32:
                raise PivotSagaError("campaign lineage exceeds the maximum supported depth")
            lineage = parent_spec.parent
        return depth

    def _active_child_count(self, parent_conn: sqlite3.Connection, *, excluding: str) -> int:
        row = parent_conn.execute(
            "SELECT COUNT(*) FROM v2_pivot_sagas "
            "WHERE parent_campaign_id=? AND idempotency_key<>?",
            (self.parent_campaign_id, excluding),
        ).fetchone()
        return int(row[0])

    def _prepare(
        self, *, idempotency_key: str, proposal: PivotProposal | dict[str, Any],
        child_spec: dict[str, Any] | str | ExplorationCampaignSpecV2 | OptimizationCampaignSpecV2,
        approval: PivotDecisionCommand | dict[str, Any] | None,
        parent_conn: sqlite3.Connection,
    ) -> dict[str, Any]:
        if not _ID.fullmatch(idempotency_key):
            raise PivotSagaError("invalid pivot saga idempotency key")
        try:
            proposal = proposal if isinstance(proposal, PivotProposal) else PivotProposal.model_validate(proposal)
        except Exception as exc:
            raise PivotSagaError("pivot proposal is invalid") from exc
        proposal_json = _canonical(proposal.model_dump(mode="json"))
        child_input = child_spec.model_dump(mode="json") if hasattr(child_spec, "model_dump") else child_spec
        child = _parse_v2_spec(child_input)
        if child.parent is not None:
            raise PivotSagaError("pivot child input cannot supply or override parent lineage")
        mode_handoff = self.parent_spec.mode == "EXPLORATION" and child.mode == "OPTIMIZATION"
        if child.mode != self.parent_spec.mode and not mode_handoff:
            raise PivotSagaError("only an approved Exploration-to-Optimization child handoff may change campaign mode")
        if child.source_repo != self.parent_spec.source_repo or child.base_commit != self.parent_spec.base_commit:
            raise PivotSagaError("pivot child must inherit its parent's source repository and base snapshot")
        if child.role_registry_digest != self.parent_spec.role_registry_digest:
            raise PivotSagaError("pivot child must inherit the pinned role registry")
        if child.feature_flags.flag_hash() != self.parent_spec.feature_flags.flag_hash():
            raise PivotSagaError("pivot child must inherit the parent's frozen feature profile")
        if not mode_handoff and child.evaluation != self.parent_spec.evaluation:
            raise PivotSagaError("pivot child cannot change the frozen evaluator binding")
        if mode_handoff and (child.evaluation is None or child.evaluation.approval_digest is None):
            raise PivotSagaError("Optimization child requires a verifier-approved evaluator handoff")
        if not child.autonomy.is_narrower_child_of(self.parent_spec.autonomy):
            raise PivotSagaError("pivot child autonomy exceeds or fails to inherit its parent scope")
        _validate_budget_inheritance(self.parent_spec, child)
        if proposal.spec_hash != self.parent_spec_hash:
            raise PivotSagaError("pivot proposal is stale against the parent approved spec")
        try:
            approval = (
                approval if isinstance(approval, PivotDecisionCommand)
                else PivotDecisionCommand.model_validate(approval) if approval is not None else None
            )
        except Exception as exc:
            raise PivotSagaError("operator pivot decision is invalid") from exc

        scope = self.parent_spec.autonomy
        if scope.mode == "FULL_AUTO":
            if approval is not None:
                raise PivotSagaError("Full Auto pivot cannot be paired with an operator decision")
            decision = scope.authorize_pivot(
                proposal.changed_scope,
                current_child_depth=self.parent_depth,
                current_child_count=self.child_count,
            )
            if decision != "AUTO_ALLOWED":
                raise PivotSagaError("Full Auto pivot is outside its versioned scope or child limits")
            decision_id = f"auto:{proposal.digest}"
            approval_request_id = None
        else:
            if (approval is None or approval.campaign_id != self.parent_campaign_id
                    or approval.proposal_id != proposal.proposal_id
                    or approval.digest != proposal.digest
                    or approval.decision != "approve"
                    or approval.rejection_action is not None):
                raise PivotSagaError("Supervised pivot requires an explicit approval for this proposal digest")
            decision_id = approval.request_id
            approval_request_id = approval.request_id

        allocation_id = "alloc-" + hashlib.sha256(
            f"{self.parent_campaign_id}:{idempotency_key}:{proposal.digest}".encode()
        ).hexdigest()[:40]
        protocol_handoff = None
        if child.mode == "OPTIMIZATION":
            try:
                from .evaluator_verification import EvaluatorProtocolAuthority
                if child.base_commit != self.parent_spec.base_commit:
                    raise PivotSagaError("Optimization handoff source snapshot differs from the parent")
                protocol_handoff = EvaluatorProtocolAuthority(parent_conn).prepare_child_handoff(
                    parent_campaign_id=self.parent_campaign_id,
                    parent_spec_hash=self.parent_spec_hash,
                    allocation_id=allocation_id,
                    protocol_id=child.evaluation.protocol_id,
                    version=child.evaluation.version,
                    protocol_digest=child.evaluation.content_sha256,
                    approval_digest=child.evaluation.approval_digest,
                )
                if protocol_handoff["payload"]["source_snapshot_id"] != child.base_commit:
                    raise PivotSagaError("approved evaluator source snapshot differs from the Optimization child")
            except PivotSagaError:
                raise
            except Exception as exc:
                raise PivotSagaError("Optimization child has no valid parent protocol approval to hand off") from exc
        lineage = ParentCampaignRefV2(
            parent_campaign_id=self.parent_campaign_id,
            allocation_id=allocation_id,
            parent_spec_hash=self.parent_spec_hash,
            pivot_proposal_id=proposal.proposal_id,
            pivot_proposal_digest=proposal.digest,
            pivot_decision_id=decision_id,
            approval_request_id=approval_request_id,
            protocol_handoff_digest=(protocol_handoff["handoff_digest"]
                                     if protocol_handoff is not None else None),
        )
        payload = child.model_dump(mode="json")
        payload["parent"] = lineage.model_dump(mode="json")
        child = _parse_v2_spec(payload)
        caps = _budget_caps(child)
        registry_request_id = "pivot-v2:" + hashlib.sha256(
            f"{self.parent_campaign_id}:{idempotency_key}:{proposal.digest}".encode()
        ).hexdigest()[:48]
        request_hash = _digest({
            "parent_campaign_id": self.parent_campaign_id,
            "parent_spec_hash": self.parent_spec_hash,
            "idempotency_key": idempotency_key,
            "proposal": proposal.model_dump(mode="json"),
            "child_input": child_input,
            "approval": approval.model_dump(mode="json") if approval is not None else None,
            "budget_caps": caps,
        })
        return {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "proposal": proposal,
            "proposal_json": proposal_json,
            "decision_json": _canonical({
                "decision_id": decision_id,
                "approval": approval.model_dump(mode="json") if approval is not None else None,
                "authorization": "FULL_AUTO_SCOPE" if approval is None else "SUPERVISED_APPROVAL",
            }),
            "allocation_id": allocation_id,
            "registry_request_id": registry_request_id,
            "child_spec": child,
            "child_spec_json": _spec_json(child),
            "child_spec_hash": child.approved_hash(),
            "budget_caps": caps,
            "protocol_handoff": protocol_handoff,
        }

    def record_pivot_proposal(
        self, proposal: PivotProposal | dict[str, Any],
    ) -> None:
        """Persist the immutable proposal that an operator may review."""
        parent_dir, parent_db = self._parent_location()
        parent_spec = self._load_parent_spec(parent_db)
        try:
            proposal = proposal if isinstance(proposal, PivotProposal) else PivotProposal.model_validate(proposal)
        except Exception as exc:
            raise PivotSagaError("pivot proposal is invalid") from exc
        if proposal.spec_hash != parent_spec.approved_hash():
            raise PivotSagaError("pivot proposal is stale against the parent approved spec")
        conn = sqlite3.connect(parent_db, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            _create_parent_saga_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            campaign = conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
            if campaign is None or campaign["status"] != "PAUSED_NEEDS_INPUT":
                raise PivotSagaError("v2 pivot proposal requires a parent paused for operator input")
            old = conn.execute(
                "SELECT digest,proposal_json,spec_hash FROM v2_pivot_proposals WHERE proposal_id=?",
                (proposal.proposal_id,),
            ).fetchone()
            encoded = _canonical(proposal.model_dump(mode="json"))
            if old is not None:
                if (old["digest"] != proposal.digest or old["proposal_json"] != encoded
                        or old["spec_hash"] != proposal.spec_hash):
                    raise PivotSagaError("pivot proposal id conflicts with durable proposal content")
                conn.commit()
                return
            now = utc_now()
            conn.execute(
                "INSERT INTO v2_pivot_proposals VALUES(?,?,?,?, 'PENDING',?,?)",
                (proposal.proposal_id, proposal.digest, proposal.spec_hash, encoded, now, now),
            )
            _append_event(conn, "V2_PIVOT_PROPOSAL_RECORDED", {
                "campaign_id": self.parent_campaign_id,
                "proposal_id": proposal.proposal_id,
                "digest": proposal.digest,
                "spec_hash": proposal.spec_hash,
            })
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def record_pivot_decision(
        self, command: PivotDecisionCommand | dict[str, Any],
    ) -> None:
        """Persist a typed operator decision; child creation trusts only this receipt."""
        parent_dir, parent_db = self._parent_location()
        parent_spec = self._load_parent_spec(parent_db)
        if parent_spec.autonomy.mode != "SUPERVISED":
            raise PivotSagaError("operator decisions apply only to Supervised campaigns")
        try:
            command = command if isinstance(command, PivotDecisionCommand) else PivotDecisionCommand.model_validate(command)
        except Exception as exc:
            raise PivotSagaError("operator pivot decision is invalid") from exc
        if command.campaign_id != self.parent_campaign_id:
            raise PivotSagaError("operator pivot decision targets a different parent")
        if command.decision == "approve" and command.rejection_action is not None:
            raise PivotSagaError("approval cannot include a rejection action")
        conn = sqlite3.connect(parent_db, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            _create_parent_saga_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            campaign = conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
            if campaign is None or campaign["status"] != "PAUSED_NEEDS_INPUT":
                raise PivotSagaError("Supervised pivot approval requires a paused parent")
            proposal_row = conn.execute(
                "SELECT * FROM v2_pivot_proposals WHERE proposal_id=?",
                (command.proposal_id,),
            ).fetchone()
            if (proposal_row is None or proposal_row["digest"] != command.digest
                    or proposal_row["spec_hash"] != parent_spec.approved_hash()):
                raise PivotSagaError("operator decision is missing, stale, or bound to another proposal")
            old = conn.execute(
                "SELECT * FROM v2_pivot_decisions WHERE request_id=?",
                (command.request_id,),
            ).fetchone()
            if old is not None:
                if (old["campaign_id"] != command.campaign_id
                        or old["proposal_id"] != command.proposal_id
                        or old["digest"] != command.digest
                        or old["decision"] != command.decision
                        or old["rejection_action"] != command.rejection_action):
                    raise PivotSagaError("pivot decision request id was reused with different content")
                conn.commit()
                return
            if proposal_row["status"] != "PENDING":
                raise PivotSagaError("pivot proposal already has a durable decision")
            status = "APPROVED" if command.decision == "approve" else "REJECTED"
            conn.execute(
                "INSERT INTO v2_pivot_decisions(request_id,campaign_id,proposal_id,digest,decision,rejection_action,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (command.request_id, command.campaign_id, command.proposal_id,
                 command.digest, command.decision, command.rejection_action, status, utc_now()),
            )
            conn.execute(
                "UPDATE v2_pivot_proposals SET status=?,updated_at=? WHERE proposal_id=?",
                (status, utc_now(), command.proposal_id),
            )
            _append_event(conn, "V2_PIVOT_DECISION_RECORDED", {
                "campaign_id": self.parent_campaign_id,
                "request_id": command.request_id,
                "proposal_id": command.proposal_id,
                "digest": command.digest,
                "decision": command.decision,
                "status": status,
            })
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def create_child(
        self, *, idempotency_key: str, proposal: PivotProposal | dict[str, Any],
        child_spec: dict[str, Any] | str | ExplorationCampaignSpecV2 | OptimizationCampaignSpecV2,
        approval: PivotDecisionCommand | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_dir, parent_db = self._parent_location()
        self.parent_spec = self._load_parent_spec(parent_db)
        self.parent_spec_hash = self.parent_spec.approved_hash()
        self.parent_depth = self._campaign_depth(self.parent_campaign_id, self.parent_spec)
        conn = sqlite3.connect(parent_db, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        ledger = self.registry.budget_ledger()
        try:
            _create_parent_saga_schema(conn)
            self.child_count = self._active_child_count(conn, excluding=idempotency_key)
            plan = self._prepare(
                idempotency_key=idempotency_key, proposal=proposal,
                child_spec=child_spec, approval=approval, parent_conn=conn,
            )
            self._record_intent(conn, plan)
            while True:
                row = conn.execute(
                    "SELECT * FROM v2_pivot_sagas WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                state = row["state"]
                if state == "ACTIVE":
                    self._verify_active_saga(
                        conn, row, plan, ledger, require_active_event=True,
                    )
                    return {
                        "parent_campaign_id": self.parent_campaign_id,
                        "child_campaign_id": row["child_id"],
                        "allocation_id": row["allocation_id"],
                        "child_spec_hash": row["child_spec_hash"],
                        "state": "ACTIVE",
                        "replayed": True,
                    }
                if state == "INTENT":
                    ledger.reserve_child(
                        allocation_id=plan["allocation_id"],
                        idempotency_key=plan["registry_request_id"],
                        parent_id=self.parent_campaign_id,
                        spec_hash=plan["child_spec_hash"],
                        caps=plan["budget_caps"],
                    )
                    self._checkpoint("AFTER_LEDGER_RESERVE")
                    self._transition(conn, idempotency_key, "ESCROWED")
                elif state == "ESCROWED":
                    entry, _, _ = self.registry.reserve(
                        plan["child_spec"].goal, plan["registry_request_id"], activate=False,
                    )
                    self._checkpoint("AFTER_REGISTRY_RESERVE")
                    self._transition(conn, idempotency_key, "CHILD_RESERVED", child_id=entry.campaign_id)
                elif state == "CHILD_RESERVED":
                    ledger.bind_child(
                        plan["allocation_id"], row["child_id"], plan["child_spec_hash"],
                    )
                    self._checkpoint("AFTER_ALLOCATION_BIND")
                    self._transition(conn, idempotency_key, "ALLOCATION_BOUND")
                elif state == "ALLOCATION_BOUND":
                    _, child_dir, _ = self.registry.reserve(
                        plan["child_spec"].goal, plan["registry_request_id"], activate=False,
                    )
                    self._initialize_child_db(
                        child_dir / "campaign.sqlite3", plan["child_spec"],
                        parent_conn=conn, child_campaign_id=row["child_id"],
                        protocol_handoff=plan["protocol_handoff"],
                    )
                    self._checkpoint("AFTER_CHILD_INITIALIZE")
                    self._transition(conn, idempotency_key, "CHILD_INITIALIZED")
                elif state == "CHILD_INITIALIZED":
                    self._verify_active_saga(
                        conn, row, plan, ledger, require_active_event=False,
                    )
                    self._transition(conn, idempotency_key, "ACTIVE")
                else:
                    raise PivotSagaError(f"unknown pivot saga state: {state}")
        except (BudgetLedgerError, RegistryError, sqlite3.DatabaseError) as exc:
            raise PivotSagaError(f"pivot child saga stopped safely and can be retried: {exc}") from exc
        finally:
            ledger.close()
            conn.close()

    def _verify_active_saga(
        self, parent_conn: sqlite3.Connection, saga: sqlite3.Row, plan: dict[str, Any],
        ledger: BudgetLedger, *, require_active_event: bool,
    ) -> None:
        """Fail closed unless registry, ledger, child DB, and saga agree exactly."""
        try:
            if (
                saga["state"] not in {"CHILD_INITIALIZED", "ACTIVE"}
                or saga["parent_campaign_id"] != self.parent_campaign_id
                or saga["parent_spec_hash"] != self.parent_spec_hash
                or saga["request_hash"] != plan["request_hash"]
                or saga["allocation_id"] != plan["allocation_id"]
                or saga["child_spec_hash"] != plan["child_spec_hash"]
                or not saga["child_id"]
            ):
                raise PivotSagaError("active pivot saga identity does not match its durable plan")

            mapping = self.registry.lookup_request(
                plan["child_spec"].goal, plan["registry_request_id"],
            )
            if mapping is None:
                raise PivotSagaError("active pivot child registry mapping is missing")
            entry, child_dir = mapping
            if entry.campaign_id != saga["child_id"]:
                raise PivotSagaError("active pivot child registry mapping points to a different child")

            allocation = ledger.conn.execute(
                "SELECT * FROM child_allocations WHERE allocation_id=?",
                (saga["allocation_id"],),
            ).fetchone()
            if (
                allocation is None
                or allocation["state"] not in (
                    {"ACTIVE", "CLOSED"} if require_active_event else {"ACTIVE"}
                )
                or allocation["idempotency_key"] != plan["registry_request_id"]
                or allocation["parent_id"] != self.parent_campaign_id
                or allocation["child_id"] != saga["child_id"]
                or allocation["spec_hash"] != saga["child_spec_hash"]
            ):
                raise PivotSagaError("active pivot child budget allocation is missing or mismatched")

            try:
                expected_caps = json.loads(saga["budget_caps_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise PivotSagaError("active pivot child budget vector is corrupt") from exc
            if (
                not isinstance(expected_caps, dict)
                or any(type(value) is not int or value <= 0 for value in expected_caps.values())
                or expected_caps != plan["budget_caps"]
                or expected_caps != _budget_caps(plan["child_spec"])
            ):
                raise PivotSagaError("active pivot child budget vector differs from its frozen spec")
            allocation_resource_rows = ledger.conn.execute(
                "SELECT resource,cap,reserved,unknown FROM child_allocation_resources "
                "WHERE allocation_id=?",
                (saga["allocation_id"],),
            ).fetchall()
            allocation_caps = {
                item["resource"]: int(item["cap"]) for item in allocation_resource_rows
            }
            child_account_rows = ledger.conn.execute(
                "SELECT resource,cap,reserved,unknown FROM budget_accounts WHERE campaign_id=?",
                (saga["child_id"],),
            ).fetchall()
            child_caps = {item["resource"]: int(item["cap"]) for item in child_account_rows}
            if allocation_caps != expected_caps or child_caps != expected_caps:
                raise PivotSagaError("active pivot child budget accounts do not match the frozen allocation")
            if allocation["state"] == "CLOSED":
                if any(int(item["reserved"]) or int(item["unknown"])
                       for item in allocation_resource_rows + child_account_rows):
                    raise PivotSagaError("closed pivot child allocation still has reserved or UNKNOWN usage")
                unsettled = ledger.conn.execute(
                    "SELECT 1 FROM budget_reservations WHERE allocation_id=? "
                    "AND state IN ('RESERVED','DISPATCHED','UNKNOWN') LIMIT 1",
                    (saga["allocation_id"],),
                ).fetchone()
                nested = ledger.conn.execute(
                    "SELECT 1 FROM child_allocations WHERE parent_id=? "
                    "AND state IN ('RESERVED','ACTIVE') LIMIT 1",
                    (saga["child_id"],),
                ).fetchone()
                if unsettled or nested:
                    raise PivotSagaError("closed pivot child allocation has unreconciled work")

            child_db = child_dir / "campaign.sqlite3"
            assert_safe_campaign_db(child_db)
            if not child_db.is_file():
                raise PivotSagaError("active pivot child database is missing")
            child_conn = sqlite3.connect(child_db, timeout=10)
            child_conn.row_factory = sqlite3.Row
            try:
                if int(child_conn.execute("PRAGMA user_version").fetchone()[0]) != 5:
                    raise PivotSagaError("active pivot child database has an incompatible schema")
                campaign_columns = {
                    item["name"] for item in child_conn.execute("PRAGMA table_info(campaigns)")
                }
                if not {"id", "spec_json", "spec_hash", "status"} <= campaign_columns:
                    raise PivotSagaError("active pivot child database has no frozen spec identity")
                campaign = child_conn.execute(
                    "SELECT spec_json,spec_hash,status FROM campaigns WHERE id=1",
                ).fetchone()
                if campaign is None:
                    raise PivotSagaError("active pivot child database has no campaign spec")
                child = _parse_v2_spec(campaign["spec_json"])
                if (allocation["state"] == "CLOSED"
                        and campaign["status"] not in {"GOAL_MET", "STOPPED"}):
                    raise PivotSagaError("closed pivot child allocation has no matching terminal child status")
                if (
                    campaign["spec_json"] != saga["child_spec_json"]
                    or _spec_json(child) != saga["child_spec_json"]
                    or _spec_json(child) != plan["child_spec_json"]
                    or campaign["spec_hash"] != saga["child_spec_hash"]
                    or child.approved_hash() != saga["child_spec_hash"]
                    or child.parent is None
                    or child.parent.model_dump(mode="json")
                    != plan["child_spec"].parent.model_dump(mode="json")
                ):
                    raise PivotSagaError("active pivot child spec or lineage differs from the durable saga")
                if isinstance(child, OptimizationCampaignSpecV2):
                    try:
                        from .evaluator_verification import EvaluatorProtocolAuthority
                        EvaluatorProtocolAuthority(child_conn).require_frozen_reference(
                            child.evaluation.protocol_id, child.evaluation.version,
                            child.evaluation.content_sha256, child.evaluation.approval_digest,
                            campaign_id=saga["child_id"], spec_hash=saga["child_spec_hash"],
                        )
                    except Exception as exc:
                        raise PivotSagaError("active Optimization child lacks its exact durable protocol handoff") from exc
            finally:
                child_conn.close()

            active_events = []
            for event in parent_conn.execute(
                "SELECT payload_json FROM events WHERE event_type='PIVOT_CHILD_ACTIVE'",
            ):
                try:
                    payload = json.loads(event["payload_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise PivotSagaError("pivot active event log is corrupt") from exc
                if payload.get("allocation_id") == saga["allocation_id"]:
                    active_events.append(payload)
            if require_active_event:
                if (
                    len(active_events) != 1
                    or active_events[0].get("campaign_id") != self.parent_campaign_id
                    or active_events[0].get("child_id") != saga["child_id"]
                    or active_events[0].get("child_spec_hash") != saga["child_spec_hash"]
                ):
                    raise PivotSagaError("active pivot saga has no unique matching ACTIVE event")
            elif active_events:
                raise PivotSagaError("pivot saga has an ACTIVE event before its durable state transition")
        except PivotSagaError:
            raise
        except Exception as exc:
            raise PivotSagaError("active pivot child could not be verified across durable stores") from exc

    def _record_intent(self, conn: sqlite3.Connection, plan: dict[str, Any]) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT request_hash FROM v2_pivot_sagas WHERE idempotency_key=?",
                (plan["idempotency_key"],),
            ).fetchone()
            if old is not None:
                if old["request_hash"] != plan["request_hash"]:
                    raise PivotSagaError("pivot saga idempotency key was reused with different content")
                conn.commit()
                return
            # Serialize capacity authorization with intent persistence. This prevents
            # concurrent requests from both observing the same final child slot.
            current_child_count = self._active_child_count(
                conn, excluding=plan["idempotency_key"],
            )
            scope = self.parent_spec.autonomy
            materialized_scope = scope.scope
            if materialized_scope is not None and (
                    self.parent_depth >= materialized_scope.max_child_depth
                    or current_child_count >= materialized_scope.max_child_campaigns):
                raise PivotSagaError("parent autonomy scope has no remaining child capacity")
            if scope.mode == "FULL_AUTO":
                authorization = scope.authorize_pivot(
                    plan["proposal"].changed_scope,
                    current_child_depth=self.parent_depth,
                    current_child_count=current_child_count,
                )
                if authorization != "AUTO_ALLOWED":
                    raise PivotSagaError("Full Auto pivot capacity changed before intent persistence")
            decision = json.loads(plan["decision_json"])
            approval = decision.get("approval")
            if approval is not None:
                campaign = conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
                if campaign is None or campaign["status"] != "PAUSED_NEEDS_INPUT":
                    raise PivotSagaError("Supervised pivot approval is stale because the parent is not paused")
                proposal_row = conn.execute(
                    "SELECT * FROM v2_pivot_proposals WHERE proposal_id=?",
                    (plan["proposal"].proposal_id,),
                ).fetchone()
                decision_row = conn.execute(
                    "SELECT * FROM v2_pivot_decisions WHERE request_id=?",
                    (approval["request_id"],),
                ).fetchone()
                if (proposal_row is None or proposal_row["status"] != "APPROVED"
                        or proposal_row["digest"] != plan["proposal"].digest
                        or proposal_row["spec_hash"] != self.parent_spec_hash
                        or decision_row is None or decision_row["campaign_id"] != self.parent_campaign_id
                        or decision_row["proposal_id"] != plan["proposal"].proposal_id
                        or decision_row["digest"] != plan["proposal"].digest
                        or decision_row["decision"] != "approve"
                        or decision_row["status"] != "APPROVED"):
                    raise PivotSagaError("Supervised pivot has no matching durable approval receipt")
            now = utc_now()
            conn.execute(
                """INSERT INTO v2_pivot_sagas(
                    idempotency_key,request_hash,parent_campaign_id,parent_spec_hash,
                    proposal_json,decision_json,child_id,allocation_id,child_spec_json,
                    child_spec_hash,budget_caps_json,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,NULL,?,?,?,?, 'INTENT',?,?)""",
                (
                    plan["idempotency_key"], plan["request_hash"], self.parent_campaign_id,
                    self.parent_spec_hash, plan["proposal_json"], plan["decision_json"],
                    plan["allocation_id"], plan["child_spec_json"], plan["child_spec_hash"],
                    _canonical(plan["budget_caps"]), now, now,
                ),
            )
            _append_event(conn, "PIVOT_CHILD_INTENT", {
                "campaign_id": self.parent_campaign_id,
                "proposal_id": plan["proposal"].proposal_id,
                "proposal_digest": plan["proposal"].digest,
                "decision": json.loads(plan["decision_json"]),
                "allocation_id": plan["allocation_id"],
                "child_spec_hash": plan["child_spec_hash"],
            })
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _transition(
        self, conn: sqlite3.Connection, idempotency_key: str, new_state: str,
        *, child_id: str | None = None,
    ) -> None:
        if new_state not in _STATES:
            raise PivotSagaError("invalid saga state transition")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM v2_pivot_sagas WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise PivotSagaError("pivot saga intent disappeared")
            if child_id is not None and row["child_id"] not in (None, child_id):
                raise PivotSagaError("registry child identity differs from the stored saga")
            current_index, next_index = _STATES.index(row["state"]), _STATES.index(new_state)
            if next_index <= current_index:
                conn.commit()
                return
            if next_index != current_index + 1:
                raise PivotSagaError("pivot saga cannot skip a recovery state")
            now = utc_now()
            conn.execute(
                "UPDATE v2_pivot_sagas SET state=?,child_id=COALESCE(child_id,?),updated_at=? "
                "WHERE idempotency_key=?",
                (new_state, child_id, now, idempotency_key),
            )
            _append_event(conn, f"PIVOT_CHILD_{new_state}", {
                "campaign_id": self.parent_campaign_id,
                "child_id": child_id or row["child_id"],
                "allocation_id": row["allocation_id"],
                "child_spec_hash": row["child_spec_hash"],
            })
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

    @staticmethod
    def _initialize_child_db(
        db_path: Path, child_spec: Any, *, parent_conn: sqlite3.Connection | None = None,
        child_campaign_id: str | None = None,
        protocol_handoff: dict[str, Any] | None = None,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        assert_safe_campaign_db(db_path)
        serialized = _spec_json(child_spec)
        expected_hash = child_spec.approved_hash()
        if db_path.exists() and db_path.stat().st_size:
            conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
            try:
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version == 5:
                    row = conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()
                    if row is None or _parse_v2_spec(row[0]).approved_hash() != expected_hash:
                        raise PivotSagaError("existing child DB belongs to a different immutable spec")
                    if protocol_handoff is not None:
                        try:
                            from .evaluator_verification import EvaluatorProtocolAuthority
                            authority = EvaluatorProtocolAuthority(conn)
                            authority.require_frozen_reference(
                                child_spec.evaluation.protocol_id, child_spec.evaluation.version,
                                child_spec.evaluation.content_sha256, child_spec.evaluation.approval_digest,
                                campaign_id=child_campaign_id, spec_hash=expected_hash,
                            )
                        except Exception as exc:
                            raise PivotSagaError("existing child DB lacks the exact approved protocol handoff") from exc
                    return
                existing_tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if version != 0 or existing_tables:
                    raise PivotSagaError("existing child DB is partial or uses an incompatible schema")
            finally:
                conn.close()
        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """CREATE TABLE events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            create_v5_graph_schema(conn)
            conn.execute(
                """CREATE TABLE campaigns(
                    id INTEGER PRIMARY KEY,
                    spec_json TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            created_at = utc_now()
            conn.execute(
                "INSERT INTO campaigns(id,spec_json,spec_hash,status,created_at,updated_at) "
                "VALUES(1,?,?, 'READY',?,?)",
                (serialized, expected_hash, created_at, created_at),
            )
            conn.execute("PRAGMA user_version=5")
            if protocol_handoff is not None:
                try:
                    from .evaluator_verification import EvaluatorProtocolAuthority
                    if child_campaign_id is None:
                        raise PivotSagaError("Optimization child handoff requires its reserved campaign id")
                    if parent_conn is None:
                        raise PivotSagaError("Optimization child protocol handoff requires the parent authority")
                    source_authority = EvaluatorProtocolAuthority(parent_conn)
                    reference = child_spec.evaluation
                    verified_handoff = source_authority.prepare_child_handoff(
                        parent_campaign_id=child_spec.parent.parent_campaign_id,
                        parent_spec_hash=child_spec.parent.parent_spec_hash,
                        allocation_id=child_spec.parent.allocation_id,
                        protocol_id=reference.protocol_id, version=reference.version,
                        protocol_digest=reference.content_sha256,
                        approval_digest=reference.approval_digest,
                    )
                    if verified_handoff["handoff_digest"] != protocol_handoff.get("handoff_digest"):
                        raise PivotSagaError("parent protocol approval changed before child initialization")
                    authority = EvaluatorProtocolAuthority(conn)
                    authority.install_child_handoff(
                        verified_handoff, child_campaign_id=child_campaign_id,
                        child_spec=child_spec,
                    )
                except PivotSagaError:
                    raise
                except Exception as exc:
                    raise PivotSagaError("could not persist the child-bound protocol handoff") from exc
            _append_event(conn, "CAMPAIGN_CREATED_FROM_PIVOT", {
                "parent_spec_hash": child_spec.parent.parent_spec_hash,
                "proposal_id": child_spec.parent.pivot_proposal_id,
                "proposal_digest": child_spec.parent.pivot_proposal_digest,
                "allocation_id": child_spec.parent.allocation_id,
                "spec_hash": expected_hash,
            })
            conn.execute("PRAGMA user_version=5")
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
