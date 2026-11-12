"""Owner-supplied evaluator assets with a Researcher-proposed metric intent.

A fixture carries evaluator bytes and public or sealed case inputs, but not a
parent EvaluatorProtocolV1. Core binds an intent from the Researcher to the
trusted fixture, then publishes the resulting content-addressed bundle,
manifests, and inputs after the model call has completed. A benchmark without
HOLDOUT may pin only DEV and VALIDATION; a three-tier fixture remains supported.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import ProtocolCase, ProtocolManifest
from .evaluator_verification import (
    EvaluatorProtocolDraftV1,
    EvaluatorProtocolIntentV1,
    EvaluatorProtocolV1,
    EvaluatorTierManifestV1,
)
from .trusted_evaluator import _case_protocol_digest


_MAX_FIXTURE_BYTES = 25_000_000
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _decode(encoded: str, label: str) -> bytes:
    try:
        result = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} must be canonical base64") from exc
    if base64.b64encode(result).decode("ascii") != encoded:
        raise ValueError(f"{label} must be canonical base64")
    return result


class EvaluatorFixtureCaseV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=_CASE_ID.pattern)
    repetition: int = Field(default=0, ge=0)
    input_base64: str = Field(min_length=4)


class EvaluatorFixtureTierV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Literal["dev", "validation", "holdout"]
    cases: tuple[EvaluatorFixtureCaseV1, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def unique_cases(self) -> "EvaluatorFixtureTierV1":
        keys = [(item.case_id, item.repetition) for item in self.cases]
        if len(set(keys)) != len(keys):
            raise ValueError("evaluator fixture tier repeats a case/repetition")
        return self


class EvaluatorFixtureV1(BaseModel):
    """Trusted benchmark/evaluator assets; intentionally excludes protocol fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    evaluator_bundle_base64: str = Field(min_length=4)
    execution_backend_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9._:-]{0,119}$",
    )
    entrypoint: tuple[str, ...] = Field(min_length=1, max_length=64)
    tiers: tuple[EvaluatorFixtureTierV1, ...] = Field(min_length=2, max_length=3)

    @field_validator("tiers")
    @classmethod
    def all_tiers(cls, values: tuple[EvaluatorFixtureTierV1, ...]):
        names = tuple(item.tier for item in values)
        if names not in {
            ("dev", "holdout", "validation"),
            ("dev", "validation"),
        }:
            raise ValueError("fixture tiers must be ordered dev, validation, with optional holdout")
        return values

    @model_validator(mode="after")
    def byte_bounds(self) -> "EvaluatorFixtureV1":
        total = len(_decode(self.evaluator_bundle_base64, "evaluator bundle"))
        for tier in self.tiers:
            manifest_cases = []
            for case in tier.cases:
                raw = _decode(case.input_base64, f"{tier.tier} case input")
                total += len(raw)
                manifest_cases.append({
                    "case_id": case.case_id, "repetition": case.repetition,
                    "input_sha256": hashlib.sha256(raw).hexdigest(),
                })
                if total > _MAX_FIXTURE_BYTES:
                    raise ValueError("evaluator fixture assets exceed 25 MiB")
            # Reserve the exact canonical manifest footprint too, so a fixture
            # cannot pass preflight and exceed the worker artifact cap at bind time.
            total += len(_canonical({
                "schema_version": 1, "protocol_sha256": "0" * 64,
                "cases": manifest_cases,
            }))
            if total > _MAX_FIXTURE_BYTES:
                raise ValueError("evaluator fixture assets and manifests exceed 25 MB")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))

    @classmethod
    def load(cls, path: str | Path) -> "EvaluatorFixtureV1":
        source = Path(path).expanduser()
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 36 * 1024 * 1024:
            raise ValueError("evaluator fixture must be a bounded regular file")
        try:
            return cls.model_validate_json(source.read_bytes())
        except Exception as exc:
            raise ValueError("evaluator fixture is malformed or unsafe") from exc

    def bind(self, intent: EvaluatorProtocolIntentV1, *, source_snapshot_id: str) -> tuple[
        EvaluatorProtocolDraftV1, dict[str, bytes],
    ]:
        """Compile model-proposed metric semantics against owner-trusted assets."""
        bundle = _decode(self.evaluator_bundle_base64, "evaluator bundle")
        bundle_digest = hashlib.sha256(bundle).hexdigest()
        provisional = EvaluatorProtocolV1(
            protocol_id=intent.protocol_id, version=intent.version,
            source_snapshot_id=source_snapshot_id,
            evaluator_bundle_sha256=bundle_digest,
            execution_backend_id=self.execution_backend_id,
            entrypoint=self.entrypoint,
            metric_id=intent.metric_id, metric_field=intent.metric_field,
            direction=intent.direction, aggregation=intent.aggregation,
            tiers=tuple(EvaluatorTierManifestV1(
                tier=tier.tier, manifest_sha256="0" * 64,
                case_count=len(tier.cases),
            ) for tier in self.tiers),
            repeatability_runs=intent.repeatability_runs,
            timeout_seconds=intent.timeout_seconds,
        )
        protocol_sha = _case_protocol_digest(provisional)
        artifacts: dict[str, bytes] = {"core-assets/bundle.zip": bundle}
        tier_refs: list[EvaluatorTierManifestV1] = []
        for tier_index, tier in enumerate(self.tiers):
            tier_key = f"tier-{tier_index}"
            manifest_cases = []
            for case in tier.cases:
                raw = _decode(case.input_base64, f"{tier.tier} case input")
                digest = hashlib.sha256(raw).hexdigest()
                manifest_cases.append(ProtocolCase(
                    case_id=case.case_id, repetition=case.repetition,
                    input_sha256=digest,
                ))
                artifacts[f"core-assets/inputs/{tier_key}/{digest}.bin"] = raw
            manifest = ProtocolManifest(protocol_sha256=protocol_sha, cases=tuple(manifest_cases))
            manifest_bytes = _canonical(manifest.model_dump(mode="json"))
            manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            artifacts[f"core-assets/manifests/{tier_key}.json"] = manifest_bytes
            tier_refs.append(EvaluatorTierManifestV1(
                tier=tier.tier, manifest_sha256=manifest_digest,
                case_count=len(tier.cases),
            ))
        protocol = provisional.model_copy(update={"tiers": tuple(tier_refs)})
        if _case_protocol_digest(protocol) != protocol_sha:
            raise ValueError("trusted evaluator binding changed the metric protocol identity")
        if sum(map(len, artifacts.values())) > _MAX_FIXTURE_BYTES:
            raise ValueError("bound evaluator fixture exceeds the 25 MB artifact budget")
        draft = EvaluatorProtocolDraftV1(
            protocol=protocol,
            evidence_refs=(f"artifact://sha256/{bundle_digest}",),
        )
        return draft, artifacts
