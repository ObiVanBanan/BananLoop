"""Shared application service used by the CLI and the local REST API."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .contracts import PivotDecisionCommand, PivotProposal, RuntimePaths
from .graph_store import GraphStoreError, MutateGraph, V2CampaignStore
from .operator_control import OperatorCommand, submit_control, submit_pivot_decision
from .operator_snapshot import read_events_after, read_snapshot
from .registry import CampaignRegistry, RegistryError, assert_safe_campaign_db
from .report import build_report
from .store import CampaignStore, StoreError, campaign_db_initialized
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
        return {
            "schema_version": 2,
            "campaign_id": campaign_id,
            "status": campaign["status"] if campaign else "UNKNOWN",
            "graph_revision": snapshot["graph_revision"],
            "nodes": [{key: node[key] for key in (
                "node_id", "phase", "status", "revision", "ready", "blockers",
            )} for node in snapshot["nodes"]],
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
