from __future__ import annotations

import hashlib
import io
import json
import stat
import sqlite3
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from bananloop.contracts import ProtocolCase, ProtocolManifest, parse_campaign_spec_document
from bananloop.evaluator_verification import (
    EvaluatorProtocolAuthority, EvaluatorProtocolV1, EvaluatorTierManifestV1,
)
from bananloop.trusted_evaluator import (
    EvaluatorBudgetProofV1,
    EvaluatorRunReceiptV1,
    TrustedEvaluatorError,
    TrustedEvaluatorRunner,
    _case_protocol_digest,
    _canonical,
    _extract_bundle,
    _check_live_output_limits,
    TrustedEvaluatorAdapter,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inputs() -> dict[tuple[str, int], bytes]:
    return {
        ("hidden-case-1", 0): b'{"prompt":"private alpha","label":"A"}',
        ("hidden-case-2", 0): b'{"prompt":"private beta","label":"B"}',
    }


def _protocol_and_manifest():
    inputs = _inputs()
    bundle_io = io.BytesIO()
    with zipfile.ZipFile(bundle_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evaluate.py", "# pinned evaluator bytes\n")
    bundle = bundle_io.getvalue()
    protocol_digest_stub = "0" * 64
    cases = tuple(ProtocolCase(case_id=case_id, repetition=repetition,
                              input_sha256=_sha(data))
                  for (case_id, repetition), data in inputs.items())
    preliminary = EvaluatorProtocolV1(
        protocol_id="quality", version=1, source_snapshot_id="a" * 40,
        evaluator_bundle_sha256=_sha(bundle), entrypoint=("python", "{evaluator}/evaluate.py"),
        metric_id="quality", metric_field="quality", direction="MAXIMIZE", aggregation="MEAN",
        tiers=(
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="9" * 64, case_count=1),
            EvaluatorTierManifestV1(tier="holdout", manifest_sha256=protocol_digest_stub,
                                    case_count=len(cases)),
        ),
        repeatability_runs=2,
    )
    manifest = ProtocolManifest(protocol_sha256=_case_protocol_digest(preliminary), cases=cases)
    manifest_bytes = json.dumps(manifest.model_dump(mode="json"), sort_keys=True,
                                separators=(",", ":")).encode()
    manifest_sha = _sha(manifest_bytes)
    protocol = preliminary.model_copy(update={
        "tiers": (
            EvaluatorTierManifestV1(tier="dev", manifest_sha256="9" * 64, case_count=1),
            EvaluatorTierManifestV1(tier="holdout", manifest_sha256=manifest_sha,
                                    case_count=len(cases)),
        ),
    })
    return protocol, manifest, manifest_bytes, bundle, inputs


def test_execution_backend_is_digest_bound_and_fails_closed_for_mismatch():
    protocol, *_ = _protocol_and_manifest()
    legacy_payload = protocol.model_dump(mode="json", exclude_none=True)
    assert protocol.content_digest() == hashlib.sha256(json.dumps(
        legacy_payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert _case_protocol_digest(protocol) == _case_protocol_digest(
        protocol.model_copy(update={"execution_backend_id": None}),
    )

    pinned = protocol.model_copy(update={"execution_backend_id": "autoresearchexam-public-v1"})
    assert pinned.content_digest() != protocol.content_digest()
    adapter = TrustedEvaluatorAdapter(
        graph=SimpleNamespace(bind_campaign_id=lambda _: None), queue=None,
        campaign_id="campaign-1", state_root=".", runner=TrustedEvaluatorRunner(),
    )
    with pytest.raises(TrustedEvaluatorError, match="backend does not match"):
        adapter._require_execution_backend(pinned)
    adapter.runner = SimpleNamespace(backend_id="autoresearchexam-public-v1")
    adapter._require_execution_backend(pinned)


def test_custom_runner_bundle_preflight_uses_the_frozen_published_bytes(tmp_path):
    protocol, _manifest, _manifest_bytes, bundle, _inputs = _protocol_and_manifest()
    protocol = protocol.model_copy(update={"execution_backend_id": "public-test"})
    published = tmp_path / "rpdi05" / "published"
    published.mkdir(parents=True)
    (published / protocol.evaluator_bundle_sha256).write_bytes(bundle)
    seen = []
    runner = SimpleNamespace(
        backend_id="public-test",
        _verify_backend_bundle=lambda actual_protocol, actual_bundle: seen.append(
            (actual_protocol.content_digest(), actual_bundle)
        ),
    )
    adapter = TrustedEvaluatorAdapter(
        graph=SimpleNamespace(bind_campaign_id=lambda _: None), queue=None,
        campaign_id="campaign-1", state_root=tmp_path, runner=runner,
    )

    adapter._preflight_runner_bundle(protocol)

    assert seen == [(protocol.content_digest(), bundle)]


def _budget_proofs():
    return (
        EvaluatorBudgetProofV1(campaign_id="campaign-1", spec_hash="4" * 64,
                               job_id="eval-job-one", fence=1, receipt_digest="1" * 64,
                               actual_amount=1, actual_usage={"evaluator_calls": 1}),
        EvaluatorBudgetProofV1(campaign_id="campaign-1", spec_hash="4" * 64,
                               job_id="eval-job-two", fence=1, receipt_digest="2" * 64,
                               actual_amount=1, actual_usage={"evaluator_calls": 1}),
    )


class _StubRunner(TrustedEvaluatorRunner):
    def __init__(self, *, change_second_result: bool = False, emit_artifact: bool = False):
        super().__init__(bwrap_binary="not-used-in-stub")
        self.calls = 0
        self.change_second_result = change_second_result
        self.emit_artifact = emit_artifact

    def _sandbox_available(self):
        return True

    def _execute_isolated(self, argv, *, readonly, output, candidate_snapshot_id, tier, timeout):
        self.calls += 1
        private_map = json.loads((readonly / "tier" / "input-map.json").read_text())
        result_cases = []
        for entry in private_map:
            body = (readonly / "tier" / entry["path"]).read_bytes()
            result_cases.append({
                "case_id": entry["case_id"], "repetition": entry["repetition"],
                "input_sha256": entry["input_sha256"], "metrics": {},
            })
            assert _sha(body) == entry["input_sha256"]
        score = 0.5 + (0.1 if self.change_second_result and self.calls > 1 else 0.0)
        result = {
            "status": "ok", "metrics": {"quality": score},
            "protocol_sha256": self._protocol_hash,
            "cases": result_cases,
            "diagnostics": {"private_case_hint": "hidden-case-1"},
        }
        if self.emit_artifact:
            payload = b"protected evaluator artifact\n"
            (output / "summary.bin").write_bytes(payload)
            result["artifact_manifest"] = [{
                "case_id": result_cases[0]["case_id"], "repetition": result_cases[0]["repetition"],
                "path": "summary.bin", "sha256": _sha(payload), "candidate_snapshot": candidate_snapshot_id,
            }]
        return json.dumps(result)


def _run(runner=None):
    protocol, manifest, manifest_bytes, bundle, inputs = _protocol_and_manifest()
    runner = runner or _StubRunner()
    runner._protocol_hash = manifest.protocol_sha256
    execution = runner.run(
        protocol=protocol, approval_digest="3" * 64, campaign_id="campaign-1",
        spec_hash="4" * 64, candidate_snapshot_id="b" * 40,
        candidate_files={"src/main.py": b"candidate code"}, evaluator_bundle=bundle,
        tier="holdout", tier_manifest=manifest_bytes, case_inputs=inputs,
    )
    return execution, protocol, manifest, manifest_bytes, bundle, inputs


def test_runner_returns_private_execution_then_final_public_receipt():
    execution, _, _, _, _, inputs = _run()
    receipt = EvaluatorRunReceiptV1.from_completed_runs(
        (execution, execution), _budget_proofs()
    )
    public = receipt.model_dump_json()
    assert receipt.tier == "holdout"
    assert receipt.result_digest
    assert len(receipt.budget_proofs) == 2
    assert "hidden-case-1" not in public
    assert "private alpha" not in public
    assert "private_case_hint" not in public
    assert len(execution.artifacts) == 0
    assert set(inputs)  # test fixture carries actual protected inputs


def test_verified_artifact_bytes_return_only_to_core_with_public_digest():
    execution, *_ = _run(_StubRunner(emit_artifact=True))
    receipt = EvaluatorRunReceiptV1.from_completed_runs((execution, execution), _budget_proofs())
    content = b"protected evaluator artifact\n"
    digest = _sha(content)
    assert execution.output_artifact_digests == (digest,)
    assert len(execution.artifacts) == 1
    assert execution.artifacts[0].sha256 == digest
    assert execution.artifacts[0].data == content
    assert content.decode() not in receipt.model_dump_json()


def test_runner_requires_exact_case_bytes_and_protocol_bound_manifest():
    protocol, _, manifest_bytes, bundle, inputs = _protocol_and_manifest()
    changed = dict(inputs)
    changed[("hidden-case-1", 0)] = b"tampered"
    runner = _StubRunner()
    with pytest.raises(TrustedEvaluatorError, match="input bytes"):
        runner.run(
            protocol=protocol, approval_digest="3" * 64, campaign_id="campaign-1",
            spec_hash="4" * 64, candidate_snapshot_id="b" * 40,
            candidate_files={"src/main.py": b"candidate code"}, evaluator_bundle=bundle,
            tier="holdout", tier_manifest=manifest_bytes, case_inputs=changed,
        )


def test_final_receipt_rejects_nonrepeatable_results_without_leaking_scores():
    runner = _StubRunner(change_second_result=True)
    first, protocol, manifest, manifest_bytes, bundle, inputs = _run(runner)
    runner._protocol_hash = manifest.protocol_sha256
    second = runner.run(
        protocol=protocol, approval_digest="3" * 64, campaign_id="campaign-1",
        spec_hash="4" * 64, candidate_snapshot_id="b" * 40,
        candidate_files={"src/main.py": b"candidate code"}, evaluator_bundle=bundle,
        tier="holdout", tier_manifest=manifest_bytes, case_inputs=inputs,
    )
    with pytest.raises(TrustedEvaluatorError, match="differ"):
        EvaluatorRunReceiptV1.from_completed_runs((first, second), _budget_proofs())


def test_runner_refuses_to_fallback_when_bubblewrap_is_unavailable():
    protocol, _, manifest_bytes, bundle, inputs = _protocol_and_manifest()
    runner = TrustedEvaluatorRunner(bwrap_binary="/definitely/missing/bwrap")
    with pytest.raises(TrustedEvaluatorError, match="refusing evaluator fallback"):
        runner.run(
            protocol=protocol, approval_digest="3" * 64, campaign_id="campaign-1",
            spec_hash="4" * 64, candidate_snapshot_id="b" * 40,
            candidate_files={"src/main.py": b"candidate code"}, evaluator_bundle=bundle,
            tier="holdout", tier_manifest=manifest_bytes, case_inputs=inputs,
        )


def test_real_bubblewrap_runner_executes_frozen_evaluator_without_network_fallback():
    protocol, manifest, manifest_bytes, _bundle, inputs = _protocol_and_manifest()
    script = b'''import json, os
from pathlib import Path
tier = Path(os.environ["BANANLOOP_TIER"])
manifest = json.loads((tier / "manifest.json").read_text())
cases = json.loads((tier / "input-map.json").read_text())
candidate = Path(os.environ["BANANLOOP_CANDIDATE"]) / "src/main.py"
quality = 0.8 if "better" in candidate.read_text() else 0.5
print(json.dumps({
    "status": "ok",
    "metrics": {"quality": quality},
    "protocol_sha256": manifest["protocol_sha256"],
    "cases": [{key: item[key] for key in ("case_id", "repetition", "input_sha256")} for item in cases],
}))
'''
    bundle_io = io.BytesIO()
    with zipfile.ZipFile(bundle_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evaluate.py", script)
    bundle = bundle_io.getvalue()
    protocol = protocol.model_copy(update={"evaluator_bundle_sha256": _sha(bundle)})
    protocol = protocol.model_copy(update={"entrypoint": ("python3", "{evaluator}/evaluate.py")})
    manifest = manifest.model_copy(update={"protocol_sha256": _case_protocol_digest(protocol)})
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    protocol = protocol.model_copy(update={
        "tiers": tuple(
            item.model_copy(update={"manifest_sha256": _sha(manifest_bytes)})
            if item.tier == "holdout" else item for item in protocol.tiers
        ),
    })
    runner = TrustedEvaluatorRunner()
    if not runner._sandbox_available():
        pytest.skip("host does not permit the production bubblewrap namespaces")
    execution = runner.run(
        protocol=protocol, approval_digest="3" * 64, campaign_id="campaign-1",
        spec_hash="4" * 64, candidate_snapshot_id="b" * 40,
        candidate_files={"src/main.py": b"better candidate"},
        evaluator_bundle=bundle, tier="holdout", tier_manifest=manifest_bytes,
        case_inputs=inputs,
    )
    assert execution.tier == "holdout"
    private_result = json.loads(execution.private_result_bytes)
    assert private_result["metrics"]["quality"]["value"] == 0.8
    assert len(private_result["cases"]) == len(inputs)
    assert "private alpha" not in execution.private_result_bytes.decode()


def test_bundle_extraction_rejects_traversal_and_symlink_entries(tmp_path):
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside.txt", b"escape")
    with pytest.raises(TrustedEvaluatorError, match="traversal"):
        _extract_bundle(traversal.getvalue(), tmp_path / "bad-traversal",
                        expected_sha256=_sha(traversal.getvalue()))

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        item = zipfile.ZipInfo("linked")
        item.create_system = 3
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(item, b"outside")
    with pytest.raises(TrustedEvaluatorError, match="symlink"):
        _extract_bundle(symlink.getvalue(), tmp_path / "bad-symlink",
                        expected_sha256=_sha(symlink.getvalue()))


def test_live_output_guard_enforces_combined_size_and_rejects_symlinks(tmp_path):
    from bananloop.trusted_evaluator import _MAX_OUTPUT_BYTES

    output = tmp_path / "output"
    output.mkdir()
    (output / "large.bin").write_bytes(b"x")
    (output / "large.bin").write_bytes(b"x" * (_MAX_OUTPUT_BYTES + 1))
    with pytest.raises(TrustedEvaluatorError, match="live artifact limit"):
        _check_live_output_limits(output)

    (output / "large.bin").unlink()
    try:
        (output / "linked").symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("filesystem does not permit symlink creation")
    with pytest.raises(TrustedEvaluatorError, match="symlink"):
        _check_live_output_limits(output)


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def test_core_snapshot_resolver_materializes_only_approved_git_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "codex@example.invalid")
    _git(repo, "config", "user.name", "Codex Test")
    (repo / "src").mkdir()
    (repo / "private").mkdir()
    (repo / "src" / "app.py").write_bytes(b"approved source")
    (repo / "private" / "notes.txt").write_bytes(b"must not be materialized")
    _git(repo, "add", "src/app.py", "private/notes.txt")
    _git(repo, "commit", "-qm", "snapshot")
    snapshot = _git(repo, "rev-parse", "HEAD")
    adapter = TrustedEvaluatorAdapter(
        graph=SimpleNamespace(bind_campaign_id=lambda _: None), queue=None,
        campaign_id="campaign-1", state_root=tmp_path,
    )
    spec = SimpleNamespace(
        source_repo=str(repo),
        autonomy=SimpleNamespace(capability_policy=SimpleNamespace(read_paths=("src/**",))),
    )
    files = adapter._snapshot_files(spec, snapshot, snapshot)
    assert files == {"src/app.py": b"approved source"}


def test_core_snapshot_resolver_rejects_candidate_not_descended_from_protocol_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "codex@example.invalid")
    _git(repo, "config", "user.name", "Codex Test")
    (repo / "main.py").write_bytes(b"first")
    _git(repo, "add", "main.py")
    _git(repo, "commit", "-qm", "first")
    source = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--orphan", "unrelated")
    _git(repo, "rm", "-rf", ".")
    (repo / "main.py").write_bytes(b"unrelated")
    _git(repo, "add", "main.py")
    _git(repo, "commit", "-qm", "unrelated")
    candidate = _git(repo, "rev-parse", "HEAD")
    adapter = TrustedEvaluatorAdapter(
        graph=SimpleNamespace(bind_campaign_id=lambda _: None), queue=None,
        campaign_id="campaign-1", state_root=tmp_path,
    )
    spec = SimpleNamespace(
        source_repo=str(repo),
        autonomy=SimpleNamespace(capability_policy=SimpleNamespace(read_paths=("**",))),
    )
    with pytest.raises(TrustedEvaluatorError, match="not descended"):
        adapter._snapshot_files(spec, source, candidate)



def test_create_integration_evidence_binds_runtime_optimization_hash_after_source_approval(
    monkeypatch, tmp_path,
):
    protocol, manifest, manifest_bytes, bundle, inputs = _protocol_and_manifest()
    validation_tier = EvaluatorTierManifestV1(
        tier="validation", manifest_sha256=_sha(manifest_bytes),
        case_count=len(manifest.cases),
    )
    protocol = protocol.model_copy(update={
        "tiers": tuple(sorted((*protocol.tiers, validation_tier), key=lambda item: item.tier)),
    })
    source_spec_hash = "e" * 64
    approval_digest = "3" * 64
    campaign_id = "campaign-1"
    optimization_spec = parse_campaign_spec_document({
        "schema_version": 2, "mode": "OPTIMIZATION",
        "goal": "Run the approved evaluator against candidate snapshots",
        "source_repo": "https://example.invalid/repo", "base_commit": "a" * 40,
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "b" * 64, "scope_version": 1},
        "budget": {"max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
                   "max_evaluator_calls": 10, "deadline_seconds": 3600,
                   "plateau_experiments": 3},
        "role_registry_digest": "c" * 64,
        "evaluation": {
            "protocol_id": protocol.protocol_id, "version": protocol.version,
            "content_sha256": protocol.content_digest(), "approval_digest": approval_digest,
        },
        "parent": None,
    })
    runtime_spec_hash = optimization_spec.approved_hash()
    assert optimization_spec.evaluation.approval_digest == approval_digest
    assert runtime_spec_hash != source_spec_hash

    proposal = SimpleNamespace(campaign_id=campaign_id, spec_hash=source_spec_hash, protocol=protocol)
    verifier_receipt = SimpleNamespace(campaign_id=campaign_id, protocol_digest=protocol.content_digest())
    monkeypatch.setattr(
        EvaluatorProtocolAuthority, "approved_protocol",
        lambda _authority, _approval_digest: (proposal, verifier_receipt),
    )
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA user_version=5")
    authority = EvaluatorProtocolAuthority(conn)
    graph = SimpleNamespace(
        bind_campaign_id=lambda _campaign_id: None,
        conn=conn,
        campaign_spec=lambda: optimization_spec,
    )
    adapter = TrustedEvaluatorAdapter(
        graph=graph, queue=None, campaign_id=campaign_id, state_root=tmp_path,
    )
    runner = _StubRunner()
    runner._protocol_hash = manifest.protocol_sha256

    def make_receipt(candidate_snapshot_id: str, prefix: str):
        execution = runner.run(
            protocol=protocol, approval_digest=approval_digest, campaign_id=campaign_id,
            spec_hash=runtime_spec_hash, candidate_snapshot_id=candidate_snapshot_id,
            candidate_files={"src/main.py": b"candidate code"}, evaluator_bundle=bundle,
            tier="validation", tier_manifest=manifest_bytes, case_inputs=inputs,
        )
        proofs = tuple(EvaluatorBudgetProofV1(
            campaign_id=campaign_id, spec_hash=runtime_spec_hash,
            job_id=f"{prefix}-{index}", fence=1, receipt_digest=f"{index:x}" * 64,
            actual_amount=1, actual_usage={"evaluator_calls": 1},
        ) for index in range(1, protocol.repeatability_runs + 1))
        receipt = EvaluatorRunReceiptV1.from_completed_runs(
            tuple(execution for _ in range(protocol.repeatability_runs)), proofs,
        )
        authority.append_run_receipt(receipt)
        return receipt

    baseline = make_receipt(protocol.source_snapshot_id, "baseline")
    candidate = make_receipt("b" * 40, "candidate")
    expected_cases = {
        (item.case_id, item.repetition): item.input_sha256 for item in manifest.cases
    }
    result = SimpleNamespace(
        metrics={"quality": SimpleNamespace(value=0.5)},
        cases=tuple(SimpleNamespace(
            case_id=case_id, repetition=repetition, input_sha256=input_sha256,
        ) for (case_id, repetition), input_sha256 in expected_cases.items()),
    )
    monkeypatch.setattr(adapter, "_snapshot_files", lambda *_args: None)
    monkeypatch.setattr(adapter, "_manifest_for_receipt", lambda _receipt: manifest)
    monkeypatch.setattr(adapter, "_load_evaluation_result", lambda _digest: result)
    monkeypatch.setattr(adapter, "_verify_budget_proofs", lambda *_args, **_kwargs: True)

    evidence = adapter.create_integration_evidence(
        baseline_run_receipt_digest=baseline.receipt_digest,
        candidate_run_receipt_digest=candidate.receipt_digest,
    )
    assert evidence.spec_hash == runtime_spec_hash
    assert evidence.approval_digest == approval_digest
    assert evidence.decision == "PASS"
    conn.close()
