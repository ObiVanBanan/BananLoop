"""Shared application service used by the CLI and the local REST API."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .contracts import PivotDecisionCommand, PivotProposal, RuntimePaths, parse_legacy_campaign_spec, utc_now
from .graph_store import GraphRepository, GraphStoreError, MutateGraph, V2CampaignStore
from .operator_control import OperatorCommand, submit_control, submit_pivot_decision
from .operator_snapshot import read_events_after, read_snapshot
from .registry import CampaignRegistry, RegistryError, assert_safe_campaign_db, assert_safe_local_path
from .report import build_report
from .store import CampaignStore, StoreError, campaign_db_initialized
from .scheduler import HybridScheduler
from .v2_pivot_saga import V2PivotSaga


class CampaignApplication:
    """Campaign-scoped use cases with one persistence path for every interface."""

    def __init__(
        self, repo: str | Path, *, campaign_id: str | None = None,
        state_dir: str | Path | None = None,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.requested_campaign_id = campaign_id
        self.state_dir = Path(state_dir).resolve() if state_dir is not None else None

    def resolve(self) -> tuple[RuntimePaths, str]:
        if self.state_dir is not None:
            if self.requested_campaign_id is not None:
                raise RegistryError("state-dir and campaign-id cannot be used together")
            paths = RuntimePaths.for_repo(self.repo, self.state_dir)
            campaign_id = "state-" + hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:32]
        else:
            state_root, selected = CampaignRegistry(self.repo).resolve(
                self.requested_campaign_id, activate=False,
            )
            paths = RuntimePaths.for_repo(self.repo, state_root)
            campaign_id = selected or "state-" + hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:32]
        assert_safe_campaign_db(paths.db)
        return paths, campaign_id

    def resolve_readonly(self) -> tuple[RuntimePaths, str]:
        """Resolve project state without registry bootstrap or active-campaign writes."""
        if self.state_dir is not None:
            if self.requested_campaign_id is not None:
                raise RegistryError("state-dir and campaign-id cannot be used together")
            paths = RuntimePaths.for_repo(self.repo, self.state_dir)
            campaign_id = "state-" + hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:32]
        else:
            state_root, selected = CampaignRegistry(self.repo).resolve_readonly(self.requested_campaign_id)
            paths = RuntimePaths.for_repo(self.repo, state_root)
            campaign_id = selected or "state-" + hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:32]
        assert_safe_campaign_db(paths.db)
        return paths, campaign_id

    @staticmethod
    def _schema_version(path: Path) -> int:
        if not path.exists():
            return 0
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()

    @staticmethod
    def _encode_campaign_cursor(created_at: str, campaign_id: str) -> str:
        raw = json.dumps(
            {"v": 1, "created_at": created_at, "campaign_id": campaign_id},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_campaign_cursor(cursor: str) -> tuple[str, str]:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise ValueError("invalid campaign cursor")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            document = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid campaign cursor") from exc
        if (not isinstance(document, dict)
                or set(document) != {"v", "created_at", "campaign_id"}
                or isinstance(document["v"], bool)
                or document["v"] != 1
                or not isinstance(document["created_at"], str)
                or len(document["created_at"]) > 64
                or not isinstance(document["campaign_id"], str)
                or len(document["campaign_id"]) > 100):
            raise ValueError("invalid campaign cursor")
        if document["campaign_id"] != "legacy" and not re.fullmatch(
            r"c-[0-9a-f]{32}", document["campaign_id"]
        ):
            raise ValueError("invalid campaign cursor")
        return document["created_at"], document["campaign_id"]

    def list_campaigns(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        """Return a bounded safe project campaign index without selecting a campaign."""
        if self.state_dir is not None or self.requested_campaign_id is not None:
            raise ValueError("campaign discovery requires a project-scoped application")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("campaign page limit must be between 1 and 100")
        after = self._decode_campaign_cursor(cursor) if cursor is not None else None
        registry = CampaignRegistry(self.repo)
        assert_safe_local_path(registry.root, "project state root")
        assert_safe_local_path(registry.path, "campaign registry")
        entries, has_more = registry.list_page(limit=limit, after=after)
        campaigns = []
        for entry, active in entries:
            if entry.origin == "managed":
                if (not re.fullmatch(r"c-[0-9a-f]{32}", entry.campaign_id)
                        or entry.state_path != f"campaigns/{entry.campaign_id}"):
                    raise RegistryError("campaign registry identity is not canonical")
            elif entry.origin != "legacy" or entry.campaign_id != "legacy" or entry.state_path != ".":
                raise RegistryError("campaign registry identity is not canonical")
            state_root = registry._validate_state_path(entry.state_path, entry.origin)
            paths = RuntimePaths.for_repo(self.repo, state_root)
            assert_safe_campaign_db(paths.db)
            status = "RESERVED"
            updated_at = entry.updated_at
            schema_version = None
            mode = None
            profile = None
            flag_hash = None
            approved_spec_hash = None
            integrity_valid = None
            if campaign_db_initialized(paths.db):
                schema = self._schema_version(paths.db)
                uri = paths.db.resolve().as_uri() + "?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT status,spec_json,spec_hash,updated_at FROM campaigns WHERE id=1"
                    ).fetchone()
                    if row is None:
                        raise StoreError("campaign is not initialized")
                    status = str(row["status"])
                    updated_at = str(row["updated_at"])
                    if schema == 5:
                        graph = V2CampaignStore(conn)
                        spec = graph.campaign_spec()
                        schema_version = int(getattr(spec, "schema_version", 1))
                    else:
                        spec = parse_legacy_campaign_spec(row["spec_json"])
                        schema_version = 1
                    approved_spec_hash = str(row["spec_hash"])
                    integrity_valid = spec.approved_hash() == approved_spec_hash
                    if not integrity_valid:
                        status = "DATA_INTEGRITY_ERROR"
                    if schema_version == 2 and integrity_valid:
                        mode = str(spec.mode)
                        profile = str(spec.feature_flags.profile)
                        flag_hash = spec.feature_flags.flag_hash()
                    elif schema_version == 1 and integrity_valid:
                        mode = "LEGACY"
                except (ValueError, TypeError, sqlite3.DatabaseError, GraphStoreError) as exc:
                    raise StoreError("campaign summary could not be read safely") from exc
                finally:
                    conn.close()
            campaigns.append({
                "campaign_id": entry.campaign_id,
                "origin": entry.origin,
                "active": active,
                "status": status,
                "schema_version": schema_version,
                "mode": mode,
                "profile": profile,
                "flag_hash": flag_hash,
                "approved_spec_hash": approved_spec_hash,
                "integrity_valid": integrity_valid,
                "updated_at": updated_at,
            })
        next_cursor = None
        if has_more and entries:
            last = entries[-1][0]
            next_cursor = self._encode_campaign_cursor(last.created_at, last.campaign_id)
        return {
            "campaigns": campaigns,
            "next_cursor": next_cursor,
            "as_of": utc_now(),
            "source": "PERSISTED",
        }

    def research_scopes(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        """List bounded PUBLIC_DEV knowledge scopes from this campaign only."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("research scope page limit must be between 1 and 50")
        after = None
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
                raise ValueError("invalid research cursor")
            try:
                raw = cursor + "=" * (-len(cursor) % 4)
                value = json.loads(base64.b64decode(raw, altchars=b"-_", validate=True))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid research cursor") from exc
            if (not isinstance(value, dict) or set(value) != {"v", "snapshot", "protocol"}
                    or isinstance(value["v"], bool) or value["v"] != 1
                    or not isinstance(value["snapshot"], str)
                    or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value["snapshot"])
                    or not isinstance(value["protocol"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", value["protocol"])):
                raise ValueError("invalid research cursor")
            after = (value["snapshot"], value["protocol"])
        paths, campaign_id = self.resolve_readonly()
        if self._schema_version(paths.db) != 5:
            raise StoreError("research knowledge requires a v2 campaign")
        from .research_ledger import ResearchLedger

        uri = paths.db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            ledger = ResearchLedger(conn, read_only=True)
            scopes, more = ledger.list_scopes(campaign_id=campaign_id, limit=limit, after=after)
        finally:
            conn.close()
        next_cursor = None
        if more and scopes:
            last = scopes[-1]
            raw = json.dumps(
                {"v": 1, "snapshot": last["source_snapshot_id"],
                 "protocol": last["protocol_digest"]}, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            next_cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return {"campaign_id": campaign_id, "scopes": scopes, "next_cursor": next_cursor,
                "as_of": utc_now(), "source": "PERSISTED"}

    def export_research_knowledge(
        self, *, source_snapshot_id: str, protocol_digest: str, max_records: int = 200,
    ) -> dict:
        """Export a size-bounded, redacted view of the canonical PUBLIC_DEV projection."""
        paths, campaign_id = self.resolve_readonly()
        if self._schema_version(paths.db) != 5:
            raise StoreError("research knowledge requires a v2 campaign")
        from .research_ledger import ResearchLedger, ResearchLedgerError

        uri = paths.db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            ledger = ResearchLedger(conn, read_only=True)
            if not ledger._available:
                raise StoreError("research ledger is unavailable")
            count = conn.execute(
                "SELECT COUNT(*) FROM research_evidence WHERE campaign_id=? "
                "AND source_snapshot_id=? AND protocol_digest=? AND classification='PUBLIC_DEV'",
                (campaign_id, source_snapshot_id, protocol_digest),
            ).fetchone()[0]
            if count == 0:
                raise ValueError("public knowledge scope has no persisted evidence")
            if count > max_records:
                raise OverflowError("knowledge scope exceeds the bounded export size")
            queue = CampaignRegistry(self.repo).worker_queue()
            try:
                # This handle must remain read-only: binding a campaign ID
                # would write the graph identity marker on an export path.
                graph = GraphRepository(conn)
                package = ledger.export_knowledge(
                    campaign_ids=(campaign_id,), source_snapshot_id=source_snapshot_id,
                    protocol_digest=protocol_digest, graph=graph, queue=queue,
                )
            finally:
                queue.close()
            projection = package.projection.model_dump(mode="json")
        except ResearchLedgerError as exc:
            raise ValueError("invalid research knowledge scope") from exc
        finally:
            conn.close()

        safe_evidence = []
        for item in projection["evidence"]:
            safe_evidence.append({
                "evidence_id": item["evidence_id"], "campaign_id": item["campaign_id"],
                "node_id": item["node_id"], "node_revision": item["node_revision"],
                "source_snapshot_id": item["source_snapshot_id"],
                "protocol_digest": item["protocol_digest"], "event_cursor": item["event_cursor"],
                "claim_key": item["claim_key"], "statement": item["statement"],
                "stance": item["stance"], "kind": item["kind"],
                "evidence_refs": ["evidence:sha256:" + hashlib.sha256(ref.encode()).hexdigest()
                                  for ref in item["evidence_refs"]],
                "confidence": item["confidence"], "intervention_type": item["intervention_type"],
                "algorithmic_layer": item["algorithmic_layer"],
                "classification": "PUBLIC_DEV",
                "verification_status": "UNVERIFIED_ROLE_FINDING",
                "causal_status": item["causal_status"],
            })
        return {
            "campaign_id": campaign_id, "source_snapshot_id": source_snapshot_id,
            "protocol_digest": protocol_digest,
            "canonical_projection_digest": projection["projection_digest"],
            "evidence": safe_evidence, "contradictions": projection["contradictions"],
            "causal_memory": projection["causal_memory"],
            "structural_memory": [{key: value for key, value in item.items() if key != "affected_paths"}
                                  for item in projection["structural_memory"]],
            "source": "PERSISTED", "as_of": utc_now(),
        }

    def observability(self) -> dict:
        """Return bounded campaign-scoped queue and budget records from read-only DB handles."""
        paths, campaign_id = self.resolve_readonly()
        captured_at = utc_now()
        event_cursor = None
        assert_safe_campaign_db(paths.db)
        if paths.db.exists():
            campaign_uri = paths.db.resolve().as_uri() + "?mode=ro"
            campaign_conn = sqlite3.connect(campaign_uri, uri=True, timeout=5)
            try:
                campaign_tables = {row[0] for row in campaign_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "events" in campaign_tables:
                    event_cursor = int(campaign_conn.execute(
                        "SELECT COALESCE(MAX(seq),0) FROM events"
                    ).fetchone()[0])
            finally:
                campaign_conn.close()

        registry = CampaignRegistry(self.repo)
        queue_result = {
            "available": False, "states": None, "leases": None, "transitions": None,
            "source": "PERSISTED", "as_of": captured_at, "event_cursor": event_cursor,
            "worker_event_cursor": None,
        }
        budget_result = {
            "available": False, "dimensions": None, "source": "PERSISTED",
            "as_of": captured_at, "event_cursor": event_cursor,
        }
        scheduler_result = {
            "available": False, "reason": None, "candidate_count": None,
            "blocked_node_count": None, "source": "PERSISTED",
            "as_of": captured_at, "event_cursor": event_cursor,
            "worker_event_cursor": None,
        }
        assert_safe_local_path(registry.root, "project state root")
        assert_safe_local_path(registry.path, "campaign registry")
        if registry.path.exists():
            uri = registry.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("BEGIN")
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "worker_jobs" in tables:
                    states = {name: 0 for name in (
                        "READY", "LEASED", "DISPATCHED", "UNKNOWN", "COMPLETED", "CANCELLED",
                    )}
                    for row in conn.execute(
                        "SELECT state,COUNT(*) AS amount FROM worker_jobs "
                        "WHERE campaign_id=? GROUP BY state", (campaign_id,),
                    ):
                        if row["state"] in states:
                            states[row["state"]] = int(row["amount"])
                    now = time.time()
                    lease_summary = conn.execute(
                        "SELECT COALESCE(SUM(CASE WHEN lease_until>? THEN 1 ELSE 0 END),0) AS active, "
                        "COALESCE(SUM(CASE WHEN lease_until IS NULL OR lease_until<=? THEN 1 ELSE 0 END),0) AS expired "
                        "FROM worker_jobs WHERE campaign_id=? AND state IN ('LEASED','DISPATCHED')",
                        (now, now, campaign_id),
                    ).fetchone()
                    max_fence = int(conn.execute(
                        "SELECT COALESCE(MAX(fence),0) FROM worker_jobs WHERE campaign_id=?",
                        (campaign_id,),
                    ).fetchone()[0])
                    transitions = {name: 0 for name in (
                        "JOB_ENQUEUED", "JOB_LEASED", "JOB_DISPATCHED", "JOB_COMPLETED",
                        "JOB_CANCELLED", "JOB_UNKNOWN", "LEASE_EXPIRED_PRE_DISPATCH",
                        "UNKNOWN_RECONCILED", "LEASE_RENEWED",
                    )} if "worker_events" in tables else None
                    if "worker_events" in tables:
                        latest_event = int(conn.execute(
                            "SELECT COALESCE(MAX(e.event_id),0) FROM worker_events e "
                            "JOIN worker_jobs j ON j.job_id=e.job_id WHERE j.campaign_id=?",
                            (campaign_id,),
                        ).fetchone()[0])
                        allowed_types = tuple(transitions or ())
                        type_marks = ",".join("?" for _ in allowed_types)
                        for row in conn.execute(
                            "SELECT event_type,COUNT(*) AS amount FROM ("
                            "SELECT event_type FROM ("
                            "SELECT e.event_id,e.event_type FROM worker_events e "
                            "JOIN worker_jobs j ON j.job_id=e.job_id WHERE j.campaign_id=? "
                            "ORDER BY e.event_id DESC LIMIT 10000) "
                            f"WHERE event_type IN ({type_marks})) GROUP BY event_type",
                            (campaign_id, *allowed_types),
                        ):
                            if transitions is not None and row["event_type"] in transitions:
                                transitions[row["event_type"]] = int(row["amount"])
                    queue_result.update({
                        "available": True, "states": states,
                        "leases": {
                            "active": int(lease_summary["active"]),
                            "expired": int(lease_summary["expired"]),
                            "max_fence": max_fence,
                        },
                        "transitions": transitions,
                        "transition_window": "latest 10000 campaign events",
                        "worker_event_cursor": latest_event if "worker_events" in tables else None,
                    })
                if "budget_accounts" in tables:
                    rows = conn.execute(
                        "SELECT resource,cap,spent,reserved,unknown,revision FROM budget_accounts "
                        "WHERE campaign_id=? ORDER BY resource LIMIT 51", (campaign_id,),
                    ).fetchall()
                    if rows and len(rows) <= 50:
                        dimensions = {}
                        for row in rows:
                            remaining = int(row["cap"]) - int(row["spent"]) - int(row["reserved"]) - int(row["unknown"])
                            dimensions[row["resource"]] = {
                                "cap": int(row["cap"]), "spent": int(row["spent"]),
                                "reserved": int(row["reserved"]), "unknown": int(row["unknown"]),
                                "available": max(0, remaining), "revision": int(row["revision"]),
                            }
                        budget_result.update({"available": True, "dimensions": dimensions})
            finally:
                conn.close()
        if paths.db.exists() and self._schema_version(paths.db) == 5 and budget_result["available"]:
            graph_uri = paths.db.resolve().as_uri() + "?mode=ro"
            graph_conn = sqlite3.connect(graph_uri, uri=True, timeout=5, isolation_level=None)
            try:
                graph = GraphRepository(graph_conn)
                active_node_ids, worker_event_cursor = self._active_scheduler_nodes(registry.path, campaign_id)
                if worker_event_cursor is None:
                    raise ValueError("worker event authority is unavailable")
                scheduling_snapshot = graph.scheduling_snapshot()
                resource_by_phase = {
                    "EXPLORATION": "agent_calls",
                    "METRIC_DISCOVERY": "agent_calls",
                    "OPTIMIZATION": "experiments",
                    "VERIFICATION": "evaluator_calls",
                }
                required_resources = {
                    resource_by_phase[node["phase"]]
                    for node in scheduling_snapshot["nodes"]
                    if node["ready"] and node["node_id"] not in active_node_ids
                    and node["phase"] in resource_by_phase
                }
                if not required_resources.issubset(budget_result["dimensions"]):
                    raise ValueError("scheduler resource snapshot is incomplete")
                plan = HybridScheduler(graph).plan(
                    budget=budget_result["dimensions"],
                    active_node_ids=active_node_ids,
                )
                scheduler_result.update({
                    "available": True, "reason": plan.reason,
                    "candidate_count": len(plan.candidates),
                    "blocked_node_count": len(plan.blocked_nodes),
                    "worker_event_cursor": worker_event_cursor,
                })
            except (GraphStoreError, sqlite3.Error, ValueError):
                # Incomplete or over-bound source data is unavailable, not an
                # empty queue and not a reason inferred from partial records.
                pass
            finally:
                graph_conn.close()
        return {
            "campaign_id": campaign_id, "queue": queue_result, "budget": budget_result,
            "scheduler": scheduler_result,
            "source": "PERSISTED", "as_of": captured_at, "event_cursor": event_cursor,
        }

    @staticmethod
    def _active_scheduler_nodes(registry_path: Path, campaign_id: str) -> tuple[set[str], int | None]:
        """Read bounded active worker node IDs/cursor without exposing request data."""
        assert_safe_local_path(registry_path, "campaign registry")
        if not registry_path.exists():
            return set(), None
        uri = registry_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            conn.execute("BEGIN")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "worker_jobs" not in tables:
                return set(), None
            worker_event_cursor = None
            if "worker_events" in tables:
                worker_event_cursor = int(conn.execute(
                    "SELECT COALESCE(MAX(e.event_id),0) FROM worker_events e "
                    "JOIN worker_jobs j ON j.job_id=e.job_id WHERE j.campaign_id=?",
                    (campaign_id,),
                ).fetchone()[0])
            sizes = [int(row[0]) for row in conn.execute(
                "SELECT length(CAST(request_json AS BLOB)) FROM worker_jobs WHERE campaign_id=? "
                "AND state IN ('LEASED','DISPATCHED') ORDER BY updated_at DESC,job_id LIMIT 10001",
                (campaign_id,),
            )]
            if len(sizes) > 10000:
                raise ValueError("active worker observation exceeds bounded window")
            if any(size > 65536 for size in sizes) or sum(sizes) > 1048576:
                raise ValueError("active worker requests exceed bounded read size")
            rows = conn.execute(
                "SELECT substr(json_extract(request_json,'$.node_id'),1,201) "
                "FROM worker_jobs WHERE campaign_id=? AND state IN ('LEASED','DISPATCHED') "
                "ORDER BY updated_at DESC,job_id LIMIT 10000",
                (campaign_id,),
            ).fetchall()
            result: set[str] = set()
            for (node_id,) in rows:
                if isinstance(node_id, str) and node_id:
                    if len(node_id) > 200:
                        raise ValueError("active worker node reference exceeds bounded length")
                    result.add(node_id)
            return result, worker_event_cursor
        finally:
            conn.rollback()
            conn.close()

    def lifecycle_projection(self) -> dict:
        """Return bounded, read-only evaluator verification and pivot handoff receipts."""
        paths, campaign_id = self.resolve_readonly()
        captured_at = utc_now()
        if self._schema_version(paths.db) != 5:
            return {
                "campaign_id": campaign_id, "available": False, "proposals": [],
                "decisions": [], "children": [], "verifier_receipts": [],
                "integration_receipts": [], "protocol_handoffs": [], "phase_state": None,
                "evaluator_run_receipt_count": None, "evaluator_state": None,
                "source": "PERSISTED", "as_of": captured_at, "event_cursor": None,
            }
        uri = paths.db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            campaign_row = conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()
            current_spec_hash = str(campaign_row["spec_hash"]) if campaign_row else ""
            event_cursor = int(conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events"
            ).fetchone()[0])
            proposals: list[dict] = []
            decisions: list[dict] = []
            children: list[dict] = []
            verifier_receipts: list[dict] = []
            integration_receipts: list[dict] = []
            protocol_handoffs: list[dict] = []
            phase_state: dict[str, dict[str, int]] | None = None
            evaluator_run_receipt_count: int | None = None
            evaluator_state: dict[str, dict[str, int] | int] | None = None
            truncated: dict[str, bool] = {}
            if "research_nodes" in tables:
                allowed_phases = (
                    "EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION",
                )
                allowed_statuses = ("PENDING", "READY", "PAUSED", "COMPLETED", "FAILED", "CANCELLED")
                phase_state = {phase: {status: 0 for status in allowed_statuses} for phase in allowed_phases}
                for row in conn.execute(
                    "SELECT phase,status,COUNT(*) AS amount FROM research_nodes "
                    "WHERE phase IN (?,?,?,?,?) AND status IN (?,?,?,?,?,?) GROUP BY phase,status",
                    (*allowed_phases, *allowed_statuses),
                ):
                    phase_state[row["phase"]][row["status"]] = int(row["amount"])
            if "v2_pivot_proposals" in tables:
                rows = conn.execute(
                    "SELECT proposal_id,digest,spec_hash,proposal_json,status,created_at,updated_at "
                    "FROM v2_pivot_proposals ORDER BY created_at,proposal_id LIMIT 101"
                ).fetchall()
                truncated["proposals"] = len(rows) > 100
                for row in rows[:100]:
                    try:
                        proposal = PivotProposal.model_validate(json.loads(row["proposal_json"]))
                        if proposal.proposal_id != row["proposal_id"] or proposal.digest != row["digest"]:
                            raise ValueError("pivot identity mismatch")
                        proposals.append({
                            "proposal_id": row["proposal_id"], "digest": row["digest"],
                            "status": "STALE" if row["spec_hash"] != current_spec_hash else row["status"],
                            "trigger_experiment_ordinal": proposal.trigger_experiment_ordinal,
                            "current_architecture": proposal.current_architecture,
                            "proposed_architecture": proposal.proposed_architecture,
                            "rationale": proposal.rationale, "expected_effect": proposal.expected_effect,
                            "changed_scope_count": len(proposal.changed_scope),
                            "resources": {
                                "additional_agent_calls": proposal.resources.additional_agent_calls,
                                "additional_evaluator_calls": proposal.resources.additional_evaluator_calls,
                                "additional_evaluation_units": proposal.resources.additional_evaluation_units,
                            },
                            "classification": "UNVERIFIED_PROPOSAL",
                            "created_at": row["created_at"], "updated_at": row["updated_at"],
                        })
                    except Exception:
                        proposals.append({"proposal_id": row["proposal_id"], "digest": row["digest"],
                                          "status": "DATA_INTEGRITY_ERROR",
                                          "classification": "UNVERIFIED_PROPOSAL"})
            if "v2_pivot_decisions" in tables:
                rows = conn.execute(
                    "SELECT proposal_id,digest,decision,rejection_action,status,created_at "
                    "FROM v2_pivot_decisions WHERE campaign_id=? "
                    "ORDER BY created_at,proposal_id LIMIT 101", (campaign_id,),
                ).fetchall()
                truncated["decisions"] = len(rows) > 100
                decisions = [{key: row[key] for key in (
                    "proposal_id", "digest", "decision", "rejection_action", "status", "created_at",
                )} for row in rows[:100]]
            if "v2_pivot_sagas" in tables:
                rows = conn.execute(
                    "SELECT child_id,state,child_spec_hash,created_at,updated_at FROM v2_pivot_sagas "
                    "WHERE parent_campaign_id=? ORDER BY created_at,idempotency_key LIMIT 101",
                    (campaign_id,),
                ).fetchall()
                truncated["children"] = len(rows) > 100
                children = [{key: row[key] for key in (
                    "child_id", "state", "child_spec_hash", "created_at", "updated_at",
                )} for row in rows[:100]]
            if "evaluator_verifier_receipts" in tables:
                rows = conn.execute(
                    "SELECT receipt_digest,proposal_digest,protocol_digest,decision,approval_digest "
                    "FROM evaluator_verifier_receipts WHERE campaign_id=? ORDER BY rowid DESC LIMIT 101",
                    (campaign_id,),
                ).fetchall()
                truncated["verifier_receipts"] = len(rows) > 100
                verifier_receipts = [dict(row) for row in rows[:100]]
            if "evaluator_integration_receipts" in tables:
                rows = conn.execute(
                    "SELECT receipt_digest,approval_digest,protocol_digest,candidate_snapshot_id,decision "
                    "FROM evaluator_integration_receipts WHERE campaign_id=? ORDER BY rowid DESC LIMIT 101",
                    (campaign_id,),
                ).fetchall()
                truncated["integration_receipts"] = len(rows) > 100
                integration_receipts = [dict(row) for row in rows[:100]]
            if "evaluator_run_receipts" in tables:
                evaluator_run_receipt_count = int(conn.execute(
                    "SELECT COUNT(*) FROM evaluator_run_receipts WHERE campaign_id=?", (campaign_id,),
                ).fetchone()[0])
            evaluator_tables = (
                "evaluator_verifier_receipts", "evaluator_integration_receipts", "evaluator_run_receipts",
            )
            if all(name in tables for name in evaluator_tables):
                evaluator_state = {
                    "verifier": {"APPROVE": 0, "REJECT": 0},
                    "integration": {"PASS": 0, "FAIL": 0},
                    "run_receipts": evaluator_run_receipt_count or 0,
                }
                for table, key, allowed in (
                    ("evaluator_verifier_receipts", "verifier", ("APPROVE", "REJECT")),
                    ("evaluator_integration_receipts", "integration", ("PASS", "FAIL")),
                ):
                    if table not in tables:
                        continue
                    for row in conn.execute(
                        f"SELECT decision,COUNT(*) AS amount FROM {table} WHERE campaign_id=? GROUP BY decision",
                        (campaign_id,),
                    ):
                        if row["decision"] in allowed:
                            evaluator_state[key][row["decision"]] = int(row["amount"])
            if "evaluator_protocol_handoffs" in tables:
                rows = conn.execute(
                    "SELECT handoff_digest,parent_campaign_id,child_campaign_id,protocol_id,protocol_version,"
                    "protocol_digest,receipt_digest FROM evaluator_protocol_handoffs "
                    "WHERE parent_campaign_id=? OR child_campaign_id=? ORDER BY rowid DESC LIMIT 101",
                    (campaign_id, campaign_id),
                ).fetchall()
                truncated["protocol_handoffs"] = len(rows) > 100
                protocol_handoffs = [dict(row) for row in rows[:100]]
        finally:
            conn.close()
        return {
            "campaign_id": campaign_id, "available": True,
            "proposals": proposals, "decisions": decisions, "children": children,
            "verifier_receipts": verifier_receipts,
            "integration_receipts": integration_receipts,
            "protocol_handoffs": protocol_handoffs, "phase_state": phase_state,
            "evaluator_run_receipt_count": evaluator_run_receipt_count,
            "evaluator_state": evaluator_state, "truncated": truncated,
            "source": "PERSISTED", "as_of": captured_at, "event_cursor": event_cursor,
        }

    @contextmanager
    def open_store(self) -> Iterator[tuple[CampaignStore, str]]:
        paths, campaign_id = self.resolve()
        if not campaign_db_initialized(paths.db):
            raise StoreError("campaign is not initialized")
        store = CampaignStore(paths.db)
        try:
            yield store, campaign_id
        finally:
            store.close()

    def snapshot(self) -> dict:
        paths, campaign_id = self.resolve()
        if self._schema_version(paths.db) == 5:
            with self.open_graph_store() as (graph, selected_id):
                return self._graph_projection(graph, selected_id)
        with self.open_store() as (store, campaign_id):
            return read_snapshot(store, campaign_id)

    def events(self, after: int = 0, limit: int = 100) -> dict:
        paths, _ = self.resolve()
        if self._schema_version(paths.db) == 5:
            with self.open_graph_store() as (graph, _):
                return read_events_after(graph, after, limit=limit)
        with self.open_store() as (store, _):
            return read_events_after(store, after, limit=limit)

    @contextmanager
    def open_graph_store(self) -> Iterator[tuple[V2CampaignStore, str]]:
        paths, campaign_id = self.resolve()
        if not campaign_db_initialized(paths.db):
            raise StoreError("campaign is not initialized")
        version = self._schema_version(paths.db)
        if version != 5:
            raise StoreError("campaign does not use the RPDI-02 v5 graph schema")
        connection = sqlite3.connect(paths.db, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            graph = V2CampaignStore(connection)
            yield graph, campaign_id
        finally:
            connection.close()

    @staticmethod
    def _graph_projection(graph: V2CampaignStore, campaign_id: str) -> dict:
        snapshot = graph.scheduling_snapshot()
        campaign = graph.conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
        nodes = []
        for node in snapshot["nodes"]:
            payload = node.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            completion = payload.get("completion_evidence") or {}
            if not isinstance(completion, dict):
                completion = {}
            refs = completion.get("evidence_refs") or []
            if not isinstance(refs, list):
                refs = []
            safe_refs = [
                "evidence:sha256:" + hashlib.sha256(str(ref).encode("utf-8")).hexdigest()
                for ref in refs if isinstance(ref, str) and ref
            ]
            nodes.append({key: node[key] for key in (
                "node_id", "phase", "status", "revision", "ready", "blockers",
            )} | {"completion_evidence_refs": sorted(set(safe_refs))})
        return {
            "schema_version": 2,
            "campaign_id": campaign_id,
            "status": campaign["status"] if campaign else "UNKNOWN",
            "graph_revision": snapshot["graph_revision"],
            "nodes": nodes,
            "edges": snapshot["edges"],
            "groups": snapshot["groups"],
            "as_of": utc_now(),
            "source": "PERSISTED",
        }

    def graph_snapshot(self) -> dict:
        with self.open_graph_store() as (graph, campaign_id):
            return self._graph_projection(graph, campaign_id)

    def mutate_graph(self, document: dict | str, idempotency_key: str) -> dict:
        request = MutateGraph.from_document(document)
        if request.idempotency_key != idempotency_key:
            raise ValueError("idempotency header does not match graph command")
        with self.open_graph_store() as (graph, _):
            return graph.mutate(request)

    def control(self, kind: str, request_id: str, *, store: CampaignStore | None = None) -> dict:
        if kind not in {"pause", "resume", "end"}:
            raise StoreError("invalid operator control kind")
        if store is None:
            paths, _ = self.resolve()
            if self._schema_version(paths.db) == 5:
                raise StoreError("operator controls are unavailable for graph campaigns; mutate node state instead")
            with self.open_store() as (opened, campaign_id):
                return submit_control(opened, campaign_id, OperatorCommand(campaign_id, request_id, kind))
        _, campaign_id = self.resolve()
        return submit_control(store, campaign_id, OperatorCommand(campaign_id, request_id, kind))

    def pivot_decision(self, command: PivotDecisionCommand, *, store: CampaignStore | None = None) -> dict:
        if store is None:
            with self.open_store() as (opened, campaign_id):
                return submit_pivot_decision(opened, campaign_id, command)
        _, campaign_id = self.resolve()
        return submit_pivot_decision(store, campaign_id, command)

    def v2_record_pivot_proposal(self, proposal: PivotProposal) -> dict:
        paths, campaign_id = self.resolve()
        if self._schema_version(paths.db) != 5:
            raise StoreError("v2 pivot proposals require a graph campaign")
        saga = V2PivotSaga(
            repo=self.repo, parent_campaign_id=campaign_id,
        )
        saga.record_pivot_proposal(proposal)
        return {
            "campaign_id": campaign_id, "proposal_id": proposal.proposal_id,
            "digest": proposal.digest, "status": "PENDING",
            "execution_plan": (
                proposal.execution_plan.model_dump(mode="json")
                if proposal.execution_plan is not None else None
            ),
            "resource_estimate_authoritative": False,
            "automatic_execution": False,
        }

    def v2_pivot_decision(self, command: PivotDecisionCommand) -> dict:
        paths, campaign_id = self.resolve()
        if self._schema_version(paths.db) != 5:
            raise StoreError("v2 pivot decisions require a graph campaign")
        if command.campaign_id != campaign_id:
            raise StoreError("pivot decision targets a different campaign")
        saga = V2PivotSaga(
            repo=self.repo, parent_campaign_id=campaign_id,
        )
        saga.record_pivot_decision(command)
        return {
            "campaign_id": campaign_id, "request_id": command.request_id,
            "proposal_id": command.proposal_id, "digest": command.digest,
            "decision": command.decision,
            "status": "APPROVED" if command.decision == "approve" else "REJECTED",
            "automatic_execution": False,
        }

    def v2_execute_pivot(self, proposal_id: str, digest: str, idempotency_key: str) -> dict:
        """Execute or resume only the exact plan and approval persisted in the parent DB."""
        paths, campaign_id = self.resolve()
        if self._schema_version(paths.db) != 5:
            raise StoreError("v2 pivot execution requires a graph campaign")
        assert_safe_campaign_db(paths.db)
        conn = sqlite3.connect(paths.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM v2_pivot_proposals WHERE proposal_id=?", (proposal_id,),
            ).fetchone()
            if row is None or row["digest"] != digest or row["spec_hash"] is None:
                raise StoreError("persisted pivot proposal identity does not match")
            proposal = PivotProposal.model_validate(json.loads(row["proposal_json"]))
            if proposal.digest != digest or proposal.spec_hash != row["spec_hash"]:
                raise StoreError("persisted pivot proposal is corrupt or stale")
            if proposal.execution_plan is None:
                raise StoreError("legacy pivot proposal has no executable plan")
            campaign = conn.execute("SELECT spec_json,spec_hash FROM campaigns WHERE id=1").fetchone()
            if campaign is None or campaign["spec_hash"] != row["spec_hash"]:
                raise StoreError("pivot proposal no longer matches the frozen parent campaign")
            parent_spec = json.loads(campaign["spec_json"])
            mode = parent_spec.get("autonomy", {}).get("mode")
            approval = None
            if mode == "SUPERVISED":
                decision = conn.execute(
                    "SELECT * FROM v2_pivot_decisions WHERE campaign_id=? AND proposal_id=? "
                    "AND digest=? AND decision='approve' AND status='APPROVED'",
                    (campaign_id, proposal_id, digest),
                ).fetchone()
                if decision is None or row["status"] != "APPROVED":
                    raise StoreError("pivot has no matching durable Supervised approval")
                approval = PivotDecisionCommand(
                    campaign_id=campaign_id, request_id=decision["request_id"],
                    proposal_id=proposal_id, digest=digest, decision="approve",
                )
            elif mode != "FULL_AUTO":
                raise StoreError("parent autonomy mode does not authorize pivot execution")
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError("persisted pivot authorization could not be verified") from exc
        finally:
            conn.close()
        saga = V2PivotSaga(repo=self.repo, parent_campaign_id=campaign_id)
        return saga.create_child(
            idempotency_key=idempotency_key,
            proposal=proposal,
            child_spec=json.loads(proposal.execution_plan.child_spec_json),
            approval=approval,
        )

    def status(self) -> dict:
        paths, campaign_id = self.resolve()
        if not campaign_db_initialized(paths.db):
            return {
                "status": "RESERVED", "stage": "BOOTSTRAP", "campaign_id": campaign_id,
                "state": str(paths.root), "initialized": False,
                "hint": "rerun the same bananloop goal to continue bootstrap",
            }
        if self._schema_version(paths.db) == 5:
            return self.snapshot()
        store = CampaignStore(paths.db)
        try:
            campaign = store.campaign()
            data = {key: campaign[key] for key in (
                "status", "stop_reason", "stage", "baseline_snapshot", "champion_snapshot",
                "experiment_count", "attempt_count", "agent_calls", "evaluator_calls",
                "non_improve_streak", "created_at", "updated_at",
            )}
            data["campaign_id"] = campaign_id
            data["initialized"] = True
            data["resources"] = store.resource_summary()
            data["resources"]["model_tokens"] = store.model_token_summary()
            measurements = build_report(store)["measurements"]
            data["evaluations"] = [{
                "experiment_id": item.get("experiment_id"), "tier": item.get("tier"),
                "metrics": {name: metric.get("value") for name, metric in
                            ((item.get("result") or {}).get("metrics") or {}).items()},
                "checks": {name: check.get("passed") for name, check in
                           ((item.get("result") or {}).get("checks") or {}).items()},
            } for item in measurements]
            research = store.research_state()
            if research:
                data["planning_mode"] = research.get("planning_mode")
            return data
        finally:
            store.close()

    @staticmethod
    def pivot_receipt(campaign_id: str, receipt: dict, store: CampaignStore) -> dict:
        return {
            "campaign_id": campaign_id, "request_id": receipt["request_id"],
            "proposal_id": receipt["proposal_id"], "digest": receipt["digest"],
            "decision": receipt["decision"], "status": receipt["status"],
            "result": json.loads(receipt["result_json"]),
            "campaign_status": store.campaign()["status"], "automatic_execution": False,
        }
