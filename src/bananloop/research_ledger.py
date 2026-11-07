"""Append-only DEV evidence and deterministic causal knowledge projections.

This module consumes only completed v2 exploration jobs. It rebuilds views from
immutable graph-completion and worker-result records, rather than treating an
agent's research summary as an authoritative database projection.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from .graph_store import GraphRepository
    from .worker_queue import DurableWorkerQueue


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CLAIM_KEY = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
_ARTIFACT_REF = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_INTERVENTION_TYPES = (
    "feature_representation", "hyperparameter", "objective", "data_sampling", "threshold",
    "architecture", "caching", "inference_logic", "test_coverage", "runtime", "data_preprocessing",
)
_ALGORITHMIC_LAYERS = (
    "data", "features", "model", "objective", "training", "inference", "evaluation", "runtime", "tests",
)


def _normalize_intervention_paths(values: tuple[str, ...]) -> tuple[str, ...]:
    paths: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
            raise ValueError("intervention paths must be safe relative POSIX paths")
        value = raw.strip()
        head, sep, locator = value.rpartition(":")
        if sep and head and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.<>-]*|[0-9]+", locator):
            value = head
        path = PurePosixPath(value)
        parts = value.split("/")
        lower = [part.lower() for part in parts]
        protected = {".git", ".bananloop_eval", "validation", "holdout", "evaluator",
                     "evaluation", "secret", "secrets", "credential", "credentials"}
        if (path.is_absolute() or PureWindowsPath(value).drive or path.as_posix() != value
                or not parts or any(part in {"", ".", ".."} for part in parts)
                or any(part in protected or part.startswith(".env")
                       or re.search(r"(evaluator|evaluation|validation|holdout|secret|credential)", part)
                       for part in lower)):
            raise ValueError("intervention path is unsafe or belongs to protected evaluation material")
        paths.add(value)
    if not paths:
        raise ValueError("structural intervention requires at least one affected path")
    return tuple(sorted(paths))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ResearchLedgerError(ValueError):
    """A research record lacks current provenance or crosses an evidence boundary."""


class ResearchFindingV1(BaseModel):
    """A compact, explicitly fallible finding returned by an evidence worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_key: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    statement: str = Field(min_length=1, max_length=2000)
    stance: Literal["SUPPORTS", "REFUTES", "UNCERTAIN"]
    kind: Literal["OBSERVATION", "CAUSAL_INFERENCE"]
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    intervention_type: Literal[
        "feature_representation", "hyperparameter", "objective", "data_sampling", "threshold",
        "architecture", "caching", "inference_logic", "test_coverage", "runtime", "data_preprocessing",
    ] | None = None
    algorithmic_layer: Literal[
        "data", "features", "model", "objective", "training", "inference", "evaluation", "runtime", "tests",
    ] | None = None
    affected_paths: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("statement")
    @classmethod
    def nonblank_statement(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("finding statement must be nonblank")
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def canonical_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 2000 for item in value):
            raise ValueError("finding evidence references must be nonempty and bounded")
        if any(not _ARTIFACT_REF.fullmatch(item) for item in value):
            raise ValueError("research findings must cite content-addressed public artifacts")
        return tuple(sorted(set(value)))

    @field_validator("affected_paths")
    @classmethod
    def canonical_intervention_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_intervention_paths(value)

    @model_validator(mode="after")
    def complete_intervention_signature(self) -> "ResearchFindingV1":
        if bool(self.intervention_type) != bool(self.algorithmic_layer):
            raise ValueError("structural intervention requires both type and algorithmic layer")
        if self.intervention_type and not self.affected_paths:
            raise ValueError("structural intervention requires affected paths")
        if not self.intervention_type and self.affected_paths:
            raise ValueError("affected paths require a typed structural intervention")
        return self


class ResearchEvidenceV1(BaseModel):
    """One immutable public-DEV finding with exact source and protocol identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1, max_length=200)
    node_revision: int = Field(ge=0)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=200)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_job_id: str = Field(min_length=1, max_length=120)
    source_fence: int = Field(ge=1)
    source_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_cursor: int = Field(ge=1)
    claim_key: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    statement: str = Field(min_length=1, max_length=2000)
    stance: Literal["SUPPORTS", "REFUTES", "UNCERTAIN"]
    kind: Literal["OBSERVATION", "CAUSAL_INFERENCE"]
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    confidence: float = Field(ge=0.0, le=1.0)
    intervention_type: Literal[
        "feature_representation", "hyperparameter", "objective", "data_sampling", "threshold",
        "architecture", "caching", "inference_logic", "test_coverage", "runtime", "data_preprocessing",
    ] | None = None
    algorithmic_layer: Literal[
        "data", "features", "model", "objective", "training", "inference", "evaluation", "runtime", "tests",
    ] | None = None
    affected_paths: tuple[str, ...] = ()
    classification: Literal["PUBLIC_DEV"] = "PUBLIC_DEV"
    verification_status: Literal["UNVERIFIED_ROLE_FINDING"] = "UNVERIFIED_ROLE_FINDING"
    causal_status: Literal["NOT_CAUSAL", "UNVERIFIED_INFERENCE"]

    @field_validator("evidence_refs")
    @classmethod
    def canonical_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return ResearchFindingV1.canonical_evidence_refs(value)

    @field_validator("affected_paths")
    @classmethod
    def canonical_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_intervention_paths(value)

    @model_validator(mode="after")
    def content_address_matches(self) -> "ResearchEvidenceV1":
        if bool(self.intervention_type) != bool(self.algorithmic_layer):
            raise ValueError("structural intervention requires both type and algorithmic layer")
        if self.intervention_type and not self.affected_paths:
            raise ValueError("structural intervention requires affected paths")
        if not self.intervention_type and self.affected_paths:
            raise ValueError("affected paths require a typed structural intervention")
        if self.kind == "CAUSAL_INFERENCE" and self.causal_status != "UNVERIFIED_INFERENCE":
            raise ValueError("agent causal claims must remain explicitly unverified")
        if self.kind == "OBSERVATION" and self.causal_status != "NOT_CAUSAL":
            raise ValueError("observations cannot be promoted to causal claims")
        if self.evidence_id != _digest(self.content_payload()):
            raise ValueError("evidence ID does not match its immutable record content")
        return self

    def content_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("evidence_id", None)
        return data


class ContradictionRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contradiction_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_key: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    supporting_evidence_ids: tuple[str, ...] = Field(min_length=1)
    refuting_evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def verify_pair_digest(self) -> "ContradictionRecordV1":
        if self.contradiction_id != _digest({
            "claim_key": self.claim_key,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "refuting_evidence_ids": self.refuting_evidence_ids,
        }):
            raise ValueError("contradiction ID does not match its evidence pair")
        return self


class CausalMemoryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_key: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    statements: tuple[str, ...] = Field(min_length=1)
    status: Literal["UNVERIFIED", "CONTRADICTED"]
    confidence_by_evidence: tuple[float, ...] = ()


class StructuralInterventionMemoryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signature: str = Field(pattern=r"^intervention:[0-9a-f]{20}$")
    intervention_type: Literal[
        "feature_representation", "hyperparameter", "objective", "data_sampling", "threshold",
        "architecture", "caching", "inference_logic", "test_coverage", "runtime", "data_preprocessing",
    ]
    algorithmic_layer: Literal[
        "data", "features", "model", "objective", "training", "inference", "evaluation", "runtime", "tests",
    ]
    affected_paths: tuple[str, ...] = Field(min_length=1, max_length=16)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    support_count: int = Field(ge=0)
    refutation_count: int = Field(ge=0)
    status: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def signature_matches(self) -> "StructuralInterventionMemoryV1":
        expected = "intervention:" + _digest({"intervention_type": self.intervention_type,
            "algorithmic_layer": self.algorithmic_layer, "affected_paths": self.affected_paths})[:20]
        if self.signature != expected:
            raise ValueError("structural intervention signature does not match its canonical fields")
        return self


class ResearchProjectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_ids: tuple[str, ...] = Field(min_length=1)
    source_snapshot_id: str
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[ResearchEvidenceV1, ...] = ()
    contradictions: tuple[ContradictionRecordV1, ...] = ()
    causal_memory: tuple[CausalMemoryV1, ...] = ()
    structural_memory: tuple[StructuralInterventionMemoryV1, ...] = ()
    projection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_projection_digest(self) -> "ResearchProjectionV1":
        if self.projection_digest != _digest(self.content_payload()):
            raise ValueError("research projection digest does not match its canonical content")
        if tuple(sorted(set(self.campaign_ids))) != self.campaign_ids:
            raise ValueError("projection campaign IDs must be unique and sorted")
        return self

    def content_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("projection_digest", None)
        return data


class KnowledgePackageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    projection: ResearchProjectionV1
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_package_digest(self) -> "KnowledgePackageV1":
        if self.package_digest != _digest({"schema_version": self.schema_version,
                                          "projection": self.projection.model_dump(mode="json")}):
            raise ValueError("knowledge package digest does not match its projection")
        return self


class ResearchLedger:
    """Append-only evidence records backed by a migrated v5 campaign database."""

    def __init__(self, connection: sqlite3.Connection, *, read_only: bool = False):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version != 5:
            raise ResearchLedgerError(f"research ledger requires SQLite schema v5; found v{version}")
        if read_only:
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_evidence'"
            ).fetchone()
            self._available = exists is not None
            if self._available:
                columns = {row[1] for row in self.conn.execute("PRAGMA table_info(research_evidence)")}
                expected = {
                    "evidence_id", "campaign_id", "node_id", "node_revision", "spec_hash",
                    "source_snapshot_id", "protocol_digest", "source_job_id", "source_fence",
                    "source_request_hash", "output_digest", "event_cursor", "claim_key", "statement",
                    "stance", "kind", "evidence_refs_json", "confidence", "intervention_type",
                    "algorithmic_layer", "affected_paths_json", "classification",
                    "verification_status", "causal_status",
                }
                if columns != expected:
                    raise ResearchLedgerError("research ledger schema is incomplete")
            return
        self._available = True
        self.conn.execute("""CREATE TABLE IF NOT EXISTS research_evidence (
            evidence_id TEXT PRIMARY KEY CHECK(length(evidence_id)=64),
            campaign_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            node_revision INTEGER NOT NULL CHECK(node_revision>=0),
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            source_snapshot_id TEXT NOT NULL,
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            source_job_id TEXT NOT NULL,
            source_fence INTEGER NOT NULL CHECK(source_fence>=1),
            source_request_hash TEXT NOT NULL CHECK(length(source_request_hash)=64),
            output_digest TEXT NOT NULL CHECK(length(output_digest)=64),
            event_cursor INTEGER NOT NULL CHECK(event_cursor>=1),
            claim_key TEXT NOT NULL,
            statement TEXT NOT NULL,
            stance TEXT NOT NULL CHECK(stance IN ('SUPPORTS','REFUTES','UNCERTAIN')),
            kind TEXT NOT NULL CHECK(kind IN ('OBSERVATION','CAUSAL_INFERENCE')),
            evidence_refs_json TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence>=0.0 AND confidence<=1.0),
            intervention_type TEXT NULL,
            algorithmic_layer TEXT NULL,
            affected_paths_json TEXT NOT NULL,
            classification TEXT NOT NULL CHECK(classification='PUBLIC_DEV'),
            verification_status TEXT NOT NULL CHECK(verification_status='UNVERIFIED_ROLE_FINDING'),
            causal_status TEXT NOT NULL CHECK(causal_status IN ('NOT_CAUSAL','UNVERIFIED_INFERENCE'))
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS research_evidence_scope_idx "
                          "ON research_evidence(campaign_id,source_snapshot_id,protocol_digest,claim_key)")
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS research_evidence_no_update
            BEFORE UPDATE ON research_evidence BEGIN SELECT RAISE(ABORT,'research evidence is immutable'); END""")
        self.conn.execute("""CREATE TRIGGER IF NOT EXISTS research_evidence_no_delete
            BEFORE DELETE ON research_evidence BEGIN SELECT RAISE(ABORT,'research evidence is immutable'); END""")

    @staticmethod
    def protocol_digest_for_binding(binding: dict[str, Any]) -> str:
        keys = (
            "role_registry_digest", "team_manifest_digest", "role_id", "adapter", "provider",
            "model", "model_version", "prompt_digest", "capability_policy_digest",
            "context_policy_digest",
        )
        if any(not isinstance(binding.get(key), str) or not binding[key] for key in keys):
            raise ResearchLedgerError("role binding is missing frozen protocol identity")
        if not _SHA256.fullmatch(binding["role_registry_digest"]) or not _SHA256.fullmatch(binding["team_manifest_digest"]):
            raise ResearchLedgerError("role binding contains an invalid protocol digest")
        return _digest({"protocol_version": 1, **{key: binding[key] for key in keys}})

    def ingest_completed_job(
        self,
        graph: "GraphRepository",
        queue: "DurableWorkerQueue",
        job_id: str,
        *,
        expected_source_snapshot_id: str | None = None,
        expected_protocol_digest: str | None = None,
    ) -> tuple[str, ...]:
        """Project only a graph-committed, queue-completed and fully pinned job."""
        job = queue.get_job(job_id)
        if job["state"] != "COMPLETED":
            raise ResearchLedgerError("research evidence requires a completed durable worker job")
        durable = queue.get_result(job_id)
        request = job["request"]
        result = durable.get("result")
        if not isinstance(result, dict):
            raise ResearchLedgerError("completed worker result is not an evidence object")
        from .exploration import EvidenceOnlyOutputV2
        from .contracts import ExplorationCampaignSpecV2

        try:
            output = EvidenceOnlyOutputV2.model_validate(result)
        except Exception as exc:
            raise ResearchLedgerError("completed job output does not satisfy the evidence contract") from exc
        spec = graph.campaign_spec()
        if not isinstance(spec, ExplorationCampaignSpecV2) or spec.approved_hash() != request.get("spec_hash"):
            raise ResearchLedgerError("completed job does not match the frozen exploration campaign")
        source_snapshot_id = request.get("base_commit")
        if not isinstance(source_snapshot_id, str) or not _GIT_OID.fullmatch(source_snapshot_id):
            raise ResearchLedgerError("worker request has no immutable source snapshot")
        if expected_source_snapshot_id is not None and source_snapshot_id != expected_source_snapshot_id:
            raise ResearchLedgerError("evidence source snapshot is stale or does not match the requested scope")
        binding = request.get("role_binding")
        if not isinstance(binding, dict) or binding.get("spec_hash") != spec.approved_hash():
            raise ResearchLedgerError("worker request has no matching pinned role binding")
        protocol_digest = self.protocol_digest_for_binding(binding)
        if expected_protocol_digest is not None and protocol_digest != expected_protocol_digest:
            raise ResearchLedgerError("evidence belongs to a different frozen role protocol")
        if (request.get("campaign_id") != job["campaign_id"]
                or request.get("role_registry_digest") != binding.get("role_registry_digest")
                or request.get("team_manifest_digest") != binding.get("team_manifest_digest")
                or request.get("capability_manifest", {}).get("base_commit") != source_snapshot_id
                or int(durable["fence"]) != int(job["fence"])
                or durable["output_digest"] != _digest_json(result)):
            raise ResearchLedgerError("worker result does not match its immutable role/source receipt")
        try:
            from .roles import RoleContextV1, TeamManifestAuthority

            team_authority = TeamManifestAuthority(queue.path)
            try:
                team_manifest = team_authority.load(
                    job["campaign_id"], spec_hash=spec.approved_hash(),
                    registry_digest=spec.role_registry_digest,
                )
            finally:
                team_authority.close()
            if team_manifest.manifest_digest != request.get("team_manifest_digest"):
                raise ResearchLedgerError("worker request is not bound to the pinned team manifest")
            member = team_manifest.member(binding["role_instance_id"])
            role_context = RoleContextV1.model_validate(request["role_context"])
            if (role_context.role_id != member.role.role_id
                    or not set(role_context.model_fields_set) <= set(member.role.context_fields) | {"role_id"}):
                raise ResearchLedgerError("worker role context exceeds its pinned context allowlist")
            expected_binding = team_manifest.job_binding(
                member.instance_id, action_id=binding["action_id"], context_digest=role_context.digest(),
            )
            if expected_binding != binding:
                raise ResearchLedgerError("worker role binding differs from its pinned manifest member")
        except ResearchLedgerError:
            raise
        except Exception as exc:
            raise ResearchLedgerError("worker role provenance cannot be confirmed from the pinned manifest") from exc
        if output.node_id != request.get("node_id"):
            raise ResearchLedgerError("worker result node does not match its immutable request")

        completion_rows = []
        for row in graph.conn.execute(
            "SELECT seq,payload_json FROM events WHERE event_type='EXPLORATION_COMPLETED' ORDER BY seq"
        ):
            payload = json.loads(row["payload_json"])
            if payload.get("job_id") == job_id:
                completion_rows.append((int(row["seq"]), payload))
        if len(completion_rows) != 1:
            raise ResearchLedgerError("job must have exactly one durable graph completion event")
        event_cursor, completion = completion_rows[0]
        if (completion.get("node_id") != output.node_id
                or completion.get("worker_fence") != int(durable["fence"])
                or completion.get("source_request_hash") != job["request_hash"]
                or completion.get("output_digest") != durable["output_digest"]
                or completion.get("spec_hash") != spec.approved_hash()):
            raise ResearchLedgerError("graph completion provenance does not match the durable worker result")
        worker_artifact_digests = tuple(sorted(item.sha256 for item in output.artifacts))
        event_artifact_digests = completion.get("artifact_digests")
        if (not isinstance(event_artifact_digests, (list, tuple))
                or any(not isinstance(item, str) for item in event_artifact_digests)
                or tuple(event_artifact_digests) != worker_artifact_digests):
            raise ResearchLedgerError("graph completion artifact digests differ from the durable worker output")
        artifact_refs = {f"artifact://sha256/{digest}" for digest in worker_artifact_digests}
        worker_evidence_refs = tuple(sorted(set(output.evidence_refs) | artifact_refs))
        event_evidence_refs = completion.get("evidence_refs")
        if (not isinstance(event_evidence_refs, (list, tuple))
                or any(not isinstance(item, str) for item in event_evidence_refs)
                or tuple(event_evidence_refs) != worker_evidence_refs):
            raise ResearchLedgerError("graph completion evidence refs differ from the durable worker output")
        if any(not set(finding.evidence_refs) <= artifact_refs for finding in output.research_findings):
            raise ResearchLedgerError("research finding must cite verified published artifact digests")
        if len({finding.claim_key for finding in output.research_findings}) != len(output.research_findings):
            raise ResearchLedgerError("one worker result cannot repeat a claim key")
        records: list[ResearchEvidenceV1] = []
        for finding in output.research_findings:
            causal_status = "UNVERIFIED_INFERENCE" if finding.kind == "CAUSAL_INFERENCE" else "NOT_CAUSAL"
            payload = {
                "campaign_id": job["campaign_id"], "node_id": output.node_id,
                "node_revision": int(request["node_revision"]), "spec_hash": spec.approved_hash(),
                "source_snapshot_id": source_snapshot_id, "protocol_digest": protocol_digest,
                "source_job_id": job_id, "source_fence": int(durable["fence"]),
                "source_request_hash": job["request_hash"], "output_digest": durable["output_digest"],
                "event_cursor": event_cursor, "claim_key": finding.claim_key,
                "statement": finding.statement, "stance": finding.stance, "kind": finding.kind,
                "evidence_refs": finding.evidence_refs, "confidence": finding.confidence,
                "intervention_type": finding.intervention_type,
                "algorithmic_layer": finding.algorithmic_layer,
                "affected_paths": finding.affected_paths,
                "classification": "PUBLIC_DEV", "verification_status": "UNVERIFIED_ROLE_FINDING",
                "causal_status": causal_status,
            }
            records.append(ResearchEvidenceV1(evidence_id=_digest(payload), **payload))

        inserted: list[str] = []
        self.conn.execute("SAVEPOINT rpdi10_evidence_append")
        try:
            for record in records:
                self._validate_current_source(graph, record, expected_protocol_digest=protocol_digest,
                                              expected_source_snapshot_id=source_snapshot_id)
                values = self._row_values(record)
                self.conn.execute(
                    "INSERT OR IGNORE INTO research_evidence VALUES(" + ",".join("?" for _ in values) + ")",
                    values,
                )
                stored = self.conn.execute(
                    "SELECT * FROM research_evidence WHERE evidence_id=?", (record.evidence_id,)
                ).fetchone()
                if stored is None or self._record_from_row(stored) != record:
                    raise ResearchLedgerError("content-addressed evidence ID already names different bytes")
                inserted.append(record.evidence_id)
            self.conn.execute("RELEASE SAVEPOINT rpdi10_evidence_append")
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT rpdi10_evidence_append")
            self.conn.execute("RELEASE SAVEPOINT rpdi10_evidence_append")
            raise
        return tuple(sorted(inserted))

    def _validate_current_source(
        self,
        graph: "GraphRepository",
        record: ResearchEvidenceV1,
        *,
        expected_protocol_digest: str,
        expected_source_snapshot_id: str,
    ) -> None:
        if record.classification != "PUBLIC_DEV":
            raise ResearchLedgerError("only public DEV evidence may enter the research ledger")
        if record.protocol_digest != expected_protocol_digest:
            raise ResearchLedgerError("evidence protocol does not match the frozen projection scope")
        if record.source_snapshot_id != expected_source_snapshot_id:
            raise ResearchLedgerError("evidence source snapshot does not match the frozen projection scope")
        if record.spec_hash != graph.campaign_spec().approved_hash():
            raise ResearchLedgerError("evidence campaign spec hash is stale")
        event = graph.conn.execute(
            "SELECT payload_json FROM events WHERE seq=? AND event_type='EXPLORATION_COMPLETED'",
            (record.event_cursor,),
        ).fetchone()
        if event is None:
            raise ResearchLedgerError("evidence source event is missing or has a different type")
        payload = json.loads(event["payload_json"])
        artifact_refs = {f"artifact://sha256/{digest}" for digest in payload.get("artifact_digests", ())}
        if (payload.get("job_id") != record.source_job_id
                or payload.get("worker_fence") != record.source_fence
                or payload.get("source_request_hash") != record.source_request_hash
                or payload.get("output_digest") != record.output_digest
                or not set(record.evidence_refs) <= artifact_refs):
            raise ResearchLedgerError("evidence does not match the immutable graph completion")

    @staticmethod
    def _row_values(record: ResearchEvidenceV1) -> tuple[Any, ...]:
        return (
            record.evidence_id, record.campaign_id, record.node_id, record.node_revision,
            record.spec_hash, record.source_snapshot_id, record.protocol_digest,
            record.source_job_id, record.source_fence, record.source_request_hash,
            record.output_digest, record.event_cursor, record.claim_key, record.statement,
            record.stance, record.kind, _canonical(record.evidence_refs), record.confidence,
            record.intervention_type, record.algorithmic_layer, _canonical(record.affected_paths),
            record.classification, record.verification_status, record.causal_status,
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ResearchEvidenceV1:
        payload = dict(row)
        payload["evidence_refs"] = tuple(json.loads(payload.pop("evidence_refs_json")))
        payload["affected_paths"] = tuple(json.loads(payload.pop("affected_paths_json")))
        return ResearchEvidenceV1.model_validate(payload)

    def records(
        self,
        *,
        campaign_ids: tuple[str, ...] | list[str],
        source_snapshot_id: str,
        protocol_digest: str,
        graph: "GraphRepository",
        queue: "DurableWorkerQueue",
    ) -> tuple[ResearchEvidenceV1, ...]:
        campaigns = tuple(sorted(set(campaign_ids)))
        if not campaigns or any(not item for item in campaigns):
            raise ResearchLedgerError("knowledge scope requires one or more campaign IDs")
        if not self._available:
            return ()
        if not source_snapshot_id or not _GIT_OID.fullmatch(source_snapshot_id):
            raise ResearchLedgerError("knowledge scope requires a full source snapshot object ID")
        if not _SHA256.fullmatch(protocol_digest):
            raise ResearchLedgerError("knowledge scope requires a frozen protocol SHA-256")
        placeholders = ",".join("?" for _ in campaigns)
        rows = self.conn.execute(
            f"SELECT * FROM research_evidence WHERE campaign_id IN ({placeholders}) "
            "AND source_snapshot_id=? AND protocol_digest=? AND classification='PUBLIC_DEV' "
            "ORDER BY evidence_id",
            (*campaigns, source_snapshot_id, protocol_digest),
        ).fetchall()
        records = tuple(self._record_from_row(row) for row in rows)
        grouped: dict[str, list[ResearchEvidenceV1]] = {}
        for record in records:
            grouped.setdefault(record.source_job_id, []).append(record)
        for job_id, stored_records in grouped.items():
            # The persisted table is only an append-only cache; it is not an
            # authority for worker-authored claims, even when a row self-hashes.
            scratch_conn = sqlite3.connect(":memory:")
            try:
                scratch_conn.execute("PRAGMA user_version=5")
                scratch = ResearchLedger(scratch_conn)
                scratch.ingest_completed_job(
                    graph, queue, job_id,
                    expected_source_snapshot_id=source_snapshot_id,
                    expected_protocol_digest=protocol_digest,
                )
                expected_rows = scratch_conn.execute(
                    "SELECT * FROM research_evidence WHERE source_job_id=? ORDER BY evidence_id",
                    (job_id,),
                ).fetchall()
                expected_records = tuple(scratch._record_from_row(row) for row in expected_rows)
            except ResearchLedgerError:
                raise
            except Exception as exc:
                raise ResearchLedgerError(
                    "ledger source cannot be revalidated against durable worker evidence"
                ) from exc
            finally:
                scratch_conn.close()
            if tuple(sorted(stored_records, key=lambda item: item.evidence_id)) != expected_records:
                raise ResearchLedgerError(
                    "ledger rows differ from findings derived from the durable worker result"
                )
        return records

    def list_scopes(
        self, *, campaign_id: str, limit: int = 50,
        after: tuple[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Read a bounded list of public knowledge scopes without schema changes."""
        if not 1 <= limit <= 50:
            raise ResearchLedgerError("knowledge scope page limit must be between 1 and 50")
        if not self._available:
            return [], False
        predicate = "AND (source_snapshot_id,protocol_digest)>(?,?)" if after else ""
        params: tuple[Any, ...] = (campaign_id, *(after or ()), limit + 1)
        rows = self.conn.execute(
            "SELECT source_snapshot_id,protocol_digest,MAX(event_cursor) AS event_cursor "
            "FROM research_evidence WHERE campaign_id=? AND classification='PUBLIC_DEV' "
            f"{predicate} GROUP BY source_snapshot_id,protocol_digest "
            "ORDER BY source_snapshot_id,protocol_digest LIMIT ?", params,
        ).fetchall()
        more = len(rows) > limit
        return [dict(row) for row in rows[:limit]], more

    def project(
        self,
        *,
        campaign_ids: tuple[str, ...] | list[str],
        source_snapshot_id: str,
        protocol_digest: str,
        graph: "GraphRepository",
        queue: "DurableWorkerQueue",
    ) -> ResearchProjectionV1:
        campaigns = tuple(sorted(set(campaign_ids)))
        evidence = self.records(campaign_ids=campaigns, source_snapshot_id=source_snapshot_id,
                                protocol_digest=protocol_digest, graph=graph, queue=queue)
        contradictions = _contradictions(evidence)
        causal_memory = _causal_memory(evidence, contradictions)
        structural_memory = _structural_memory(evidence)
        payload = {
            "campaign_ids": campaigns, "source_snapshot_id": source_snapshot_id,
            "protocol_digest": protocol_digest,
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "contradictions": [item.model_dump(mode="json") for item in contradictions],
            "causal_memory": [item.model_dump(mode="json") for item in causal_memory],
            "structural_memory": [item.model_dump(mode="json") for item in structural_memory],
        }
        return ResearchProjectionV1(projection_digest=_digest(payload), **payload)

    def export_knowledge(
        self,
        *,
        campaign_ids: tuple[str, ...] | list[str],
        source_snapshot_id: str,
        protocol_digest: str,
        graph: "GraphRepository",
        queue: "DurableWorkerQueue",
    ) -> KnowledgePackageV1:
        """Export reproducible public DEV knowledge; hidden classes are unrepresentable."""
        projection = self.project(campaign_ids=campaign_ids, source_snapshot_id=source_snapshot_id,
                                  protocol_digest=protocol_digest, graph=graph, queue=queue)
        payload = {"schema_version": 1, "projection": projection.model_dump(mode="json")}
        return KnowledgePackageV1(projection=projection, package_digest=_digest(payload))


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contradictions(evidence: tuple[ResearchEvidenceV1, ...]) -> tuple[ContradictionRecordV1, ...]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for item in evidence:
        if item.stance in {"SUPPORTS", "REFUTES"}:
            grouped.setdefault(item.claim_key, {}).setdefault(item.stance, []).append(item.evidence_id)
    records: list[ContradictionRecordV1] = []
    for claim_key, stances in sorted(grouped.items()):
        supporting = tuple(sorted(stances.get("SUPPORTS", ())))
        refuting = tuple(sorted(stances.get("REFUTES", ())))
        if not supporting or not refuting:
            continue
        payload = {"claim_key": claim_key, "supporting_evidence_ids": supporting,
                   "refuting_evidence_ids": refuting}
        records.append(ContradictionRecordV1(contradiction_id=_digest(payload), **payload))
    return tuple(records)


def _causal_memory(
    evidence: tuple[ResearchEvidenceV1, ...],
    contradictions: tuple[ContradictionRecordV1, ...],
) -> tuple[CausalMemoryV1, ...]:
    contradicted = {item.claim_key for item in contradictions}
    grouped: dict[str, list[ResearchEvidenceV1]] = {}
    for item in evidence:
        if item.kind == "CAUSAL_INFERENCE":
            grouped.setdefault(item.claim_key, []).append(item)
    return tuple(CausalMemoryV1(
        claim_key=claim_key,
        evidence_ids=tuple(sorted(item.evidence_id for item in records)),
        statements=tuple(sorted({item.statement for item in records})),
        status="CONTRADICTED" if claim_key in contradicted else "UNVERIFIED",
        confidence_by_evidence=tuple(item.confidence for item in sorted(records, key=lambda item: item.evidence_id)),
    ) for claim_key, records in sorted(grouped.items()))


def _structural_memory(
    evidence: tuple[ResearchEvidenceV1, ...],
) -> tuple[StructuralInterventionMemoryV1, ...]:
    grouped: dict[tuple[str, str, tuple[str, ...]], list[ResearchEvidenceV1]] = {}
    for item in evidence:
        if item.intervention_type and item.algorithmic_layer:
            key = (item.intervention_type, item.algorithmic_layer, item.affected_paths)
            grouped.setdefault(key, []).append(item)
    records: list[StructuralInterventionMemoryV1] = []
    for (intervention_type, algorithmic_layer, affected_paths), items in sorted(grouped.items()):
        signature_payload = {"intervention_type": intervention_type,
                             "algorithmic_layer": algorithmic_layer,
                             "affected_paths": affected_paths}
        records.append(StructuralInterventionMemoryV1(
            signature="intervention:" + _digest(signature_payload)[:20],
            intervention_type=intervention_type, algorithmic_layer=algorithmic_layer,
            affected_paths=affected_paths,
            evidence_ids=tuple(sorted(item.evidence_id for item in items)),
            support_count=sum(item.stance == "SUPPORTS" for item in items),
            refutation_count=sum(item.stance == "REFUTES" for item in items),
        ))
    return tuple(records)
