"""Side-effect-free authorization for the future RPDI-14 paired runner.

This module validates both preregistered tracks and both exact run gates, then
produces a signed authorization artifact. It never reveals task assignments or
launches models, tools, or evaluators.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .evidence import (
    EvidenceError, SignedEvidence, load_trust_policy, sign_evidence,
)
from .prereg import (
    GateEvidence, PreregError, TrackManifest, canonical_bytes, validate_run_gate,
    validate_track_pair,
)


AUTHORIZER_ROLE = "RUN_START_AUTHORIZER"
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


class RunStartError(ValueError):
    """The paired benchmark cannot receive a run-start authorization."""


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
) -> None:
    """Check the signed authorization is bound to the exact supplied run inputs."""
    try:
        validate_track_pair(optimization, exploration)
    except PreregError as exc:
        raise RunStartError(str(exc)) from exc
    try:
        trust_policy, trust_sha = load_trust_policy(trust_policy_path)
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
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
            bytes.fromhex(authorization.document.signature_ed25519),
            canonical_bytes(authorization.document.unsigned_payload()),
        )
    except Exception as exc:
        raise RunStartError("run-start authorization signature is invalid") from exc
