"""Side-effect-free authorization for the future RPDI-14 paired runner.

This module validates both preregistered tracks and both exact run gates, then
produces a signed authorization artifact. Its guarded assignment entrypoint
opens task material only after authorization and frozen-pin checks; this module
never launches models, tools, or evaluators.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Mapping, TypeVar

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .evidence import (
    EvidenceArtifactRef, EvidenceError, SignedEvidence, load_trust_policy,
    sign_evidence, verify_evidence_artifact, verify_protected_directory,
)
from .prereg import (
    GateEvidence, PreregError, TrackManifest, canonical_bytes, validate_run_gate,
    validate_track_pair,
)


AUTHORIZER_ROLE = "RUN_START_AUTHORIZER"
ASSIGNMENT_ROLE = "TASK_ASSIGNMENT_AUTHORIZER"
GATE_REVIEWER_ROLES = frozenset({
    "INDEPENDENT_TECHNICAL_REVIEWER", "INDEPENDENT_STATISTICAL_REVIEWER",
    "INDEPENDENT_EVALUATOR_ISOLATION_AUDITOR", "EXPLORATION_GATE_VERIFIER",
    "BENCHMARK_OPERATOR", "INTEGRATED_E2E_VERIFIER", "SCREEN_READER_REVIEWER",
})


@dataclass(frozen=True)
class RunStartAuthorization:
    document: SignedEvidence
    raw_bytes: bytes
    artifact_sha256: str
    pair_sha256: str
    trust_policy_sha256: str


@dataclass(frozen=True)
class TaskMaterial:
    task_payload: bytes
    rights_record: bytes
    initial_tree_sha256: str


@dataclass(frozen=True)
class AssignedTask:
    task_id: str
    blinded_pair_id_sha256: str
    task_data_sha256: str
    initial_tree_sha256: str
    rights_record_sha256: str
    task_payload: bytes


@dataclass(frozen=True)
class TrackAssignments:
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: str
    tasks: tuple[AssignedTask, ...]


@dataclass(frozen=True)
class AuthorizedAssignmentPlan:
    authorization_sha256: str
    track_pair_sha256: str
    optimization: TrackAssignments
    exploration: TrackAssignments
    assignment_evidence: EvidenceArtifactRef


@dataclass(frozen=True)
class ArmAssignmentProjection:
    """One task plus a Merkle proof, suitable for delivery to one arm worker."""
    authorization_sha256: str
    track_pair_sha256: str
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: str
    task: AssignedTask
    inclusion_proof: tuple[tuple[str, str], ...]
    assignment_evidence: EvidenceArtifactRef


class RunStartError(ValueError):
    """The paired benchmark cannot receive a run-start authorization."""


T = TypeVar("T")


def _load_pinned_tasks(
    manifest: TrackManifest,
    material_loader: Callable[[TrackManifest], Mapping[str, TaskMaterial]],
) -> TrackAssignments:
    materials = material_loader(manifest)
    if not isinstance(materials, Mapping) or set(materials) != {pin.task_id for pin in manifest.task_pins}:
        raise RunStartError(f"{manifest.track} task material set differs from the frozen task pins")
    assignments = []
    for pin in manifest.task_pins:
        material = materials.get(pin.task_id)
        if not isinstance(material, TaskMaterial):
            raise RunStartError(f"{manifest.track} task {pin.task_id} has no typed sealed material")
        if not isinstance(material.task_payload, bytes) or not isinstance(material.rights_record, bytes):
            raise RunStartError(f"{manifest.track} task {pin.task_id} material must be immutable bytes")
        if hashlib.sha256(material.task_payload).hexdigest() != pin.task_data_sha256:
            raise RunStartError(f"{manifest.track} task {pin.task_id} bytes do not match the frozen task digest")
        if hashlib.sha256(material.rights_record).hexdigest() != pin.rights_record_sha256:
            raise RunStartError(f"{manifest.track} task {pin.task_id} rights record does not match the frozen digest")
        if material.initial_tree_sha256 != pin.initial_tree_sha256:
            raise RunStartError(f"{manifest.track} task {pin.task_id} initial tree differs from the frozen digest")
        assignments.append(AssignedTask(
            task_id=pin.task_id,
            blinded_pair_id_sha256=pin.blinded_pair_id_sha256,
            task_data_sha256=pin.task_data_sha256,
            initial_tree_sha256=pin.initial_tree_sha256,
            rights_record_sha256=pin.rights_record_sha256,
            task_payload=material.task_payload,
        ))
    return TrackAssignments(
        track=manifest.track, manifest_sha256=manifest.manifest_sha256,
        tasks=tuple(assignments),
    )


def _validate_authorizer(signer, trust_policy, optimization: TrackManifest) -> None:
    if signer is None or signer.revoked or signer.permitted_roles != frozenset({AUTHORIZER_ROLE}):
        raise EvidenceError("run-start signing key is not trusted exclusively for authorization")
    excluded_principals = (set(optimization.arm_author_principal_ids)
                           | set(optimization.arm_operator_principal_ids)
                           | set(trust_policy.independent_principal_exclusions))
    if signer.principal_id in excluded_principals:
        raise EvidenceError("run-start authorizer cannot be an arm author or operator")
    if any(candidate.principal_id == signer.principal_id
           and candidate.permitted_roles & GATE_REVIEWER_ROLES
           for candidate in trust_policy.signers.values()):
        raise EvidenceError("run-start authorizer must be independent from gate reviewers")
    fingerprint = hashlib.sha256(signer.public_key).digest()
    if any(key != signer.key_id and hashlib.sha256(other.public_key).digest() == fingerprint
           for key, other in trust_policy.signers.items()):
        raise EvidenceError("run-start key is aliased under another trusted signer identity")


def track_pair_sha256(optimization: TrackManifest, exploration: TrackManifest) -> str:
    value = {
        "optimization_manifest_sha256": optimization.manifest_sha256,
        "exploration_manifest_sha256": exploration.manifest_sha256,
    }
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def gate_evidence_sha256(evidence: GateEvidence) -> str:
    return hashlib.sha256(canonical_bytes(evidence)).hexdigest()


def authorize_run_start(
    optimization: TrackManifest,
    exploration: TrackManifest,
    optimization_evidence: GateEvidence,
    exploration_evidence: GateEvidence,
    *,
    evidence_root: str,
    trust_policy_path: str,
    authorization_key_id: str,
    authorization_private_key: Ed25519PrivateKey,
) -> RunStartAuthorization:
    """Return a signed authorization only after both frozen tracks and gates pass.

    The trust policy is loaded and validated from its protected configured
    path here. The signing key is supplied by the trusted local service/HSM;
    it must match a signer granted only the run-start authorization role.
    """
    try:
        trust_policy, trust_sha = load_trust_policy(trust_policy_path)
        verify_protected_directory(evidence_root)
        validate_track_pair(optimization, exploration)
        if optimization.track != "OPTIMIZATION" or exploration.track != "EXPLORATION":
            raise PreregError("run start requires Optimization and Exploration manifests")
        validate_run_gate(
            exploration, exploration_evidence,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        validate_run_gate(
            optimization, optimization_evidence,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        signer = trust_policy.signers.get(authorization_key_id)
        _validate_authorizer(signer, trust_policy, optimization)
        assert signer is not None
        public_bytes = authorization_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if public_bytes != signer.public_key:
            raise EvidenceError("run-start signing key does not match the protected trust policy")
        key_fingerprint = hashlib.sha256(public_bytes).digest()
        if any(key != signer.key_id and hashlib.sha256(other.public_key).digest() == key_fingerprint
               for key, other in trust_policy.signers.items()):
            raise EvidenceError("run-start key is aliased under another trusted signer identity")
        pair_sha = track_pair_sha256(optimization, exploration)
        payload = {
            "optimization_manifest_sha256": optimization.manifest_sha256,
            "exploration_manifest_sha256": exploration.manifest_sha256,
            "optimization_gate_evidence_sha256": gate_evidence_sha256(optimization_evidence),
            "exploration_gate_evidence_sha256": gate_evidence_sha256(exploration_evidence),
            "track_pair_sha256": pair_sha,
            "trust_policy_sha256": trust_sha,
        }
        raw = sign_evidence(
            {
                "schema_version": 1,
                "evidence_type": "RUN_AUTHORIZATION",
                "subject_sha": pair_sha,
                "conclusion": "ACCEPTED",
                "issuer_role": AUTHORIZER_ROLE,
                "payload": payload,
            },
            key_id=signer.key_id,
            principal_id=signer.principal_id,
            private_key=authorization_private_key,
        )
        document = SignedEvidence.model_validate_json(raw, strict=True)
        return RunStartAuthorization(
            document=document,
            raw_bytes=raw,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            pair_sha256=pair_sha,
            trust_policy_sha256=trust_sha,
        )
    except (EvidenceError, PreregError, ValueError) as exc:
        raise RunStartError(str(exc)) from exc


def verify_run_start_authorization(
    authorization: RunStartAuthorization,
    *,
    optimization: TrackManifest,
    exploration: TrackManifest,
    optimization_evidence: GateEvidence,
    exploration_evidence: GateEvidence,
    trust_policy_path: str,
    evidence_root: str,
) -> None:
    """Check the signed authorization is bound to the exact supplied run inputs."""
    try:
        validate_track_pair(optimization, exploration)
    except PreregError as exc:
        raise RunStartError(str(exc)) from exc
    try:
        trust_policy, trust_sha = load_trust_policy(trust_policy_path)
        verify_protected_directory(evidence_root)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc
    if trust_sha != authorization.trust_policy_sha256:
        raise RunStartError("protected trust policy changed after run-start authorization")
    pair_sha = track_pair_sha256(optimization, exploration)
    expected_payload = {
        "optimization_manifest_sha256": optimization.manifest_sha256,
        "exploration_manifest_sha256": exploration.manifest_sha256,
        "optimization_gate_evidence_sha256": gate_evidence_sha256(optimization_evidence),
        "exploration_gate_evidence_sha256": gate_evidence_sha256(exploration_evidence),
        "track_pair_sha256": pair_sha,
        "trust_policy_sha256": authorization.trust_policy_sha256,
    }
    if hashlib.sha256(authorization.raw_bytes).hexdigest() != authorization.artifact_sha256:
        raise RunStartError("run-start authorization bytes do not match the artifact digest")
    if canonical_bytes(authorization.document) != authorization.raw_bytes:
        raise RunStartError("run-start authorization document differs from its signed bytes")
    if authorization.pair_sha256 != pair_sha:
        raise RunStartError("run-start authorization is for a different track pair")
    if authorization.document.payload != expected_payload:
        raise RunStartError("run-start authorization is for different manifests or gate evidence")
    signer = trust_policy.signers.get(authorization.document.issuer_key_id)
    try:
        _validate_authorizer(signer, trust_policy, optimization)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc
    assert signer is not None
    if (authorization.document.issuer_principal_id != signer.principal_id
            or authorization.document.issuer_role != AUTHORIZER_ROLE
            or authorization.document.evidence_type != "RUN_AUTHORIZATION"
            or authorization.document.conclusion != "ACCEPTED"
            or authorization.document.subject_sha != pair_sha):
        raise RunStartError("run-start authorization identity is invalid")
    try:
        validate_run_gate(
            exploration, exploration_evidence,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        validate_run_gate(
            optimization, optimization_evidence,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
    except PreregError as exc:
        raise RunStartError(f"run-start gate evidence no longer verifies: {exc}") from exc
    try:
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
            bytes.fromhex(authorization.document.signature_ed25519),
            canonical_bytes(authorization.document.unsigned_payload()),
        )
    except Exception as exc:
        raise RunStartError("run-start authorization signature is invalid") from exc


def verify_arm_run_authorization(
    authorization: RunStartAuthorization,
    *,
    manifest: TrackManifest,
    gate_evidence: GateEvidence,
    trust_policy_path: str,
    evidence_root: str,
) -> None:
    """Revalidate one arm without loading the opposite track's task manifest."""
    if manifest.track not in {"OPTIMIZATION", "EXPLORATION"}:
        raise RunStartError("arm manifest has an invalid track")
    try:
        trust_policy, trust_sha = load_trust_policy(trust_policy_path)
        verify_protected_directory(evidence_root)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc
    if trust_sha != authorization.trust_policy_sha256:
        raise RunStartError("protected trust policy changed after run-start authorization")
    payload = authorization.document.payload
    expected_keys = {
        "optimization_manifest_sha256", "exploration_manifest_sha256",
        "optimization_gate_evidence_sha256", "exploration_gate_evidence_sha256",
        "track_pair_sha256", "trust_policy_sha256",
    }
    if set(payload) != expected_keys:
        raise RunStartError("run-start authorization payload shape is invalid")
    own_manifest_key = f"{manifest.track.lower()}_manifest_sha256"
    own_gate_key = f"{manifest.track.lower()}_gate_evidence_sha256"
    if (payload[own_manifest_key] != manifest.manifest_sha256
            or payload[own_gate_key] != gate_evidence_sha256(gate_evidence)
            or payload["trust_policy_sha256"] != trust_sha
            or any(not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key])
                   for key in expected_keys)):
        raise RunStartError("run-start authorization does not bind this arm's frozen inputs")
    pair = hashlib.sha256(canonical_bytes({
        "optimization_manifest_sha256": payload["optimization_manifest_sha256"],
        "exploration_manifest_sha256": payload["exploration_manifest_sha256"],
    })).hexdigest()
    if pair != authorization.pair_sha256 or payload["track_pair_sha256"] != pair:
        raise RunStartError("run-start authorization pair digest is invalid")
    if (hashlib.sha256(authorization.raw_bytes).hexdigest() != authorization.artifact_sha256
            or canonical_bytes(authorization.document) != authorization.raw_bytes):
        raise RunStartError("run-start authorization bytes are invalid")
    signer = trust_policy.signers.get(authorization.document.issuer_key_id)
    try:
        _validate_authorizer(signer, trust_policy, manifest)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc
    if (signer is None or authorization.document.issuer_principal_id != signer.principal_id
            or authorization.document.issuer_role != AUTHORIZER_ROLE
            or authorization.document.evidence_type != "RUN_AUTHORIZATION"
            or authorization.document.conclusion != "ACCEPTED"
            or authorization.document.subject_sha != pair):
        raise RunStartError("run-start authorization identity is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
            bytes.fromhex(authorization.document.signature_ed25519),
            canonical_bytes(authorization.document.unsigned_payload()),
        )
        validate_run_gate(manifest, gate_evidence, evidence_root=evidence_root,
                          trust_policy=trust_policy)
    except Exception as exc:
        raise RunStartError(f"arm run gate or run-start signature failed verification: {exc}") from exc
def assign_tasks_after_run_authorization(
    authorization: RunStartAuthorization,
    optimization: TrackManifest,
    exploration: TrackManifest,
    optimization_evidence: GateEvidence,
    exploration_evidence: GateEvidence,
    *,
    trust_policy_path: str,
    evidence_root: str,
    assignment_key_id: str,
    assignment_private_key: Ed25519PrivateKey,
    material_loader: Callable[[TrackManifest], Mapping[str, TaskMaterial]],
    assignment_hook: Callable[[AuthorizedAssignmentPlan], T],
) -> T:
    """Open task material and invoke assignment only after verifying signed authorization.

    This is the only RPDI-14 assignment entrypoint. `material_loader` is called
    only after the signed authorization, frozen pair, both gate-evidence
    digests, protected evidence root, current trust policy, and both signed
    gates have been rechecked. The assignment hook is called only after both
    tracks' task and rights bytes plus initial-tree identities match every
    frozen task pin. The hook must durably assign both tracks atomically or
    idempotently by `authorization_sha256`; it must not launch either arm.
    """
    verify_run_start_authorization(
        authorization, optimization=optimization, exploration=exploration,
        optimization_evidence=optimization_evidence,
        exploration_evidence=exploration_evidence,
        trust_policy_path=trust_policy_path,
        evidence_root=evidence_root,
    )
    try:
        optimization_assignments = _load_pinned_tasks(optimization, material_loader)
        exploration_assignments = _load_pinned_tasks(exploration, material_loader)
    except RunStartError:
        raise
    except Exception as exc:
        raise RunStartError(f"sealed task loading failed: {exc}") from exc
    try:
        policy, _ = load_trust_policy(trust_policy_path)
        signer = policy.signers.get(assignment_key_id)
        _validate_assignment_signer(signer, policy, optimization, exploration)
        public = assignment_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )
        if public != signer.public_key:
            raise EvidenceError("task-assignment signing key does not match protected trust policy")
        pair_payload = _assignment_payload(
            authorization, optimization, exploration,
            optimization_assignments, exploration_assignments,
        )
        raw = sign_evidence({
            "schema_version": 1, "evidence_type": "TASK_ASSIGNMENT",
            "subject_sha": authorization.pair_sha256, "conclusion": "ACCEPTED",
            "issuer_role": ASSIGNMENT_ROLE, "payload": pair_payload,
        }, key_id=signer.key_id, principal_id=signer.principal_id,
            private_key=assignment_private_key)
        digest = hashlib.sha256(raw).hexdigest()
        verify_protected_directory(evidence_root)
        path = f"assignment-{digest}.json"
        root_fd = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(root_fd)
        except FileExistsError:
            from .evidence import _read_evidence_bounded
            if _read_evidence_bounded(Path(evidence_root), PurePosixPath(path)) != raw:
                raise EvidenceError("existing assignment evidence differs from deterministic artifact")
        finally:
            os.close(root_fd)
        reference = EvidenceArtifactRef(path=path, artifact_sha256=digest)
    except (EvidenceError, OSError, ValueError) as exc:
        raise RunStartError(f"cannot seal authorized task assignment: {exc}") from exc
    plan = AuthorizedAssignmentPlan(
        authorization_sha256=authorization.artifact_sha256,
        track_pair_sha256=authorization.pair_sha256,
        optimization=optimization_assignments,
        exploration=exploration_assignments,
        assignment_evidence=reference,
    )
    return assignment_hook(plan)


def _assignment_payload(authorization, optimization, exploration, opt_assignments, exp_assignments):
    return {
        "authorization_sha256": authorization.artifact_sha256,
        "track_pair_sha256": authorization.pair_sha256,
        "optimization_manifest_sha256": optimization.manifest_sha256,
        "exploration_manifest_sha256": exploration.manifest_sha256,
        "optimization_assignments_sha256": _track_assignments_sha256(optimization, opt_assignments),
        "exploration_assignments_sha256": _track_assignments_sha256(exploration, exp_assignments),
    }


def _track_assignments_sha256(manifest: TrackManifest, assignments: TrackAssignments) -> str:
    leaves = [_assignment_leaf(manifest.track, manifest.manifest_sha256, task)
              for task in sorted(assignments.tasks, key=lambda item: item.task_id)]
    return _merkle_root(leaves)


def _assignment_leaf(track: str, manifest_sha256: str, task: AssignedTask) -> bytes:
    record = {
        "track": track,
        "manifest_sha256": manifest_sha256,
        "task_id": task.task_id,
        "blinded_pair_id_sha256": task.blinded_pair_id_sha256,
        "task_data_sha256": task.task_data_sha256,
        "initial_tree_sha256": task.initial_tree_sha256,
        "rights_record_sha256": task.rights_record_sha256,
    }
    return hashlib.sha256(b"\x00" + canonical_bytes(record)).digest()


def _merkle_root(level: list[bytes]) -> str:
    if not level:
        raise RunStartError("cannot seal an empty task assignment")
    current = list(level)
    while len(current) > 1:
        current = [hashlib.sha256(b"\x01" + current[i] +
                                  current[min(i + 1, len(current) - 1)]).digest()
                   for i in range(0, len(current), 2)]
    return current[0].hex()


def _merkle_proof(track: str, manifest_sha256: str, assignments: TrackAssignments,
                  task_id: str) -> tuple[tuple[str, str], ...]:
    tasks = sorted(assignments.tasks, key=lambda item: item.task_id)
    try:
        index = next(i for i, item in enumerate(tasks) if item.task_id == task_id)
    except StopIteration as exc:
        raise RunStartError("task is not present in the sealed assignment") from exc
    level = [_assignment_leaf(track, manifest_sha256, task) for task in tasks]
    proof: list[tuple[str, str]] = []
    while len(level) > 1:
        sibling = index - 1 if index % 2 else min(index + 1, len(level) - 1)
        proof.append(("LEFT" if index % 2 else "RIGHT", level[sibling].hex()))
        level = [hashlib.sha256(b"\x01" + level[i] +
                                level[min(i + 1, len(level) - 1)]).digest()
                 for i in range(0, len(level), 2)]
        index //= 2
    return tuple(proof)


def _verify_merkle_proof(leaf: bytes, proof: tuple[tuple[str, str], ...], root: str) -> bool:
    current = leaf
    try:
        for side, sibling_hex in proof:
            sibling = bytes.fromhex(sibling_hex)
            if len(sibling) != 32 or side not in {"LEFT", "RIGHT"}:
                return False
            current = hashlib.sha256(b"\x01" + (sibling + current if side == "LEFT"
                                                   else current + sibling)).digest()
    except (TypeError, ValueError):
        return False
    return current.hex() == root


def project_assignment_for_track(
    plan: AuthorizedAssignmentPlan,
    track: Literal["OPTIMIZATION", "EXPLORATION"],
    task_id: str,
) -> ArmAssignmentProjection:
    """Create a one-task projection; arm boundaries must never receive the pair plan."""
    if track not in {"OPTIMIZATION", "EXPLORATION"}:
        raise RunStartError("unknown assignment track")
    assignments = plan.optimization if track == "OPTIMIZATION" else plan.exploration
    # The Merkle leaf binds the selected item to the frozen track identity.
    for task in assignments.tasks:
        if task.task_id == task_id:
            selected = task
            break
    else:
        raise RunStartError("task is not present in the sealed assignment")
    return ArmAssignmentProjection(
        authorization_sha256=plan.authorization_sha256,
        track_pair_sha256=plan.track_pair_sha256,
        track=track,
        manifest_sha256=assignments.manifest_sha256,
        task=selected,
        inclusion_proof=_merkle_proof(track, assignments.manifest_sha256, assignments, task_id),
        assignment_evidence=plan.assignment_evidence,
    )


def verify_assignment_plan(plan, *, authorization, optimization, exploration,
                           trust_policy, evidence_root) -> None:
    """Authenticate complete assignment and rights/tree pins before dispatch."""
    if (plan.authorization_sha256 != authorization.artifact_sha256
            or plan.track_pair_sha256 != authorization.pair_sha256):
        raise RunStartError("assignment plan is not bound to run authorization")
    for manifest, assignments, track in (
        (optimization, plan.optimization, "OPTIMIZATION"),
        (exploration, plan.exploration, "EXPLORATION"),
    ):
        pins = {pin.task_id: pin for pin in manifest.task_pins}
        if (assignments.track != track or assignments.manifest_sha256 != manifest.manifest_sha256
                or len(assignments.tasks) != len(pins)
                or {item.task_id for item in assignments.tasks} != set(pins)):
            raise RunStartError("assignment plan task set differs from frozen manifest")
        for task in assignments.tasks:
            pin = pins[task.task_id]
            if (task.blinded_pair_id_sha256 != pin.blinded_pair_id_sha256
                    or task.task_data_sha256 != pin.task_data_sha256
                    or task.initial_tree_sha256 != pin.initial_tree_sha256
                    or task.rights_record_sha256 != pin.rights_record_sha256
                    or hashlib.sha256(task.task_payload).hexdigest() != pin.task_data_sha256):
                raise RunStartError("assignment plan task payload or rights/tree pins are invalid")
    expected = _assignment_payload(authorization, optimization, exploration,
                                   plan.optimization, plan.exploration)
    verified = verify_evidence_artifact(
        root=evidence_root, reference=plan.assignment_evidence, policy=trust_policy,
        evidence_type="TASK_ASSIGNMENT", subject_sha=authorization.pair_sha256,
        conclusion="ACCEPTED", issuer_role=ASSIGNMENT_ROLE, expected_payload=expected,
    )
    signer = trust_policy.signers.get(verified.document.issuer_key_id)
    try:
        _validate_assignment_signer(signer, trust_policy, optimization, exploration)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc


def verify_assignment_projection(
    projection: ArmAssignmentProjection, *, authorization, manifest,
    trust_policy, evidence_root,
) -> None:
    """Verify one arm's task projection against the signed pair assignment."""
    if (not isinstance(projection, ArmAssignmentProjection)
            or projection.authorization_sha256 != authorization.artifact_sha256
            or projection.track_pair_sha256 != authorization.pair_sha256):
        raise RunStartError("arm assignment projection is not bound to run authorization")
    if projection.track not in {"OPTIMIZATION", "EXPLORATION"} or projection.track != manifest.track:
        raise RunStartError("arm assignment projection has an invalid track")
    task = projection.task
    if not isinstance(task, AssignedTask) or not isinstance(task.task_payload, bytes):
        raise RunStartError("arm assignment projection has malformed task material")
    pins = {pin.task_id: pin for pin in manifest.task_pins}
    pin = pins.get(task.task_id)
    if (projection.manifest_sha256 != manifest.manifest_sha256 or pin is None
            or task.blinded_pair_id_sha256 != pin.blinded_pair_id_sha256
            or task.task_data_sha256 != pin.task_data_sha256
            or task.initial_tree_sha256 != pin.initial_tree_sha256
            or task.rights_record_sha256 != pin.rights_record_sha256
            or hashlib.sha256(task.task_payload).hexdigest() != pin.task_data_sha256):
        raise RunStartError("arm task payload or rights/tree pins are invalid")
    proof = projection.inclusion_proof
    if (not isinstance(proof, tuple)
            or any(not isinstance(step, tuple) or len(step) != 2
                   or not isinstance(step[0], str) or step[0] not in {"LEFT", "RIGHT"}
                   or not isinstance(step[1], str) or not re.fullmatch(r"[0-9a-f]{64}", step[1])
                   for step in proof)):
        raise RunStartError("arm assignment inclusion proof is malformed")
    pair_payload = authorization.document.payload
    optimization_sha = pair_payload.get("optimization_manifest_sha256")
    exploration_sha = pair_payload.get("exploration_manifest_sha256")
    if (pair_payload.get("track_pair_sha256") != authorization.pair_sha256
            or pair_payload.get(f"{projection.track.lower()}_manifest_sha256") != manifest.manifest_sha256
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                   for item in (optimization_sha, exploration_sha))):
        raise RunStartError("run-start authorization does not bind the arm assignment manifest")
    track_digest_key = ("optimization_assignments_sha256" if projection.track == "OPTIMIZATION"
                        else "exploration_assignments_sha256")
    expected = {
        "authorization_sha256": authorization.artifact_sha256,
        "track_pair_sha256": authorization.pair_sha256,
        "optimization_manifest_sha256": optimization_sha,
        "exploration_manifest_sha256": exploration_sha,
        # The assignment artifact authenticates the complete task-set root;
        # the single task and its proof authenticate membership without
        # disclosing the other task identifiers to this arm.
    }
    verified = verify_evidence_artifact(
        root=evidence_root, reference=projection.assignment_evidence, policy=trust_policy,
        evidence_type="TASK_ASSIGNMENT", subject_sha=authorization.pair_sha256,
        conclusion="ACCEPTED", issuer_role=ASSIGNMENT_ROLE, expected_payload=expected,
    )
    if (set(verified.document.payload) != {
            "authorization_sha256", "track_pair_sha256", "optimization_manifest_sha256",
            "exploration_manifest_sha256", "optimization_assignments_sha256",
            "exploration_assignments_sha256",
    } or any(not re.fullmatch(r"[0-9a-f]{64}", verified.document.payload.get(key, ""))
             for key in ("optimization_assignments_sha256", "exploration_assignments_sha256"))):
        raise RunStartError("signed pair assignment evidence has an invalid payload shape")
    assignment_root = verified.document.payload[track_digest_key]
    if not _verify_merkle_proof(
            _assignment_leaf(projection.track, projection.manifest_sha256, task),
            proof, assignment_root):
        raise RunStartError("task is not included in the signed assignment root")
    signer = trust_policy.signers.get(verified.document.issuer_key_id)
    try:
        _validate_assignment_signer(signer, trust_policy, manifest)
    except EvidenceError as exc:
        raise RunStartError(str(exc)) from exc


def _validate_assignment_signer(signer, policy, optimization, exploration=None) -> None:
    if signer is None or signer.revoked or signer.permitted_roles != frozenset({ASSIGNMENT_ROLE}):
        raise EvidenceError("task-assignment signing key is not trusted exclusively for assignment")
    manifests = (optimization,) if exploration is None else (optimization, exploration)
    excluded = set().union(*(set(manifest.arm_author_principal_ids)
                             | set(manifest.arm_operator_principal_ids) for manifest in manifests))
    if signer.principal_id in (excluded
                               | set(policy.independent_principal_exclusions)):
        raise EvidenceError("task-assignment authorizer must be independent from arm authors and operators")
    if any(other.key_id != signer.key_id and (
            other.principal_id == signer.principal_id or other.public_key == signer.public_key
    ) for other in policy.signers.values()):
        raise EvidenceError("task-assignment authorizer identity or key is aliased under another trust role")
