"""Fail-closed metered boundary for one billable RPDI-14 invocation.

The runner must route every model, tool and evaluator invocation through this
boundary. It records a durable intent before dispatch, requires a provider-side
conservative ceiling proof, and signs a receipt only after a trusted adapter
verifies authoritative usage. Meter signatures authenticate the configured
meter's statement; they do not prove a provider was honest or that an opaque
adapter did not make hidden calls. Therefore adapters must expose exactly one
external invocation per call and verify provider receipts by durable action ID.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .accounting import ActionReceipt, ResourceUsage, make_receipt
from .evidence import (
    EvidenceArtifactRef, EvidenceError, TrustPolicy, canonical_bytes, load_trust_policy,
    sign_evidence, verify_protected_directory,
)
from .execution import (
    ArmAssignmentProjection, RunStartAuthorization, RunStartError,
    verify_assignment_projection, verify_arm_run_authorization,
)
from .prereg import ArmConfig, GateEvidence, ResourceCaps, TrackManifest, canonical_bytes
from .worker import ArmWorkerError, ArmWorkerResult, BubblewrapArmWorker
from .provider_broker import OneCallProviderAdapter, OneShotProviderBroker, ProviderBrokerError


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTION_METER_ROLE = "ACTION_METER_ATTESTOR"
_USAGE_FIELDS = tuple(ResourceCaps.model_fields)


class DispatchBoundaryError(RuntimeError):
    """A billable action cannot safely cross or settle the dispatch boundary."""


class DispatchOutcomeUnknown(DispatchBoundaryError):
    """The invocation may have happened but authoritative usage is unavailable."""

    def __init__(self, message: str, *, receipt: ActionReceipt, evidence_ref: EvidenceArtifactRef):
        super().__init__(message)
        self.receipt = receipt
        self.evidence_ref = evidence_ref


class DispatchOverBound(DispatchBoundaryError):
    """A completed action exceeded a frozen reservation or resource ceiling."""

    def __init__(self, message: str, *, receipt: ActionReceipt, evidence_ref: EvidenceArtifactRef):
        super().__init__(message)
        self.receipt = receipt
        self.evidence_ref = evidence_ref


@dataclass(frozen=True)
class InvocationOutput:
    value: Any
    provider_receipt: bytes | None


@dataclass(frozen=True)
class MeteredDispatch:
    value: Any
    receipt: ActionReceipt
    evidence_ref: EvidenceArtifactRef


@dataclass(frozen=True)
class _PendingIntent:
    sequence: int
    action_id: str
    action_kind: str
    actor_id_digest: str
    bounds: ResourceUsage
    previous_receipt_sha256: str | None
    arm_configuration_sha256: str
    bound_proof_sha256: str
    provider_receipt_sha256: str | None = None


ProviderUsageVerifier = Callable[[bytes, dict[str, str]], ResourceUsage]
ProviderBoundVerifier = Callable[[TrackManifest, ArmConfig, str, str, str, str,
                                  ResourceUsage, bytes], bool]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _known_usage(value: ResourceUsage, *, label: str) -> dict[str, int]:
    result = {}
    for name in _USAGE_FIELDS:
        amount = getattr(value, name)
        if amount is None:
            raise DispatchBoundaryError(f"{label} must bound every resource dimension; {name} is unknown")
        result[name] = amount
    return result


def _action_dimension(action_kind: str) -> tuple[str, int]:
    return {
        "MODEL_CALL": ("agent_calls", 1),
        "TOOL_CALL": ("agent_calls", 1),
        "EVALUATOR_CALL": ("evaluator_calls", 1),
        "RETRY": ("retries", 1),
        "EXPERIMENT": ("experiments", 1),
    }.get(action_kind, ("", 0))


def _write_exclusive(path: Path, content: bytes) -> None:
    """Create one bounded canonical artifact beneath an already protected root."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    verify_protected_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        # A crash can leave the exact signed artifact before its SQLite commit.
        # Accept only byte-identical replay; never replace a conflicting file.
        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        existing_fd = os.open(path, read_flags)
        try:
            info = os.fstat(existing_fd)
            if not stat.S_ISREG(info.st_mode):
                raise DispatchBoundaryError("existing receipt artifact is not a regular file")
            chunks = []
            total = 0
            while total <= 1_000_000:
                chunk = os.read(existing_fd, min(65536, 1_000_001 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > 1_000_000 or b"".join(chunks) != content:
                raise DispatchBoundaryError("existing receipt artifact conflicts with the sealed receipt")
        finally:
            os.close(existing_fd)
        return
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


class MeteredDispatchBoundary:
    """Durable, authorization-gated meter for exactly one external action/call.

    `provider_bound_verifier` must validate the exact bound against provider-side
    max tokens/cost or a pinned price schedule. Returning false blocks the call.
    `provider_usage_verifier` must authenticate the provider's receipt and bind
    it to the action context. Missing or unverifiable receipts are UNKNOWN.
    Both callbacks are trusted runtime integrations, not agent-supplied code.
    """

    def __init__(
        self,
        authorization: RunStartAuthorization,
        manifest: TrackManifest,
        gate_evidence: GateEvidence,
        *,
        track: Literal["OPTIMIZATION", "EXPLORATION"],
        assignment: ArmAssignmentProjection,
        task_id: str,
        pair_id: str,
        arm_label: Literal["A", "B"],
        arm_config: ArmConfig,
        evidence_root: str | Path,
        trust_policy_path: str | Path,
        meter_key_id: str,
        meter_private_key: Ed25519PrivateKey,
        provider_usage_verifier: ProviderUsageVerifier,
        provider_bound_verifier: ProviderBoundVerifier,
    ) -> None:
        self.authorization = authorization
        self.manifest = manifest
        self.gate_evidence = gate_evidence
        self.track = track
        self.assignment = assignment
        self.task_id = task_id
        self.pair_id = pair_id
        self.arm_label = arm_label
        self.arm_config = arm_config
        self.evidence_root = Path(evidence_root)
        self.trust_policy_path = Path(trust_policy_path)
        self.meter_key_id = meter_key_id
        self.meter_private_key = meter_private_key
        if not callable(provider_usage_verifier) or not callable(provider_bound_verifier):
            raise DispatchBoundaryError("trusted provider usage and bound verifiers are mandatory")
        self.provider_usage_verifier = provider_usage_verifier
        self.provider_bound_verifier = provider_bound_verifier
        if track not in {"OPTIMIZATION", "EXPLORATION"} or arm_label not in {"A", "B"}:
            raise DispatchBoundaryError("invalid benchmark track or blinded arm label")
        if not _DIGEST.fullmatch(pair_id):
            raise DispatchBoundaryError("pair_id must be a lowercase SHA-256 digest")
        manifest = self.manifest
        if manifest.track != track:
            raise DispatchBoundaryError("arm track does not match its single-track manifest")
        if pair_id not in {item.blinded_pair_id_sha256 for item in manifest.task_pins}:
            raise DispatchBoundaryError("pair_id is not pinned in the authorized track manifest")
        if arm_config not in manifest.arms:
            raise DispatchBoundaryError("arm configuration is not present in the frozen manifest")
        self.manifest = manifest
        self.caps = manifest.common_caps
        verify_protected_directory(self.evidence_root)
        self.db_path = self.evidence_root / "rpdi14-action-meter.sqlite3"
        if self.db_path.is_symlink() or (self.db_path.exists() and not self.db_path.is_file()):
            raise DispatchBoundaryError("meter database must be a regular non-symlink file")
        if self.db_path.exists():
            info = self.db_path.stat()
            if ((hasattr(os, "geteuid") and info.st_uid not in {0, os.geteuid()})
                    or info.st_mode & 0o077):
                raise DispatchBoundaryError("existing meter database must be trusted and mode 0600")
        self._validate_assignment(self._verify_authority())
        self.conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.db_path, 0o600)
        except OSError as exc:
            self.conn.close()
            raise DispatchBoundaryError("cannot protect action-meter database with mode 0600") from exc
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._protect_sqlite_files()
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meter_heads (
              pair_id TEXT NOT NULL, arm_label TEXT NOT NULL,
              sequence INTEGER NOT NULL DEFAULT 0,
              receipt_sha256 TEXT,
              state TEXT NOT NULL CHECK(state IN ('OPEN','CLOSED_UNKNOWN','CLOSED_OVER_BOUND')),
              PRIMARY KEY(pair_id,arm_label)
            );
            CREATE TABLE IF NOT EXISTS meter_actions (
              pair_id TEXT NOT NULL, arm_label TEXT NOT NULL, sequence INTEGER NOT NULL,
              action_id TEXT NOT NULL, action_kind TEXT NOT NULL, actor_id_digest TEXT NOT NULL,
              bounds_json TEXT NOT NULL, previous_receipt_sha256 TEXT,
              arm_configuration_sha256 TEXT NOT NULL, bound_proof_sha256 TEXT NOT NULL,
              provider_receipt_sha256 TEXT,
              state TEXT NOT NULL CHECK(state IN ('PENDING','SEALED','UNKNOWN','OVER_BOUND')),
              receipt_json TEXT, evidence_path TEXT, evidence_sha256 TEXT,
              PRIMARY KEY(pair_id,arm_label,sequence),
              UNIQUE(pair_id,arm_label,action_id)
            );
        """)
        existing_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(meter_actions)")}
        migrations = {
            "arm_configuration_sha256": "TEXT NOT NULL DEFAULT ''",
            "bound_proof_sha256": "TEXT NOT NULL DEFAULT ''",
            "provider_receipt_sha256": "TEXT",
        }
        for column, declaration in migrations.items():
            if column not in existing_columns:
                self.conn.execute(f"ALTER TABLE meter_actions ADD COLUMN {column} {declaration}")
        self.conn.execute(
            "INSERT OR IGNORE INTO meter_heads(pair_id,arm_label,state) VALUES(?,?, 'OPEN')",
            (pair_id, arm_label),
        )
        self._protect_sqlite_files()
        # A process restart after pre-dispatch is conservatively treated as an
        # unknown external outcome; it is never retried or refunded.
        self._recover_pending()

    def _protect_sqlite_files(self) -> None:
        """Require the database and SQLite sidecars to remain private regular files."""
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.is_symlink():
                self.conn.close()
                raise DispatchBoundaryError(f"SQLite {suffix or 'database'} file may not be a symlink")
            if path.exists():
                try:
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        raise DispatchBoundaryError("SQLite files must be regular files")
                    os.chmod(path, 0o600)
                    if path.stat().st_mode & 0o077:
                        raise DispatchBoundaryError("cannot protect SQLite files with mode 0600")
                except OSError as exc:
                    self.conn.close()
                    raise DispatchBoundaryError("cannot protect SQLite files with mode 0600") from exc

    def close(self) -> None:
        self.conn.close()

    def _verify_authority(self) -> TrustPolicy:
        try:
            verify_arm_run_authorization(
                self.authorization,
                manifest=self.manifest,
                gate_evidence=self.gate_evidence,
                trust_policy_path=str(self.trust_policy_path),
                evidence_root=str(self.evidence_root),
            )
        except RunStartError as exc:
            raise DispatchBoundaryError(f"run-start authorization no longer verifies: {exc}") from exc
        try:
            policy, _ = load_trust_policy(self.trust_policy_path)
            signer = policy.signers.get(self.meter_key_id)
            if (signer is None or signer.revoked
                    or signer.permitted_roles != frozenset({_ACTION_METER_ROLE})
                    or signer.principal_id in policy.independent_principal_exclusions
                    or signer.principal_id in set(self.manifest.arm_author_principal_ids)
                    | set(self.manifest.arm_operator_principal_ids)):
                raise EvidenceError("meter signing key is not trusted exclusively for the action meter role")
            public = self.meter_private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
            )
            if public != signer.public_key:
                raise EvidenceError("meter private key does not match protected trust policy")
            return policy
        except EvidenceError as exc:
            raise DispatchBoundaryError(str(exc)) from exc

    def _ensure_authorized(self) -> TrustPolicy:
        policy = self._verify_authority()
        self._validate_assignment(policy)
        return policy

    def _validate_assignment(self, policy: TrustPolicy) -> None:
        try:
            verify_assignment_projection(
                self.assignment, authorization=self.authorization,
                manifest=self.manifest,
                trust_policy=policy, evidence_root=self.evidence_root,
            )
        except RunStartError as exc:
            raise DispatchBoundaryError(f"task assignment evidence no longer verifies: {exc}") from exc
        manifest = self.manifest
        projection = self.assignment
        task = projection.task
        if (projection.track != self.track or projection.manifest_sha256 != manifest.manifest_sha256
                or task.task_id != self.task_id or task.blinded_pair_id_sha256 != self.pair_id):
            raise DispatchBoundaryError("task/pair is not present in the authorized arm projection")
        pin = next((item for item in manifest.task_pins if item.task_id == self.task_id), None)
        if (pin is None or task.task_data_sha256 != pin.task_data_sha256
                or task.initial_tree_sha256 != pin.initial_tree_sha256
                or task.rights_record_sha256 != pin.rights_record_sha256
                or hashlib.sha256(task.task_payload).hexdigest() != pin.task_data_sha256):
            raise DispatchBoundaryError("assigned task bytes or rights/tree pins do not match the manifest")

    def _read_head(self) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM meter_heads WHERE pair_id=? AND arm_label=?",
            (self.pair_id, self.arm_label),
        ).fetchone()
        if row is None:
            raise DispatchBoundaryError("meter chain head is missing")
        if row["state"] != "OPEN":
            raise DispatchBoundaryError(f"meter chain is closed: {row['state']}")
        return row

    def _totals(self, *, exclude_pending_sequence: int | None = None) -> dict[str, int]:
        totals = {name: 0 for name in _USAGE_FIELDS}
        rows = self.conn.execute(
            "SELECT sequence,state,receipt_json FROM meter_actions WHERE pair_id=? AND arm_label=? ORDER BY sequence",
            (self.pair_id, self.arm_label),
        ).fetchall()
        for row in rows:
            if row["state"] == "PENDING" and int(row["sequence"]) == exclude_pending_sequence:
                continue
            if row["state"] == "PENDING":
                raise DispatchBoundaryError("an earlier dispatch intent is unresolved")
            if row["state"] != "SEALED":
                raise DispatchBoundaryError("unknown or over-bound usage closes the action chain")
            receipt = ActionReceipt.model_validate_json(row["receipt_json"], strict=True)
            for name in _USAGE_FIELDS:
                amount = getattr(receipt.usage, name)
                if amount is None:
                    raise DispatchBoundaryError(f"prior action usage is unknown for {name}")
                totals[name] += amount
        return totals

    def _reserve_intent(
        self, *, action_id: str, action_kind: str, actor_id_digest: str, bounds: ResourceUsage,
        bound_proof: bytes, provider_receipt_sha256: str | None = None,
    ) -> _PendingIntent:
        if not _DIGEST.fullmatch(action_id) or not _DIGEST.fullmatch(actor_id_digest):
            raise DispatchBoundaryError("action and actor IDs must be lowercase SHA-256 digests")
        if action_kind not in {"MODEL_CALL", "TOOL_CALL", "EVALUATOR_CALL", "RETRY", "EXPERIMENT", "OTHER"}:
            raise DispatchBoundaryError("unsupported billable action kind")
        bound_values = _known_usage(bounds, label="pre-dispatch resource bounds")
        required_resource, minimum = _action_dimension(action_kind)
        if required_resource and bound_values[required_resource] < minimum:
            raise DispatchBoundaryError(f"{action_kind} bound must reserve {required_resource}")
        if action_kind == "OTHER" and not any(bound_values.values()):
            raise DispatchBoundaryError("OTHER action must reserve at least one resource dimension")
        if not isinstance(bound_proof, bytes) or not bound_proof or len(bound_proof) > 1_000_000:
            raise DispatchBoundaryError("provider ceiling proof must be bounded nonempty bytes")
        self._ensure_authorized()
        # Refuse a sealed chain before asking any adapter to prepare a new call.
        self._read_head()
        try:
            if not self.provider_bound_verifier(
                self.manifest, self.arm_config, self.pair_id, self.arm_label,
                action_id, action_kind, bounds, bound_proof,
            ):
                raise DispatchBoundaryError(
                    "provider-side token/cost ceiling or conservative price bound was not verified"
                )
        except DispatchBoundaryError:
            raise
        except Exception as exc:
            raise DispatchBoundaryError("provider ceiling proof could not be verified") from exc

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            head = self._read_head()
            totals = self._totals()
            for name, bound in bound_values.items():
                if totals[name] + bound > getattr(self.caps, name):
                    raise DispatchBoundaryError(f"pre-dispatch bound exceeds frozen {name} cap")
            sequence = int(head["sequence"]) + 1
            previous = head["receipt_sha256"]
            arm_configuration_sha256 = _digest(canonical_bytes(self.arm_config))
            bound_proof_sha256 = _digest(bound_proof)
            if provider_receipt_sha256 is not None and not _DIGEST.fullmatch(provider_receipt_sha256):
                raise DispatchBoundaryError("provider receipt digest must be lowercase SHA-256")
            self.conn.execute(
                "INSERT INTO meter_actions(pair_id,arm_label,sequence,action_id,action_kind,actor_id_digest,"
                "bounds_json,previous_receipt_sha256,arm_configuration_sha256,bound_proof_sha256,"
                "provider_receipt_sha256,state,receipt_json,evidence_path,evidence_sha256) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,NULL,NULL)",
                (self.pair_id, self.arm_label, sequence, action_id, action_kind,
                 actor_id_digest, _canonical(bounds.model_dump(mode="json")).decode(), previous,
                 arm_configuration_sha256, bound_proof_sha256, provider_receipt_sha256),
            )
            self.conn.commit()
            return _PendingIntent(
                sequence, action_id, action_kind, actor_id_digest, bounds, previous,
                arm_configuration_sha256, bound_proof_sha256, provider_receipt_sha256,
            )
        except Exception:
            self.conn.rollback()
            raise

    def _seal(
        self, intent: _PendingIntent, *, usage: ResourceUsage, status: Literal["SEALED", "UNKNOWN", "OVER_BOUND"],
    ) -> tuple[ActionReceipt, EvidenceArtifactRef]:
        policy = self._ensure_authorized()
        receipt = make_receipt(
            pair_id=self.pair_id,
            arm_label=self.arm_label,
            sequence=intent.sequence,
            action_id=intent.action_id,
            action_kind=intent.action_kind,
            actor_id_digest=intent.actor_id_digest,
            usage=usage.model_dump(mode="json"),
            previous_receipt_sha256=intent.previous_receipt_sha256,
        )
        usage_sha = _digest(canonical_bytes(receipt.usage))
        signed = sign_evidence(
            {
                "schema_version": 1, "evidence_type": "ACTION_RECEIPT",
                "subject_sha": receipt.receipt_sha256, "conclusion": "ACCEPTED",
                "issuer_role": _ACTION_METER_ROLE,
                "payload": {
                    "pair_id_sha256": self.pair_id,
                    "arm_label": self.arm_label,
                    "sequence": str(receipt.sequence),
                    "action_id_sha256": receipt.action_id,
                    "usage_sha256": usage_sha,
                    "settlement_state": status,
                },
            },
            key_id=self.meter_key_id,
            principal_id=policy.signers[self.meter_key_id].principal_id,
            private_key=self.meter_private_key,
        )
        relative = (Path("action-receipts") / self.pair_id / self.arm_label /
                    f"{intent.sequence:06d}-{receipt.receipt_sha256}.json")
        reference = EvidenceArtifactRef(path=relative.as_posix(), artifact_sha256=_digest(signed))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            head = self._read_head()
            if (int(head["sequence"]) != intent.sequence - 1
                    or head["receipt_sha256"] != intent.previous_receipt_sha256):
                raise DispatchBoundaryError("meter head changed since the pre-dispatch intent")
            pending = self.conn.execute(
                "SELECT state FROM meter_actions WHERE pair_id=? AND arm_label=? AND sequence=?",
                (self.pair_id, self.arm_label, intent.sequence),
            ).fetchone()
            if pending is None or pending["state"] != "PENDING":
                raise DispatchBoundaryError("pre-dispatch intent is no longer pending")
            self.conn.rollback()
        except Exception:
            self.conn.rollback()
            raise
        _write_exclusive(self.evidence_root / relative, signed)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            head = self._read_head()
            if (int(head["sequence"]) != intent.sequence - 1
                    or head["receipt_sha256"] != intent.previous_receipt_sha256):
                raise DispatchBoundaryError("meter head no longer matches the pending intent predecessor")
            current = self.conn.execute(
                "SELECT state FROM meter_actions WHERE pair_id=? AND arm_label=? AND sequence=?",
                (self.pair_id, self.arm_label, intent.sequence),
            ).fetchone()
            if current is None or current["state"] != "PENDING":
                raise DispatchBoundaryError("pre-dispatch intent is no longer pending")
            changed_action = self.conn.execute(
                "UPDATE meter_actions SET state=?,receipt_json=?,evidence_path=?,evidence_sha256=?,provider_receipt_sha256=? "
                "WHERE pair_id=? AND arm_label=? AND sequence=? AND state='PENDING'",
                (status, canonical_bytes(receipt).decode(), reference.path, reference.artifact_sha256,
                 intent.provider_receipt_sha256, self.pair_id, self.arm_label, intent.sequence),
            )
            if changed_action.rowcount != 1:
                raise DispatchBoundaryError("pending action settlement lost its compare-and-swap")
            next_state = {"SEALED": "OPEN", "UNKNOWN": "CLOSED_UNKNOWN", "OVER_BOUND": "CLOSED_OVER_BOUND"}[status]
            changed_head = self.conn.execute(
                "UPDATE meter_heads SET sequence=?,receipt_sha256=?,state=? WHERE pair_id=? AND arm_label=?",
                (intent.sequence, receipt.receipt_sha256, next_state, self.pair_id, self.arm_label),
            )
            if changed_head.rowcount != 1:
                raise DispatchBoundaryError("meter head settlement lost its compare-and-swap")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return receipt, reference

    def _recover_pending(self) -> None:
        row = self.conn.execute(
            "SELECT * FROM meter_actions WHERE pair_id=? AND arm_label=? AND state='PENDING' ORDER BY sequence",
            (self.pair_id, self.arm_label),
        ).fetchone()
        if row is None:
            return
        self._ensure_authorized()
        intent = _PendingIntent(
            sequence=int(row["sequence"]), action_id=row["action_id"], action_kind=row["action_kind"],
            actor_id_digest=row["actor_id_digest"],
            bounds=ResourceUsage.model_validate_json(row["bounds_json"], strict=True),
            previous_receipt_sha256=row["previous_receipt_sha256"],
            arm_configuration_sha256=row["arm_configuration_sha256"],
            bound_proof_sha256=row["bound_proof_sha256"],
            provider_receipt_sha256=row["provider_receipt_sha256"],
        )
        unknown = ResourceUsage(**{name: None for name in _USAGE_FIELDS})
        self._seal(intent, usage=unknown, status="UNKNOWN")

    def dispatch_isolated_model_worker(
        self,
        *,
        worker: BubblewrapArmWorker,
        argv: list[str] | tuple[str, ...],
        workspace: str | Path,
        timeout_seconds: int,
        memory_bytes: int,
        actor_id_digest: str,
        bounds: ResourceUsage,
        bound_proof: bytes,
        provider_adapter: OneCallProviderAdapter,
        provider_timeout_seconds: int,
        action_id: str,
    ) -> MeteredDispatch:
        """Dispatch one model-backed task through a networkless worker and metered broker.

        The host creates a private one-action Unix broker for the frozen arm
        config and output ceiling. The worker returns the same provider-signed
        receipt delivered by that broker; the trusted usage verifier binds it
        to the action ID. Provider credentials stay in the host adapter.
        """
        if not isinstance(worker, BubblewrapArmWorker):
            raise DispatchBoundaryError("a bubblewrap isolated arm worker is required")
        try:
            reserved = _known_usage(bounds, label="pre-dispatch resource bounds")
        except (AttributeError, DispatchBoundaryError) as exc:
            raise DispatchBoundaryError("complete action resource bounds are required before dispatch") from exc
        if (type(timeout_seconds) is not int or timeout_seconds < 1
                or timeout_seconds > reserved["wall_seconds"]
                or timeout_seconds > self.caps.wall_seconds):
            raise DispatchBoundaryError("worker timeout exceeds the frozen action or arm wall cap")
        if (type(provider_timeout_seconds) is not int or not 1 <= provider_timeout_seconds <= 3600
                or provider_timeout_seconds > timeout_seconds):
            raise DispatchBoundaryError("provider timeout must fit within the worker wall ceiling")
        if type(memory_bytes) is not int or memory_bytes < 1:
            raise DispatchBoundaryError("worker memory ceiling must be positive")
        if memory_bytes != self.arm_config.worker_memory_bytes:
            raise DispatchBoundaryError("worker memory ceiling differs from the frozen arm configuration")
        if (reserved["agent_calls"] != 1 or reserved["input_tokens"] < 1
                or reserved["output_tokens"] < 1):
            raise DispatchBoundaryError("model actions require one call and positive input/output token ceilings")

        def run_one_model_action() -> InvocationOutput:
            try:
                with OneShotProviderBroker(
                    provider_adapter, action_id=action_id, arm_config=self.arm_config,
                    resource_bounds=bounds,
                    timeout_seconds=provider_timeout_seconds,
                ) as broker:
                    result = worker.run(
                        argv=argv, workspace=workspace, projection=self.assignment,
                        manifest=self.manifest, arm_config=self.arm_config,
                        arm_label=self.arm_label, action_id=action_id,
                        timeout_seconds=timeout_seconds,
                        memory_bytes=memory_bytes, provider_broker_socket=broker.socket_path,
                    )
                    broker_receipt = broker.verify_complete()
                if (not isinstance(result, ArmWorkerResult)
                        or result.task_id != self.task_id
                        or result.track != self.track
                        or result.pair_id_sha256 != self.pair_id
                        or not math.isfinite(result.elapsed_seconds)
                        or result.elapsed_seconds < 0
                        or result.elapsed_seconds > timeout_seconds):
                    raise ValueError("worker result is bound to another task or exceeds its wall limit")
                envelope = json.loads(
                    result.output, object_pairs_hook=_unique_json_object,
                    parse_float=_finite_json_float,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")),
                )
                if (not isinstance(envelope, dict)
                        or set(envelope) != {"schema_version", "result", "provider_receipt_base64"}
                        or type(envelope.get("schema_version")) is not int
                        or envelope.get("schema_version") != 1
                        or not isinstance(envelope.get("provider_receipt_base64"), str)
                        or _canonical(envelope) != result.output):
                    raise ValueError("worker result has an invalid model action envelope")
                receipt = base64.b64decode(envelope["provider_receipt_base64"], validate=True)
                if not receipt or len(receipt) > 256_000 or receipt != broker_receipt:
                    raise ValueError("worker receipt differs from the host broker receipt")
                return InvocationOutput(envelope["result"], receipt)
            except (ArmWorkerError, ProviderBrokerError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise DispatchBoundaryError(f"isolated model worker failed: {type(exc).__name__}") from exc

        return self.dispatch_one(
            action_id=action_id, action_kind="MODEL_CALL", actor_id_digest=actor_id_digest,
            bounds=bounds, bound_proof=bound_proof, operation=run_one_model_action,
        )

    def dispatch_one(
        self,
        *,
        action_id: str,
        action_kind: Literal["MODEL_CALL", "TOOL_CALL", "EVALUATOR_CALL", "RETRY", "EXPERIMENT", "OTHER"],
        actor_id_digest: str,
        bounds: ResourceUsage,
        bound_proof: bytes,
        operation: Callable[[], InvocationOutput],
    ) -> MeteredDispatch:
        """Reserve, invoke exactly once, authenticate usage and append signed evidence."""
        if not callable(operation):
            raise DispatchBoundaryError("one trusted single-invocation adapter is required")
        intent = self._reserve_intent(
            action_id=action_id, action_kind=action_kind, actor_id_digest=actor_id_digest,
            bounds=bounds, bound_proof=bound_proof,
        )
        started = time.monotonic()
        try:
            output = operation()
        except BaseException as exc:
            usage = ResourceUsage(**{name: None for name in _USAGE_FIELDS})
            receipt, ref = self._seal(intent, usage=usage, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                f"billable invocation ended without a verifiable provider receipt ({type(exc).__name__})",
                receipt=receipt, evidence_ref=ref,
            ) from exc
        if not isinstance(output, InvocationOutput) or not isinstance(output.provider_receipt, bytes):
            usage = ResourceUsage(**{name: None for name in _USAGE_FIELDS})
            receipt, ref = self._seal(intent, usage=usage, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                "provider receipt is missing or malformed after dispatch", receipt=receipt, evidence_ref=ref,
            )
        try:
            if len(output.provider_receipt) > 1_000_000:
                raise ValueError("provider receipt exceeds 1 MB")
            context = {
                "pair_id_sha256": self.pair_id, "arm_label": self.arm_label,
                "sequence": str(intent.sequence), "action_id_sha256": intent.action_id,
                "action_kind": intent.action_kind,
                "arm_configuration_sha256": intent.arm_configuration_sha256,
            }
            usage = self.provider_usage_verifier(output.provider_receipt, context)
            if not isinstance(usage, ResourceUsage):
                raise ValueError("provider verifier must return ResourceUsage")
            elapsed_wall_seconds = math.ceil(max(0.0, time.monotonic() - started))
            if usage.wall_seconds is None:
                raise ValueError("provider usage receipt does not report wall time")
            usage = ResourceUsage.model_validate({
                **usage.model_dump(mode="python"),
                "wall_seconds": max(usage.wall_seconds, elapsed_wall_seconds),
            }, strict=True)
            intent = _PendingIntent(
                intent.sequence, intent.action_id, intent.action_kind, intent.actor_id_digest,
                intent.bounds, intent.previous_receipt_sha256, intent.arm_configuration_sha256,
                intent.bound_proof_sha256, _digest(output.provider_receipt),
            )
        except Exception as exc:
            unknown = ResourceUsage(**{name: None for name in _USAGE_FIELDS})
            receipt, ref = self._seal(intent, usage=unknown, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                "provider usage receipt could not be authenticated", receipt=receipt, evidence_ref=ref,
            ) from exc
        unknown_dimensions = usage.unknown_dimensions()
        if unknown_dimensions:
            receipt, ref = self._seal(intent, usage=usage, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                "provider usage is incomplete: " + ", ".join(unknown_dimensions),
                receipt=receipt, evidence_ref=ref,
            )
        bound_values = _known_usage(intent.bounds, label="pre-dispatch resource bounds")
        actual_values = _known_usage(usage, label="provider actual usage")
        totals = self._totals(exclude_pending_sequence=intent.sequence)
        over = [name for name, amount in actual_values.items()
                if amount > bound_values[name] or totals[name] + amount > getattr(self.caps, name)]
        if over:
            receipt, ref = self._seal(intent, usage=usage, status="OVER_BOUND")
            raise DispatchOverBound(
                "post-dispatch actual usage exceeded reserved or frozen ceiling: " + ", ".join(sorted(over)),
                receipt=receipt, evidence_ref=ref,
            )
        required, minimum = _action_dimension(action_kind)
        if action_kind == "OTHER" and not any(actual_values.values()):
            receipt, ref = self._seal(intent, usage=usage, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                "OTHER action receipt must account for at least one resource dimension",
                receipt=receipt, evidence_ref=ref,
            )
        if required and actual_values[required] < minimum:
            receipt, ref = self._seal(intent, usage=usage, status="UNKNOWN")
            raise DispatchOutcomeUnknown(
                f"provider receipt does not account for {required}", receipt=receipt, evidence_ref=ref,
            )
        receipt, ref = self._seal(intent, usage=usage, status="SEALED")
        return MeteredDispatch(value=output.value, receipt=receipt, evidence_ref=ref)

    def receipts(self) -> tuple[ActionReceipt, ...]:
        rows = self.conn.execute(
            "SELECT receipt_json FROM meter_actions WHERE pair_id=? AND arm_label=? "
            "AND state IN ('SEALED','UNKNOWN','OVER_BOUND') ORDER BY sequence",
            (self.pair_id, self.arm_label),
        ).fetchall()
        return tuple(ActionReceipt.model_validate_json(row["receipt_json"], strict=True) for row in rows)

    def receipt_attestations(self) -> tuple[tuple[ActionReceipt, EvidenceArtifactRef], ...]:
        rows = self.conn.execute(
            "SELECT receipt_json,evidence_path,evidence_sha256 FROM meter_actions "
            "WHERE pair_id=? AND arm_label=? AND state IN ('SEALED','UNKNOWN','OVER_BOUND') ORDER BY sequence",
            (self.pair_id, self.arm_label),
        ).fetchall()
        result = []
        for row in rows:
            if not row["evidence_path"] or not row["evidence_sha256"]:
                raise DispatchBoundaryError("sealed receipt is missing its evidence reference")
            result.append((
                ActionReceipt.model_validate_json(row["receipt_json"], strict=True),
                EvidenceArtifactRef(path=row["evidence_path"], artifact_sha256=row["evidence_sha256"]),
            ))
        return tuple(result)

    def chain_head(self) -> tuple[int, str | None, str]:
        row = self.conn.execute(
            "SELECT sequence,receipt_sha256,state FROM meter_heads WHERE pair_id=? AND arm_label=?",
            (self.pair_id, self.arm_label),
        ).fetchone()
        if row is None:
            raise DispatchBoundaryError("meter chain head is missing")
        return int(row["sequence"]), row["receipt_sha256"], row["state"]
