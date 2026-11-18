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
    panel_json = json.dumps({"rows": ["public-row"] * 3000}, separators=(",", ":"))
    with zipfile.ZipFile(bundle_stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("runner.py", "from pathlib import Path\n# loads data/public-cloud.npy\n")
        bundle.writestr("manifest.json", '{"cloud":"data/public-cloud.npy"}\n')
        bundle.writestr("data/public-cloud.npy", binary_content)
        bundle.writestr("data/public-panel.json", panel_json)
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
    assert review["evaluator_data_assets"] == [{
        "path": "data/public-panel.json",
        "size_bytes": len(panel_json.encode()),
        "sha256": hashlib.sha256(panel_json.encode()).hexdigest(),
    }]
    bundle_review = review["evaluator_bundle_review"]
    assert bundle_review["archive_sha256_verified"] is True
    assert bundle_review["archive_sha256"] == bundle_digest
    assert bundle_review["entry_count"] == 4
    assert {entry["path"] for entry in bundle_review["entries"]} == {
        "runner.py", "manifest.json", "data/public-cloud.npy", "data/public-panel.json",
    }
    assert bundle_review["embedded_json_manifests"] == [{
        "path": "manifest.json",
        "sha256": hashlib.sha256(b'{"cloud":"data/public-cloud.npy"}\n').hexdigest(),
        "content": {"cloud": "data/public-cloud.npy"},
    }]
    serialized = json.dumps(review, separators=(",", ":"))
    assert "opaque-public-cloud-payload" not in serialized
    assert "public-row" not in serialized
    assert "runner.py" in serialized


def test_newton_scope_proof_checks_public_dev_without_revealing_inputs(tmp_path):
    from benchmarks.autoresearchexam.sketched_newton_runner import (
        AREPublicSketchedNewtonEvaluatorRunner as Runner, DEV_CASE_ID, TASK_ID, TASK_VERSION,
    )
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('runner.py', '# evaluator\n')
    published = tmp_path / 'published'
    published.mkdir()
    bundle_digest = _publish(published, bundle.getvalue())
    protocol = EvaluatorProtocolV1(
        protocol_id='newton-proof', version=1, source_snapshot_id='a'*40,
        evaluator_bundle_sha256=bundle_digest, execution_backend_id=Runner.backend_id,
        entrypoint=('python3', '{evaluator}/runner.py'), metric_id='task_score',
        metric_field='task_score', direction='MAXIMIZE', aggregation='MEAN',
        tiers=(EvaluatorTierManifestV1(tier='dev', manifest_sha256='0'*64, case_count=1),
               EvaluatorTierManifestV1(tier='validation', manifest_sha256='0'*64, case_count=1)),
        repeatability_runs=1, timeout_seconds=30)
    good = Runner.aggregate_case_input(tier='dev', task_id=TASK_ID, task_version=TASK_VERSION,
        contract_sha256='', dev_image_id=Runner.default_dev_image_id, dev_cell_count=1)
    for payload, expected in ((good, True), (good+b' ', False)):
        allowed = {bundle_digest}
        tiers=[]
        for tier, case_id, data in [('dev', DEV_CASE_ID, payload), ('validation','secret-case',b'SECRET_CASE_BYTES')]:
            inp=_publish(published,data); allowed.add(inp)
            manifest=ProtocolManifest(protocol_sha256=_case_protocol_digest(protocol),
                cases=(ProtocolCase(case_id=case_id,repetition=1,input_sha256=inp),))
            md=_publish(published,json.dumps(manifest.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode());allowed.add(md)
            tiers.append(EvaluatorTierManifestV1(tier=tier,manifest_sha256=md,case_count=1))
        bound=protocol.model_copy(update={'tiers':tuple(tiers)})
        proposal=SimpleNamespace(protocol=bound,researcher_intent=None,evidence_refs=(),
            proposal_digest='b'*64,source_job_id='job',source_node_id='node',source_output_digest='c'*64)
        review=_build_protocol_review_material(proposal,published_artifact_root=published,allowed_artifact_digests=allowed)
        assert review['tier_manifest_summaries'][0]['newton_public_dev_matches_registered_backend_scope'] is expected
        inventory = review['evaluator_bundle_review']['newton_registered_backend_entry_check']
        assert inventory['exact_entry_set_match'] is False
        assert 'sketched_newton_runner.py' in inventory['missing_entries']
        serialized=json.dumps(review)
        assert DEV_CASE_ID not in serialized and 'SECRET_CASE_BYTES' not in serialized and 'secret-case' not in serialized
        assert 'newton_public_dev_matches_registered_backend_scope' not in review['tier_manifest_summaries'][1]
