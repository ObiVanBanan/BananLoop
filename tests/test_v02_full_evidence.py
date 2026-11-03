import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from benchmarks.v02_full.evidence import (
    EvidenceArtifactRef, EvidenceError, TrustPolicy, TrustedSigner, sign_evidence,
    verify_evidence_artifact,
)


def _signed_file(root, signer, *, subject=None, role="INDEPENDENT_TECHNICAL_REVIEWER"):
    payload = {"schema_version": 1, "evidence_type": "TECHNICAL_REVIEW",
               "subject_sha": subject or "a" * 64, "conclusion": "PASS",
               "issuer_role": role, "payload": {"review": "accepted"}}
    raw = sign_evidence(payload, key_id=signer.key_id, principal_id=signer.principal_id,
                        private_key=_private_key())
    path = root / "review.json"
    path.write_bytes(raw)
    ref = EvidenceArtifactRef(path="review.json", artifact_sha256=hashlib.sha256(raw).hexdigest())
    return path, ref


def _signer(*, revoked=False):
    return TrustedSigner(
        key_id="reviewer-key", principal_id="reviewer-1",
        public_key=_private_key().public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        ),
        permitted_roles=frozenset({"INDEPENDENT_TECHNICAL_REVIEWER"}), revoked=revoked,
    )


def _private_key():
    return Ed25519PrivateKey.from_private_bytes(b"r" * 32)


def _policy(*signers):
    return TrustPolicy({item.key_id: item for item in signers},
                       independent_principal_exclusions=frozenset({"arm-author", "arm-operator"}))


def test_evidence_verifier_accepts_a_valid_canonical_authenticated_document(tmp_path):
    signer = _signer()
    _, reference = _signed_file(tmp_path, signer)
    verified = verify_evidence_artifact(
        root=tmp_path, reference=reference,
        policy=_policy(signer), evidence_type="TECHNICAL_REVIEW",
        subject_sha="a" * 64, conclusion="PASS", issuer_role="INDEPENDENT_TECHNICAL_REVIEWER",
        expected_payload={"review": "accepted"},
    )
    assert verified.document.issuer_principal_id == "reviewer-1"


@pytest.mark.parametrize("failure", ["missing", "changed_bytes", "bad_signature", "wrong_subject", "wrong_role"])
def test_evidence_verifier_rejects_invalid_or_out_of_scope_artifacts(tmp_path, failure):
    signer = _signer()
    path, reference = _signed_file(tmp_path, signer)
    if failure == "missing":
        path.unlink()
        with pytest.raises(EvidenceError, match="missing"):
            verify_evidence_artifact(
                root=tmp_path, reference=reference, policy=_policy(signer),
                evidence_type="TECHNICAL_REVIEW", subject_sha="a" * 64, conclusion="PASS",
                issuer_role="INDEPENDENT_TECHNICAL_REVIEWER",
            )
        return
    if failure == "changed_bytes":
        path.write_bytes(path.read_bytes() + b" ")
        expected_ref = reference
    elif failure == "bad_signature":
        changed = json.loads(path.read_bytes())
        changed["signature_ed25519"] = "0" * 128
        raw = json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        path.write_bytes(raw)
        expected_ref = EvidenceArtifactRef(path="review.json", artifact_sha256=hashlib.sha256(raw).hexdigest())
    else:
        expected_ref = reference
    with pytest.raises(EvidenceError):
        verify_evidence_artifact(
                root=tmp_path, reference=expected_ref, policy=_policy(signer),
            evidence_type="TECHNICAL_REVIEW",
            subject_sha=("b" * 64 if failure == "wrong_subject" else "a" * 64),
            conclusion="PASS",
            issuer_role=("SCREEN_READER_REVIEWER" if failure == "wrong_role"
                         else "INDEPENDENT_TECHNICAL_REVIEWER"),
        )


def test_evidence_verifier_rejects_unknown_revoked_and_symlinked_signers(tmp_path):
    signer = _signer()
    path, reference = _signed_file(tmp_path, signer)
    other_private = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
    other = TrustedSigner(key_id="other-key", principal_id="other",
                          public_key=other_private.public_key().public_bytes(
                              encoding=serialization.Encoding.Raw,
                              format=serialization.PublicFormat.Raw,
                          ),
                          permitted_roles=frozenset({"INDEPENDENT_TECHNICAL_REVIEWER"}))
    with pytest.raises(EvidenceError, match="unknown or revoked"):
        verify_evidence_artifact(
            root=tmp_path, reference=reference, policy=_policy(other),
            evidence_type="TECHNICAL_REVIEW", subject_sha="a" * 64, conclusion="PASS",
            issuer_role="INDEPENDENT_TECHNICAL_REVIEWER",
        )
    revoked = _signer(revoked=True)
    with pytest.raises(EvidenceError, match="unknown or revoked"):
        verify_evidence_artifact(
            root=tmp_path, reference=reference, policy=_policy(revoked),
            evidence_type="TECHNICAL_REVIEW", subject_sha="a" * 64, conclusion="PASS",
            issuer_role="INDEPENDENT_TECHNICAL_REVIEWER",
        )
    link = tmp_path / "nested-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    linked_ref = EvidenceArtifactRef(path="nested-link/review.json", artifact_sha256=reference.artifact_sha256)
    with pytest.raises(EvidenceError, match="symlinked"):
        verify_evidence_artifact(
            root=tmp_path, reference=linked_ref, policy=_policy(signer),
            evidence_type="TECHNICAL_REVIEW", subject_sha="a" * 64, conclusion="PASS",
            issuer_role="INDEPENDENT_TECHNICAL_REVIEWER",
        )
