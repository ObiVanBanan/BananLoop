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
from .evidence import EvidenceArtifactRef, EvidenceError, TrustPolicy, verify_evidence_artifact
from .prereg import (
    InferencePlan, PreregError, ResourceCaps, TrackManifest, _normal_critical,
    _student_t_critical_approx, canonical_bytes, verify_frozen_manifest,
)


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class BlindedPairScore(ReportModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_score: float | None = None
    arm_b_score: float | None = None
    arm_a_output_sha256: Digest | None = None
    arm_b_output_sha256: Digest | None = None
    arm_a_receipts: tuple[ActionReceipt, ...] = ()
    arm_b_receipts: tuple[ActionReceipt, ...] = ()
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
            if self.reason is not None:
                raise ValueError("scored pair cannot have a failure/exclusion reason")
        else:
            if self.arm_a_score is not None or self.arm_b_score is not None:
                raise ValueError("unscored pair must not contain a score")
            if self.reason is None or not self.reason.strip():
                raise ValueError("failed, excluded, and missing pairs require a reason")
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
    package_sha256: Digest

    @model_validator(mode="after")
    def package_is_canonical(self) -> "BlindedScorePackage":
        pair_ids = tuple(item.pair_id_sha256 for item in self.pairs)
        if tuple(sorted(set(pair_ids))) != pair_ids:
            raise ValueError("blinded pair records must be unique and sorted by pair digest")
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
    """Reject internally inconsistent summaries; receipt-chain authenticity is separate."""
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


def _verified_accounting(receipts, *, pair_id: str, arm_label: Literal["A", "B"], caps,
                         claimed: AccountingResult | None, attestation: EvidenceArtifactRef | None,
                         manifest: TrackManifest, evidence_root: str,
                         trust_policy: TrustPolicy) -> tuple[AccountingResult | None, bool]:
    """Recompute accounting from receipts pinned by a trusted ledger-head signer."""
    if not receipts or attestation is None:
        return None, False
    try:
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
            return None, False
        actual = validate_accounting_chain(
            receipts, pair_id=pair_id, arm_label=arm_label, caps=caps,
            expected_final_receipt_sha256=verified_head.payload["final_receipt_sha256"],
        )
    except (EvidenceError, ValueError, IndexError):
        return None, False
    return actual, (
        claimed is not None
        and claimed.model_dump(mode="json") == actual.model_dump(mode="json")
        and _accounting_summary_is_eligible(actual, caps)
    )


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


def report_digest(report: PairedReport | dict) -> str:
    payload = report.content_payload() if isinstance(report, PairedReport) else dict(report)
    payload.pop("report_sha256", None)
    return _digest(payload)


def aggregate_blinded_scores(
    manifest: TrackManifest,
    package: BlindedScorePackage,
    *,
    scoring_attestation: EvidenceArtifactRef,
    unblinding_attestation: EvidenceArtifactRef,
    evidence_root: str,
    trust_policy: TrustPolicy,
) -> PairedReport:
    """Verify complete blinded result closure, then unblind and aggregate once."""
    try:
        if manifest.manifest_sha256 is None:
            raise ReportError("track manifest must be frozen before scoring")
        verify_frozen_manifest(manifest, manifest.manifest_sha256)
        if package.package_sha256 != score_package_digest(package):
            raise ReportError("score package digest does not match its canonical contents")
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
            expected_payload={"score_package_sha256": package.package_sha256,
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
    bananloop_label = unblinder.payload["bananloop_arm_label"]
    inference: InferencePlan = manifest.inference

    pair_reports: list[ReportPair] = []
    deltas: list[float] = []
    wins = ties = losses = 0
    for item in package.pairs:
        caps_a, caps_b = manifest.arms[0].caps, manifest.arms[1].caps
        accounting_a, eligible_a = _verified_accounting(
            item.arm_a_receipts, pair_id=item.pair_id_sha256, arm_label="A",
            caps=caps_a, claimed=item.arm_a_accounting,
            attestation=item.arm_a_ledger_attestation, manifest=manifest,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        accounting_b, eligible_b = _verified_accounting(
            item.arm_b_receipts, pair_id=item.pair_id_sha256, arm_label="B",
            caps=caps_b, claimed=item.arm_b_accounting,
            attestation=item.arm_b_ledger_attestation, manifest=manifest,
            evidence_root=evidence_root, trust_policy=trust_policy,
        )
        accounting_ok = eligible_a and eligible_b
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
