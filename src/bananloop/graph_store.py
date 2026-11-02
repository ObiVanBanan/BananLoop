from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .contracts import CampaignSpec, ExplorationCampaignSpecV2, OptimizationCampaignSpecV2
from .contracts import parse_campaign_spec_document


class GraphStoreError(RuntimeError):
    pass


class GraphRevisionConflict(GraphStoreError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"graph revision conflict: expected {expected}, current {actual}")


class GraphIdempotencyConflict(GraphStoreError):
    pass


class AddNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["AddNode"]
    node_id: str = Field(min_length=1, max_length=200)
    phase: Literal[
        "EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION"
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class AddEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["AddEdge"]
    source_id: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)
    edge_type: Literal["REQUIRES", "DERIVED_FROM", "RELATED_TO"]
    constraint: dict[str, Any] = Field(default_factory=lambda: {"policy": "verified_completion"})


class AddGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["AddGroup"]
    group_id: str = Field(min_length=1, max_length=200)
    owner_node_id: str = Field(min_length=1, max_length=200)
    kind: Literal["AND", "OR"]
    member_node_ids: tuple[str, ...] = Field(min_length=1)
    readiness_policy: Literal["verified_completion"] = "verified_completion"

    @field_validator("member_node_ids")
    @classmethod
    def unique_members(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("group member node IDs must be unique")
        return value

    @model_validator(mode="after")
    def owner_is_not_member(self) -> "AddGroup":
        if self.owner_node_id in self.member_node_ids:
            raise ValueError("a readiness group owner cannot be one of its own members")
        return self


class TransitionNodePhase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["TransitionNodePhase"]
    node_id: str = Field(min_length=1, max_length=200)
    expected_node_revision: int = Field(ge=0)
    from_phase: Literal[
        "EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION"
    ]
    to_phase: Literal[
        "EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION"
    ]
    guard: Literal[
        "METRIC_PROPOSAL", "PROTOCOL_FROZEN", "PROTOCOL_REJECTED", "CANDIDATE_READY",
        "REFRAME", "REGRESSION_FAILED", "VERIFIED_RESULT",
    ]
    actor: Literal["CORE", "VERIFIER"]
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def canonical_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref or len(ref) > 2000 for ref in value):
            raise ValueError("phase transition evidence references must be nonempty and bounded")
        return tuple(sorted(set(value)))


class TransitionNodeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["TransitionNodeStatus"]
    node_id: str = Field(min_length=1, max_length=200)
    expected_node_revision: int = Field(ge=0)
    expected_status: Literal["PENDING", "READY", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"]
    to_status: Literal["PENDING", "READY", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"]
    guard: Literal["SCHEDULER_READY", "HUMAN_PAUSE", "HUMAN_RESUME", "VERIFIED_COMPLETION", "VERIFIED_FAILURE", "HUMAN_CANCEL"]
    actor: Literal["CORE", "HUMAN"]
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def canonical_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref or len(ref) > 2000 for ref in value):
            raise ValueError("status transition evidence references must be nonempty and bounded")
        return tuple(sorted(set(value)))


GraphChange = Annotated[
    Union[AddNode, AddEdge, AddGroup, TransitionNodePhase, TransitionNodeStatus],
    Field(discriminator="type"),
]
_graph_change_adapter = TypeAdapter(GraphChange)


class MutateGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_graph_revision: int = Field(ge=0)
    changes: tuple[GraphChange, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def canonical_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref for ref in value):
            raise ValueError("evidence references must be nonempty")
        return tuple(sorted(set(value)))

    @classmethod
    def from_document(cls, document: dict[str, Any] | str) -> "MutateGraph":
        raw = json.loads(document) if isinstance(document, str) else document
        if not isinstance(raw, dict):
            raise ValueError("graph mutation must be a JSON object")
        raw = dict(raw)
        raw["changes"] = tuple(_graph_change_adapter.validate_python(change) for change in raw.get("changes", ()))
        return cls.model_validate(raw)


class ExplorationCompletionV2(BaseModel):
    """Trusted-Core projection of evidence from one fenced exploration job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_graph_revision: int = Field(ge=0)
    node_id: str = Field(min_length=1, max_length=200)
    node_revision: int = Field(ge=0)
    job_id: str = Field(min_length=1, max_length=120)
    worker_fence: int = Field(ge=1)
    source_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: tuple[str, ...] = ()
    artifact_digests: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    next_experiment: str = Field(min_length=1, max_length=4000)

    @field_validator("evidence_refs", "artifact_digests", "limitations")
    @classmethod
    def canonical_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 2000 for item in value):
            raise ValueError("completion evidence entries must be nonempty and bounded")
        return tuple(sorted(set(value)))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_verified_completion(payload_json: str) -> bool:
    try:
        evidence = json.loads(payload_json).get("completion_evidence")
    except (TypeError, ValueError, AttributeError):
        return False
    if not isinstance(evidence, dict):
        return False
    if evidence.get("status") == "COMPLETED" and evidence.get("evidence_refs"):
        return True
    return all(evidence.get(key) for key in (
        "job_id", "worker_fence", "source_request_hash", "output_digest", "spec_hash"
    ))


class GraphRepository:
    """Transactional RPDI-02 graph repository over an already migrated v5 database."""

    def __init__(self, connection: sqlite3.Connection, *, campaign_id: str | None = None):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        foreign_keys_enabled = self.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if foreign_keys_enabled != 1:
            raise GraphStoreError("graph repository requires SQLite foreign keys")
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version != 5:
            raise GraphStoreError(f"graph repository requires schema version 5; found {version}")
        self._campaign_id = None
        if campaign_id is not None:
            self.bind_campaign_id(campaign_id)

    @classmethod
    def open(cls, path: str, *, campaign_id: str | None = None) -> "GraphRepository":
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            return cls(conn, campaign_id=campaign_id)
        except Exception:
            conn.close()
            raise

    def bind_campaign_id(self, campaign_id: str) -> None:
        """Bind this graph DB to one immutable external campaign identity."""
        if not isinstance(campaign_id, str) or not campaign_id or len(campaign_id) > 200:
            raise GraphStoreError("campaign identity must be nonempty and bounded")
        self.conn.execute("SAVEPOINT graph_campaign_identity_bind")
        try:
            self.conn.execute("CREATE TABLE IF NOT EXISTS graph_campaign_identity ("
                              "id INTEGER PRIMARY KEY CHECK(id=1), campaign_id TEXT NOT NULL UNIQUE)")
            self.conn.execute("CREATE TRIGGER IF NOT EXISTS graph_campaign_identity_no_update "
                              "BEFORE UPDATE ON graph_campaign_identity BEGIN "
                              "SELECT RAISE(ABORT,'campaign identity is immutable'); END")
            self.conn.execute("CREATE TRIGGER IF NOT EXISTS graph_campaign_identity_no_delete "
                              "BEFORE DELETE ON graph_campaign_identity BEGIN "
                              "SELECT RAISE(ABORT,'campaign identity is immutable'); END")
            self.conn.execute("INSERT OR IGNORE INTO graph_campaign_identity(id,campaign_id) VALUES(1,?)",
                              (campaign_id,))
            row = self.conn.execute("SELECT campaign_id FROM graph_campaign_identity WHERE id=1").fetchone()
            if row is None or row[0] != campaign_id:
                raise GraphStoreError("graph database is already bound to a different campaign")
            self.conn.execute("RELEASE SAVEPOINT graph_campaign_identity_bind")
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT graph_campaign_identity_bind")
            self.conn.execute("RELEASE SAVEPOINT graph_campaign_identity_bind")
            raise
        self._campaign_id = campaign_id

    @property
    def campaign_id(self) -> str | None:
        if self._campaign_id is not None:
            return self._campaign_id
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_campaign_identity'"
        ).fetchone()
        if exists is None:
            return None
        row = self.conn.execute("SELECT campaign_id FROM graph_campaign_identity WHERE id=1").fetchone()
        return str(row[0]) if row is not None else None

    @property
    def evaluator_protocols(self):
        from .evaluator_verification import EvaluatorProtocolAuthority
        return EvaluatorProtocolAuthority(self.conn)

    def close(self) -> None:
        self.conn.close()

    def campaign_spec(self) -> CampaignSpec | ExplorationCampaignSpecV2 | OptimizationCampaignSpecV2:
        row = self.conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()
        if row is None:
            raise GraphStoreError("campaign is not initialized")
        try:
            return parse_campaign_spec_document(row["spec_json"])
        except (ValueError, TypeError) as exc:
            raise GraphStoreError(f"campaign spec cannot be loaded by the v2 store: {exc}") from exc

    def graph_revision(self) -> int:
        row = self.conn.execute("SELECT revision FROM graph_meta WHERE id=1").fetchone()
        if row is None:
            raise GraphStoreError("graph metadata is missing")
        return int(row["revision"])

    def command_response(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT response_json FROM commands WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def mutate(self, request: MutateGraph | dict[str, Any] | str) -> dict[str, Any]:
        if not isinstance(request, MutateGraph):
            request = MutateGraph.from_document(request)
        payload = request.model_dump(mode="json")
        encoded = _canonical_json(payload)
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            replay = self.conn.execute(
                "SELECT command_id,payload_hash,response_json FROM commands WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if replay is not None:
                if replay["payload_hash"] != payload_hash:
                    raise GraphIdempotencyConflict("idempotency key was reused with different content")
                self.conn.commit()
                return json.loads(replay["response_json"])

            reused_id = self.conn.execute(
                "SELECT idempotency_key,payload_hash FROM commands WHERE command_id=?",
                (request.command_id,),
            ).fetchone()
            if reused_id is not None:
                raise GraphIdempotencyConflict("command ID was reused")

            actual_revision = self.graph_revision()
            if actual_revision != request.expected_graph_revision:
                raise GraphRevisionConflict(request.expected_graph_revision, actual_revision)

            # Allow a batch to declare references before or after its nodes;
            # foreign keys still reject any unresolved identity at commit.
            for change in request.changes:
                if isinstance(change, AddNode):
                    self._apply_change(change)
            for change in request.changes:
                if not isinstance(change, AddNode):
                    self._apply_change(change)
            self._validate_requires_acyclic()

            changed = self.conn.execute(
                "UPDATE graph_meta SET revision=revision+1 WHERE id=1 AND revision=?",
                (request.expected_graph_revision,),
            ).rowcount
            if changed != 1:
                raise GraphRevisionConflict(request.expected_graph_revision, self.graph_revision())

            new_revision = request.expected_graph_revision + 1
            event_payload = {
                "command_id": request.command_id,
                "graph_revision": new_revision,
                "payload_hash": payload_hash,
                "evidence_refs": list(request.evidence_refs),
                "changes": payload["changes"],
            }
            event_cursor = self.conn.execute(
                "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("GRAPH_MUTATED", _canonical_json(event_payload), _now()),
            ).lastrowid
            response = {
                "command_id": request.command_id,
                "status": "COMMITTED",
                "graph_revision": new_revision,
                "event_cursor": int(event_cursor),
                "evidence_refs": list(request.evidence_refs),
            }
            self.conn.execute(
                "INSERT INTO commands(command_id,command_type,idempotency_key,payload_hash,status,response_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    request.command_id, "MUTATE_GRAPH", request.idempotency_key, payload_hash,
                    "COMMITTED", _canonical_json(response), _now(),
                ),
            )
            self.conn.commit()
            return response
        except Exception:
            self.conn.rollback()
            raise

    def complete_exploration(self, request: ExplorationCompletionV2 | dict[str, Any]) -> dict[str, Any]:
        """Atomically append evidence and advance one v2 exploration node.

        Queue settlement lives in another database; this method is an
        idempotent projection from its durable result stream.
        """
        if not isinstance(request, ExplorationCompletionV2):
            request = ExplorationCompletionV2.model_validate(request)
        spec = self.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2):
            raise GraphStoreError("evidence-only completion requires an EXPLORATION v2 campaign")
        if request.spec_hash != spec.approved_hash():
            raise GraphStoreError("completion spec hash does not match the approved campaign")
        payload = request.model_dump(mode="json")
        # Revision is a first-commit CAS precondition, not part of replay identity.
        identity = {k: v for k, v in payload.items() if k != "expected_graph_revision"}
        encoded = _canonical_json(identity)
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            replay = self.conn.execute(
                "SELECT payload_hash,response_json FROM commands WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if replay is not None:
                if replay["payload_hash"] != payload_hash:
                    raise GraphIdempotencyConflict("idempotency key was reused with different completion evidence")
                self.conn.commit()
                return json.loads(replay["response_json"])
            reused_id = self.conn.execute(
                "SELECT 1 FROM commands WHERE command_id=?", (request.command_id,)
            ).fetchone()
            if reused_id is not None:
                raise GraphIdempotencyConflict("command ID was reused")
            revision = self.graph_revision()
            if revision != request.expected_graph_revision:
                raise GraphRevisionConflict(request.expected_graph_revision, revision)
            node = self.conn.execute(
                "SELECT phase,status,payload_json,revision FROM research_nodes WHERE node_id=?",
                (request.node_id,),
            ).fetchone()
            if (node is None or node["phase"] != "EXPLORATION" or node["status"] != "READY"
                    or int(node["revision"]) != request.node_revision):
                raise GraphStoreError("completion target must be a READY EXPLORATION node")
            evidence = {
                "job_id": request.job_id, "worker_fence": request.worker_fence,
                "source_request_hash": request.source_request_hash,
                "output_digest": request.output_digest, "spec_hash": request.spec_hash,
                "evidence_refs": list(request.evidence_refs),
                "artifact_digests": list(request.artifact_digests),
                "limitations": list(request.limitations),
                "next_experiment": request.next_experiment,
            }
            node_payload = json.loads(node["payload_json"])
            node_payload["completion_evidence"] = evidence
            changed = self.conn.execute(
                "UPDATE research_nodes SET phase='CONSOLIDATION',status='COMPLETED',payload_json=?,revision=revision+1,updated_at=? "
                "WHERE node_id=? AND phase='EXPLORATION' AND status='READY' AND revision=?",
                (_canonical_json(node_payload), _now(), request.node_id, node["revision"]),
            ).rowcount
            if changed != 1:
                raise GraphStoreError("completion target changed during commit")
            changed = self.conn.execute(
                "UPDATE graph_meta SET revision=revision+1 WHERE id=1 AND revision=?",
                (request.expected_graph_revision,),
            ).rowcount
            if changed != 1:
                raise GraphRevisionConflict(request.expected_graph_revision, self.graph_revision())
            new_revision = request.expected_graph_revision + 1
            event_payload = {"node_id": request.node_id, "graph_revision": new_revision, **evidence}
            cursor = self.conn.execute(
                "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("EXPLORATION_COMPLETED", _canonical_json(event_payload), _now()),
            ).lastrowid
            response = {"command_id": request.command_id, "status": "COMMITTED",
                        "graph_revision": new_revision, "event_cursor": int(cursor),
                        "node_id": request.node_id, "job_id": request.job_id,
                        "worker_fence": request.worker_fence}
            self.conn.execute(
                "INSERT INTO commands(command_id,command_type,idempotency_key,payload_hash,status,response_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (request.command_id, "COMPLETE_EXPLORATION", request.idempotency_key,
                 payload_hash, "COMMITTED", _canonical_json(response), _now()),
            )
            self.conn.commit()
            return response
        except Exception:
            self.conn.rollback()
            raise

    def group_satisfied(self, group_id: str, *, verified_completed_node_ids: set[str]) -> bool:
        """Evaluate AND/OR membership from IDs already approved by a trusted verifier."""
        group = self.conn.execute(
            "SELECT kind,readiness_policy FROM graph_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        if group is None:
            raise GraphStoreError(f"unknown graph group {group_id!r}")
        if group["readiness_policy"] != "verified_completion":
            raise GraphStoreError("unsupported readiness policy")
        members = [
            row["node_id"]
            for row in self.conn.execute(
                """SELECT node_id FROM graph_group_members
                   WHERE group_id=? ORDER BY position""",
                (group_id,),
            )
        ]
        if group["kind"] == "AND":
            return bool(members) and all(node_id in verified_completed_node_ids for node_id in members)
        return any(node_id in verified_completed_node_ids for node_id in members)

    def scheduling_snapshot(self) -> dict[str, Any]:
        """Return a revision-consistent view of nodes and dependency blockers."""
        self.conn.execute("BEGIN")
        try:
            revision = self.graph_revision()
            nodes = [dict(row) for row in self.conn.execute(
                "SELECT node_id,phase,status,payload_json,revision,created_at,updated_at "
                "FROM research_nodes ORDER BY node_id"
            )]
            edges = [dict(row) for row in self.conn.execute(
                "SELECT src_id,dst_id,edge_type FROM research_edges "
                "WHERE edge_type='REQUIRES' ORDER BY src_id,dst_id"
            )]
            groups = [dict(row) for row in self.conn.execute(
                "SELECT group_id,owner_node_id,kind FROM graph_groups ORDER BY owner_node_id,group_id"
            )]
            members = {row["group_id"]: [item[0] for item in self.conn.execute(
                "SELECT node_id FROM graph_group_members WHERE group_id=? ORDER BY position",
                (row["group_id"],),
            )] for row in groups}
            by_id = {node["node_id"]: node for node in nodes}
            completed = {node_id for node_id, node in by_id.items()
                         if node["status"] == "COMPLETED" and _has_verified_completion(node["payload_json"])}
            prerequisites: dict[str, list[str]] = {}
            for edge in edges:
                prerequisites.setdefault(edge["dst_id"], []).append(edge["src_id"])
            owned_groups: dict[str, list[dict[str, Any]]] = {}
            for group in groups:
                owned_groups.setdefault(group["owner_node_id"], []).append(group)
            result: list[dict[str, Any]] = []
            for node in nodes:
                if node["phase"] not in {"EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION", "CONSOLIDATION"}:
                    raise GraphStoreError(f"node {node['node_id']!r} has an unknown phase")
                if node["status"] not in {"PENDING", "READY", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"}:
                    raise GraphStoreError(f"node {node['node_id']!r} has an unknown status")
                blockers = [f"requires:{dep}" for dep in sorted(prerequisites.get(node["node_id"], ()))
                            if dep not in completed]
                for group in owned_groups.get(node["node_id"], ()):
                    group_members = members[group["group_id"]]
                    count = sum(member in completed for member in group_members)
                    satisfied = bool(group_members) and (count == len(group_members) if group["kind"] == "AND" else count > 0)
                    if not satisfied:
                        blockers.append(f"{group['kind'].lower()}:{group['group_id']}")
                result.append({**node, "payload": json.loads(node["payload_json"]),
                               "ready": node["status"] in {"PENDING", "READY"} and not blockers,
                               "blockers": blockers})
            return {"graph_revision": revision, "nodes": result}
        finally:
            self.conn.rollback()

    def _apply_change(self, change: GraphChange) -> None:
        now = _now()
        if isinstance(change, AddNode):
            self.conn.execute(
                """INSERT INTO research_nodes
                   (node_id,phase,status,payload_json,revision,created_at,updated_at)
                   VALUES(?,?, 'PENDING', ?,0,?,?)""",
                (change.node_id, change.phase, _canonical_json(change.payload), now, now),
            )
            return
        if isinstance(change, AddEdge):
            if change.source_id == change.target_id and change.edge_type == "REQUIRES":
                raise GraphStoreError("REQUIRES self-cycle is invalid")
            if change.edge_type == "REQUIRES":
                target = self.conn.execute("SELECT status FROM research_nodes WHERE node_id=?", (change.target_id,)).fetchone()
                if target is not None and target["status"] != "PENDING":
                    raise GraphStoreError("REQUIRES cannot be added to a node after scheduling has started")
            self.conn.execute(
                "INSERT INTO research_edges(src_id,dst_id,edge_type,constraint_json) VALUES(?,?,?,?)",
                (change.source_id, change.target_id, change.edge_type, _canonical_json(change.constraint)),
            )
            return
        if isinstance(change, AddGroup):
            owner = self.conn.execute("SELECT status FROM research_nodes WHERE node_id=?", (change.owner_node_id,)).fetchone()
            if owner is not None and owner["status"] != "PENDING":
                raise GraphStoreError("readiness groups cannot be added after scheduling has started")
            self.conn.execute(
                """INSERT INTO graph_groups(group_id,owner_node_id,kind,readiness_policy)
                   VALUES(?,?,?,?)""",
                (change.group_id, change.owner_node_id, change.kind, change.readiness_policy),
            )
            for position, node_id in enumerate(change.member_node_ids):
                self.conn.execute(
                    """INSERT INTO graph_group_members(group_id,node_id,position)
                       VALUES(?,?,?)""",
                    (change.group_id, node_id, position),
                )
            return
        if isinstance(change, TransitionNodePhase):
            self._transition_node_phase(change)
            return
        if isinstance(change, TransitionNodeStatus):
            self._transition_node_status(change)
            return
        raise GraphStoreError(f"unsupported graph change {type(change).__name__}")

    def _transition_node_phase(self, change: TransitionNodePhase) -> None:
        allowed = {
            ("EXPLORATION", "METRIC_DISCOVERY"): ("METRIC_PROPOSAL", "CORE"),
            ("METRIC_DISCOVERY", "OPTIMIZATION"): ("PROTOCOL_FROZEN", "VERIFIER"),
            ("METRIC_DISCOVERY", "EXPLORATION"): ("PROTOCOL_REJECTED", "VERIFIER"),
            ("OPTIMIZATION", "VERIFICATION"): ("CANDIDATE_READY", "CORE"),
            ("OPTIMIZATION", "EXPLORATION"): ("REFRAME", "CORE"),
            ("VERIFICATION", "OPTIMIZATION"): ("REGRESSION_FAILED", "VERIFIER"),
            ("VERIFICATION", "CONSOLIDATION"): ("VERIFIED_RESULT", "VERIFIER"),
        }
        expected = allowed.get((change.from_phase, change.to_phase))
        if expected != (change.guard, change.actor):
            raise GraphStoreError("phase transition is not in the guarded transition matrix")
        if change.to_phase == "CONSOLIDATION" and change.from_phase == "EXPLORATION":
            raise GraphStoreError("evidence-only consolidation must use verified exploration completion")
        row = self.conn.execute(
            "SELECT phase,status,payload_json,revision FROM research_nodes WHERE node_id=?", (change.node_id,)
        ).fetchone()
        if row is None or row["phase"] != change.from_phase or int(row["revision"]) != change.expected_node_revision:
            raise GraphStoreError("phase transition node revision or source phase is stale")
        if row["status"] != "COMPLETED":
            raise GraphStoreError("research phase can change only after the current phase is completed")
        payload = json.loads(row["payload_json"])
        self._validate_verifier_phase_evidence(change, payload)
        payload["last_phase_transition"] = {
            "from_phase": change.from_phase, "to_phase": change.to_phase, "guard": change.guard,
            "actor": change.actor, "evidence_refs": list(change.evidence_refs),
        }
        next_status = "COMPLETED" if change.to_phase == "CONSOLIDATION" else "PENDING"
        changed = self.conn.execute(
            "UPDATE research_nodes SET phase=?,status=?,payload_json=?,revision=revision+1,updated_at=? "
            "WHERE node_id=? AND phase=? AND status='COMPLETED' AND revision=?",
            (change.to_phase, next_status, _canonical_json(payload), _now(), change.node_id,
             change.from_phase, change.expected_node_revision),
        ).rowcount
        if changed != 1:
            raise GraphStoreError("phase transition lost its node revision race")

    def _validate_verifier_phase_evidence(self, change: TransitionNodePhase,
                                         node_payload: dict[str, Any]) -> None:
        from .contracts import OptimizationCampaignSpecV2
        from .evaluator_verification import EvaluatorVerificationError

        if change.guard not in {"PROTOCOL_FROZEN", "PROTOCOL_REJECTED", "CANDIDATE_READY", "VERIFIED_RESULT", "REGRESSION_FAILED"}:
            return
        has_campaign = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
        ).fetchone()
        if has_campaign is None:
            if change.guard == "PROTOCOL_FROZEN":
                raise GraphStoreError("protocol freeze requires an Optimization spec pinned to verifier approval")
            if change.guard in {"CANDIDATE_READY", "VERIFIED_RESULT", "REGRESSION_FAILED"}:
                raise GraphStoreError("Optimization transition requires a pinned evaluator campaign")
            raise GraphStoreError("verifier transition requires an initialized campaign spec")
        spec = self.campaign_spec()
        approval_refs = [ref.removeprefix("verifier://approval/") for ref in change.evidence_refs
                         if isinstance(ref, str) and ref.startswith("verifier://approval/")]
        receipt_refs = [ref.removeprefix("verifier://receipt/") for ref in change.evidence_refs
                        if isinstance(ref, str) and ref.startswith("verifier://receipt/")]
        integration_refs = [ref.removeprefix("integration://receipt/") for ref in change.evidence_refs
                            if isinstance(ref, str) and ref.startswith("integration://receipt/")]
        evaluator_refs = [ref.removeprefix("evaluator://receipt/") for ref in change.evidence_refs
                          if isinstance(ref, str) and ref.startswith("evaluator://receipt/")]
        try:
            if change.guard == "PROTOCOL_FROZEN":
                if not isinstance(spec, OptimizationCampaignSpecV2) or spec.evaluation.approval_digest is None:
                    raise GraphStoreError("protocol freeze requires an Optimization spec pinned to verifier approval")
                if approval_refs != [spec.evaluation.approval_digest]:
                    raise GraphStoreError("protocol freeze evidence must cite the exact pinned verifier approval")
                campaign_id = self.campaign_id
                if campaign_id is None:
                    raise GraphStoreError("protocol freeze requires an immutable graph campaign identity")
                frozen = self.evaluator_protocols.require_frozen_reference(
                    spec.evaluation.protocol_id, spec.evaluation.version,
                    spec.evaluation.content_sha256, spec.evaluation.approval_digest,
                    campaign_id=campaign_id,
                )
                if node_payload.get("source_snapshot_id") != frozen.source_snapshot_id:
                    raise GraphStoreError("protocol approval source snapshot does not match the scheduled node")
            elif change.guard == "PROTOCOL_REJECTED":
                if len(receipt_refs) != 1:
                    raise GraphStoreError("protocol rejection requires one durable verifier receipt reference")
                campaign_id = self.campaign_id
                if campaign_id is None:
                    raise GraphStoreError("protocol rejection requires an immutable graph campaign identity")
                rejected = self.evaluator_protocols.require_rejected_proposal(
                    receipt_refs[0], spec_hash=spec.approved_hash(), campaign_id=campaign_id,
                )
                if node_payload.get("source_snapshot_id") != rejected.source_snapshot_id:
                    raise GraphStoreError("protocol rejection source snapshot does not match the scheduled node")
            elif change.guard == "CANDIDATE_READY":
                if not isinstance(spec, OptimizationCampaignSpecV2) or spec.evaluation.approval_digest is None:
                    raise GraphStoreError("candidate readiness requires a pinned Optimization evaluator")
                if len(evaluator_refs) != 1 or len(change.evidence_refs) != 1:
                    raise GraphStoreError("candidate readiness requires one evaluator run receipt reference")
                candidate_snapshot_id = node_payload.get("candidate_snapshot_id")
                source_snapshot_id = node_payload.get("source_snapshot_id")
                if not isinstance(candidate_snapshot_id, str) or not isinstance(source_snapshot_id, str):
                    raise GraphStoreError("candidate readiness requires pinned source and candidate snapshots")
                campaign_id = self.campaign_id
                if campaign_id is None:
                    raise GraphStoreError("candidate readiness requires an immutable graph campaign identity")
                run = self.evaluator_protocols.require_run_receipt(
                    evaluator_refs[0], campaign_id=campaign_id, spec_hash=spec.approved_hash(),
                    approval_digest=spec.evaluation.approval_digest,
                    protocol_digest=spec.evaluation.content_sha256,
                    candidate_snapshot_id=candidate_snapshot_id,
                )
                if (run.source_snapshot_id != source_snapshot_id
                        or run.protocol_id != spec.evaluation.protocol_id
                        or run.protocol_version != spec.evaluation.version):
                    raise GraphStoreError("evaluator receipt is stale against the scheduled candidate or protocol")
            elif change.guard in {"VERIFIED_RESULT", "REGRESSION_FAILED"}:
                if not isinstance(spec, OptimizationCampaignSpecV2) or spec.evaluation.approval_digest is None:
                    raise GraphStoreError("integration verification requires a pinned Optimization evaluator")
                if len(integration_refs) != 1:
                    raise GraphStoreError("integration phase transition requires one durable verifier receipt")
                candidate_snapshot_id = node_payload.get("candidate_snapshot_id")
                if not isinstance(candidate_snapshot_id, str):
                    raise GraphStoreError("integration phase transition has no pinned candidate snapshot")
                campaign_id = self.campaign_id
                if campaign_id is None:
                    raise GraphStoreError("integration verification requires an immutable graph campaign identity")
                self.evaluator_protocols.require_integration_decision(
                    integration_refs[0], decision="PASS" if change.guard == "VERIFIED_RESULT" else "FAIL",
                    campaign_id=campaign_id, approval_digest=spec.evaluation.approval_digest,
                    spec_hash=spec.approved_hash(), source_snapshot_id=node_payload.get("source_snapshot_id", ""),
                    protocol_digest=spec.evaluation.content_sha256,
                    candidate_snapshot_id=candidate_snapshot_id,
                )
        except EvaluatorVerificationError as exc:
            raise GraphStoreError(str(exc)) from exc

    def _transition_node_status(self, change: TransitionNodeStatus) -> None:
        allowed = {
            ("PENDING", "READY"): ("SCHEDULER_READY", "CORE", False),
            ("READY", "PAUSED"): ("HUMAN_PAUSE", "HUMAN", False),
            ("PAUSED", "READY"): ("HUMAN_RESUME", "HUMAN", False),
            ("READY", "COMPLETED"): ("VERIFIED_COMPLETION", "CORE", True),
            ("READY", "FAILED"): ("VERIFIED_FAILURE", "CORE", True),
            ("PENDING", "CANCELLED"): ("HUMAN_CANCEL", "HUMAN", False),
            ("READY", "CANCELLED"): ("HUMAN_CANCEL", "HUMAN", False),
            ("PAUSED", "CANCELLED"): ("HUMAN_CANCEL", "HUMAN", False),
        }
        expected = allowed.get((change.expected_status, change.to_status))
        if expected is None or expected[:2] != (change.guard, change.actor):
            raise GraphStoreError("node status transition is not in the guarded transition matrix")
        if expected[2] and not change.evidence_refs:
            raise GraphStoreError("terminal verified status requires evidence references")
        if not expected[2] and change.evidence_refs:
            raise GraphStoreError("this node status transition does not accept evidence references")
        row = self.conn.execute(
            "SELECT status,payload_json,revision FROM research_nodes WHERE node_id=?", (change.node_id,)
        ).fetchone()
        if row is None or row["status"] != change.expected_status or int(row["revision"]) != change.expected_node_revision:
            raise GraphStoreError("node status revision or source state is stale")
        if change.expected_status == "PENDING" and change.to_status == "READY":
            completed = {item["node_id"] for item in self.conn.execute(
                "SELECT node_id,payload_json,status FROM research_nodes WHERE status='COMPLETED'")
                if _has_verified_completion(item["payload_json"])}
            blockers = [edge["src_id"] for edge in self.conn.execute(
                "SELECT src_id FROM research_edges WHERE dst_id=? AND edge_type='REQUIRES' ORDER BY src_id",
                (change.node_id,)) if edge["src_id"] not in completed]
            for group in self.conn.execute(
                "SELECT group_id,kind FROM graph_groups WHERE owner_node_id=? ORDER BY group_id", (change.node_id,)
            ):
                group_members = [item[0] for item in self.conn.execute(
                    "SELECT node_id FROM graph_group_members WHERE group_id=? ORDER BY position", (group["group_id"],)
                )]
                count = sum(member in completed for member in group_members)
                if not group_members or (count != len(group_members) if group["kind"] == "AND" else count == 0):
                    blockers.append(f"{group['kind'].lower()}:{group['group_id']}")
            if blockers:
                raise GraphStoreError("node is dependency-blocked: " + ", ".join(map(str, blockers)))
        payload = json.loads(row["payload_json"])
        if change.to_status in {"COMPLETED", "FAILED"}:
            payload["completion_evidence"] = {"status": change.to_status, "guard": change.guard,
                                               "actor": change.actor, "evidence_refs": list(change.evidence_refs)}
        payload["last_status_transition"] = {"from_status": change.expected_status, "to_status": change.to_status,
                                               "guard": change.guard, "actor": change.actor}
        changed = self.conn.execute(
            "UPDATE research_nodes SET status=?,payload_json=?,revision=revision+1,updated_at=? "
            "WHERE node_id=? AND status=? AND revision=?",
            (change.to_status, _canonical_json(payload), _now(), change.node_id,
             change.expected_status, change.expected_node_revision),
        ).rowcount
        if changed != 1:
            raise GraphStoreError("node status transition lost its revision race")

    def _validate_requires_acyclic(self) -> None:
        adjacency: dict[str, list[str]] = {}
        indegree: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT src_id,dst_id FROM research_edges WHERE edge_type='REQUIRES'"
        ):
            adjacency.setdefault(row["src_id"], []).append(row["dst_id"])
            indegree[row["dst_id"]] = indegree.get(row["dst_id"], 0) + 1
            indegree.setdefault(row["src_id"], indegree.get(row["src_id"], 0))
        queue = [node_id for node_id, count in indegree.items() if count == 0]
        visited = 0
        while queue:
            node_id = queue.pop()
            visited += 1
            for target in adjacency.get(node_id, ()):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(indegree):
            raise GraphStoreError("REQUIRES edges must be acyclic")


class V2CampaignStore(GraphRepository):
    """Schema-v5 adapter for campaigns carrying either preserved v1 or strict v2 specs."""

    def spec(self) -> CampaignSpec | ExplorationCampaignSpecV2 | OptimizationCampaignSpecV2:
        return self.campaign_spec()
