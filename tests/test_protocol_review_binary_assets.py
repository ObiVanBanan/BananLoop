from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from bananloop.contracts import ProtocolCase, ProtocolManifest
from bananloop.evaluator_verification import (
    EvaluatorProtocolV1,
    EvaluatorTierManifestV1,
    _build_protocol_review_material,
)
from bananloop.trusted_evaluator import _case_protocol_digest


def _publish(root: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    (root / digest).write_bytes(content)
    return digest


def test_protocol_review_redacts_binary_public_assets_to_digest_metadata(tmp_path):
    bundle_stream = io.BytesIO()
    binary_content = b"\x00opaque-public-cloud-payload\xff"
    with zipfile.ZipFile(bundle_stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("runner.py", "from pathlib import Path\n# loads data/public-cloud.npy\n")
        bundle.writestr("manifest.json", '{"cloud":"data/public-cloud.npy"}\n')
        bundle.writestr("data/public-cloud.npy", binary_content)
    bundle_bytes = bundle_stream.getvalue()
    bundle_digest = hashlib.sha256(bundle_bytes).hexdigest()

    protocol = EvaluatorProtocolV1(
        protocol_id="binary-asset-review-v1",
        version=1,
        source_snapshot_id="a" * 40,
        evaluator_bundle_sha256=bundle_digest,
        execution_backend_id="test-public-binary-backend-v1",
        entrypoint=("python3", "{evaluator}/runner.py"),
        metric_id="public_score",
        metric_field="score",
        direction="MAXIMIZE",
        aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="0" * 64, case_count=1),
            EvaluatorTierManifestV1(tier="validation", manifest_sha256="0" * 64, case_count=1),
        ),
        repeatability_runs=2,
        timeout_seconds=30,
    )

    published = tmp_path / "published"
    published.mkdir()
    allowed = {_publish(published, bundle_bytes)}
    bound_tiers = []
    for tier_name in ("dev", "validation"):
        case_bytes = f"{tier_name}-case-input".encode()
        input_digest = _publish(published, case_bytes)
        manifest = ProtocolManifest(
            schema_version=1,
            protocol_sha256=_case_protocol_digest(protocol),
            cases=(ProtocolCase(
                case_id=f"{tier_name}-aggregate", repetition=1,
                input_sha256=input_digest,
            ),),
        )
        manifest_bytes = json.dumps(
            manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ).encode()
        manifest_digest = _publish(published, manifest_bytes)
        allowed.update({input_digest, manifest_digest})
        bound_tiers.append(EvaluatorTierManifestV1(
            tier=tier_name, manifest_sha256=manifest_digest, case_count=1,
        ))
    protocol = protocol.model_copy(update={"tiers": tuple(bound_tiers)})
    proposal = SimpleNamespace(
        protocol=protocol,
        researcher_intent=None,
        evidence_refs=(),
        proposal_digest="b" * 64,
        source_job_id="job-binary-fixture",
        source_node_id="node-binary-fixture",
        source_output_digest="c" * 64,
    )

    review = _build_protocol_review_material(
        proposal,
        published_artifact_root=published,
        allowed_artifact_digests=allowed,
    )

    assert any(item["path"] == "runner.py" for item in review["evaluator_source_files"])
    assert review["evaluator_binary_assets"] == [{
        "path": "data/public-cloud.npy",
        "size_bytes": len(binary_content),
        "sha256": hashlib.sha256(binary_content).hexdigest(),
    }]
    serialized = json.dumps(review, separators=(",", ":"))
    assert "opaque-public-cloud-payload" not in serialized
    assert "runner.py" in serialized
