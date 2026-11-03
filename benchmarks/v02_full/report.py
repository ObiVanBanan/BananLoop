"""Deterministic blinded-score verification and paired report projection.

This consumes sealed score artifacts after both arms finish. It is not a runner
or scorer and does not reveal task IDs or evaluator inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .accounting import ActionReceipt, AccountingResult, validate_accounting_chain
from .evidence import (
    EvidenceArtifactRef, EvidenceError, TrustPolicy,
    verify_evidence_artifact, verify_output_anonymization,
)
from .prereg import (
    GateEvidence, InferencePlan, PreregError, ResourceCaps, TrackManifest, _normal_critical,
    _student_t_critical_approx, blind_label_assignment_digest, canonical_bytes, validate_run_gate,
)


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ActionReceiptAttestation(ReportModel):
    receipt_sha256: Digest
    evidence_ref: EvidenceArtifactRef


class BlindedPairScore(ReportModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_score: float | None = None
    arm_b_score: float | None = None
    arm_a_output_sha256: Digest | None = None
    arm_b_output_sha256: Digest | None = None
    arm_a_output_ref: EvidenceArtifactRef | None = None
    arm_b_output_ref: EvidenceArtifactRef | None = None
    arm_a_raw_output_sha256: Digest | None = None
    arm_b_raw_output_sha256: Digest | None = None
    arm_a_raw_output_ref: EvidenceArtifactRef | None = None
    arm_b_raw_output_ref: EvidenceArtifactRef | None = None
    arm_a_anonymization_attestation: EvidenceArtifactRef | None = None
    arm_b_anonymization_attestation: EvidenceArtifactRef | None = None
    arm_a_receipts: tuple[ActionReceipt, ...] = ()
    arm_b_receipts: tuple[ActionReceipt, ...] = ()
    arm_a_action_attestations: tuple[ActionReceiptAttestation, ...] = ()
    arm_b_action_attestations: tuple[ActionReceiptAttestation, ...] = ()
    arm_a_ledger_attestation: EvidenceArtifactRef | None = None
    arm_b_ledger_attestation: EvidenceArtifactRef | None = None
    arm_a_accounting: AccountingResult | None = None
    arm_b_accounting: AccountingResult | None = None
    reason: str | None = None
    pair_result_sha256: Digest

    @model_validator(mode="after")
    def validate_result_identity(self) -> "BlindedPairScore":
        if self.status == "SCORED":
            if any(value is None for value in (self.arm_a_score, self.arm_b_score,
                                               self.arm_a_output_sha256, self.arm_b_output_sha256)):
                raise ValueError("scored pair requires both anonymized outputs and scores")
            if self.arm_a_output_ref is None or self.arm_b_output_ref is None:
                raise ValueError("scored pair requires content-addressed output artifact refs")
            if any(value is None for value in (
                self.arm_a_raw_output_sha256, self.arm_b_raw_output_sha256,
                self.arm_a_raw_output_ref, self.arm_b_raw_output_ref,
                self.arm_a_anonymization_attestation, self.arm_b_anonymization_attestation,
            )):
                raise ValueError("scored pair requires raw output evidence and independent anonymization attestations")
            if self.reason is not None:
                raise ValueError("scored pair cannot have a failure/exclusion reason")
        else:
            if self.arm_a_score is not None or self.arm_b_score is not None:
                raise ValueError("unscored pair must not contain a score")
            if self.reason is None or not self.reason.strip():
                raise ValueError("failed, excluded, and missing pairs require a reason")
        if ((self.arm_a_output_sha256 is None) != (self.arm_a_output_ref is None)
                or (self.arm_b_output_sha256 is None) != (self.arm_b_output_ref is None)):
            raise ValueError("each output digest must be paired with its artifact reference")
        if ((self.arm_a_raw_output_sha256 is None) != (self.arm_a_raw_output_ref is None)
                or (self.arm_b_raw_output_sha256 is None) != (self.arm_b_raw_output_ref is None)):
            raise ValueError("each raw output digest must be paired with its artifact reference")
        if ((self.arm_a_raw_output_sha256 is None) != (self.arm_a_anonymization_attestation is None)
                or (self.arm_b_raw_output_sha256 is None) != (self.arm_b_anonymization_attestation is None)):
            raise ValueError("raw outputs must have corresponding anonymization attestations")
        if (self.arm_a_output_ref is not None
                and self.arm_a_output_ref.artifact_sha256 != self.arm_a_output_sha256):
            raise ValueError("arm A output ref must match its output digest")
        if (self.arm_b_output_ref is not None
                and self.arm_b_output_ref.artifact_sha256 != self.arm_b_output_sha256):
            raise ValueError("arm B output ref must match its output digest")
        if (self.arm_a_raw_output_ref is not None
                and self.arm_a_raw_output_ref.artifact_sha256 != self.arm_a_raw_output_sha256):
            raise ValueError("arm A raw output ref must match its output digest")
        if (self.arm_b_raw_output_ref is not None
                and self.arm_b_raw_output_ref.artifact_sha256 != self.arm_b_raw_output_sha256):
            raise ValueError("arm B raw output ref must match its output digest")
        if self.pair_result_sha256 != pair_score_digest(self):
            raise ValueError("pair result digest does not match its canonical score record")
        return self

    def content_payload(self) -> dict:
        value = self.model_dump(mode="json")
        value.pop("pair_result_sha256")
        return value


class BlindedScorePackage(ReportModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    evaluator_pin_sha256: Digest
    pairs: tuple[BlindedPairScore, ...] = Field(min_length=1)
    score_payload_sha256: Digest
    package_sha256: Digest

    @model_validator(mode="after")
    def package_is_canonical(self) -> "BlindedScorePackage":
        pair_ids = tuple(item.pair_id_sha256 for item in self.pairs)
        if tuple(sorted(set(pair_ids))) != pair_ids:
            raise ValueError("blinded pair records must be unique and sorted by pair digest")
        if self.score_payload_sha256 != score_payload_digest(self):
            raise ValueError("score payload digest does not match scorer-visible fields")
        if self.package_sha256 != score_package_digest(self):
            raise ValueError("score package digest does not match its canonical contents")
        return self

    def content_payload(self) -> dict:
        value = self.model_dump(mode="json")
        value.pop("package_sha256")
        return value


class ReportPair(ReportModel):
    pair_id_sha256: Digest
    status: Literal["ELIGIBLE", "INELIGIBLE_ACCOUNTING", "FAILED", "EXCLUDED", "MISSING"]
    accounting_status: Literal["ELIGIBLE", "INELIGIBLE", "UNKNOWN"]
    bananloop_score: float | None
    codex_direct_score: float | None
    oriented_benefit_delta: float | None
    bananloop_accounting: AccountingResult | None
    codex_direct_accounting: AccountingResult | None
    reason: str | None


class PairedReport(ReportModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    gate_evidence_sha256: Digest
    score_package_sha256: Digest
    evaluator_pin_sha256: Digest
    bananloop_arm_label: Literal["A", "B"]
    decision: Literal[
        "BANANLOOP_SUPERIOR", "CODEX_DIRECT_SUPERIOR", "NO_DETECTED_DIFFERENCE", "UNDERPOWERED",
    ]
    planned_pairs: int
    usable_pairs: int
    required_pairs: int
    wins: int
    ties: int
    losses: int
    mean_oriented_benefit_delta: float | None
    interval_low: float | None
    interval_high: float | None
    pair_results: tuple[ReportPair, ...]
    report_sha256: Digest

    @model_validator(mode="after")
    def report_digest_is_valid(self) -> "PairedReport":
        if self.report_sha256 != report_digest(self):
            raise ValueError("report digest does not match the canonical paired report")
        return self

    def content_payload(self) -> dict:
        value = self.model_dump(mode="json")
        value.pop("report_sha256")
        return value


class ReportError(ValueError):
    """A result package cannot support a reproducible blinded report."""


def _accounting_summary_is_eligible(result: AccountingResult | None, caps) -> bool:
    """Reject internally inconsistent summaries after action-origin verification."""
    if result is None or result.status != "ELIGIBLE" or result.action_count < 1:
        return False
    if result.final_receipt_sha256 is None:
        return False
    dimensions = set(ResourceCaps.model_fields)
    if set(result.totals) != dimensions or result.unknown_dimensions or result.over_cap_dimensions:
        return False
    for name in dimensions:
        value = result.totals[name]
        if type(value) is not int or value < 0 or value > getattr(caps, name):
            return False
    return True


def _verified_accounting(receipts, action_attestations, *, pair_id: str,
                         arm_label: Literal["A", "B"], caps,
                         claimed: AccountingResult | None, attestation: EvidenceArtifactRef | None,
                         manifest: TrackManifest, evidence_root: str,
                         trust_policy: TrustPolicy) -> tuple[AccountingResult | None, bool, tuple[str, bytes] | None]:
    """Recompute accounting from meter-attested receipts and a signed ledger head."""
    if not receipts or attestation is None or len(action_attestations) != len(receipts):
        return None, False, None
    try:
        if tuple(item.receipt_sha256 for item in action_attestations) != tuple(
                item.receipt_sha256 for item in receipts):
            return None, False, None
        meter_principals: set[str] = set()
        meter_keys: set[str] = set()
        settlements_sealed = True
        for receipt, receipt_attestation in zip(receipts, action_attestations, strict=True):
            usage_sha = _digest(receipt.usage)
            verified_action = verify_evidence_artifact(
                root=evidence_root, reference=receipt_attestation.evidence_ref,
                policy=trust_policy, evidence_type="ACTION_RECEIPT",
                subject_sha=receipt.receipt_sha256, conclusion="ACCEPTED",
                issuer_role="ACTION_METER_ATTESTOR",
                expected_payload={
                    "pair_id_sha256": pair_id,
                    "arm_label": arm_label,
                    "sequence": str(receipt.sequence),
                    "action_id_sha256": receipt.action_id,
                    "usage_sha256": usage_sha,
                },
            ).document
            if verified_action.issuer_principal_id in trust_policy.independent_principal_exclusions:
                return None, False, None
            meter_principals.add(verified_action.issuer_principal_id)
            meter_keys.add(verified_action.issuer_key_id)
            settlements_sealed = settlements_sealed and (
                verified_action.payload.get("settlement_state") == "SEALED"
            )
        if len(meter_principals) != 1 or len(meter_keys) != 1:
            return None, False, None
        head = receipts[-1].receipt_sha256
        verified_head = verify_evidence_artifact(
            root=evidence_root, reference=attestation, policy=trust_policy,
            evidence_type="RESOURCE_LEDGER_HEAD", subject_sha=manifest.manifest_sha256,
            conclusion="ACCEPTED", issuer_role="INDEPENDENT_OPERATOR",
            expected_payload={
                "pair_id_sha256": pair_id, "arm_label": arm_label,
                "action_count": str(len(receipts)), "final_receipt_sha256": head,
            },
        ).document
        if verified_head.issuer_principal_id in trust_policy.independent_principal_exclusions:
            return None, False, None
        meter_principal = next(iter(meter_principals))
        meter_key_id = next(iter(meter_keys))
        meter_public_key = trust_policy.signers[meter_key_id].public_key
        head_public_key = trust_policy.signers[verified_head.issuer_key_id].public_key
        if (verified_head.issuer_principal_id == meter_principal
                or hashlib.sha256(head_public_key).digest() == hashlib.sha256(meter_public_key).digest()):
            return None, False, None
        actual = validate_accounting_chain(
            receipts, pair_id=pair_id, arm_label=arm_label, caps=caps,
            expected_final_receipt_sha256=verified_head.payload["final_receipt_sha256"],
        )
    except (EvidenceError, ValueError, IndexError):
        return None, False, None
    return actual, (
        settlements_sealed
        and claimed is not None
        and claimed.model_dump(mode="json") == actual.model_dump(mode="json")
        and _accounting_summary_is_eligible(actual, caps)
    ), (meter_principal, hashlib.sha256(meter_public_key).digest())


def _digest(value: BaseModel | dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def pair_score_digest(pair: BlindedPairScore | dict) -> str:
    payload = pair.content_payload() if isinstance(pair, BlindedPairScore) else dict(pair)
    payload.pop("pair_result_sha256", None)
    return _digest(payload)


def score_package_digest(package: BlindedScorePackage | dict) -> str:
    payload = package.content_payload() if isinstance(package, BlindedScorePackage) else dict(package)
    payload.pop("package_sha256", None)
    return _digest(payload)


_SCORER_VISIBLE_PAIR_FIELDS = (
    "pair_id_sha256", "status", "arm_a_score", "arm_b_score",
    "arm_a_output_sha256", "arm_b_output_sha256",
)


def score_payload_projection(package: BlindedScorePackage | dict) -> dict:
    """Project only anonymous outputs and scores; omit all resource/account data."""
    value = package.model_dump(mode="json") if isinstance(package, BlindedScorePackage) else dict(package)
    return {
        "schema_version": value["schema_version"],
        "track": value["track"],
        "manifest_sha256": value["manifest_sha256"],
        "evaluator_pin_sha256": value["evaluator_pin_sha256"],
        "pairs": [
            {field: pair.get(field) for field in _SCORER_VISIBLE_PAIR_FIELDS}
            for pair in value["pairs"]
        ],
    }


def score_payload_digest(package: BlindedScorePackage | dict) -> str:
    """Digest the exact scorer-visible projection without accounting metadata."""
    return _digest(score_payload_projection(package))


def report_digest(report: PairedReport | dict) -> str:
    payload = report.content_payload() if isinstance(report, PairedReport) else dict(report)
    payload.pop("report_sha256", None)
    return _digest(payload)


def aggregate_blinded_scores(
    manifest: TrackManifest,
    package: BlindedScorePackage,
    *,
    gate_evidence: GateEvidence,
    scoring_attestation: EvidenceArtifactRef,
    unblinding_attestation: EvidenceArtifactRef,
    evidence_root: str,
    trust_policy: TrustPolicy,
) -> PairedReport:
    """Verify complete blinded result closure, then unblind and aggregate once."""
    try:
        if manifest.manifest_sha256 is None:
            raise ReportError("track manifest must be frozen before scoring")
        validate_run_gate(
            manifest, gate_evidence, evidence_root=evidence_root,
            trust_policy=trust_policy,
        )
        if package.package_sha256 != score_package_digest(package):
            raise ReportError("score package digest does not match its canonical contents")
        if package.score_payload_sha256 != score_payload_digest(package):
            raise ReportError("scorer-attested score projection does not match package outcomes")
        if any(item.pair_result_sha256 != pair_score_digest(item) for item in package.pairs):
            raise ReportError("a blinded pair result digest does not match its canonical contents")
        if package.manifest_sha256 != manifest.manifest_sha256 or package.track != manifest.track:
            raise ReportError("score package is for a different frozen track manifest")
        if len({item.blinded_pair_id_sha256 for item in manifest.task_pins}) != len(manifest.task_pins):
            raise ReportError("frozen manifest contains duplicate blinded pair IDs")
        evaluator_sha = _digest(manifest.evaluator)
        if package.evaluator_pin_sha256 != evaluator_sha:
            raise ReportError("score package is for a different frozen evaluator")
        expected_pair_ids = {item.blinded_pair_id_sha256 for item in manifest.task_pins}
        if {item.pair_id_sha256 for item in package.pairs} != expected_pair_ids:
            raise ReportError("score package must preserve every preregistered pair exactly once")
        scorer = verify_evidence_artifact(
            root=evidence_root, reference=scoring_attestation, policy=trust_policy,
            evidence_type="BLINDED_SCORE_PACKAGE", subject_sha=manifest.manifest_sha256,
            conclusion="ACCEPTED", issuer_role="INDEPENDENT_SCORER",
            expected_payload={"score_payload_sha256": package.score_payload_sha256,
                              "evaluator_pin_sha256": evaluator_sha},
        ).document
        unblinder = verify_evidence_artifact(
            root=evidence_root, reference=unblinding_attestation, policy=trust_policy,
            evidence_type="UNBLINDING", subject_sha=package.package_sha256,
            conclusion="ACCEPTED", issuer_role="BENCHMARK_OPERATOR",
            expected_payload={"score_package_sha256": package.package_sha256},
        ).document
    except (EvidenceError, PreregError) as exc:
        raise ReportError(str(exc)) from exc
    if set(scorer.payload) != {"score_payload_sha256", "evaluator_pin_sha256"}:
        raise ReportError("scorer attestation must not contain unblinding or unrelated payload fields")
    if set(unblinder.payload) != {
        "score_package_sha256", "bananloop_arm_label", "codex_direct_arm_label",
        "blinding_assignment_sha256",
    }:
        raise ReportError("unblinding attestation must contain only the frozen package and label mapping")
    if scorer.issuer_principal_id in trust_policy.independent_principal_exclusions:
        raise ReportError("an arm author/operator cannot serve as the independent scorer")
    if unblinder.issuer_principal_id in trust_policy.independent_principal_exclusions:
        raise ReportError("an arm author/operator cannot serve as the unblinding operator")
    if scorer.issuer_principal_id == unblinder.issuer_principal_id:
        raise ReportError("independent scorer and unblinding operator must be different principals")
    scorer_signer = trust_policy.signers[scorer.issuer_key_id]
    unblinder_signer = trust_policy.signers[unblinder.issuer_key_id]
    if (scorer.issuer_key_id == unblinder.issuer_key_id
            or hashlib.sha256(scorer_signer.public_key).digest()
            == hashlib.sha256(unblinder_signer.public_key).digest()):
        raise ReportError("independent scorer and unblinder must use distinct signing keys")
    if (unblinder.payload.get("bananloop_arm_label") not in {"A", "B"}
            or unblinder.payload.get("codex_direct_arm_label") not in {"A", "B"}
            or unblinder.payload["bananloop_arm_label"] == unblinder.payload["codex_direct_arm_label"]):
        raise ReportError("unblinding artifact must map the two distinct anonymized labels")
    try:
        assignment_sha = blind_label_assignment_digest(
            unblinder.payload["bananloop_arm_label"],
            unblinder.payload["codex_direct_arm_label"],
        )
    except (TypeError, ValueError) as exc:
        raise ReportError("unblinding mapping is invalid") from exc
    if (assignment_sha != manifest.blinding_assignment_sha256
            or unblinder.payload["blinding_assignment_sha256"] != manifest.blinding_assignment_sha256):
        raise ReportError("post-run unblinding differs from the pre-run blinded label assignment")
    bananloop_label = unblinder.payload["bananloop_arm_label"]
    inference: InferencePlan = manifest.inference

    pair_reports: list[ReportPair] = []
    deltas: list[float] = []
    wins = ties = losses = 0
    anonymizer_principals: set[str] = set()
    anonymizer_key_fingerprints: set[bytes] = set()
    for item in package.pairs:
        for label, output_ref, output_sha, raw_ref, raw_sha, attestation_ref in (
            ("A", item.arm_a_output_ref, item.arm_a_output_sha256,
             item.arm_a_raw_output_ref, item.arm_a_raw_output_sha256,
             item.arm_a_anonymization_attestation),
            ("B", item.arm_b_output_ref, item.arm_b_output_sha256,
             item.arm_b_raw_output_ref, item.arm_b_raw_output_sha256,
             item.arm_b_anonymization_attestation),
        ):
            if item.status == "SCORED":
                if any(value is None for value in (
                    output_ref, output_sha, raw_ref, raw_sha, attestation_ref,
                )):
                    raise ReportError(f"arm {label} lacks required output anonymization evidence")
                try:
                    anonymizer = verify_output_anonymization(
                        root=evidence_root, policy=trust_policy, reference=attestation_ref,
                        pair_id_sha256=item.pair_id_sha256, arm_label=label,
                        raw_reference=raw_ref, raw_sha256=raw_sha,
                        sanitized_reference=output_ref, sanitized_sha256=output_sha,
                        anonymizer_sha256=manifest.evaluator.output_anonymizer_sha256,
                    )
                except EvidenceError as exc:
                    raise ReportError(f"arm {label} output anonymization verification failed: {exc}") from exc
                document = anonymizer.document
                signer = trust_policy.signers[document.issuer_key_id]
                fingerprint = hashlib.sha256(signer.public_key).digest()
                if (document.issuer_principal_id in {scorer.issuer_principal_id,
                                                     unblinder.issuer_principal_id}
                        or document.issuer_key_id in {scorer.issuer_key_id, unblinder.issuer_key_id}
                        or fingerprint in {hashlib.sha256(scorer_signer.public_key).digest(),
                                           hashlib.sha256(unblinder_signer.public_key).digest()}):
                    raise ReportError("output anonymizer must be independent from scorer and unblinder")
                anonymizer_principals.add(document.issuer_principal_id)
                anonymizer_key_fingerprints.add(fingerprint)
        caps_a, caps_b = manifest.arms[0].caps, manifest.arms[1].caps
        accounting_a, eligible_a, meter_a = _verified_accounting(
            item.arm_a_receipts, item.arm_a_action_attestations,
            pair_id=item.pair_id_sha256, arm_label="A",
            caps=caps_a, claimed=item.arm_a_accounting,
            attestation=item.arm_a_ledger_attestation, manifest=manifest,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        accounting_b, eligible_b, meter_b = _verified_accounting(
            item.arm_b_receipts, item.arm_b_action_attestations,
            pair_id=item.pair_id_sha256, arm_label="B",
            caps=caps_b, claimed=item.arm_b_accounting,
            attestation=item.arm_b_ledger_attestation, manifest=manifest,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        accounting_ok = eligible_a and eligible_b
        for meter in (meter_a, meter_b):
            if meter is None:
                continue
            meter_principal, meter_fingerprint = meter
            if (meter_principal in {scorer.issuer_principal_id, unblinder.issuer_principal_id}
                    or meter_principal in anonymizer_principals
                    or meter_fingerprint in {
                        hashlib.sha256(scorer_signer.public_key).digest(),
                        hashlib.sha256(unblinder_signer.public_key).digest(),
                    }
                    or meter_fingerprint in anonymizer_key_fingerprints):
                raise ReportError("action meter must be independent from scorer and unblinding operator")
        accounting_unknown = (
            accounting_a is None or accounting_b is None
            or accounting_a.status == "INELIGIBLE_UNKNOWN_ACCOUNTING"
            or accounting_b.status == "INELIGIBLE_UNKNOWN_ACCOUNTING"
        )
        accounting_status = "ELIGIBLE" if accounting_ok else (
            "UNKNOWN" if accounting_unknown else "INELIGIBLE"
        )
        if item.status != "SCORED":
            pair_reports.append(ReportPair(
                pair_id_sha256=item.pair_id_sha256, status=item.status,
                accounting_status=accounting_status,
                bananloop_score=None, codex_direct_score=None,
                oriented_benefit_delta=None,
                bananloop_accounting=(accounting_a if bananloop_label == "A" else accounting_b),
                codex_direct_accounting=(accounting_b if bananloop_label == "A" else accounting_a),
                reason=item.reason,
            ))
            continue
        assert item.arm_a_score is not None and item.arm_b_score is not None
        bl_score = item.arm_a_score if bananloop_label == "A" else item.arm_b_score
        codex_score = item.arm_b_score if bananloop_label == "A" else item.arm_a_score
        delta = bl_score - codex_score
        if inference.direction == "LOWER_IS_BETTER":
            delta = -delta
        if not accounting_ok:
            pair_reports.append(ReportPair(
                pair_id_sha256=item.pair_id_sha256, status="INELIGIBLE_ACCOUNTING",
                accounting_status=accounting_status,
                bananloop_score=bl_score, codex_direct_score=codex_score,
                oriented_benefit_delta=None,
                bananloop_accounting=(accounting_a if bananloop_label == "A" else accounting_b),
                codex_direct_accounting=(accounting_b if bananloop_label == "A" else accounting_a),
                reason="one or both action-accounting chains are missing, invalid, unknown, inconsistent, or over cap",
            ))
            continue
        deltas.append(delta)
        if delta > 0:
            wins += 1
        elif delta < 0:
            losses += 1
        else:
            ties += 1
        pair_reports.append(ReportPair(
            pair_id_sha256=item.pair_id_sha256, status="ELIGIBLE",
            accounting_status=accounting_status,
            bananloop_score=bl_score, codex_direct_score=codex_score,
            oriented_benefit_delta=delta,
            bananloop_accounting=(accounting_a if bananloop_label == "A" else accounting_b),
            codex_direct_accounting=(accounting_b if bananloop_label == "A" else accounting_a),
            reason=None,
        ))
    usable = len(deltas)
    mean = sum(deltas) / usable if usable else None
    low = high = None
    if usable >= 2:
        variance = sum((delta - mean) ** 2 for delta in deltas) / (usable - 1)
        half_width = (_student_t_critical_approx(
            _normal_critical(inference.coverage_percent, inference.sidedness), usable - 1,
        ) * math.sqrt(variance / usable))
        low = mean - half_width
        high = None if inference.sidedness == "ONE_SIDED" else mean + half_width
    if usable < inference.required_pairs or usable < inference.minimum_usable_pairs:
        decision = "UNDERPOWERED"
    elif inference.sidedness == "ONE_SIDED":
        decision = "BANANLOOP_SUPERIOR" if low is not None and low > 0 else "NO_DETECTED_DIFFERENCE"
    elif low is not None and low > 0:
        decision = "BANANLOOP_SUPERIOR"
    elif high is not None and high < 0:
        decision = "CODEX_DIRECT_SUPERIOR"
    else:
        decision = "NO_DETECTED_DIFFERENCE"
    payload = {
        "schema_version": 1, "track": manifest.track,
        "manifest_sha256": manifest.manifest_sha256,
        "gate_evidence_sha256": _digest(gate_evidence),
        "score_package_sha256": package.package_sha256,
        "evaluator_pin_sha256": evaluator_sha,
        "bananloop_arm_label": bananloop_label, "decision": decision,
        "planned_pairs": len(manifest.task_pins), "usable_pairs": usable,
        "required_pairs": inference.required_pairs,
        "wins": wins, "ties": ties, "losses": losses,
        "mean_oriented_benefit_delta": mean, "interval_low": low, "interval_high": high,
        "pair_results": tuple(item.model_dump() for item in pair_reports),
    }
    return PairedReport(report_sha256=report_digest(payload), **payload)
