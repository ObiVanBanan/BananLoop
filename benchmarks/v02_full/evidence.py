"""Offline verification of Ed25519-signed RPDI gate evidence artifacts.

The verifier trust policy contains public keys only. Issuers keep private
keys outside the verifier and public evidence bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_EVIDENCE_BYTES = 1_000_000
MAX_SCORE_OUTPUT_BYTES = 16_000_000
MAX_TRUST_POLICY_BYTES = 2_000_000


class EvidenceError(ValueError):
    """A gate artifact is missing, malformed, unsigned, or out of scope."""


class EvidenceArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str = Field(min_length=1, max_length=500)
    artifact_sha256: Digest


class SignedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    evidence_type: Literal[
        "RPDI13_E2E", "RPDI13_ACCESSIBILITY", "TECHNICAL_REVIEW", "STATISTICAL_REVIEW",
        "EVALUATOR_ISOLATION", "EXPLORATION_VERIFICATION", "RIGHTS_REGISTER", "SPEND_AUTHORIZATION",
        "OPERATOR_ACCEPTANCE", "OWNER_APPROVAL", "INDEPENDENT_OPERATOR", "CI_JOB",
        "BLINDED_SCORE_PACKAGE", "UNBLINDING", "RESOURCE_LEDGER_HEAD",
        "RUN_AUTHORIZATION", "TASK_ASSIGNMENT", "ACTION_RECEIPT",
    ]
    subject_sha: Digest
    conclusion: Literal["PASS", "ACCEPTED", "SUCCESS"]
    issuer_key_id: str = Field(min_length=1, max_length=128)
    issuer_principal_id: str = Field(min_length=1, max_length=200)
    issuer_role: str = Field(min_length=1, max_length=100)
    payload: dict[str, str]
    signature_ed25519: Annotated[str, Field(pattern=r"^[0-9a-f]{128}$")]

    def unsigned_payload(self) -> dict:
        data = self.model_dump(mode="json")
        data.pop("signature_ed25519")
        return data


@dataclass(frozen=True)
class TrustedSigner:
    key_id: str
    principal_id: str
    public_key: bytes
    permitted_roles: frozenset[str]
    revoked: bool = False

    def __post_init__(self) -> None:
        if not _KEY_ID.fullmatch(self.key_id) or not self.principal_id or len(self.public_key) != 32:
            raise ValueError("trusted signer needs a valid key ID, principal and Ed25519 public key")
        if not self.permitted_roles:
            raise ValueError("trusted signer must have at least one permitted role")


@dataclass(frozen=True)
class TrustPolicy:
    signers: dict[str, TrustedSigner]
    independent_principal_exclusions: frozenset[str]

    def __post_init__(self) -> None:
        if not self.signers or any(key != signer.key_id for key, signer in self.signers.items()):
            raise ValueError("trust policy key map must be nonempty and keyed by signer key ID")
        if not self.independent_principal_exclusions:
            raise ValueError("trust policy must exclude arm authors/operators from independent roles")
        object.__setattr__(self, "signers", MappingProxyType(dict(self.signers)))


def verify_protected_directory(path: str | Path) -> None:
    """Require a POSIX directory path that cannot be redirected by untrusted users."""
    directory = Path(path)
    if os.name != "posix" or os.open not in getattr(os, "supports_dir_fd", set()):
        raise EvidenceError("protected evidence roots require POSIX descriptor-relative no-follow opens")
    if not directory.is_absolute() or ".." in directory.parts:
        raise EvidenceError("protected directory path must be absolute and traversal-free")
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not nofollow_flag:
        raise EvidenceError("platform cannot securely open the protected directory path")
    descriptors: list[int] = []
    try:
        current_fd = os.open("/", os.O_RDONLY | directory_flag | nofollow_flag)
        descriptors.append(current_fd)
        service_uid = os.geteuid() if hasattr(os, "geteuid") else 0
        parts = directory.parts[1:]
        for index, part in enumerate(parts):
            next_fd = os.open(part, os.O_RDONLY | directory_flag | nofollow_flag, dir_fd=current_fd)
            descriptors.append(next_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise EvidenceError("protected path contains a non-directory component")
            is_final = index == len(parts) - 1
            trusted_owner = info.st_uid in {0, service_uid}
            sticky_shared = bool(info.st_mode & stat.S_ISVTX)
            if is_final:
                if info.st_mode & 0o022 or not trusted_owner:
                    raise EvidenceError("protected directory must be root/service-owned and not group/other writable")
            elif (not trusted_owner or info.st_mode & 0o022) and not sticky_shared:
                raise EvidenceError("protected path contains an untrusted writable directory")
            current_fd = next_fd
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceError("protected directory is missing, symlinked, or unreadable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def load_trust_policy(path: str | Path) -> tuple[TrustPolicy, str]:
    """Load a canonical public-key trust root from a protected local file."""
    policy_path = Path(path)
    if os.name != "posix" or os.open not in getattr(os, "supports_dir_fd", set()):
        raise EvidenceError("protected trust-policy loading requires POSIX descriptor-relative no-follow opens")
    if not policy_path.is_absolute() or ".." in policy_path.parts:
        raise EvidenceError("trust policy path must be absolute and traversal-free")
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not nofollow_flag:
        raise EvidenceError("platform cannot securely open the protected trust-policy path")
    descriptors: list[int] = []
    try:
        current_fd = os.open("/", os.O_RDONLY | directory_flag | nofollow_flag)
        descriptors.append(current_fd)
        service_uid = os.geteuid() if hasattr(os, "geteuid") else 0
        for part in policy_path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | directory_flag | nofollow_flag, dir_fd=current_fd)
            descriptors.append(next_fd)
            directory_info = os.fstat(next_fd)
            trusted_owners = {0, service_uid}
            sticky_shared = bool(directory_info.st_mode & stat.S_ISVTX)
            if (not stat.S_ISDIR(directory_info.st_mode)
                    or ((directory_info.st_uid not in trusted_owners
                         or directory_info.st_mode & 0o022) and not sticky_shared)):
                raise EvidenceError("trust policy path contains an untrusted writable directory")
            current_fd = next_fd
        parent_info = os.fstat(current_fd)
        if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_mode & 0o022
                or parent_info.st_uid not in {0, service_uid}):
            raise EvidenceError("trust policy directory must be a protected non-writable directory")
        flags = os.O_RDONLY | nofollow_flag | getattr(os, "O_BINARY", 0)
        fd = os.open(policy_path.parts[-1], flags, dir_fd=current_fd)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise EvidenceError("trust policy must be a regular file not writable by group or others")
        if info.st_uid not in {0, service_uid}:
            raise EvidenceError("trust policy must be owned by root or the current service user")
        raw_buffer = bytearray()
        while len(raw_buffer) <= MAX_TRUST_POLICY_BYTES:
            chunk = os.read(fd, min(65536, MAX_TRUST_POLICY_BYTES + 1 - len(raw_buffer)))
            if not chunk:
                break
            raw_buffer.extend(chunk)
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceError("trust policy is missing or unreadable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if len(raw_buffer) > MAX_TRUST_POLICY_BYTES:
        raise EvidenceError("trust policy exceeds its configured byte limit")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate trust-policy key {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(bytes(raw_buffer), object_pairs_hook=unique_object)
        if canonical_bytes(document) != bytes(raw_buffer):
            raise ValueError("trust policy must use canonical JSON")
        if set(document) != {"schema_version", "independent_principal_exclusions", "signers"}:
            raise ValueError("trust policy has unexpected fields")
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("unsupported trust-policy schema version")
        if not isinstance(document["signers"], list):
            raise ValueError("trust policy signers must be a list")
        signers = {}
        public_key_fingerprints = set()
        for item in document["signers"]:
            if not isinstance(item, dict):
                raise ValueError("each trust signer must be an object")
            if set(item) != {"key_id", "principal_id", "public_key_ed25519", "permitted_roles", "revoked"}:
                raise ValueError("trust signer has unexpected fields")
            if (not isinstance(item["key_id"], str) or not isinstance(item["principal_id"], str)
                    or not isinstance(item["public_key_ed25519"], str)
                    or not isinstance(item["permitted_roles"], list)
                    or any(not isinstance(role, str) or not role for role in item["permitted_roles"])
                    or len(item["permitted_roles"]) != len(set(item["permitted_roles"]))
                    or type(item["revoked"]) is not bool):
                raise ValueError("trust signer fields have invalid types or duplicate roles")
            signer = TrustedSigner(
                key_id=item["key_id"], principal_id=item["principal_id"],
                public_key=bytes.fromhex(item["public_key_ed25519"]),
                permitted_roles=frozenset(item["permitted_roles"]), revoked=item["revoked"],
            )
            if signer.key_id in signers:
                raise ValueError("trust policy contains duplicate signer key IDs")
            fingerprint = hashlib.sha256(signer.public_key).hexdigest()
            if fingerprint in public_key_fingerprints:
                raise ValueError("trust policy must not alias one public key under multiple signer identities")
            public_key_fingerprints.add(fingerprint)
            signers[signer.key_id] = signer
        exclusions = document["independent_principal_exclusions"]
        if (not isinstance(exclusions, list) or any(not isinstance(value, str) or not value for value in exclusions)
                or len(exclusions) != len(set(exclusions))):
            raise ValueError("trust-policy exclusions must be unique nonempty principal IDs")
        policy = TrustPolicy(signers=signers, independent_principal_exclusions=frozenset(exclusions))
    except (TypeError, ValueError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"trust policy is malformed: {exc}") from exc
    return policy, hashlib.sha256(bytes(raw_buffer)).hexdigest()


@dataclass(frozen=True)
class VerifiedEvidence:
    reference: EvidenceArtifactRef
    document: SignedEvidence


def canonical_bytes(value: BaseModel | dict) -> bytes:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sign_evidence(document: dict, *, key_id: str, principal_id: str,
                  private_key: Ed25519PrivateKey) -> bytes:
    """Create an issuer artifact; private keys are never part of TrustPolicy."""
    unsigned = dict(document)
    unsigned.update({"issuer_key_id": key_id, "issuer_principal_id": principal_id})
    unsigned.pop("signature_ed25519", None)
    signature = private_key.sign(canonical_bytes(unsigned)).hex()
    return canonical_bytes({**unsigned, "signature_ed25519": signature})


def _read_evidence_bounded(root: Path, relative: PurePosixPath, *, max_bytes: int = MAX_EVIDENCE_BYTES) -> bytes:
    """Open without following symlinks and cap bytes actually read."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    binary_flag = getattr(os, "O_BINARY", 0)
    read_flag = os.O_RDONLY | nofollow_flag | binary_flag
    descriptors: list[int] = []
    try:
        if os.open in getattr(os, "supports_dir_fd", set()) and directory_flag and nofollow_flag:
            current_fd = os.open(root, os.O_RDONLY | directory_flag | nofollow_flag)
            descriptors.append(current_fd)
            for part in relative.parts[:-1]:
                current_fd = os.open(part, os.O_RDONLY | directory_flag | nofollow_flag,
                                     dir_fd=current_fd)
                descriptors.append(current_fd)
            fd = os.open(relative.parts[-1], read_flag, dir_fd=current_fd)
        else:
            fd = os.open(root.joinpath(*relative.parts), read_flag)
        descriptors.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EvidenceError("evidence artifact is not a regular file")
        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > max_bytes:
            raise EvidenceError("artifact exceeds its configured byte limit")
        return bytes(content)
    except OSError as exc:
        raise EvidenceError("evidence artifact is missing, symlinked, or unreadable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_content_artifact(
    *, root: str | Path, reference: EvidenceArtifactRef, expected_sha256: str,
    max_bytes: int = MAX_SCORE_OUTPUT_BYTES,
) -> str:
    """Verify bounded regular-file bytes under a trusted root without following symlinks."""
    raw_path = reference.path
    if "\\" in raw_path or "\x00" in raw_path:
        raise EvidenceError("content artifact path must be canonical relative POSIX")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or relative.as_posix() != raw_path or any(
        part in {"", ".", ".."} for part in raw_path.split("/")
    ):
        raise EvidenceError("content artifact path is absolute or traverses outside its root")
    trusted_root = Path(root).resolve()
    path = trusted_root.joinpath(*relative.parts)
    cursor = trusted_root
    contains_symlink = False
    for part in relative.parts:
        cursor = cursor / part
        contains_symlink = contains_symlink or cursor.is_symlink()
    if contains_symlink or not path.is_file() or not path.resolve().is_relative_to(trusted_root):
        raise EvidenceError("content artifact is missing, symlinked, or outside the trusted root")
    content = _read_evidence_bounded(trusted_root, relative, max_bytes=max_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if digest != reference.artifact_sha256 or digest != expected_sha256:
        raise EvidenceError("content artifact bytes do not match the pinned SHA-256")
    return digest


def verify_evidence_artifact(
    *, root: str | Path, reference: EvidenceArtifactRef, policy: TrustPolicy,
    evidence_type: str, subject_sha: str, conclusion: str, issuer_role: str,
    expected_payload: dict[str, str] | None = None,
) -> VerifiedEvidence:
    """Verify artifact bytes, canonical encoding, signer authorization and scope."""
    raw_path = reference.path
    if "\\" in raw_path or "\x00" in raw_path:
        raise EvidenceError("evidence artifact path must be canonical relative POSIX")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or relative.as_posix() != raw_path or any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise EvidenceError("evidence artifact path is absolute or traverses outside its root")
    trusted_root = Path(root).resolve()
    path = trusted_root.joinpath(*relative.parts)
    cursor = trusted_root
    contains_symlink = False
    for part in relative.parts:
        cursor = cursor / part
        contains_symlink = contains_symlink or cursor.is_symlink()
    if contains_symlink or not path.is_file() or not path.resolve().is_relative_to(trusted_root):
        raise EvidenceError("evidence artifact is missing, symlinked, or outside the trusted root")
    raw = _read_evidence_bounded(trusted_root, relative)
    if hashlib.sha256(raw).hexdigest() != reference.artifact_sha256:
        raise EvidenceError("evidence artifact bytes do not match the pinned SHA-256")
    try:
        decoded = json.loads(raw)
        document = SignedEvidence.model_validate(decoded, strict=True)
    except Exception as exc:
        raise EvidenceError("evidence artifact is not a valid signed document") from exc
    if canonical_bytes(document) != raw:
        raise EvidenceError("evidence artifact must use canonical JSON encoding")
    signer = policy.signers.get(document.issuer_key_id)
    if signer is None or signer.revoked:
        raise EvidenceError("evidence signer is unknown or revoked")
    if signer.principal_id != document.issuer_principal_id:
        raise EvidenceError("evidence signer principal does not match the trust policy")
    if issuer_role not in signer.permitted_roles or document.issuer_role != issuer_role:
        raise EvidenceError("evidence signer is not trusted for the required role")
    try:
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
            bytes.fromhex(document.signature_ed25519), canonical_bytes(document.unsigned_payload()),
        )
    except (InvalidSignature, ValueError):
        raise EvidenceError("evidence signature verification failed")
    if (document.evidence_type != evidence_type or document.subject_sha != subject_sha
            or document.conclusion != conclusion):
        raise EvidenceError("signed evidence type, subject or conclusion does not match the gate")
    if expected_payload is not None and any(document.payload.get(key) != value
                                            for key, value in expected_payload.items()):
        raise EvidenceError("signed evidence payload does not match the frozen gate inputs")
    return VerifiedEvidence(reference=reference, document=document)
