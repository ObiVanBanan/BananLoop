from __future__ import annotations

import hashlib
import json
import math
from statistics import fmean
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import EvaluationResult, MetricSpec
from .evaluator_binding import canonical_hash


class ReferenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    repetition: int = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: Literal["positive", "negative"]
    expected_metric_value: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class OperatorReferenceManifest(BaseModel):
    """Pinned case labels and baseline values approved outside bootstrap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_metric: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]
    case_source: str = Field(min_length=1)
    label_source: str = Field(min_length=1)
    reference_basis: str = Field(min_length=1)
    cases: list[ReferenceCase] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_cases(self) -> "OperatorReferenceManifest":
        keys = [(case.case_id, case.repetition) for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("reference manifest case/repetition keys must be unique")
        labels: dict[str, set[str]] = defaultdict(set)
        for case in self.cases:
            labels[case.case_id].add(case.label)
        if any(len(case_labels) != 1 for case_labels in labels.values()):
            raise ValueError("a reference case cannot have conflicting labels across repetitions")
        if not any(case.label == "positive" for case in self.cases):
            raise ValueError("reference manifest requires positive cases")
        if not any(case.label == "negative" for case in self.cases):
            raise ValueError("reference manifest requires negative cases")
        return self


class ReferenceManifestError(ValueError):
    pass


def parse_reference_manifest(data: bytes, expected_sha256: str) -> OperatorReferenceManifest:
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ReferenceManifestError("operator reference manifest SHA-256 does not match the approved digest")
    try:
        return OperatorReferenceManifest.model_validate_json(data)
    except ValueError as exc:
        raise ReferenceManifestError(f"invalid operator reference manifest: {exc}") from exc


def _case_key(case: Any) -> tuple[str, int]:
    return str(case.case_id), int(case.repetition)


def verify_reference_results(
    *,
    manifest: OperatorReferenceManifest,
    manifest_sha256: str,
    approved_by: str,
    primary_metric: MetricSpec,
    full_result: EvaluationResult,
    smoke_results: list[EvaluationResult],
) -> dict[str, Any]:
    approver = approved_by.strip()
    if not approver:
        raise ReferenceManifestError("operator approver identity is required")
    if not smoke_results:
        raise ReferenceManifestError("at least one readiness smoke result is required")
    if manifest.primary_metric != primary_metric.name or manifest.direction != primary_metric.direction.value:
        raise ReferenceManifestError("reference manifest metric/direction does not match the campaign primary metric")
    if full_result.protocol_sha256 != manifest.protocol_sha256:
        raise ReferenceManifestError("reference manifest protocol does not match the full DEV baseline")
    if full_result.status != "ok":
        raise ReferenceManifestError("full DEV baseline did not complete successfully")

    expected = {(_case_key(case)): case for case in manifest.cases}
    actual = {_case_key(case): case for case in full_result.cases}
    if len(actual) != len(full_result.cases) or set(actual) != set(expected):
        raise ReferenceManifestError("reference manifest case/repetition set does not exactly match the full DEV baseline")

    for key, reference in expected.items():
        case = actual[key]
        if case.input_sha256 != reference.input_sha256:
            raise ReferenceManifestError(f"reference input identity mismatch for case {key[0]!r}")
        value = case.metrics.get(primary_metric.name)
        if value is None:
            raise ReferenceManifestError(f"full DEV baseline case {key[0]!r} is missing the primary metric")
        if not math.isfinite(value):
            raise ReferenceManifestError(f"full DEV baseline case {key[0]!r} has a non-finite primary metric")
        if abs(float(value) - reference.expected_metric_value) > reference.tolerance:
            raise ReferenceManifestError(f"full DEV baseline value differs from the approved reference for case {key[0]!r}")

    smoke_receipts: list[dict[str, Any]] = []
    positive_ids = {case.case_id for case in manifest.cases if case.label == "positive"}
    negative_ids = {case.case_id for case in manifest.cases if case.label == "negative"}
    for index, result in enumerate(smoke_results, start=1):
        if result.protocol_sha256 != manifest.protocol_sha256:
            raise ReferenceManifestError(f"smoke repeat {index} protocol differs from the approved reference")
        by_key = {_case_key(case): case for case in result.cases}
        if len(by_key) != len(result.cases):
            raise ReferenceManifestError(f"smoke repeat {index} contains duplicate case/repetition keys")
        case_values: dict[str, list[float]] = defaultdict(list)
        for key, case in by_key.items():
            reference = expected.get(key)
            if reference is None:
                raise ReferenceManifestError(f"smoke repeat {index} contains a case outside the approved manifest")
            if case.input_sha256 != reference.input_sha256:
                raise ReferenceManifestError(f"smoke repeat {index} input identity mismatch for case {key[0]!r}")
            value = case.metrics.get(primary_metric.name)
            if value is None or not math.isfinite(value):
                raise ReferenceManifestError(f"smoke repeat {index} case {key[0]!r} is missing a finite primary metric")
            if abs(float(value) - reference.expected_metric_value) > reference.tolerance:
                raise ReferenceManifestError(f"smoke repeat {index} differs from the approved reference for case {key[0]!r}")
            case_values[key[0]].append(float(value))
        if not (positive_ids & set(case_values)) or not (negative_ids & set(case_values)):
            raise ReferenceManifestError(f"smoke repeat {index} does not include both approved label groups")
        positive_mean = fmean(fmean(case_values[key]) for key in positive_ids & set(case_values))
        negative_mean = fmean(fmean(case_values[key]) for key in negative_ids & set(case_values))
        separation = positive_mean - negative_mean if primary_metric.direction.value == "maximize" else negative_mean - positive_mean
        if separation < primary_metric.min_improvement:
            raise ReferenceManifestError(f"smoke repeat {index} does not separate operator-approved positive and negative cases")
        smoke_receipts.append({
            "repeat": index,
            "positive_case_count": len(positive_ids & set(case_values)),
            "negative_case_count": len(negative_ids & set(case_values)),
            "directed_separation": separation,
            "passed": True,
        })

    input_manifest = [
        {"case_id": case.case_id, "repetition": case.repetition, "input_sha256": case.input_sha256}
        for case in sorted(manifest.cases, key=lambda item: (item.case_id, item.repetition))
    ]
    body = {
        "schema_version": 1,
        "status": "INDEPENDENTLY_VALIDATED",
        "manifest_sha256": manifest_sha256,
        "approved_by": approver,
        "case_source": manifest.case_source,
        "label_source": manifest.label_source,
        "reference_basis": manifest.reference_basis,
        "protocol_sha256": manifest.protocol_sha256,
        "primary_metric": manifest.primary_metric,
        "direction": manifest.direction,
        "case_input_manifest_sha256": canonical_hash(input_manifest),
        "case_count": len(manifest.cases),
        "positive_case_count": len(positive_ids),
        "negative_case_count": len(negative_ids),
        "baseline_result_sha256": canonical_hash(full_result.model_dump(mode="json")),
        "smoke_repeats": smoke_receipts,
        "checks": {
            "manifest_digest_pinned": True,
            "protocol_matches": True,
            "full_case_set_matches": True,
            "full_baseline_values_match": True,
            "smoke_values_match": True,
            "positive_negative_separation": True,
        },
    }
    body["receipt_sha256"] = canonical_hash(body)
    return body
