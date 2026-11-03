"""Blind scorer input/output contracts, separate from arm accounting evidence.

The scorer contract exposes only anonymized outputs, frozen evaluator pins,
blinded pair digests, and outcome status. It never accepts action receipts,
resource summaries, arm identities, or an unblinding map.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EvidenceError, read_content_artifact
from .prereg import TrackManifest, canonical_bytes
from .report import (
    BlindedScorePackage, pair_score_digest, score_payload_digest,
)


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_OUTPUT_BYTES = 2_000_000
MAX_REQUEST_BYTES = 64_000_000


class ScorerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate scorer JSON key")
        result[key] = value
    return result


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("scorer number must be finite")
    return value


class ScorerInputPair(ScorerModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_output_sha256: Digest | None
    arm_a_output_base64: str | None
    arm_b_output_sha256: Digest | None
    arm_b_output_base64: str | None

    @model_validator(mode="after")
    def output_bytes_are_bound(self) -> "ScorerInputPair":
        for label in ("arm_a", "arm_b"):
            digest = getattr(self, f"{label}_output_sha256")
            encoded = getattr(self, f"{label}_output_base64")
            if (digest is None) != (encoded is None):
                raise ValueError("scorer output digest and bytes must be provided together")
            if encoded is not None:
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    raise ValueError("scorer input has malformed base64 output") from exc
                if (not raw or len(raw) > MAX_OUTPUT_BYTES or _sha(raw) != digest
                        or base64.b64encode(raw).decode("ascii") != encoded):
                    raise ValueError("scorer input output bytes do not match their canonical digest")
        if self.status == "SCORED" and any(
                getattr(self, f"{label}_output_base64") is None for label in ("arm_a", "arm_b")):
            raise ValueError("scored pair requires both anonymized arm outputs")
        if self.status != "SCORED" and any(
                getattr(self, f"{label}_output_base64") is not None for label in ("arm_a", "arm_b")):
            raise ValueError("unscored pair inputs must not disclose partial arm outputs")
        return self


class BlindScorerInput(ScorerModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    evaluator_pin_sha256: Digest
    evaluator_protocol_sha256: Digest
    evaluator_bundle_sha256: Digest
    rubric_sha256: Digest
    validation_set_sha256: Digest
    holdout_set_sha256: Digest
    pairs: tuple[ScorerInputPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sorted_unique_pairs(self) -> "BlindScorerInput":
        ids = tuple(pair.pair_id_sha256 for pair in self.pairs)
        if tuple(sorted(set(ids))) != ids:
            raise ValueError("scorer input pairs must be unique and sorted by digest")
        return self


class ScorerOutputPair(ScorerModel):
    pair_id_sha256: Digest
    status: Literal["SCORED", "FAILED", "EXCLUDED", "MISSING"]
    arm_a_output_sha256: Digest | None
    arm_b_output_sha256: Digest | None
    arm_a_score: float | None
    arm_b_score: float | None

    @model_validator(mode="after")
    def scores_match_status(self) -> "ScorerOutputPair":
        scores = (self.arm_a_score, self.arm_b_score)
        if self.status == "SCORED":
            if any(value is None or not math.isfinite(value) for value in scores):
                raise ValueError("scored pair requires two finite scores")
            if self.arm_a_output_sha256 is None or self.arm_b_output_sha256 is None:
                raise ValueError("scored result requires both output digests")
        elif any(value is not None for value in scores):
            raise ValueError("unscored pair cannot contain scores")
        return self


class BlindScorerOutput(ScorerModel):
    schema_version: Literal[1] = 1
    track: Literal["OPTIMIZATION", "EXPLORATION"]
    manifest_sha256: Digest
    evaluator_pin_sha256: Digest
    pairs: tuple[ScorerOutputPair, ...] = Field(min_length=1)
    score_payload_sha256: Digest

    @model_validator(mode="after")
    def output_is_sorted_and_bound(self) -> "BlindScorerOutput":
        ids = tuple(pair.pair_id_sha256 for pair in self.pairs)
        if tuple(sorted(set(ids))) != ids:
            raise ValueError("scorer output pairs must be unique and sorted by digest")
        if self.score_payload_sha256 != scorer_output_digest(self):
            raise ValueError("scorer output digest does not match its blind score projection")
        return self


class ScorerError(ValueError):
    """A blind scorer request or response is malformed or misbound."""


def scorer_output_projection(output: BlindScorerOutput | dict) -> dict:
    value = output.model_dump(mode="json") if isinstance(output, BlindScorerOutput) else dict(output)
    return {
        "schema_version": value["schema_version"],
        "track": value["track"],
        "manifest_sha256": value["manifest_sha256"],
        "evaluator_pin_sha256": value["evaluator_pin_sha256"],
        "pairs": [
            {
                "pair_id_sha256": pair["pair_id_sha256"],
                "arm_a_score": pair.get("arm_a_score"),
                "arm_b_score": pair.get("arm_b_score"),
                "arm_a_output_sha256": pair.get("arm_a_output_sha256"),
                "arm_b_output_sha256": pair.get("arm_b_output_sha256"),
            }
            for pair in value["pairs"] if pair["status"] == "SCORED"
        ],
    }


def scorer_output_digest(output: BlindScorerOutput | dict) -> str:
    return _sha(canonical_bytes(scorer_output_projection(output)))


def build_blind_scorer_input(
    manifest: TrackManifest,
    package: BlindedScorePackage,
    *,
    evidence_root: str,
) -> BlindScorerInput:
    """Read only anonymous output artifacts and frozen evaluator pins for one track."""
    if manifest.manifest_sha256 is None:
        raise ScorerError("manifest must be frozen before scorer input is prepared")
    evaluator_sha = _sha(canonical_bytes(manifest.evaluator))
    if (package.manifest_sha256 != manifest.manifest_sha256 or package.track != manifest.track
            or package.evaluator_pin_sha256 != evaluator_sha):
        raise ScorerError("score package does not match the frozen track and evaluator")
    expected_pairs = {pin.blinded_pair_id_sha256 for pin in manifest.task_pins}
    if {pair.pair_id_sha256 for pair in package.pairs} != expected_pairs:
        raise ScorerError("score package pair set differs from the frozen manifest")
    input_pairs = []
    for pair in package.pairs:
        outputs: dict[str, bytes | None] = {"arm_a": None, "arm_b": None}
        if pair.status == "SCORED":
            for label in ("arm_a", "arm_b"):
                reference = getattr(pair, f"{label}_output_ref")
                digest = getattr(pair, f"{label}_output_sha256")
                if reference is None or digest is None:
                    raise ScorerError("scored pair lacks an anonymized output artifact")
                try:
                    outputs[label] = read_content_artifact(
                        root=evidence_root, reference=reference, expected_sha256=digest,
                        max_bytes=MAX_OUTPUT_BYTES,
                    )
                except EvidenceError as exc:
                    raise ScorerError(f"scorer output artifact failed verification: {exc}") from exc
        input_pairs.append(ScorerInputPair(
            pair_id_sha256=pair.pair_id_sha256, status=pair.status,
            arm_a_output_sha256=_sha(outputs["arm_a"]) if outputs["arm_a"] is not None else None,
            arm_a_output_base64=(base64.b64encode(outputs["arm_a"]).decode("ascii")
                                 if outputs["arm_a"] is not None else None),
            arm_b_output_sha256=_sha(outputs["arm_b"]) if outputs["arm_b"] is not None else None,
            arm_b_output_base64=(base64.b64encode(outputs["arm_b"]).decode("ascii")
                                 if outputs["arm_b"] is not None else None),
        ))
    result = BlindScorerInput(
        track=manifest.track, manifest_sha256=manifest.manifest_sha256,
        evaluator_pin_sha256=evaluator_sha,
        evaluator_protocol_sha256=manifest.evaluator.protocol_sha256,
        evaluator_bundle_sha256=manifest.evaluator.bundle_sha256,
        rubric_sha256=manifest.evaluator.rubric_sha256,
        validation_set_sha256=manifest.evaluator.validation_set_sha256,
        holdout_set_sha256=manifest.evaluator.holdout_set_sha256,
        pairs=tuple(input_pairs),
    )
    if len(canonical_bytes(result)) > MAX_REQUEST_BYTES:
        raise ScorerError("blind scorer request exceeds its byte limit")
    return result


def validate_blind_scorer_output(
    raw: bytes,
    *,
    request: BlindScorerInput,
) -> BlindScorerOutput:
    """Reject malformed, changed, partial, or extra scorer rows before joining accounting."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REQUEST_BYTES:
        raise ScorerError("scorer output is empty or exceeds its byte limit")
    try:
        value = json.loads(
            raw, object_pairs_hook=_unique_object, parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite scorer value")),
        )
        if isinstance(value, dict) and isinstance(value.get("pairs"), list):
            value["pairs"] = tuple(value["pairs"])
        output = BlindScorerOutput.model_validate(value, strict=True)
    except Exception as exc:
        raise ScorerError(f"scorer output is malformed: {type(exc).__name__}") from exc
    if canonical_bytes(output) != raw:
        raise ScorerError("scorer output must use canonical JSON")
    if (output.track != request.track or output.manifest_sha256 != request.manifest_sha256
            or output.evaluator_pin_sha256 != request.evaluator_pin_sha256):
        raise ScorerError("scorer output is for different frozen inputs")
    if len(output.pairs) != len(request.pairs):
        raise ScorerError("scorer output must preserve every blind pair")
    for source, scored in zip(request.pairs, output.pairs, strict=True):
        if (source.pair_id_sha256 != scored.pair_id_sha256 or source.status != scored.status
                or source.arm_a_output_sha256 != scored.arm_a_output_sha256
                or source.arm_b_output_sha256 != scored.arm_b_output_sha256):
            raise ScorerError("scorer output changed pair identity, status, or output binding")
        if source.status != "SCORED" and any(
                getattr(scored, field) is not None for field in ("arm_a_score", "arm_b_score")):
            raise ScorerError("scorer returned scores for an unscored arm pair")
    return output


def apply_blind_scorer_output(
    package: BlindedScorePackage,
    output: BlindScorerOutput,
) -> BlindedScorePackage:
    """Join blind scores to host-held accounting only after scorer output is frozen."""
    if (package.track != output.track or package.manifest_sha256 != output.manifest_sha256
            or package.evaluator_pin_sha256 != output.evaluator_pin_sha256
            or len(package.pairs) != len(output.pairs)):
        raise ScorerError("scorer output and host package do not share a frozen identity")
    score_rows = {item.pair_id_sha256: item for item in output.pairs}
    if set(score_rows) != {item.pair_id_sha256 for item in package.pairs}:
        raise ScorerError("scorer output pair set differs from the host package")
    merged = []
    for pair in package.pairs:
        row = score_rows[pair.pair_id_sha256]
        if (pair.status != row.status or pair.arm_a_output_sha256 != row.arm_a_output_sha256
                or pair.arm_b_output_sha256 != row.arm_b_output_sha256):
            raise ScorerError("scorer output changed host-pinned outcome or output bytes")
        payload = pair.model_dump(mode="python")
        payload.update(arm_a_score=row.arm_a_score, arm_b_score=row.arm_b_score)
        payload.pop("pair_result_sha256")
        merged.append({**payload, "pair_result_sha256": pair_score_digest(payload)})
    package_payload = package.model_dump(mode="python")
    package_payload["pairs"] = tuple(merged)
    package_payload["score_payload_sha256"] = scorer_output_digest(output)
    package_payload.pop("package_sha256")
    from .report import score_package_digest
    package_payload["package_sha256"] = score_package_digest(package_payload)
    try:
        return BlindedScorePackage.model_validate(package_payload, strict=True)
    except Exception as exc:
        raise ScorerError("cannot merge scorer output with host accounting package") from exc
