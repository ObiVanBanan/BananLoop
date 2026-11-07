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
import ctypes
import ctypes.wintypes
import functools
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
_WINDOWS_SYSTEM_SID = b"\x01\x01\x00\x00\x00\x00\x00\x05\x12\x00\x00\x00"
_WINDOWS_ADMINISTRATORS_SID = b"\x01\x02\x00\x00\x00\x00\x00\x05\x20\x00\x00\x00\x20\x02\x00\x00"
_WINDOWS_OWNER_RIGHTS_SID = b"\x01\x01\x00\x00\x00\x00\x00\x03\x04\x00\x00\x00"
_WINDOWS_ALLOWED_ACE_TYPES = frozenset({0, 4, 5, 9, 11})
_WINDOWS_NON_GRANT_ACE_TYPES = frozenset({1, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17,
                                         18, 19, 20, 21})


class EvidenceError(ValueError):
    """A gate artifact is missing, malformed, unsigned, or out of scope."""


def _sid_bytes_from_ace(ace_bytes: bytes, sid_offset: int) -> bytes:
    """Copy one structurally valid SID wholly contained in a bounded ACE."""
    if (not isinstance(ace_bytes, bytes) or type(sid_offset) is not int
            or sid_offset < 0 or sid_offset + 8 > len(ace_bytes)):
        raise EvidenceError("protected Windows ACL entry has a truncated SID")
    revision, subauthority_count = ace_bytes[sid_offset:sid_offset + 2]
    if revision != 1 or subauthority_count > 15:
        raise EvidenceError("protected Windows ACL entry has an invalid SID header")
    sid_size = 8 + 4 * subauthority_count
    if sid_offset + sid_size > len(ace_bytes):
        raise EvidenceError("protected Windows ACL entry has a truncated SID")
    return ace_bytes[sid_offset:sid_offset + sid_size]


def _windows_ace_is_allowing(ace_type: int) -> bool:
    """Classify known ACE kinds; reject new/unknown types fail-closed."""
    if ace_type in _WINDOWS_ALLOWED_ACE_TYPES:
        return True
    if ace_type in _WINDOWS_NON_GRANT_ACE_TYPES:
        return False
    raise EvidenceError("protected Windows DACL contains an unsupported ACE type")


def _windows_acl_writer_is_trusted(sid: bytes, *, user_sid: bytes, owner_sid: bytes) -> bool:
    """Treat OWNER RIGHTS as trusted only after that path's owner is trusted."""
    trusted_principals = {
        user_sid, _WINDOWS_SYSTEM_SID, _WINDOWS_ADMINISTRATORS_SID,
    }
    return sid in trusted_principals or (
        sid == _WINDOWS_OWNER_RIGHTS_SID and owner_sid in trusted_principals
    )


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
        "OUTPUT_ANONYMIZATION", "BLINDING_ASSIGNMENT", "SCORING_REQUEST",
        "WORKER_LAUNCH",
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


@functools.lru_cache(maxsize=1)
def _windows_current_user_sid() -> bytes:
    """Return the current process token SID for Windows ACL checks."""
    if os.name != "nt":
        raise EvidenceError("Windows token identity is unavailable on this platform")
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD,
                                        ctypes.POINTER(ctypes.c_void_p)]
    advapi.OpenProcessToken.restype = ctypes.wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                            ctypes.wintypes.DWORD,
                                            ctypes.POINTER(ctypes.wintypes.DWORD)]
    advapi.GetTokenInformation.restype = ctypes.wintypes.BOOL
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = ctypes.wintypes.DWORD
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.wintypes.BOOL

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    token = ctypes.c_void_p()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise EvidenceError("cannot inspect the Windows service identity")
    try:
        required = ctypes.wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if not required.value:
            raise EvidenceError("cannot inspect the Windows service identity")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, buffer, required.value, ctypes.byref(required)):
            raise EvidenceError("cannot inspect the Windows service identity")
        sid = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents.User.Sid
        length = advapi.GetLengthSid(sid)
        if not length:
            raise EvidenceError("Windows service token has no valid user SID")
        return ctypes.string_at(sid, length)
    finally:
        kernel.CloseHandle(token)


def _windows_protected_acl(path: Path) -> None:
    """Require service, SYSTEM, or Administrators to own/change this ACL.

    ACL checks are path-based because Python does not expose Windows
    ``openat``. Every ancestor is checked before the leaf, so untrusted users
    cannot replace a checked component. The service identity, SYSTEM, and
    local Administrators are the trusted OS principals; protecting against a
    concurrent replacement by one of those principals is out of scope.
    """
    if os.name != "nt":
        raise EvidenceError("Windows ACL verification is unavailable on this platform")
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32", use_last_error=True)
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = ctypes.wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", ctypes.wintypes.DWORD),
            ("AclBytesInUse", ctypes.wintypes.DWORD),
            ("AclBytesFree", ctypes.wintypes.DWORD),
        ]

    get_security = advapi.GetNamedSecurityInfoW
    get_security.argtypes = [
        ctypes.c_wchar_p, ctypes.c_int, ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = ctypes.wintypes.DWORD
    get_acl_info = advapi.GetAclInformation
    get_acl_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.c_int]
    get_acl_info.restype = ctypes.wintypes.BOOL
    get_ace = advapi.GetAce
    get_ace.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    get_ace.restype = ctypes.wintypes.BOOL
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    status = get_security(str(path), 1, 0x00000001 | 0x00000004,
                          ctypes.byref(owner), None, ctypes.byref(dacl), None,
                          ctypes.byref(descriptor))
    if status != 0:
        raise EvidenceError("cannot read the protected Windows ACL")
    try:
        user_sid = _windows_current_user_sid()
        if not owner.value or not dacl.value:
            raise EvidenceError("protected Windows path requires an explicit owner and DACL")
        owner_sid = ctypes.string_at(owner, advapi.GetLengthSid(owner))
        trusted_owner = owner_sid in {
            user_sid, _WINDOWS_SYSTEM_SID, _WINDOWS_ADMINISTRATORS_SID,
        }
        if not trusted_owner:
            raise EvidenceError("protected Windows path has an untrusted owner")

        info = AclSizeInformation()
        if not get_acl_info(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise EvidenceError("cannot inspect the protected Windows DACL")
        if (info.AclBytesInUse < 8
                or info.AceCount > (info.AclBytesInUse - 8) // 4):
            raise EvidenceError("protected Windows DACL has inconsistent bounds")
        write_mask = (
            0x00000002 | 0x00000004 | 0x00000010 | 0x00000040 | 0x00000100
            | 0x00010000 | 0x00040000 | 0x00080000
            | 0x10000000 | 0x40000000
        )
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace)) or not ace.value:
                raise EvidenceError("cannot inspect a protected Windows ACL entry")
            header = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte * 4)).contents
            ace_type = int(header[0])
            ace_size = int(header[2]) | (int(header[3]) << 8)
            ace_address = ace.value
            acl_address = dacl.value
            if (ace_size < 8 or ace_size % 4
                    or ace_address < acl_address + 8
                    or ace_address + ace_size > acl_address + info.AclBytesInUse):
                raise EvidenceError("protected Windows ACL entry is malformed")
            ace_bytes = ctypes.string_at(ace, ace_size)
            if int(header[1]) & 0x08:  # INHERIT_ONLY_ACE does not apply to this object.
                continue
            if not _windows_ace_is_allowing(ace_type):
                continue
            mask = int.from_bytes(ace_bytes[4:8], "little")
            if not mask & write_mask:
                continue
            if ace_type == 4:  # ACCESS_ALLOWED_COMPOUND_ACE
                sid_offset = 12
            elif ace_type in {5, 11}:  # object ACEs have optional GUID fields
                if ace_size < 12:
                    raise EvidenceError("protected Windows object ACE is malformed")
                object_flags = int.from_bytes(ace_bytes[8:12], "little")
                if object_flags & ~0x3:
                    raise EvidenceError("protected Windows object ACE has invalid flags")
                sid_offset = 12 + (16 if object_flags & 0x1 else 0) + (16 if object_flags & 0x2 else 0)
            elif ace_type in {0, 9}:  # basic and callback ACEs
                sid_offset = 8
            else:
                raise EvidenceError("protected Windows path has an unsupported write ACL entry")
            sid_bytes = _sid_bytes_from_ace(ace_bytes, sid_offset)
            # OWNER RIGHTS (S-1-3-4) is scoped to this ACL's owner, already
            # checked above. It cannot grant a separate untrusted principal.
            trusted_writer = _windows_acl_writer_is_trusted(
                sid_bytes, user_sid=user_sid, owner_sid=owner_sid,
            )
            if not trusted_writer:
                raise EvidenceError(
                    "protected Windows path is writable by an untrusted principal "
                    f"(sid={sid_bytes.hex()}, mask=0x{mask:08x})"
                )
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)


def _verify_windows_path(path: Path, *, final_is_directory: bool) -> None:
    """Verify local path components before reading a Windows trust root."""
    if not path.is_absolute() or ".." in path.parts or path.drive.startswith("\\\\"):
        raise EvidenceError("protected Windows path must be absolute, local, and traversal-free")
    current = Path(path.anchor)
    components = path.parts[1:]
    if not components:
        raise EvidenceError("protected Windows path cannot be a filesystem root")
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    for index, component in enumerate(components):
        current = current / component
        final = index == len(components) - 1
        info = current.lstat()
        is_directory = stat.S_ISDIR(info.st_mode)
        is_regular_file = stat.S_ISREG(info.st_mode)
        expected_type = (
            is_directory if final_is_directory else is_regular_file
        ) if final else is_directory
        if (getattr(info, "st_file_attributes", 0) & reparse
                or not expected_type):
            raise EvidenceError("protected Windows path contains a reparse point or wrong object type")
        _windows_protected_acl(current)


def verify_protected_directory(path: str | Path) -> None:
    """Require a directory path protected from untrusted path replacement."""
    directory = Path(path)
    if os.name == "nt":
        try:
            _verify_windows_path(directory, final_is_directory=True)
        except EvidenceError:
            raise
        except OSError as exc:
            raise EvidenceError("protected Windows directory is missing or unreadable") from exc
        return
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


def _parse_trust_policy(raw: bytes) -> tuple[TrustPolicy, str]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate trust-policy key {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(raw, object_pairs_hook=unique_object)
        if canonical_bytes(document) != raw:
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
    return policy, hashlib.sha256(raw).hexdigest()


def load_trust_policy(path: str | Path) -> tuple[TrustPolicy, str]:
    """Load a canonical public-key trust root from a protected local file."""
    policy_path = Path(path)
    if os.name == "nt":
        raw = bytearray()
        try:
            _verify_windows_path(policy_path, final_is_directory=False)
            with policy_path.open("rb") as stream:
                while len(raw) <= MAX_TRUST_POLICY_BYTES:
                    chunk = stream.read(min(65536, MAX_TRUST_POLICY_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
        except EvidenceError:
            raise
        except OSError as exc:
            raise EvidenceError("trust policy is missing or unreadable") from exc
        if len(raw) > MAX_TRUST_POLICY_BYTES:
            raise EvidenceError("trust policy exceeds its configured byte limit")
        return _parse_trust_policy(bytes(raw))
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

    return _parse_trust_policy(bytes(raw_buffer))

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
            target = root.joinpath(*relative.parts)
            if os.name == "nt":
                _verify_windows_path(target, final_is_directory=False)
            fd = os.open(target, read_flag)
        descriptors.append(fd)
        file_info = os.fstat(fd)
        if (not stat.S_ISREG(file_info.st_mode)
                or (os.name == "nt" and getattr(file_info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))):
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


def read_content_artifact(
    *, root: str | Path, reference: EvidenceArtifactRef, expected_sha256: str,
    max_bytes: int = MAX_SCORE_OUTPUT_BYTES,
) -> bytes:
    """Read and verify bounded regular-file bytes without following symlinks."""
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
    return content


def verify_content_artifact(
    *, root: str | Path, reference: EvidenceArtifactRef, expected_sha256: str,
    max_bytes: int = MAX_SCORE_OUTPUT_BYTES,
) -> str:
    """Verify bounded regular-file bytes under a trusted root without following symlinks."""
    content = read_content_artifact(
        root=root, reference=reference, expected_sha256=expected_sha256, max_bytes=max_bytes,
    )
    return hashlib.sha256(content).hexdigest()


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


def verify_output_anonymization(
    *, root: str | Path, policy: TrustPolicy, reference: EvidenceArtifactRef,
    pair_id_sha256: str, arm_label: Literal["A", "B"],
    raw_reference: EvidenceArtifactRef, raw_sha256: str,
    sanitized_reference: EvidenceArtifactRef, sanitized_sha256: str,
    anonymizer_sha256: str,
) -> VerifiedEvidence:
    """Verify retained raw/sanitized bytes and independent approval of their mapping."""
    verify_content_artifact(
        root=root, reference=raw_reference, expected_sha256=raw_sha256,
    )
    verify_content_artifact(
        root=root, reference=sanitized_reference, expected_sha256=sanitized_sha256,
    )
    verified = verify_evidence_artifact(
        root=root, reference=reference, policy=policy,
        evidence_type="OUTPUT_ANONYMIZATION", subject_sha=raw_sha256,
        conclusion="ACCEPTED", issuer_role="INDEPENDENT_ANONYMIZER",
        expected_payload={
            "pair_id_sha256": pair_id_sha256,
            "arm_label": arm_label,
            "raw_output_sha256": raw_sha256,
            "sanitized_output_sha256": sanitized_sha256,
            "anonymizer_sha256": anonymizer_sha256,
        },
    )
    if set(verified.document.payload) != {
        "pair_id_sha256", "arm_label", "raw_output_sha256",
        "sanitized_output_sha256", "anonymizer_sha256",
    }:
        raise EvidenceError("output anonymization attestation has unrelated payload fields")
    if verified.document.issuer_principal_id in policy.independent_principal_exclusions:
        raise EvidenceError("an arm author/operator cannot attest output anonymization")
    return verified
