"""Trusted Core storage and validation for v2 evaluator protocol freeze receipts.

Worker prose is never a freeze record. Proposals and verifier decisions are
linked back to durable, fenced queue jobs and a campaign's pinned role
manifest before an immutable receipt can authorize a graph phase transition.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ARTIFACT_REF = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_REQUIRED_VERIFIER_CHECKS = frozenset({
    "protocol_identity", "evaluator_bundle_review", "metric_contract", "tier_manifests",
    "frozen_case_scope", "no_hidden_data_dependency", "fair_comparison", "error_semantics",
})
_INTEGRATION_CHECK_NAMES = frozenset({
    "candidate_identity", "protocol_identity", "evaluation_receipt",
    "budget_receipt", "artifact_integrity", "regression_policy",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_completed_role_job(queue: Any, job_id: str, *, expected_role_id: str):
    """Return a durable job only after binding it to its pinned manifest member."""
    try:
        job = queue.get_job(job_id)
        durable = queue.get_result(job_id)
    except Exception as exc:
        raise EvaluatorVerificationError("role job has no durable completion receipt") from exc
    request = job.get("request")
    result = durable.get("result")
    if job.get("state") != "COMPLETED" or not isinstance(request, dict) or not isinstance(result, dict):
        raise EvaluatorVerificationError("protocol workflow requires a completed durable role job")
    if (int(durable.get("fence", -1)) != int(job.get("fence", -2))
            or durable.get("output_digest") != _digest(result)
            or not _SHA256.fullmatch(str(job.get("request_hash", "")))):
        raise EvaluatorVerificationError("role result receipt does not match its current job fence or request")
    binding = request.get("role_binding")
    if not isinstance(binding, dict):
        raise EvaluatorVerificationError("role job has no pinned binding")
    try:
        from .roles import RoleContextV1, TeamManifestAuthority

        authority = TeamManifestAuthority(queue.path)
        try:
            manifest = authority.load(
                job["campaign_id"], spec_hash=binding["spec_hash"],
                registry_digest=binding["role_registry_digest"],
            )
        finally:
            authority.close()
        member = manifest.member(binding["role_instance_id"])
        context = RoleContextV1.model_validate(request["role_context"])
        if (member.role.role_id != expected_role_id or context.role_id != expected_role_id
                or manifest.manifest_digest != binding.get("team_manifest_digest")
                or request.get("team_manifest_digest") != manifest.manifest_digest
                or request.get("role_registry_digest") != manifest.registry_digest
                or not set(context.model_fields_set) <= set(member.role.context_fields) | {"role_id"}):
            raise EvaluatorVerificationError("role job identity or context is outside its pinned manifest")
        expected_binding = manifest.job_binding(
            member.instance_id, action_id=binding["action_id"], context_digest=context.digest(),
        )
        if expected_binding != binding:
            raise EvaluatorVerificationError("role job binding differs from its pinned manifest member")
    except EvaluatorVerificationError:
        raise
    except Exception as exc:
        raise EvaluatorVerificationError("role binding cannot be confirmed from the pinned manifest") from exc
    return job, durable, request, result, binding, member


class EvaluatorVerificationError(ValueError):
    """A proposal or verifier result lacks valid immutable provenance."""


class EvaluatorTierManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Literal["dev", "validation", "holdout"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1, le=1_000_000)


class EvaluatorProtocolV1(BaseModel):
    """Content-addressed evaluator contract; case bytes and labels stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    protocol_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,119}$")
    version: int = Field(ge=1)
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    evaluator_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entrypoint: tuple[str, ...] = Field(min_length=1, max_length=64)
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    metric_field: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,119}$")
    direction: Literal["MAXIMIZE", "MINIMIZE"]
    aggregation: Literal["MEAN", "MEDIAN", "MIN", "MAX"]
    tiers: tuple[EvaluatorTierManifestV1, ...] = Field(min_length=1, max_length=3)
    repeatability_runs: int = Field(default=2, ge=2, le=10)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)

    @field_validator("source_snapshot_id")
    @classmethod
    def full_git_snapshot(cls, value: str) -> str:
        if not _GIT_OID.fullmatch(value):
            raise ValueError("protocol source snapshot must be a full Git object ID")
        return value

    @field_validator("entrypoint")
    @classmethod
    def safe_entrypoint(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part or len(part) > 2048 or "\x00" in part for part in value):
            raise ValueError("evaluator entrypoint must be a bounded argv vector")
        if any(part in {"sh", "bash", "cmd", "powershell", "pwsh"} for part in value[:1]):
            raise ValueError("evaluator entrypoint cannot invoke a command shell")
        if any(part.startswith("--eval") or part.startswith("-c") for part in value):
            raise ValueError("evaluator entrypoint cannot evaluate inline command text")
        return value

    @field_validator("tiers")
    @classmethod
    def ordered_unique_tiers(
        cls, value: tuple[EvaluatorTierManifestV1, ...],
    ) -> tuple[EvaluatorTierManifestV1, ...]:
        names = tuple(item.tier for item in value)
        if len(set(names)) != len(names) or names != tuple(sorted(names)):
            raise ValueError("evaluator tiers must be unique and canonically ordered")
        if "dev" not in names:
            raise ValueError("an evaluator protocol requires a DEV tier")
        return value

    def content_digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class EvaluatorProtocolDraftV1(BaseModel):
    """Worker-authored metric/evaluator proposal; not an approval or freeze."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: EvaluatorProtocolV1
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("evidence_refs")
    @classmethod
    def canonical_artifact_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _ARTIFACT_REF.fullmatch(item) for item in value):
            raise ValueError("protocol draft evidence must cite published content-addressed artifacts")
        return tuple(sorted(set(value)))


class MetricDiscoveryProposalV1(BaseModel):
    """Untrusted protocol proposal tied to one completed researcher job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    source_node_id: str = Field(min_length=1, max_length=200)
    source_job_id: str = Field(min_length=1, max_length=120)
    source_fence: int = Field(ge=1)
    source_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_cursor: int = Field(ge=1)
    proposer_role_id: str = Field(min_length=1, max_length=120)
    proposer_role_instance_id: str = Field(min_length=1, max_length=200)
    proposer_binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol: EvaluatorProtocolV1
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source_snapshot_id")
    @classmethod
    def full_snapshot(cls, value: str) -> str:
        if not _GIT_OID.fullmatch(value):
            raise ValueError("proposal source snapshot must be a full Git object ID")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def verified_artifact_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _ARTIFACT_REF.fullmatch(item) for item in value):
            raise ValueError("protocol proposals must cite content-addressed published artifacts")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def digest_matches(self) -> "MetricDiscoveryProposalV1":
        if self.protocol.source_snapshot_id != self.source_snapshot_id:
            raise ValueError("proposal and evaluator protocol source snapshots differ")
        if self.proposal_digest != _digest(self.content_payload()):
            raise ValueError("protocol proposal digest does not match its canonical content")
        return self

    def content_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("proposal_digest", None)
        return data


class ProtocolVerifierOutputV1(BaseModel):
    """Strict result schema for a pinned independent protocol-verifier role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["APPROVE", "REJECT"]
    checks: tuple[Literal[
        "protocol_identity", "evaluator_bundle_review", "metric_contract", "tier_manifests",
        "frozen_case_scope", "no_hidden_data_dependency", "fair_comparison", "error_semantics",
    ], ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=4000)
    public_diagnostics: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("checks")
    @classmethod
    def unique_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("verifier checks must be unique")
        return tuple(sorted(value))

    @field_validator("rationale", "public_diagnostics")
    @classmethod
    def nonblank_public_text(cls, value):
        values = (value,) if isinstance(value, str) else value
        if any(not item.strip() for item in values):
            raise ValueError("verifier text fields must be nonblank")
        return value.strip() if isinstance(value, str) else tuple(item.strip() for item in values)

    @model_validator(mode="after")
    def approved_checks_complete(self) -> "ProtocolVerifierOutputV1":
        if self.decision == "APPROVE" and set(self.checks) != _REQUIRED_VERIFIER_CHECKS:
            raise ValueError("approval requires every independent protocol verification check")
        return self


class VerifierReceiptV1(BaseModel):
    """Append-only proof that a distinct pinned verifier completed a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    protocol_id: str = Field(min_length=1, max_length=120)
    protocol_version: int = Field(ge=1)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposer_job_id: str = Field(min_length=1, max_length=120)
    proposer_fence: int = Field(ge=1)
    proposer_role_instance_id: str = Field(min_length=1, max_length=200)
    verifier_job_id: str = Field(min_length=1, max_length=120)
    verifier_fence: int = Field(ge=1)
    verifier_role_id: str = Field(min_length=1, max_length=120)
    verifier_role_instance_id: str = Field(min_length=1, max_length=200)
    verifier_binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["APPROVE", "REJECT"]
    checks: tuple[str, ...]
    approval_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def identity_and_approval(self) -> "VerifierReceiptV1":
        if self.proposer_role_instance_id == self.verifier_role_instance_id:
            raise ValueError("a protocol author cannot verify their own proposal")
        payload = self.content_payload()
        if self.receipt_digest != _digest(payload):
            raise ValueError("verifier receipt digest does not match its canonical content")
        expected_approval = _digest({"receipt_digest": self.receipt_digest,
                                     "protocol_digest": self.protocol_digest}) if self.decision == "APPROVE" else None
        if self.approval_digest != expected_approval:
            raise ValueError("approval digest does not match an approved verifier receipt")
        return self

    def content_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("receipt_digest", None)
        data.pop("approval_digest", None)
        return data


class IntegrationVerifierOutputV1(BaseModel):
    """Typed integration result tied to the candidate and frozen evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    candidate_snapshot_id: str = Field(min_length=40, max_length=64)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["PASS", "FAIL"]
    checks: tuple[Literal[
        "candidate_identity", "protocol_identity", "evaluation_receipt",
        "budget_receipt", "artifact_integrity", "regression_policy",
    ], ...] = Field(min_length=1, max_length=6)
    diagnostics: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("candidate_snapshot_id")
    @classmethod
    def full_candidate_snapshot(cls, value: str) -> str:
        if not _GIT_OID.fullmatch(value):
            raise ValueError("candidate snapshot must be a full Git object ID")
        return value

    @field_validator("checks")
    @classmethod
    def canonical_unique_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("integration verifier checks must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def complete_pass_checks(self) -> "IntegrationVerifierOutputV1":
        if self.decision == "PASS" and set(self.checks) != {
            "candidate_identity", "protocol_identity", "evaluation_receipt",
            "budget_receipt", "artifact_integrity", "regression_policy",
        }:
            raise ValueError("verified pass requires every integration check")
        return self


class IntegrationCoreEvidenceV1(BaseModel):
    """Deterministic Core proof required before an integration role can PASS."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_snapshot_id: str = Field(min_length=40, max_length=64)
    baseline_run_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_run_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_settlement_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_integrity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    regression_policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    check_results: dict[str, StrictBool]
    decision: Literal["PASS", "FAIL"]

    @field_validator("source_snapshot_id", "candidate_snapshot_id")
    @classmethod
    def complete_git_ids(cls, value: str) -> str:
        if not _GIT_OID.fullmatch(value):
            raise ValueError("integration evidence requires full Git object IDs")
        return value

    @model_validator(mode="after")
    def digest_and_decision_match(self) -> "IntegrationCoreEvidenceV1":
        if set(self.check_results) != _INTEGRATION_CHECK_NAMES:
            raise ValueError("Core integration evidence must record every deterministic check")
        expected_decision = "PASS" if all(self.check_results.values()) else "FAIL"
        if self.decision != expected_decision:
            raise ValueError("Core integration decision does not match its deterministic checks")
        payload = self.model_dump(mode="json")
        payload.pop("evidence_digest")
        if self.evidence_digest != _digest(payload):
            raise ValueError("Core integration evidence digest does not match its content")
        return self


class IntegrationVerifierReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str = Field(min_length=1, max_length=200)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=40, max_length=64)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_snapshot_id: str = Field(min_length=40, max_length=64)
    core_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_job_id: str = Field(min_length=1, max_length=120)
    verifier_fence: int = Field(ge=1)
    verifier_role_instance_id: str = Field(min_length=1, max_length=200)
    verifier_binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["PASS", "FAIL"]
    checks: tuple[str, ...]

    @model_validator(mode="after")
    def digest_matches(self) -> "IntegrationVerifierReceiptV1":
        if not _GIT_OID.fullmatch(self.source_snapshot_id) or not _GIT_OID.fullmatch(self.candidate_snapshot_id):
            raise ValueError("integration receipt requires full immutable source and candidate commits")
        payload = self.model_dump(mode="json")
        payload.pop("receipt_digest", None)
        if self.receipt_digest != _digest(payload):
            raise ValueError("integration verifier receipt digest does not match its content")
        return self


class EvaluatorProtocolAuthority:
    """Append-only v5 campaign-DB records for proposals and verifier receipts."""

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version != 5:
            raise EvaluatorVerificationError(f"evaluator protocol authority requires schema v5; found v{version}")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_protocol_proposals (
            proposal_digest TEXT PRIMARY KEY CHECK(length(proposal_digest)=64),
            campaign_id TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            source_snapshot_id TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            protocol_version INTEGER NOT NULL CHECK(protocol_version>=1),
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            proposer_role_instance_id TEXT NOT NULL,
            proposal_json TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_verifier_receipts (
            receipt_digest TEXT PRIMARY KEY CHECK(length(receipt_digest)=64),
            proposal_digest TEXT NOT NULL REFERENCES evaluator_protocol_proposals(proposal_digest),
            campaign_id TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            source_snapshot_id TEXT NOT NULL,
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            proposer_role_instance_id TEXT NOT NULL,
            verifier_job_id TEXT NOT NULL UNIQUE,
            verifier_role_id TEXT NOT NULL,
            verifier_role_instance_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
            approval_digest TEXT UNIQUE,
            receipt_json TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_integration_receipts (
            receipt_digest TEXT PRIMARY KEY CHECK(length(receipt_digest)=64),
            campaign_id TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            source_snapshot_id TEXT NOT NULL,
            approval_digest TEXT NOT NULL REFERENCES evaluator_verifier_receipts(approval_digest),
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            candidate_snapshot_id TEXT NOT NULL,
            core_evidence_digest TEXT REFERENCES evaluator_integration_evidence(evidence_digest),
            verifier_job_id TEXT NOT NULL UNIQUE,
            verifier_role_instance_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('PASS','FAIL')),
            receipt_json TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_run_receipts (
            receipt_digest TEXT PRIMARY KEY CHECK(length(receipt_digest)=64),
            campaign_id TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            approval_digest TEXT NOT NULL REFERENCES evaluator_verifier_receipts(approval_digest),
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            candidate_snapshot_id TEXT NOT NULL,
            result_digest TEXT NOT NULL CHECK(length(result_digest)=64),
            receipt_json TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_integration_evidence (
            evidence_digest TEXT PRIMARY KEY CHECK(length(evidence_digest)=64),
            campaign_id TEXT NOT NULL,
            spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
            approval_digest TEXT NOT NULL REFERENCES evaluator_verifier_receipts(approval_digest),
            protocol_digest TEXT NOT NULL CHECK(length(protocol_digest)=64),
            candidate_snapshot_id TEXT NOT NULL,
            baseline_run_receipt_digest TEXT NOT NULL REFERENCES evaluator_run_receipts(receipt_digest),
            candidate_run_receipt_digest TEXT NOT NULL REFERENCES evaluator_run_receipts(receipt_digest),
            decision TEXT NOT NULL CHECK(decision IN ('PASS','FAIL')),
            evidence_json TEXT NOT NULL
        )""")
        integration_columns = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(evaluator_integration_receipts)")}
        if "core_evidence_digest" not in integration_columns:
            self.conn.execute(
                "ALTER TABLE evaluator_integration_receipts ADD COLUMN core_evidence_digest TEXT"
            )
        for table in ("evaluator_protocol_proposals", "evaluator_verifier_receipts",
                      "evaluator_integration_receipts", "evaluator_run_receipts",
                      "evaluator_integration_evidence"):
            self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                              "BEGIN SELECT RAISE(ABORT,'evaluator verification records are immutable'); END")
            self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                              "BEGIN SELECT RAISE(ABORT,'evaluator verification records are immutable'); END")

    def _append_proposal(self, proposal: MetricDiscoveryProposalV1) -> MetricDiscoveryProposalV1:
        encoded = _canonical(proposal.model_dump(mode="json"))
        self.conn.execute("INSERT OR IGNORE INTO evaluator_protocol_proposals VALUES(?,?,?,?,?,?,?,?,?,?)", (
            proposal.proposal_digest, proposal.campaign_id, proposal.spec_hash,
            proposal.source_snapshot_id, proposal.source_job_id, proposal.protocol.protocol_id,
            proposal.protocol.version, proposal.protocol.content_digest(),
            proposal.proposer_role_instance_id, encoded,
        ))
        row = self.conn.execute("SELECT proposal_json FROM evaluator_protocol_proposals WHERE proposal_digest=?",
                                (proposal.proposal_digest,)).fetchone()
        if row is None or row[0] != encoded:
            raise EvaluatorVerificationError("proposal digest already names different bytes")
        return proposal

    def proposal(self, proposal_digest: str) -> MetricDiscoveryProposalV1:
        row = self.conn.execute("SELECT proposal_json FROM evaluator_protocol_proposals WHERE proposal_digest=?",
                                (proposal_digest,)).fetchone()
        if row is None:
            raise EvaluatorVerificationError("unknown evaluator protocol proposal")
        try:
            return MetricDiscoveryProposalV1.model_validate_json(row[0])
        except Exception as exc:
            raise EvaluatorVerificationError("stored evaluator proposal is corrupt") from exc

    def _append_verified_receipt(self, receipt: VerifierReceiptV1) -> VerifierReceiptV1:
        proposal = self.proposal(receipt.proposal_digest)
        if (receipt.campaign_id != proposal.campaign_id or receipt.spec_hash != proposal.spec_hash
                or receipt.source_snapshot_id != proposal.source_snapshot_id
                or receipt.protocol_digest != proposal.protocol.content_digest()
                or receipt.protocol_id != proposal.protocol.protocol_id
                or receipt.protocol_version != proposal.protocol.version
                or receipt.proposer_role_instance_id != proposal.proposer_role_instance_id):
            raise EvaluatorVerificationError("verifier receipt does not match the immutable proposal scope")
        encoded = _canonical(receipt.model_dump(mode="json"))
        self.conn.execute("INSERT OR IGNORE INTO evaluator_verifier_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            receipt.receipt_digest, receipt.proposal_digest, receipt.campaign_id, receipt.spec_hash,
            receipt.source_snapshot_id, receipt.protocol_digest, receipt.proposer_role_instance_id,
            receipt.verifier_job_id, receipt.verifier_role_id, receipt.verifier_role_instance_id,
            receipt.decision, receipt.approval_digest, encoded,
        ))
        row = self.conn.execute("SELECT receipt_json FROM evaluator_verifier_receipts WHERE receipt_digest=?",
                                (receipt.receipt_digest,)).fetchone()
        if row is None or row[0] != encoded:
            raise EvaluatorVerificationError("verifier receipt digest already names different bytes")
        return receipt

    def record_completed_proposal(self, graph: Any, queue: Any, job_id: str) -> MetricDiscoveryProposalV1:
        """Accept only a role-pinned researcher proposal committed to the graph."""
        job, durable, request, result, binding, member = _validate_completed_role_job(
            queue, job_id, expected_role_id="researcher",
        )
        from .contracts import ExplorationCampaignSpecV2, OptimizationCampaignSpecV2
        from .exploration import EvidenceOnlyOutputV2

        spec = graph.campaign_spec()
        if (not isinstance(spec, (ExplorationCampaignSpecV2, OptimizationCampaignSpecV2))
                or spec.approved_hash() != request.get("spec_hash")
                or job.get("campaign_id") != request.get("campaign_id")
                or graph.campaign_id != job.get("campaign_id")):
            # Campaign identity is also bound by the pinned team manifest; the
            # graph's immutable spec hash is the authoritative local check.
            raise EvaluatorVerificationError("research proposal does not match the current frozen campaign spec")
        try:
            output = EvidenceOnlyOutputV2.model_validate(result)
        except Exception as exc:
            raise EvaluatorVerificationError("research job output is outside the strict evidence contract") from exc
        draft = output.protocol_draft
        if draft is None:
            raise EvaluatorVerificationError("completed researcher job contains no metric-discovery proposal")
        snapshot = request.get("base_commit")
        if (not isinstance(snapshot, str) or not _GIT_OID.fullmatch(snapshot)
                or draft.protocol.source_snapshot_id != snapshot
                or request.get("capability_manifest", {}).get("base_commit") != snapshot):
            raise EvaluatorVerificationError("metric proposal is stale or outside its pinned source snapshot")
        events = []
        for row in graph.conn.execute(
            "SELECT seq,payload_json FROM events WHERE event_type='EXPLORATION_COMPLETED' ORDER BY seq"
        ):
            payload = json.loads(row["payload_json"])
            if payload.get("job_id") == job_id:
                events.append((int(row["seq"]), payload))
        if len(events) != 1:
            raise EvaluatorVerificationError("proposal job must have one graph-committed completion event")
        cursor, event = events[0]
        if (event.get("node_id") != output.node_id
                or event.get("worker_fence") != int(durable["fence"])
                or event.get("source_request_hash") != job.get("request_hash")
                or event.get("output_digest") != durable.get("output_digest")
                or event.get("spec_hash") != spec.approved_hash()):
            raise EvaluatorVerificationError("proposal provenance does not match its graph completion event")
        available_artifacts = {f"artifact://sha256/{item}" for item in event.get("artifact_digests", ())}
        if not set(draft.evidence_refs) <= available_artifacts:
            raise EvaluatorVerificationError("proposal evidence does not cite verified published artifacts")
        if f"artifact://sha256/{draft.protocol.evaluator_bundle_sha256}" not in available_artifacts:
            raise EvaluatorVerificationError("evaluator bundle digest must identify a verified published artifact")
        payload = {
            "schema_version": 1, "campaign_id": job["campaign_id"],
            "spec_hash": spec.approved_hash(), "source_snapshot_id": snapshot,
            "source_node_id": output.node_id,
            "source_job_id": job_id, "source_fence": int(durable["fence"]),
            "source_request_hash": job["request_hash"], "source_output_digest": durable["output_digest"],
            "source_event_cursor": cursor, "proposer_role_id": member.role.role_id,
            "proposer_role_instance_id": member.instance_id,
            "proposer_binding_digest": _digest(binding),
            "protocol": draft.protocol.model_dump(mode="json"),
            "evidence_refs": draft.evidence_refs,
        }
        proposal = MetricDiscoveryProposalV1(proposal_digest=_digest(payload), **payload)
        return self._append_proposal(proposal)

    def record_completed_verifier_job(self, queue: Any, verifier_job_id: str) -> VerifierReceiptV1:
        """Validate the pinned verifier job and persist its decision exactly once."""
        job, durable, request, result, binding, member = _validate_completed_role_job(
            queue, verifier_job_id, expected_role_id="independent_verifier",
        )
        proposal_digest = request.get("proposal_digest")
        proposal = self.proposal(proposal_digest) if isinstance(proposal_digest, str) else None
        if proposal is None:
            raise EvaluatorVerificationError("verifier job is not bound to a stored metric proposal")
        if (request.get("campaign_id") != proposal.campaign_id
                or request.get("source_spec_hash") != proposal.spec_hash
                or request.get("source_snapshot_id") != proposal.source_snapshot_id
                or request.get("protocol_digest") != proposal.protocol.content_digest()
                or request.get("protocol_id") != proposal.protocol.protocol_id
                or int(request.get("protocol_version", -1)) != proposal.protocol.version
                or member.instance_id == proposal.proposer_role_instance_id):
            raise EvaluatorVerificationError("verifier job is stale, self-authored, or bound to another protocol")
        # Verifier agents receive the public proposal only. Hidden evaluator
        # cases, labels, and process capabilities stay behind the Core runner.
        policy = member.role.capability_policy
        if policy.tools or policy.read_paths or policy.write_paths or policy.network_access:
            raise EvaluatorVerificationError("protocol-verifier role must have no worker tools, paths, or network")
        try:
            output = ProtocolVerifierOutputV1.model_validate(result)
        except Exception as exc:
            raise EvaluatorVerificationError("verifier output does not satisfy the strict decision contract") from exc
        if output.proposal_digest != proposal.proposal_digest:
            raise EvaluatorVerificationError("verifier output does not match its bound proposal")
        receipt = make_verifier_receipt(
            proposal, verifier_job_id=verifier_job_id, verifier_fence=int(durable["fence"]),
            verifier_role_id=member.role.role_id, verifier_role_instance_id=member.instance_id,
            verifier_binding_digest=_digest(binding), verifier_request_hash=job["request_hash"],
            verifier_output_digest=durable["output_digest"], output=output,
        )
        return self._append_verified_receipt(receipt)

    def approved_protocol(self, approval_digest: str) -> tuple[MetricDiscoveryProposalV1, VerifierReceiptV1]:
        if not _SHA256.fullmatch(approval_digest):
            raise EvaluatorVerificationError("optimization requires a full verifier approval digest")
        row = self.conn.execute("SELECT receipt_json FROM evaluator_verifier_receipts "
                                "WHERE approval_digest=? AND decision='APPROVE'", (approval_digest,)).fetchone()
        if row is None:
            raise EvaluatorVerificationError("evaluator protocol has no independent verifier approval")
        receipt = VerifierReceiptV1.model_validate_json(row[0])
        return self.proposal(receipt.proposal_digest), receipt

    def require_frozen_reference(self, protocol_id: str, version: int, protocol_digest: str,
                                 approval_digest: str, *, campaign_id: str,
                                 spec_hash: str | None = None) -> VerifierReceiptV1:
        proposal, receipt = self.approved_protocol(approval_digest)
        if (proposal.protocol.protocol_id != protocol_id or proposal.protocol.version != version
                or proposal.protocol.content_digest() != protocol_digest
                or receipt.protocol_digest != protocol_digest
                or receipt.campaign_id != campaign_id or proposal.campaign_id != campaign_id
                or (spec_hash is not None and receipt.spec_hash != spec_hash)):
            raise EvaluatorVerificationError("frozen evaluator reference does not match its verifier receipt")
        return receipt

    def require_rejected_proposal(self, receipt_digest: str, *, spec_hash: str,
                                  campaign_id: str) -> VerifierReceiptV1:
        row = self.conn.execute("SELECT receipt_json FROM evaluator_verifier_receipts "
                                "WHERE receipt_digest=? AND decision='REJECT'", (receipt_digest,)).fetchone()
        if row is None:
            raise EvaluatorVerificationError("protocol rejection requires a durable REJECT verifier receipt")
        receipt = VerifierReceiptV1.model_validate_json(row[0])
        proposal = self.proposal(receipt.proposal_digest)
        if receipt.spec_hash != spec_hash or receipt.campaign_id != campaign_id or proposal.campaign_id != campaign_id:
            raise EvaluatorVerificationError("protocol rejection receipt belongs to another frozen campaign spec")
        return receipt

    def record_completed_integration_job(self, queue: Any, verifier_job_id: str) -> IntegrationVerifierReceiptV1:
        job, durable, request, result, binding, member = _validate_completed_role_job(
            queue, verifier_job_id, expected_role_id="integration_verifier",
        )
        policy = member.role.capability_policy
        if policy.tools or policy.read_paths or policy.write_paths or policy.network_access:
            raise EvaluatorVerificationError("integration-verifier role must have no worker tools, paths, or network")
        try:
            output = IntegrationVerifierOutputV1.model_validate(result)
        except Exception as exc:
            raise EvaluatorVerificationError("integration verifier output is outside its strict result contract") from exc
        approval_digest = request.get("approval_digest")
        protocol_id = request.get("protocol_id")
        protocol_version = request.get("protocol_version")
        protocol_digest = request.get("protocol_digest")
        spec_hash = request.get("spec_hash")
        source_snapshot_id = request.get("source_snapshot_id")
        evidence_digest = request.get("core_evidence_digest")
        if not isinstance(approval_digest, str) or not isinstance(protocol_id, str):
            raise EvaluatorVerificationError("integration verifier job has no frozen evaluator reference")
        if not isinstance(evidence_digest, str) or not _SHA256.fullmatch(evidence_digest):
            raise EvaluatorVerificationError("integration verifier job has no Core-derived evidence digest")
        if isinstance(protocol_version, bool) or not isinstance(protocol_version, int):
            raise EvaluatorVerificationError("integration verifier job has no protocol version")
        self.require_frozen_reference(protocol_id, protocol_version, protocol_digest,
                                      approval_digest, campaign_id=job["campaign_id"])
        if (output.protocol_digest != protocol_digest
                or output.candidate_snapshot_id != request.get("candidate_snapshot_id")
                or request.get("campaign_id") != job.get("campaign_id")
                or not isinstance(source_snapshot_id, str) or not _GIT_OID.fullmatch(source_snapshot_id)):
            raise EvaluatorVerificationError("integration verifier result is stale or cross-bound")
        evidence_row = self.conn.execute(
            "SELECT evidence_json FROM evaluator_integration_evidence WHERE evidence_digest=?",
            (evidence_digest,),
        ).fetchone()
        if evidence_row is None:
            raise EvaluatorVerificationError("integration verifier job cites no stored Core evidence")
        evidence = IntegrationCoreEvidenceV1.model_validate_json(evidence_row[0])
        if (evidence.campaign_id != job["campaign_id"] or evidence.spec_hash != spec_hash
                or evidence.source_snapshot_id != source_snapshot_id
                or evidence.approval_digest != approval_digest
                or evidence.protocol_digest != protocol_digest
                or evidence.candidate_snapshot_id != output.candidate_snapshot_id):
            raise EvaluatorVerificationError("Core integration evidence is stale or cross-bound")
        # A role decision is necessary but cannot override a deterministic Core
        # failure. Preserve FAIL on either source of evidence.
        effective_decision = ("PASS" if output.decision == "PASS"
                              and evidence.decision == "PASS" else "FAIL")
        payload = {
            "campaign_id": job["campaign_id"], "spec_hash": spec_hash,
            "source_snapshot_id": source_snapshot_id, "approval_digest": approval_digest,
            "protocol_digest": protocol_digest, "candidate_snapshot_id": output.candidate_snapshot_id,
            "verifier_job_id": verifier_job_id, "verifier_fence": int(durable["fence"]),
            "verifier_role_instance_id": member.instance_id,
            "verifier_binding_digest": _digest(binding),
            "verifier_request_hash": job["request_hash"],
            "verifier_output_digest": durable["output_digest"],
            "core_evidence_digest": evidence_digest,
            "decision": effective_decision, "checks": output.checks,
        }
        receipt = IntegrationVerifierReceiptV1(receipt_digest=_digest(payload), **payload)
        encoded = _canonical(receipt.model_dump(mode="json"))
        self.conn.execute("""INSERT OR IGNORE INTO evaluator_integration_receipts (
            receipt_digest,campaign_id,spec_hash,source_snapshot_id,approval_digest,
            protocol_digest,candidate_snapshot_id,core_evidence_digest,verifier_job_id,
            verifier_role_instance_id,decision,receipt_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            receipt.receipt_digest, receipt.campaign_id, receipt.spec_hash,
            receipt.source_snapshot_id, receipt.approval_digest, receipt.protocol_digest,
            receipt.candidate_snapshot_id, receipt.core_evidence_digest, receipt.verifier_job_id,
            receipt.verifier_role_instance_id, receipt.decision, encoded,
        ))
        row = self.conn.execute("SELECT receipt_json FROM evaluator_integration_receipts WHERE receipt_digest=?",
                                (receipt.receipt_digest,)).fetchone()
        if row is None or row[0] != encoded:
            raise EvaluatorVerificationError("integration receipt digest already names different bytes")
        return receipt

    def require_integration_decision(self, receipt_digest: str, *, decision: str,
                                     campaign_id: str, approval_digest: str,
                                     spec_hash: str, source_snapshot_id: str,
                                     protocol_digest: str, candidate_snapshot_id: str) -> IntegrationVerifierReceiptV1:
        row = self.conn.execute("SELECT receipt_json FROM evaluator_integration_receipts "
                                "WHERE receipt_digest=? AND decision=?", (receipt_digest, decision)).fetchone()
        if row is None:
            raise EvaluatorVerificationError("graph transition requires a durable matching integration decision")
        receipt = IntegrationVerifierReceiptV1.model_validate_json(row[0])
        if (receipt.campaign_id != campaign_id or receipt.approval_digest != approval_digest
                or receipt.spec_hash != spec_hash or receipt.source_snapshot_id != source_snapshot_id
                or receipt.protocol_digest != protocol_digest
                or receipt.candidate_snapshot_id != candidate_snapshot_id):
            raise EvaluatorVerificationError("integration receipt is stale or belongs to another candidate/protocol")
        evidence_row = self.conn.execute(
            "SELECT evidence_json FROM evaluator_integration_evidence WHERE evidence_digest=?",
            (receipt.core_evidence_digest,),
        ).fetchone()
        if evidence_row is None:
            raise EvaluatorVerificationError("integration receipt has no Core-derived proof record")
        evidence = IntegrationCoreEvidenceV1.model_validate_json(evidence_row[0])
        if ((decision == "PASS" and evidence.decision != "PASS")
                or evidence.campaign_id != campaign_id
                or evidence.spec_hash != spec_hash
                or evidence.source_snapshot_id != source_snapshot_id
                or evidence.approval_digest != approval_digest
                or evidence.protocol_digest != protocol_digest
                or evidence.candidate_snapshot_id != candidate_snapshot_id):
            raise EvaluatorVerificationError("integration decision is not supported by matching Core evidence")
        return receipt

    def _append_integration_evidence(self, evidence: IntegrationCoreEvidenceV1) -> IntegrationCoreEvidenceV1:
        """Internal Core-only append after the trusted evaluator derived evidence."""
        proposal, _ = self.approved_protocol(evidence.approval_digest)
        self.require_frozen_reference(
            protocol_id=proposal.protocol.protocol_id,
            version=proposal.protocol.version,
            protocol_digest=evidence.protocol_digest, approval_digest=evidence.approval_digest,
            campaign_id=evidence.campaign_id,
        )
        baseline = self.require_run_receipt(
            evidence.baseline_run_receipt_digest, campaign_id=evidence.campaign_id,
            spec_hash=evidence.spec_hash, approval_digest=evidence.approval_digest,
            protocol_digest=evidence.protocol_digest,
            candidate_snapshot_id=evidence.source_snapshot_id,
        )
        candidate = self.require_run_receipt(
            evidence.candidate_run_receipt_digest, campaign_id=evidence.campaign_id,
            spec_hash=evidence.spec_hash, approval_digest=evidence.approval_digest,
            protocol_digest=evidence.protocol_digest,
            candidate_snapshot_id=evidence.candidate_snapshot_id,
        )
        if baseline.tier != "validation" or candidate.tier != "validation":
            raise EvaluatorVerificationError(
                "integration evidence requires VALIDATION receipts; HOLDOUT cannot feed graph decisions"
            )
        if (baseline.tier_manifest_sha256 != candidate.tier_manifest_sha256
                or baseline.case_count != candidate.case_count):
            raise EvaluatorVerificationError("integration evidence run receipts use different evaluator scopes")
        encoded = _canonical(evidence.model_dump(mode="json"))
        self.conn.execute("""INSERT OR IGNORE INTO evaluator_integration_evidence (
            evidence_digest,campaign_id,spec_hash,approval_digest,protocol_digest,
            candidate_snapshot_id,baseline_run_receipt_digest,candidate_run_receipt_digest,
            decision,evidence_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            evidence.evidence_digest, evidence.campaign_id, evidence.spec_hash,
            evidence.approval_digest, evidence.protocol_digest,
            evidence.candidate_snapshot_id, evidence.baseline_run_receipt_digest,
            evidence.candidate_run_receipt_digest, evidence.decision, encoded,
        ))
        row = self.conn.execute(
            "SELECT evidence_json FROM evaluator_integration_evidence WHERE evidence_digest=?",
            (evidence.evidence_digest,),
        ).fetchone()
        if row is None or row[0] != encoded:
            raise EvaluatorVerificationError("integration evidence digest already names different bytes")
        return evidence

    def append_run_receipt(self, receipt: Any) -> Any:
        """Persist a Core-verified evaluator receipt as an immutable record."""
        from .trusted_evaluator import EvaluatorRunReceiptV1

        if not isinstance(receipt, EvaluatorRunReceiptV1):
            raise EvaluatorVerificationError("only a validated evaluator run receipt can be persisted")
        self.require_frozen_reference(
            receipt.protocol_id, receipt.protocol_version, receipt.protocol_digest,
            receipt.approval_digest, campaign_id=receipt.campaign_id,
        )
        encoded = _canonical(receipt.model_dump(mode="json"))
        self.conn.execute("INSERT OR IGNORE INTO evaluator_run_receipts VALUES(?,?,?,?,?,?,?,?)", (
            receipt.receipt_digest, receipt.campaign_id, receipt.spec_hash,
            receipt.approval_digest, receipt.protocol_digest, receipt.candidate_snapshot_id,
            receipt.result_digest, encoded,
        ))
        row = self.conn.execute(
            "SELECT receipt_json FROM evaluator_run_receipts WHERE receipt_digest=?",
            (receipt.receipt_digest,),
        ).fetchone()
        if row is None or row[0] != encoded:
            raise EvaluatorVerificationError("evaluator run receipt digest already names different bytes")
        return receipt

    def require_run_receipt(self, receipt_digest: str, *, campaign_id: str,
                            spec_hash: str, approval_digest: str,
                            protocol_digest: str, candidate_snapshot_id: str):
        from .trusted_evaluator import EvaluatorRunReceiptV1

        row = self.conn.execute(
            "SELECT receipt_json FROM evaluator_run_receipts WHERE receipt_digest=?",
            (receipt_digest,),
        ).fetchone()
        if row is None:
            raise EvaluatorVerificationError("integration verification requires a durable evaluator run receipt")
        receipt = EvaluatorRunReceiptV1.model_validate_json(row[0])
        if (receipt.campaign_id != campaign_id or receipt.spec_hash != spec_hash
                or receipt.approval_digest != approval_digest
                or receipt.protocol_digest != protocol_digest
                or receipt.candidate_snapshot_id != candidate_snapshot_id):
            raise EvaluatorVerificationError("evaluator run receipt is stale or cross-bound")
        return receipt


class EvaluatorVerificationAdapter:
    """Queue the independent verifier with proposal-only context and no tools."""

    def __init__(self, *, graph: Any, queue: Any, campaign_id: str):
        self.graph, self.queue, self.campaign_id = graph, queue, campaign_id
        self.graph.bind_campaign_id(campaign_id)
        self.authority = EvaluatorProtocolAuthority(graph.conn)

    def enqueue_verifier(self, proposal_digest: str) -> dict[str, Any]:
        proposal = self.authority.proposal(proposal_digest)
        if proposal.campaign_id != self.campaign_id:
            raise EvaluatorVerificationError("proposal belongs to another campaign")
        proposer_job = self.queue.get_job(proposal.source_job_id)
        source_request = proposer_job["request"]
        if (proposer_job["state"] != "COMPLETED"
                or proposer_job["request_hash"] != proposal.source_request_hash
                or int(proposer_job["fence"]) != proposal.source_fence):
            raise EvaluatorVerificationError("proposal source job is stale or no longer completed")
        binding = source_request.get("role_binding")
        if not isinstance(binding, dict):
            raise EvaluatorVerificationError("proposal source job has no pinned team")
        from .roles import (
            RoleContextSourceV1, RoleContextValueV1, TeamManifestAuthority,
            build_role_context,
        )

        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=proposal.spec_hash,
                registry_digest=binding["role_registry_digest"],
            )
        finally:
            manifest_authority.close()
        verifier_members = [item for item in manifest.members if item.role.role_id == "independent_verifier"]
        if len(verifier_members) != 1:
            raise EvaluatorVerificationError("pinned team must contain exactly one independent_verifier role")
        verifier = verifier_members[0]
        if "public_task" not in verifier.role.context_fields:
            raise EvaluatorVerificationError("independent_verifier role must allow the bounded public protocol context")
        if verifier.instance_id == proposal.proposer_role_instance_id:
            raise EvaluatorVerificationError("protocol proposal author cannot be assigned as its verifier")
        policy = verifier.role.capability_policy
        if policy.tools or policy.read_paths or policy.write_paths or policy.network_access:
            raise EvaluatorVerificationError("independent_verifier must be configured without worker tools or paths")
        campaign_spec = self.graph.campaign_spec()
        if campaign_spec.approved_hash() != proposal.spec_hash:
            raise EvaluatorVerificationError("proposal belongs to a stale campaign spec")
        public_context = _canonical({
            "protocol": proposal.protocol.model_dump(mode="json"),
            "proposal_digest": proposal.proposal_digest,
            "evidence_refs": proposal.evidence_refs,
        })
        context = build_role_context(verifier, RoleContextSourceV1(
            campaign_goal=RoleContextValueV1(value=campaign_spec.goal, classification="PUBLIC_SPEC",
                                              source_ref="campaign://spec/goal"),
            fields={"public_task": RoleContextValueV1(
                value=public_context, classification="PUBLIC_NODE",
                source_ref=f"graph://nodes/{proposal.source_node_id}",
            )},
        ))
        action_id = f"protocol-verification:{proposal.proposal_digest[:32]}"
        role_binding = manifest.job_binding(
            verifier.instance_id, action_id=action_id, context_digest=context.digest(),
        )
        request = {
            "schema": "evaluator-protocol-verification-v1",
            "campaign_id": self.campaign_id, "source_spec_hash": proposal.spec_hash,
            "source_snapshot_id": proposal.source_snapshot_id,
            "protocol_id": proposal.protocol.protocol_id,
            "protocol_version": proposal.protocol.version,
            "protocol_digest": proposal.protocol.content_digest(),
            "proposal_digest": proposal.proposal_digest,
            "role_binding": role_binding,
            "role_context": context.model_dump(mode="json", exclude_unset=True),
            "team_manifest_digest": manifest.manifest_digest,
            "role_registry_digest": manifest.registry_digest,
        }
        identity = _digest({"campaign_id": self.campaign_id,
                            "proposal_digest": proposal.proposal_digest,
                            "role_instance_id": verifier.instance_id,
                            "action_id": action_id})[:40]
        return self.queue.enqueue(
            job_id=f"verify-{identity}", enqueue_key=f"verify-{identity}",
            campaign_id=self.campaign_id, action_id=action_id,
            resource="agent_calls", upper_bound=1, request=request,
            role_allocation={
                "manifest_digest": manifest.manifest_digest,
                "role_instance_id": verifier.instance_id,
                "role_id": verifier.role.role_id,
                "calls_limit": verifier.role.calls_per_instance,
                "tokens_limit": verifier.role.tokens_per_instance,
                "token_upper_bound": verifier.role.tokens_per_instance // verifier.role.calls_per_instance,
            },
        )

    def run_verifier_one(self, *, verifier_job_id: str, worker_id: str, executor: Any,
                         lease_seconds: int = 300) -> VerifierReceiptV1:
        job = self.queue.get_job(verifier_job_id)
        if job["state"] != "READY":
            raise EvaluatorVerificationError("protocol verifier job is not READY")
        binding = job["request"].get("role_binding")
        if not isinstance(binding, dict):
            raise EvaluatorVerificationError("protocol verifier job has no role binding")
        from .roles import RoleContractError, TeamManifestAuthority

        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=binding["spec_hash"],
                registry_digest=binding["role_registry_digest"],
            )
        finally:
            manifest_authority.close()
        member = manifest.member(binding["role_instance_id"])
        if executor.manifest_digest != manifest.manifest_digest or executor.member != member:
            raise EvaluatorVerificationError("executor is not the pinned independent verifier")
        lease = self.queue.claim(verifier_job_id, worker_id, lease_seconds=lease_seconds)
        fence = int(lease["fence"])
        try:
            self.queue.mark_dispatched(verifier_job_id, worker_id, fence)
            raw = executor.dispatch(dict(lease["request"]), None)
            output = ProtocolVerifierOutputV1.model_validate(raw)
            if output.proposal_digest != lease["request"].get("proposal_digest"):
                raise EvaluatorVerificationError("verifier returned a decision for another proposal")
            payload = output.model_dump(mode="json")
            output_digest = _digest(payload)
            queue_receipt = _digest({"request_hash": lease["request_hash"], "fence": fence,
                                     "output_digest": output_digest})
            self.queue.complete(
                verifier_job_id, worker_id, fence, output_digest=output_digest,
                actual_amount=1, receipt_digest=queue_receipt, result_payload=payload,
            )
        except Exception:
            # Once dispatched, failure is unknown spend; never release it as
            # an unused call. The queue records UNKNOWN under the same fence.
            try:
                self.queue.cancel(verifier_job_id, reason="protocol verifier failed after dispatch")
            except Exception:
                pass
            raise
        return self.authority.record_completed_verifier_job(self.queue, verifier_job_id)


class IntegrationVerificationAdapter:
    """Queue a tool-free integration reviewer over Core-computed evidence."""

    def __init__(self, *, graph: Any, queue: Any, campaign_id: str,
                 trusted_evaluator: Any):
        self.graph, self.queue, self.campaign_id = graph, queue, campaign_id
        self.graph.bind_campaign_id(campaign_id)
        self.authority = EvaluatorProtocolAuthority(graph.conn)
        self.trusted_evaluator = trusted_evaluator

    def enqueue_integration(self, *, baseline_run_receipt_digest: str,
                            candidate_run_receipt_digest: str) -> tuple[dict[str, Any], IntegrationCoreEvidenceV1]:
        evidence = self.trusted_evaluator.create_integration_evidence(
            baseline_run_receipt_digest=baseline_run_receipt_digest,
            candidate_run_receipt_digest=candidate_run_receipt_digest,
        )
        spec = self.graph.campaign_spec()
        from .contracts import OptimizationCampaignSpecV2

        if not isinstance(spec, OptimizationCampaignSpecV2) or spec.evaluation.approval_digest is None:
            raise EvaluatorVerificationError("integration review requires a pinned Optimization evaluator")
        from .roles import (
            RoleContextSourceV1, RoleContextValueV1,
            TeamManifestAuthority, build_role_context,
        )

        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=spec.approved_hash(),
                registry_digest=spec.role_registry_digest,
            )
        finally:
            manifest_authority.close()
        members = [item for item in manifest.members if item.role.role_id == "integration_verifier"]
        if len(members) != 1:
            raise EvaluatorVerificationError("pinned team must have exactly one integration_verifier")
        member = members[0]
        policy = member.role.capability_policy
        if policy.tools or policy.read_paths or policy.write_paths or policy.network_access:
            raise EvaluatorVerificationError("integration-verifier role must have no tools, paths, or network")
        if "public_task" not in member.role.context_fields:
            raise EvaluatorVerificationError("integration-verifier role must accept bounded public evidence")
        public_summary = _canonical({
            "evidence_digest": evidence.evidence_digest,
            "decision": evidence.decision,
            "check_results": evidence.check_results,
            "protocol_digest": evidence.protocol_digest,
            "candidate_snapshot_id": evidence.candidate_snapshot_id,
            "baseline_run_receipt_digest": evidence.baseline_run_receipt_digest,
            "candidate_run_receipt_digest": evidence.candidate_run_receipt_digest,
            "budget_settlement_digest": evidence.budget_settlement_digest,
            "artifact_integrity_digest": evidence.artifact_integrity_digest,
            "regression_policy_digest": evidence.regression_policy_digest,
        })
        source = RoleContextSourceV1(
            campaign_goal=RoleContextValueV1(value=spec.goal, classification="PUBLIC_SPEC",
                                             source_ref="campaign://spec/goal"),
            fields={"public_task": RoleContextValueV1(
                value=public_summary, classification="PUBLIC_EVIDENCE",
                source_ref=f"evidence://integration/{evidence.evidence_digest}",
            )},
        )
        context = build_role_context(member, source)
        action_id = f"integration-verification:{evidence.evidence_digest[:32]}"
        role_binding = manifest.job_binding(
            member.instance_id, action_id=action_id, context_digest=context.digest(),
        )
        protocol, _ = self.authority.approved_protocol(spec.evaluation.approval_digest)
        request = {
            "schema": "integration-verification-v1", "campaign_id": self.campaign_id,
            "spec_hash": spec.approved_hash(), "source_snapshot_id": evidence.source_snapshot_id,
            "approval_digest": evidence.approval_digest,
            "protocol_id": protocol.protocol.protocol_id,
            "protocol_version": protocol.protocol.version,
            "protocol_digest": evidence.protocol_digest,
            "candidate_snapshot_id": evidence.candidate_snapshot_id,
            "core_evidence_digest": evidence.evidence_digest,
            "role_binding": role_binding,
            "role_context": context.model_dump(mode="json", exclude_unset=True),
            "team_manifest_digest": manifest.manifest_digest,
            "role_registry_digest": manifest.registry_digest,
        }
        identity = _digest({"campaign_id": self.campaign_id,
                            "evidence_digest": evidence.evidence_digest,
                            "role_instance_id": member.instance_id,
                            "action_id": action_id})[:40]
        job = self.queue.enqueue(
            job_id=f"integration-{identity}", enqueue_key=f"integration-{identity}",
            campaign_id=self.campaign_id, action_id=action_id,
            resource="agent_calls", upper_bound=1, request=request,
            role_allocation={
                "manifest_digest": manifest.manifest_digest,
                "role_instance_id": member.instance_id, "role_id": member.role.role_id,
                "calls_limit": member.role.calls_per_instance,
                "tokens_limit": member.role.tokens_per_instance,
                "token_upper_bound": member.role.tokens_per_instance // member.role.calls_per_instance,
            },
        )
        return job, evidence

    def run_integration_one(self, *, verifier_job_id: str, worker_id: str,
                            executor: Any, lease_seconds: int = 300) -> IntegrationVerifierReceiptV1:
        job = self.queue.get_job(verifier_job_id)
        if (job["state"] != "READY" or job["campaign_id"] != self.campaign_id
                or job["request"].get("schema") != "integration-verification-v1"):
            raise EvaluatorVerificationError("integration verifier job is not READY or is cross-bound")
        binding = job["request"].get("role_binding")
        if not isinstance(binding, dict):
            raise EvaluatorVerificationError("integration verifier job has no role binding")
        from .roles import RoleContextV1, TeamManifestAuthority

        manifest_authority = TeamManifestAuthority(self.queue.path)
        try:
            manifest = manifest_authority.load(
                self.campaign_id, spec_hash=binding["spec_hash"],
                registry_digest=binding["role_registry_digest"],
            )
        finally:
            manifest_authority.close()
        request = job["request"]
        member = manifest.member(binding["role_instance_id"])
        context = RoleContextV1.model_validate(request.get("role_context"))
        expected_binding = manifest.job_binding(
            member.instance_id, action_id=binding.get("action_id"),
            context_digest=context.digest(),
        )
        policy = member.role.capability_policy
        if (member.role.role_id != "integration_verifier"
                or policy.tools or policy.read_paths or policy.write_paths or policy.network_access
                or not set(context.model_fields_set) <= set(member.role.context_fields) | {"role_id"}
                or manifest.manifest_digest != request.get("team_manifest_digest")
                or manifest.registry_digest != request.get("role_registry_digest")
                or expected_binding != binding
                or executor.manifest_digest != manifest.manifest_digest
                or executor.member != member):
            raise EvaluatorVerificationError("executor is not the pinned, tool-free integration verifier")

        from .contracts import OptimizationCampaignSpecV2

        spec = self.graph.campaign_spec()
        if (not isinstance(spec, OptimizationCampaignSpecV2)
                or spec.approved_hash() != request.get("spec_hash")
                or spec.approved_hash() != binding.get("spec_hash")
                or spec.evaluation.approval_digest != request.get("approval_digest")):
            raise EvaluatorVerificationError("integration verifier job is stale against the pinned campaign spec")
        proposal, _ = self.authority.approved_protocol(request.get("approval_digest"))
        protocol = proposal.protocol
        evidence_row = self.authority.conn.execute(
            "SELECT evidence_json FROM evaluator_integration_evidence WHERE evidence_digest=?",
            (request.get("core_evidence_digest"),),
        ).fetchone()
        if evidence_row is None:
            raise EvaluatorVerificationError("integration verifier job cites no stored Core evidence")
        evidence = IntegrationCoreEvidenceV1.model_validate_json(evidence_row[0])
        if (evidence.campaign_id != self.campaign_id
                or evidence.spec_hash != spec.approved_hash()
                or evidence.approval_digest != request.get("approval_digest")
                or evidence.protocol_digest != protocol.content_digest()
                or evidence.source_snapshot_id != request.get("source_snapshot_id")
                or evidence.candidate_snapshot_id != request.get("candidate_snapshot_id")
                or evidence.protocol_digest != request.get("protocol_digest")):
            raise EvaluatorVerificationError("integration verifier evidence is stale or cross-bound")
        lease = self.queue.claim(verifier_job_id, worker_id, lease_seconds=lease_seconds)
        fence = int(lease["fence"])
        try:
            self.queue.mark_dispatched(verifier_job_id, worker_id, fence)
            output = IntegrationVerifierOutputV1.model_validate(
                executor.dispatch(dict(lease["request"]), None),
            )
            request = lease["request"]
            if (output.candidate_snapshot_id != request.get("candidate_snapshot_id")
                    or output.protocol_digest != request.get("protocol_digest")):
                raise EvaluatorVerificationError("integration verifier returned a decision for another scope")
            payload = output.model_dump(mode="json")
            output_digest = _digest(payload)
            queue_receipt = _digest({"request_hash": lease["request_hash"],
                                     "fence": fence, "output_digest": output_digest})
            self.queue.complete(
                verifier_job_id, worker_id, fence, output_digest=output_digest,
                actual_amount=1, receipt_digest=queue_receipt, result_payload=payload,
            )
        except Exception:
            try:
                self.queue.cancel(verifier_job_id, reason="integration verifier failed after dispatch")
            except Exception:
                pass
            raise
        return self.authority.record_completed_integration_job(self.queue, verifier_job_id)


def make_verifier_receipt(proposal: MetricDiscoveryProposalV1, *, verifier_job_id: str,
                          verifier_fence: int, verifier_role_id: str, verifier_role_instance_id: str,
                          verifier_binding_digest: str, verifier_request_hash: str,
                          verifier_output_digest: str, output: ProtocolVerifierOutputV1) -> VerifierReceiptV1:
    """Build content-addressed receipt after Core validated durable role/job provenance."""
    if output.proposal_digest != proposal.proposal_digest:
        raise EvaluatorVerificationError("verifier output names a different protocol proposal")
    payload = {
        "proposal_digest": proposal.proposal_digest, "campaign_id": proposal.campaign_id,
        "spec_hash": proposal.spec_hash, "source_snapshot_id": proposal.source_snapshot_id,
        "protocol_id": proposal.protocol.protocol_id, "protocol_version": proposal.protocol.version,
        "protocol_digest": proposal.protocol.content_digest(),
        "proposer_job_id": proposal.source_job_id, "proposer_fence": proposal.source_fence,
        "proposer_role_instance_id": proposal.proposer_role_instance_id,
        "verifier_job_id": verifier_job_id, "verifier_fence": verifier_fence,
        "verifier_role_id": verifier_role_id, "verifier_role_instance_id": verifier_role_instance_id,
        "verifier_binding_digest": verifier_binding_digest,
        "verifier_request_hash": verifier_request_hash,
        "verifier_output_digest": verifier_output_digest,
        "decision": output.decision, "checks": output.checks,
    }
    receipt_digest = _digest(payload)
    approval_digest = (_digest({"receipt_digest": receipt_digest,
                                "protocol_digest": payload["protocol_digest"]})
                       if output.decision == "APPROVE" else None)
    payload["approval_digest"] = approval_digest
    return VerifierReceiptV1(receipt_digest=receipt_digest, **payload)
