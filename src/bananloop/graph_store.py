from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

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


GraphChange = Annotated[Union[AddNode, AddEdge, AddGroup], Field(discriminator="type")]
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphRepository:
    """Transactional RPDI-02 graph repository over an already migrated v5 database."""

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        foreign_keys_enabled = self.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if foreign_keys_enabled != 1:
            raise GraphStoreError("graph repository requires SQLite foreign keys")
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version != 5:
            raise GraphStoreError(f"graph repository requires schema version 5; found {version}")

    @classmethod
    def open(cls, path: str) -> "GraphRepository":
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            return cls(conn)
        except Exception:
            conn.close()
            raise

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
            self.conn.execute(
                "INSERT INTO research_edges(src_id,dst_id,edge_type,constraint_json) VALUES(?,?,?,?)",
                (change.source_id, change.target_id, change.edge_type, _canonical_json(change.constraint)),
            )
            return
        if isinstance(change, AddGroup):
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
        raise GraphStoreError(f"unsupported graph change {type(change).__name__}")

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
