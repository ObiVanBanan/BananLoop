"""Freeze Titanic tier inputs into BananLoop's trusted evaluator format.

This module runs on the trusted owner side. The returned content-addressed
assets belong in ``state_root/rpdi05/published`` and must never enter an agent
workspace.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from .features import MODEL_FEATURES
from bananloop.contracts import ProtocolCase, ProtocolManifest
from bananloop.evaluator_verification import (
    EvaluatorProtocolV1,
    EvaluatorTierManifestV1,
)
from bananloop.trusted_evaluator import _case_protocol_digest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _evaluator_bundle() -> bytes:
    # Candidates can change only numeric model parameters. They are never
    # imported or executed in the evaluator process that can read tier inputs.
    source = r'''import json
import math
import os
from pathlib import Path

candidate = Path(os.environ["BANANLOOP_CANDIDATE"])
tier = Path(os.environ["BANANLOOP_TIER"])
model = json.loads((candidate / "src" / "model.json").read_text())
allowed = set(ALLOWED_FEATURES_PLACEHOLDER)
coefficients = model.get("coefficients")
all_weights = [model.get("intercept"), *(coefficients.values() if isinstance(coefficients, dict) else ())]
if (set(model) != {"schema", "intercept", "coefficients"}
        or model["schema"] != "titanic-linear-v1"
        or not isinstance(model["intercept"], (int, float))
        or not isinstance(coefficients, dict) or set(coefficients) - allowed
        or any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not (-100 <= value <= 100) for value in all_weights)):
    raise ValueError("candidate model does not satisfy the bounded Titanic schema")

def number(value):
    try:
        return float(value) if value not in ("?", "", None) else 0.0
    except (TypeError, ValueError):
        return 0.0

def predict(row):
    values = {
        "sex_female": float(row.get("sex") == "female"),
        "pclass_1": float(row.get("pclass") == "1"),
        "pclass_2": float(row.get("pclass") == "2"),
        "pclass_3": float(row.get("pclass") == "3"),
        "age_years": number(row.get("age")) / 100.0,
        "sibsp": number(row.get("sibsp")) / 10.0,
        "parch": number(row.get("parch")) / 10.0,
        "fare_log1p": math.log1p(max(0.0, number(row.get("fare")))) / math.log1p(600.0),
        "embarked_C": float(row.get("embarked") == "C"),
        "embarked_Q": float(row.get("embarked") == "Q"),
        "embarked_S": float(row.get("embarked") == "S"),
        "male_pclass1_child": float(
            row.get("sex") == "male" and row.get("pclass") == "1"
            and number(row.get("age")) <= 18.0 and row.get("age") not in ("?", "", None)
        ),
    }
    score = model["intercept"] + sum(coefficients.get(key, 0.0) * value
                                     for key, value in values.items())
    return int(score >= 0.0)

manifest = json.loads((tier / "manifest.json").read_text())
case_map = json.loads((tier / "input-map.json").read_text())
correct = 0
measurements = []
for item in case_map:
    case = json.loads((tier / item["path"]).read_text())
    correct += predict(case["features"]) == case["label"]
    measurements.append({
        "case_id": item["case_id"],
        "repetition": item["repetition"],
        "input_sha256": item["input_sha256"],
    })
print(json.dumps({
    "status": "ok",
    "metrics": {"accuracy": correct / len(case_map)},
    "protocol_sha256": manifest["protocol_sha256"],
    "cases": measurements,
}, sort_keys=True, separators=(",", ":")))
'''
    source = source.replace("ALLOWED_FEATURES_PLACEHOLDER", repr(MODEL_FEATURES))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evaluate.py", source)
    return buffer.getvalue()


def freeze_titanic_evaluator(
    benchmark_dir: str | Path,
    source_snapshot_id: str,
) -> dict[str, Any]:
    """Return immutable protocol and private content-addressed evaluator assets."""
    root = Path(benchmark_dir).resolve()
    private = root / "evaluator-private"
    if not private.is_dir() or private.is_symlink():
        raise ValueError("prepared private Titanic tiers are missing")
    if len(source_snapshot_id) not in (40, 64) or any(
        char not in "0123456789abcdef" for char in source_snapshot_id
    ):
        raise ValueError("source snapshot must be a full lowercase Git object ID")

    bundle = _evaluator_bundle()
    bundle_digest = _sha256(bundle)
    preliminary = EvaluatorProtocolV1(
        protocol_id="titanic-survival-accuracy", version=1,
        source_snapshot_id=source_snapshot_id,
        evaluator_bundle_sha256=bundle_digest,
        entrypoint=("python3", "{evaluator}/evaluate.py"),
        metric_id="accuracy", metric_field="accuracy", direction="MAXIMIZE",
        aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="0" * 64, case_count=1),
        ),
        repeatability_runs=2,
    )
    protocol_digest = _case_protocol_digest(preliminary)
    assets: dict[str, bytes] = {bundle_digest: bundle}
    tier_refs = []
    for tier in ("dev", "holdout", "validation"):
        payload = json.loads((private / f"{tier}.json").read_text(encoding="utf-8"))
        cases = []
        input_map = []
        for index, (features, label) in enumerate(zip(payload["rows"], payload["labels"], strict=True)):
            case_id = f"titanic-{tier}-{index:04d}"
            case_bytes = _canonical({"features": features, "label": label})
            input_digest = _sha256(case_bytes)
            assets[input_digest] = case_bytes
            cases.append(ProtocolCase(
                case_id=case_id, repetition=0, input_sha256=input_digest,
            ))
            input_map.append({
                "case_id": case_id, "repetition": 0,
                "input_sha256": input_digest, "path": f"inputs/{index:05d}.json",
            })
        manifest = ProtocolManifest(protocol_sha256=protocol_digest, cases=tuple(cases))
        manifest_bytes = _canonical(manifest.model_dump(mode="json"))
        manifest_digest = _sha256(manifest_bytes)
        assets[manifest_digest] = manifest_bytes
        tier_refs.append(EvaluatorTierManifestV1(
            tier=tier, manifest_sha256=manifest_digest, case_count=len(cases),
        ))
        map_bytes = _canonical(input_map)
        map_digest = _sha256(map_bytes)
        assets[map_digest] = map_bytes

    protocol = preliminary.model_copy(update={"tiers": tuple(tier_refs)})
    # TrustedEvaluatorAdapter resolves only from state_root/rpdi05/published.
    # The benchmark directory is the isolated campaign state root in the E2E
    # harness, so keep evaluator bytes outside both the candidate repo and its
    # candidate-visible subdirectory.
    published = root / "rpdi05" / "published"
    rpdi05_root = root / "rpdi05"
    if rpdi05_root.is_symlink() or published.is_symlink():
        raise ValueError("published evaluator asset directory cannot be a symlink")
    published.mkdir(mode=0o700, parents=True, exist_ok=True)
    for digest, content in assets.items():
        destination = published / digest
        if destination.exists() and _sha256(destination.read_bytes()) != digest:
            raise ValueError("content-addressed evaluator asset collision")
        if not destination.exists():
            destination.write_bytes(content)
            destination.chmod(0o400)
    result = {
        "protocol": protocol.model_dump(mode="json"),
        "protocol_digest": protocol.content_digest(),
        "protocol_sha256": protocol_digest,
        "published_dir": str(published),
        "asset_digests": sorted(assets),
    }
    result_bytes = json.dumps(result, sort_keys=True, indent=2).encode() + b"\n"
    manifest_path = private / "evaluator-protocol.json"
    manifest_path.write_bytes(result_bytes)
    manifest_path.chmod(0o600)
    return result
