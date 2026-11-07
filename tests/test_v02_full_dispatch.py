import hashlib
import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from benchmarks.v02_full.accounting import ResourceUsage
from benchmarks.v02_full.dispatch import (
    DispatchBoundaryError, DispatchOutcomeUnknown, DispatchOverBound,
    InvocationOutput, MeteredDispatchBoundary,
)
from benchmarks.v02_full.execution import (
    TaskMaterial, assign_tasks_after_run_authorization, authorize_run_start,
    project_assignment_for_track,
)
from benchmarks.v02_full.prereg import canonical_bytes, freeze_manifest

_spec = importlib.util.spec_from_file_location("prereg_test_helpers", Path(__file__).with_name("test_v02_full_prereg.py"))
_helpers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _helpers
_spec.loader.exec_module(_helpers)


def _setup(tmp_path):
    opt_raw = _helpers._raw_manifest("OPTIMIZATION", task_id="opt", suffix="opt")
    exp_raw = _helpers._raw_manifest("EXPLORATION", task_id="exp", suffix="exp")
    def materials(raw):
        mapping = {}
        for pin in raw["task_pins"]:
            payload = f"data-{pin['task_id']}".encode()
            rights = f"rights-{pin['task_id']}".encode()
            mapping[pin["task_id"]] = TaskMaterial(payload, rights, pin["initial_tree_sha256"])
        return mapping
    opt_material, exp_material = materials(opt_raw), materials(exp_raw)
    opt = freeze_manifest(opt_raw); exp = freeze_manifest(exp_raw)
    root = tmp_path / "evidence"; root.mkdir(mode=0o700, exist_ok=True)
    opt_ev, policy = _helpers._valid_evidence(opt, root)
    exp_ev, _ = _helpers._valid_evidence(exp, root)
    policy_path = _helpers._write_trust_policy(tmp_path / "protected" / "trust.json", policy)
    auth = authorize_run_start(
        opt, exp, opt_ev, exp_ev, evidence_root=str(root), trust_policy_path=str(policy_path),
        authorization_key_id="key-run_start_authorizer",
        authorization_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_helpers._sha("secret-RUN_START_AUTHORIZER"))),
    )
    plans = []
    assign_tasks_after_run_authorization(
        auth, opt, exp, opt_ev, exp_ev, trust_policy_path=str(policy_path), evidence_root=str(root),
        assignment_key_id="key-task_assignment_authorizer",
        assignment_private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_helpers._sha("secret-TASK_ASSIGNMENT_AUTHORIZER"))),
        material_loader=lambda manifest: opt_material if manifest.track == "OPTIMIZATION" else exp_material,
        assignment_hook=lambda plan: plans.append(plan),
    )
    pin = opt.task_pins[0]
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_helpers._sha("secret-ACTION_METER_ATTESTOR")))
    full = {name: 0 for name in ResourceUsage.model_fields}
    def bound_verifier(manifest, arm, pair, label, action, kind, bounds, proof):
        return (manifest is opt and arm in manifest.arms and pair == pin.blinded_pair_id_sha256
                and label == "A" and action == _helpers._sha("action") and kind == "MODEL_CALL"
                and proof == b"provider-ceiling-v1" and bounds.agent_calls == 1)
    def usage_verifier(receipt, context):
        assert context["action_kind"] == "MODEL_CALL"
        return ResourceUsage(**{**full, "agent_calls": 1, "input_tokens": 4, "output_tokens": 2,
                                "wall_seconds": 1, "spend_microusd": 3})
    boundary = MeteredDispatchBoundary(
        auth, manifest=opt, gate_evidence=opt_ev, track="OPTIMIZATION",
        assignment=project_assignment_for_track(plans[0], "OPTIMIZATION", pin.task_id),
        task_id=pin.task_id, pair_id=pin.blinded_pair_id_sha256, arm_label="A", arm_config=opt.arms[0],
        evidence_root=root, trust_policy_path=policy_path, meter_key_id="key-action_meter_attestor",
        meter_private_key=key, provider_usage_verifier=usage_verifier,
        provider_bound_verifier=bound_verifier,
    )
    return boundary, pin


def _bounds(**updates):
    value = {name: 0 for name in ResourceUsage.model_fields}
    value.update(updates)
    return ResourceUsage(**value)


@contextmanager
def _test_provider_broker(boundary, intent):
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker, ProviderBrokerError

    class Adapter:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            raise AssertionError("test broker must not invoke a provider")

        def cancel_action(self, action_id):
            pass

    broker = OneShotProviderBroker(
        Adapter(), action_id=intent.action_id, arm_config=boundary.arm_config,
        resource_bounds=intent.bounds, timeout_seconds=1,
    )
    try:
        broker.__enter__()
    except ProviderBrokerError as exc:
        if not hasattr(__import__("socket"), "AF_UNIX"):
            pytest.skip(f"host Python lacks required AF_UNIX transport: {exc}")
        raise
    except OSError as exc:
        pytest.skip(f"host sandbox denies Unix socket bind: {exc}")
    try:
        yield broker
    finally:
        broker.__exit__(RuntimeError, RuntimeError("test cleanup"), None)


def test_dispatch_rejects_same_role_arm_config_substitution(tmp_path):
    boundary, _ = _setup(tmp_path)
    try:
        # The fixture freezes BANANLOOP_V02_FULL as A and CODEX_DIRECT as B.
        # A caller must not be able to change model/bundle data while keeping
        # the semantic arm and its signed blinded label.
        boundary._arm_config = boundary.arm_config.model_copy(
            update={"worker_bundle_sha256": "f" * 64},
        )
        with pytest.raises(DispatchBoundaryError, match="arm configuration differs"):
            boundary._ensure_authorized()
    finally:
        boundary.close()


def test_dispatch_rejects_blinded_label_swap(tmp_path):
    boundary, _ = _setup(tmp_path)
    try:
        boundary.arm_label = "B"
        with pytest.raises(DispatchBoundaryError, match="signed pre-run assignment"):
            boundary._ensure_authorized()
    finally:
        boundary.close()


def test_pending_recovery_rejects_unpinned_arm_config_digest(tmp_path):
    boundary, _ = _setup(tmp_path)
    bounds = _bounds(agent_calls=1)
    action_id = _helpers._sha("action")
    try:
        intent = boundary._reserve_intent(
            action_id=action_id, action_kind="MODEL_CALL",
            actor_id_digest=_helpers._sha("actor"), bounds=bounds,
            bound_proof=b"provider-ceiling-v1",
        )
        boundary.conn.execute(
            "UPDATE meter_actions SET arm_configuration_sha256=? WHERE pair_id=? AND arm_label=? AND sequence=?",
            ("f" * 64, boundary.pair_id, boundary.arm_label, intent.sequence),
        )
        with pytest.raises(DispatchBoundaryError, match="pending action arm configuration"):
            boundary._recover_pending()
    finally:
        boundary.close()


@pytest.mark.parametrize("mutation", ["config", "label"])
def test_provider_bound_verifier_cannot_change_arm_context_before_dispatch(tmp_path, mutation):
    boundary, _ = _setup(tmp_path)
    original_verifier = boundary.provider_bound_verifier
    operation_called = False

    def mutating_verifier(*args):
        result = original_verifier(*args)
        if mutation == "config":
            boundary._arm_config = boundary.arm_config.model_copy(
                update={"worker_bundle_sha256": "f" * 64},
            )
        else:
            boundary.arm_label = "B"
        return result

    def operation():
        nonlocal operation_called
        operation_called = True
        return None

    boundary.provider_bound_verifier = mutating_verifier
    try:
        with pytest.raises(DispatchBoundaryError, match="configuration differs|label does not match"):
            boundary.dispatch_one(
                action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
                actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
                bound_proof=b"provider-ceiling-v1", operation=operation,
            )
        assert not operation_called
        assert boundary.conn.execute("SELECT COUNT(*) FROM meter_actions").fetchone()[0] == 0
    finally:
        boundary.close()


def test_dispatch_persists_signed_receipt_and_exposes_report_refs(tmp_path):
    boundary, _ = _setup(tmp_path)
    result = boundary.dispatch_one(
        action_id=_helpers._sha("action"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
        bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10, wall_seconds=10, spend_microusd=10),
        bound_proof=b"provider-ceiling-v1",
        operation=lambda: InvocationOutput("ok", b"authoritative-provider-receipt"),
    )
    assert result.value == "ok"
    assert boundary.chain_head() == (1, result.receipt.receipt_sha256, "OPEN")
    refs = boundary.receipt_attestations()
    assert refs[0][0] == result.receipt and refs[0][1] == result.evidence_ref
    raw = (boundary.evidence_root / result.evidence_ref.path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result.evidence_ref.artifact_sha256
    boundary.close()


def test_caps_and_unproven_bound_block_callback(tmp_path):
    boundary, _ = _setup(tmp_path)
    called = []
    with pytest.raises(DispatchBoundaryError, match="provider-side"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
            bounds=_bounds(agent_calls=1), bound_proof=b"bad proof",
            operation=lambda: called.append(True),
        )
    assert not called
    boundary.close()


def test_provider_receipt_failure_seals_unknown_and_blocks_next_dispatch(tmp_path):
    boundary, _ = _setup(tmp_path)
    boundary.provider_usage_verifier = lambda receipt, context: (_ for _ in ()).throw(ValueError("bad signature"))
    with pytest.raises(DispatchOutcomeUnknown):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
            bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10, wall_seconds=10, spend_microusd=10),
            bound_proof=b"provider-ceiling-v1",
            operation=lambda: InvocationOutput(None, b"unverifiable"),
        )
    with pytest.raises(DispatchBoundaryError, match="closed"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action2"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
            bounds=_bounds(agent_calls=1), bound_proof=b"provider-ceiling-v1", operation=lambda: None,
        )
    boundary.close()


def test_pending_intent_recovery_is_unknown_and_assignment_is_rechecked(tmp_path):
    boundary, pin = _setup(tmp_path)
    boundary._reserve_intent(action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
        actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1), bound_proof=b"provider-ceiling-v1")
    boundary.close()
    reopened, _ = _setup_reopen(tmp_path, pin)
    assert reopened.chain_head()[2] == "CLOSED_UNKNOWN"
    with pytest.raises(DispatchBoundaryError):
        reopened.dispatch_one(action_id=_helpers._sha("other"), action_kind="MODEL_CALL",
            actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
            bound_proof=b"provider-ceiling-v1", operation=lambda: None)
    reopened.close()


def _setup_reopen(tmp_path, pin):
    # Reuse the validated setup inputs but point at the existing evidence and meter database.
    fresh, _ = _setup(tmp_path)
    return fresh, pin


def test_cap_rejection_and_tampered_authorization_never_call_operation(tmp_path):
    boundary, _ = _setup(tmp_path)
    boundary.provider_bound_verifier = lambda *args: True
    called = []
    with pytest.raises(DispatchBoundaryError, match="cap"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
            bounds=_bounds(agent_calls=21), bound_proof=b"provider-ceiling-v1",
            operation=lambda: called.append(True),
        )
    assert called == []
    from dataclasses import replace
    boundary.authorization = replace(boundary.authorization, raw_bytes=b"tampered")
    with pytest.raises(DispatchBoundaryError):
        boundary.dispatch_one(
            action_id=_helpers._sha("action2"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
            bounds=_bounds(agent_calls=1), bound_proof=b"provider-ceiling-v1",
            operation=lambda: called.append(True),
        )
    assert called == []
    boundary.close()


def test_receipt_artifact_tampering_is_detectable_by_report_reference(tmp_path):
    boundary, _ = _setup(tmp_path)
    result = boundary.dispatch_one(
        action_id=_helpers._sha("action"), action_kind="MODEL_CALL", actor_id_digest=_helpers._sha("actor"),
        bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10, wall_seconds=10, spend_microusd=10),
        bound_proof=b"provider-ceiling-v1",
        operation=lambda: InvocationOutput("ok", b"authoritative-provider-receipt"),
    )
    artifact = boundary.evidence_root / result.evidence_ref.path
    artifact.write_bytes(b"tampered")
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() != result.evidence_ref.artifact_sha256
    boundary.close()


def test_forged_assignment_plan_cannot_bypass_signed_rights_pins(tmp_path):
    from dataclasses import replace
    boundary, _ = _setup(tmp_path)
    projection = boundary.assignment
    task = projection.task
    forged_task = replace(task, rights_record_sha256=_helpers._sha("forged-rights"))
    boundary.assignment = replace(
        projection, task=forged_task,
    )
    called = []
    with pytest.raises(DispatchBoundaryError, match="assignment"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
            actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
            bound_proof=b"provider-ceiling-v1", operation=lambda: called.append(True),
        )
    assert called == []
    boundary.close()


def test_arm_boundary_receives_only_its_track_assignment_projection(tmp_path):
    boundary, _ = _setup(tmp_path)
    projection = boundary.assignment
    assert projection.track == "OPTIMIZATION"
    assert projection.task.task_id == "opt-0"
    assert len(projection.inclusion_proof) == 4
    assert not hasattr(projection, "tasks")
    assert not hasattr(projection, "assignments")
    assert not hasattr(projection, "exploration")
    assert not hasattr(boundary, "exploration")
    assert not hasattr(boundary, "exploration_evidence")
    boundary.close()


def test_tampered_task_inclusion_proof_blocks_before_dispatch(tmp_path):
    from dataclasses import replace
    boundary, _ = _setup(tmp_path)
    first_side, first_digest = boundary.assignment.inclusion_proof[0]
    bad = "0" * 64 if first_digest != "0" * 64 else "1" * 64
    boundary.assignment = replace(
        boundary.assignment,
        inclusion_proof=((first_side, bad),) + boundary.assignment.inclusion_proof[1:],
    )
    called = []
    with pytest.raises(DispatchBoundaryError, match="assignment"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
            actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
            bound_proof=b"provider-ceiling-v1", operation=lambda: called.append(True),
        )
    assert called == []
    boundary.close()


def test_worker_envelope_contains_one_task_and_no_evaluator_material(tmp_path):
    import json
    from benchmarks.v02_full.worker import _arm_envelope

    boundary, _ = _setup(tmp_path)
    raw = _arm_envelope(
        boundary.assignment, boundary.manifest, boundary.arm_config, arm_label="A",
        action_id=_helpers._sha("envelope-action"),
    )
    envelope = json.loads(raw)
    assert envelope["task_id"] == boundary.task_id
    assert envelope["track"] == "OPTIMIZATION"
    assert envelope["action_id"] == _helpers._sha("envelope-action")
    assert "evaluator" not in envelope
    assert "validation_set" not in envelope
    assert "holdout_set" not in envelope
    assert "exploration" not in envelope
    assert "optimization" not in envelope
    boundary.close()


def test_worker_runtime_fingerprint_binds_command_workspace_and_sandbox(tmp_path):
    from benchmarks.v02_full.worker import worker_bundle_fingerprint

    workspace = tmp_path / "runtime"
    workspace.mkdir()
    code = workspace / "worker.py"
    code.write_text("print('first')\n", encoding="utf-8")
    code.chmod(0o700)
    sandbox = tmp_path / "bwrap"
    sandbox.write_bytes(b"bubblewrap-fixture")
    sandbox.chmod(0o700)
    command = ["/workspace/worker.py", "/workspace/worker.py"]
    initial = worker_bundle_fingerprint(workspace, command,
                                        sandbox_binary=sandbox)
    code.write_text("print('changed')\n", encoding="utf-8")
    changed_code = worker_bundle_fingerprint(workspace, command,
                                             sandbox_binary=sandbox)
    changed_command = worker_bundle_fingerprint(workspace, [command[0], "/workspace/other.py"],
                                                sandbox_binary=sandbox)
    sandbox.write_bytes(b"different-bubblewrap")
    changed_sandbox = worker_bundle_fingerprint(workspace, command,
                                                sandbox_binary=sandbox)
    assert len({initial, changed_code, changed_command, changed_sandbox}) == 4


def test_worker_refuses_shell_commands_and_unsupported_sandbox_without_fallback(tmp_path, monkeypatch):
    import benchmarks.v02_full.worker as worker_module
    from benchmarks.v02_full.worker import ArmWorkerError, BubblewrapArmWorker

    boundary, _ = _setup(tmp_path)
    sandbox = tmp_path / "bwrap"
    sandbox.write_bytes(b"fake")
    sandbox.chmod(0o700)
    worker = BubblewrapArmWorker(bwrap_binary=str(sandbox))
    action_id = _helpers._sha("action")
    run_args = {
        "argv": ["/bin/sh", "-c", "echo unsafe"], "workspace": tmp_path,
        "projection": boundary.assignment, "manifest": boundary.manifest,
        "arm_config": boundary.arm_config, "arm_label": "A", "action_id": action_id,
        "timeout_seconds": 2, "memory_bytes": 128 * 1024 * 1024,
        "evidence_root": boundary.evidence_root,
        "trust_policy_path": boundary.trust_policy_path,
    }
    with pytest.raises(ArmWorkerError, match="metered-dispatch permit"):
        worker.run(**run_args)
    intent = boundary._reserve_intent(
        action_id=action_id, action_kind="MODEL_CALL",
        actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
        bound_proof=b"provider-ceiling-v1",
    )
    with _test_provider_broker(boundary, intent) as broker:
        run_args["provider_broker_socket"] = broker.socket_path
        run_args["_metered_permit"] = boundary._worker_launch_permit(intent, broker)
        with pytest.raises(ArmWorkerError, match="shell"):
            worker.run(**run_args)
        monkeypatch.setattr(worker_module, "worker_bundle_fingerprint",
                            lambda *args, **kwargs: boundary.arm_config.worker_bundle_sha256)
        monkeypatch.setattr(worker, "available", lambda: False)
        with pytest.raises(ArmWorkerError, match="refusing local execution fallback"):
            worker.run(
                **{**run_args, "argv": ["/usr/bin/python3", "/workspace/worker.py"]},
            )
    boundary.close()


def test_worker_launch_permit_is_signed_and_single_use(tmp_path):
    import json
    from benchmarks.v02_full.evidence import EvidenceArtifactRef
    from benchmarks.v02_full.worker import (
        ArmWorkerError, _consume_metered_arm_launch_permit,
        _verify_metered_arm_launch_permit,
    )

    boundary, _ = _setup(tmp_path)
    intent = boundary._reserve_intent(
        action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
        actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
        bound_proof=b"provider-ceiling-v1",
    )
    try:
        with _test_provider_broker(boundary, intent) as broker:
            permit = boundary._worker_launch_permit(intent, broker)
            verify_args = dict(
                projection=boundary.assignment, manifest=boundary.manifest,
                arm_config=boundary.arm_config, arm_label=boundary.arm_label,
                action_id=intent.action_id, evidence_root=boundary.evidence_root,
                trust_policy_path=boundary.trust_policy_path,
                provider_broker_socket=broker.socket_path,
            )
            payload = _verify_metered_arm_launch_permit(permit, **verify_args)
            with pytest.raises(ArmWorkerError, match="worker launch permit does not verify"):
                _verify_metered_arm_launch_permit(
                    permit, **{**verify_args, "provider_broker_socket": None},
                )
            with _test_provider_broker(boundary, intent) as other_broker:
                with pytest.raises(ArmWorkerError, match="worker launch permit does not verify"):
                    _verify_metered_arm_launch_permit(
                        permit, **{**verify_args,
                                   "provider_broker_socket": other_broker.socket_path},
                    )
            raw = (boundary.evidence_root / permit.path).read_bytes()
            forged = json.loads(raw)
            forged["payload"]["arm_configuration_sha256"] = "f" * 64
            forged_raw = canonical_bytes(forged)
            forged_path = "forged-worker-launch.json"
            (boundary.evidence_root / forged_path).write_bytes(forged_raw)
            forged_ref = EvidenceArtifactRef(
                path=forged_path, artifact_sha256=hashlib.sha256(forged_raw).hexdigest(),
            )
            with pytest.raises(ArmWorkerError, match="worker launch permit does not verify"):
                _verify_metered_arm_launch_permit(forged_ref, **verify_args)
            _consume_metered_arm_launch_permit(payload, evidence_root=boundary.evidence_root)
            assert boundary.conn.execute(
                "SELECT worker_launch_state FROM meter_actions WHERE pair_id=? AND arm_label=? AND sequence=?",
                (boundary.pair_id, boundary.arm_label, intent.sequence),
            ).fetchone()[0] == "LAUNCHED"
            with pytest.raises(ArmWorkerError, match="already been consumed"):
                _consume_metered_arm_launch_permit(payload, evidence_root=boundary.evidence_root)
    finally:
        boundary.close()


def test_non_model_meter_intent_cannot_issue_worker_launch_permit(tmp_path):
    boundary, _ = _setup(tmp_path)
    boundary.provider_bound_verifier = lambda *args, **kwargs: True
    try:
        intent = boundary._reserve_intent(
            action_id=_helpers._sha("tool-action"), action_kind="TOOL_CALL",
            actor_id_digest=_helpers._sha("actor"), bounds=_bounds(agent_calls=1),
            bound_proof=b"tool-provider-ceiling-v1",
        )
        with pytest.raises(DispatchBoundaryError, match="matching durable metered intent"):
            boundary._worker_launch_permit(intent, None)
    finally:
        boundary.close()


@pytest.mark.parametrize("worker_output", ["valid", "wrong_identity", "duplicate_key", "noncanonical", "nonfinite"])
def test_metered_boundary_can_dispatch_one_isolated_model_action(tmp_path, monkeypatch, worker_output):
    import base64
    import json
    import benchmarks.v02_full.dispatch as dispatch_module
    from benchmarks.v02_full.provider_broker import ProviderInvocation
    from benchmarks.v02_full.worker import ArmWorkerResult, BubblewrapArmWorker

    boundary, pin = _setup(tmp_path)
    observed = {}
    action_id = _helpers._sha("action")

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, prompt, *, action_id, arm_config, resource_bounds,
                        timeout_seconds):
            assert prompt == b"one frozen task prompt"
            assert action_id == _helpers._sha("action")
            assert arm_config == boundary.arm_config
            assert resource_bounds.input_tokens == 10
            assert resource_bounds.output_tokens == 10
            assert resource_bounds.spend_microusd == 10
            assert timeout_seconds == 5
            return ProviderInvocation(b"test-only answer", b"authoritative-provider-receipt")

        def cancel_action(self, action_id):
            return None

    class TestOnlyWorker(BubblewrapArmWorker):
        def run(self, **kwargs):
            observed.update(kwargs)
            provider_result = ProviderInvocation(b"test-only answer", b"authoritative-provider-receipt")
            envelope = {
                "schema_version": 1, "result": {"answer": provider_result.output.decode()},
                "provider_receipt_base64": base64.b64encode(provider_result.provider_receipt).decode(),
            }
            output = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
            if worker_output == "duplicate_key":
                output = output[:-1] + b',"schema_version":1}'
            elif worker_output == "noncanonical":
                output = json.dumps(envelope, sort_keys=False).encode()
            elif worker_output == "nonfinite":
                output = output.replace(b'"answer":"test-only answer"', b'"answer":NaN')
            result_track = "EXPLORATION" if worker_output == "wrong_identity" else kwargs["manifest"].track
            return ArmWorkerResult(output, kwargs["projection"].task.task_id,
                                   result_track, pin.blinded_pair_id_sha256, 0.1)

    from benchmarks.v02_full.provider_broker import OneShotProviderBroker

    class TestOnlyBroker(OneShotProviderBroker):
        def __init__(self, adapter, **kwargs):
            assert isinstance(adapter, TestOnlyProvider)
            assert kwargs["action_id"] == action_id
            assert kwargs["arm_config"] == boundary.arm_config
            assert kwargs["resource_bounds"].input_tokens == 10
            assert kwargs["resource_bounds"].output_tokens == 10
            self.action_id = kwargs["action_id"]
            self.arm_config = kwargs["arm_config"]
            self.resource_bounds = kwargs["resource_bounds"]
            self.socket_path = Path("/test-only/provider.sock")
            class Server:
                def getsockname(self):
                    return str(self.socket_path)
            self._server = Server()
            self._server.socket_path = self.socket_path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def verify_complete(self):
            return b"authoritative-provider-receipt"

    monkeypatch.setattr(dispatch_module, "OneShotProviderBroker", TestOnlyBroker)
    monkeypatch.setattr(dispatch_module, "_broker_socket_identity", lambda path: {
        "broker_socket_path_sha256": _helpers._sha("fake-broker-path"),
        "broker_socket_device": "1", "broker_socket_inode": "2",
    })

    dispatch = lambda: boundary.dispatch_isolated_model_worker(
        worker=TestOnlyWorker(bwrap_binary="test-only"), argv=("/workspace/worker",),
        workspace=tmp_path, timeout_seconds=10, memory_bytes=128 * 1024 * 1024,
        actor_id_digest=_helpers._sha("actor-A"),
        bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10,
                       wall_seconds=10, spend_microusd=10),
        bound_proof=b"provider-ceiling-v1", provider_adapter=TestOnlyProvider(),
        provider_timeout_seconds=5, action_id=action_id,
    )
    if worker_output != "valid":
        from benchmarks.v02_full.dispatch import DispatchOutcomeUnknown
        with pytest.raises(DispatchOutcomeUnknown, match="billable invocation ended"):
            dispatch()
        assert boundary.chain_head()[2] == "CLOSED_UNKNOWN"
        boundary.close()
        return
    result = dispatch()
    assert result.value == {"answer": "test-only answer"}
    assert observed["projection"].task.task_id == pin.task_id
    assert observed["manifest"].track == "OPTIMIZATION"
    assert "exploration" not in observed
    assert result.receipt.action_kind == "MODEL_CALL"
    boundary.close()


def test_worker_timeout_and_memory_are_checked_against_frozen_arm_caps_before_reservation(tmp_path):
    from benchmarks.v02_full.dispatch import DispatchBoundaryError
    from benchmarks.v02_full.worker import BubblewrapArmWorker

    boundary, _ = _setup(tmp_path)
    run_calls = []

    class NeverRunWorker(BubblewrapArmWorker):
        def run(self, **kwargs):
            run_calls.append(kwargs)
            raise AssertionError("worker must not start")

    base = {
        "worker": NeverRunWorker(bwrap_binary="test-only"),
        "argv": ("/workspace/worker",), "workspace": tmp_path,
        "memory_bytes": boundary.arm_config.worker_memory_bytes,
        "actor_id_digest": _helpers._sha("actor-A"),
        "bounds": _bounds(agent_calls=1, input_tokens=10, output_tokens=10,
                           wall_seconds=10, spend_microusd=10),
        "bound_proof": b"provider-ceiling-v1", "provider_adapter": object(),
        "provider_timeout_seconds": 5, "action_id": _helpers._sha("action"),
    }
    with pytest.raises(DispatchBoundaryError, match="worker timeout exceeds"):
        boundary.dispatch_isolated_model_worker(timeout_seconds=11, **base)
    mismatched = {**base, "memory_bytes": boundary.arm_config.worker_memory_bytes * 2}
    with pytest.raises(DispatchBoundaryError, match="memory ceiling differs"):
        boundary.dispatch_isolated_model_worker(timeout_seconds=10, **mismatched)
    assert run_calls == []
    assert boundary.chain_head() == (0, None, "OPEN")
    boundary.close()


def test_worker_cgroup_enforces_tree_memory_and_process_bounds(tmp_path):
    from benchmarks.v02_full.worker import (
        _cgroup_controllers_available, _configure_worker_cgroup, _is_cgroup2_path,
    )
    from benchmarks.v02_full.worker_launcher import attach_to_cgroup

    root = tmp_path / "delegated"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    (root / "cgroup.subtree_control").write_text("memory pids", encoding="ascii")
    # Marker files in an ordinary directory must never be accepted as cgroup v2.
    assert not _cgroup_controllers_available(root)
    assert not _is_cgroup2_path(root)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"29 23 0:26 / {root} rw,nosuid - cgroup2 cgroup rw\n", encoding="utf-8")
    assert _is_cgroup2_path(root, mountinfo=mountinfo)
    nested = root / "nested"
    nested.mkdir()
    mountinfo.write_text(
        f"29 23 0:26 / {root} rw,nosuid - cgroup2 cgroup rw\n"
        f"30 29 0:27 / {nested} rw - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    assert not _is_cgroup2_path(nested, mountinfo=mountinfo)
    child = root / "worker"
    child.mkdir()
    for name in ("memory.max", "memory.oom.group", "pids.max", "cgroup.procs",
                 "cgroup.kill", "cgroup.events"):
        (child / name).write_text("populated 0" if name == "cgroup.events" else "", encoding="ascii")
    _configure_worker_cgroup(child, memory_bytes=128 * 1024 * 1024, process_limit=32)
    assert (child / "memory.max").read_text(encoding="ascii") == str(128 * 1024 * 1024)
    assert (child / "memory.oom.group").read_text(encoding="ascii") == "1"
    assert (child / "pids.max").read_text(encoding="ascii") == "32"
    attach_to_cgroup(str(child / "cgroup.procs"))
    assert (child / "cgroup.procs").read_text(encoding="ascii") == str(__import__("os").getpid())
    (root / "cgroup.subtree_control").write_text("", encoding="ascii")
    assert not _cgroup_controllers_available(root)


def test_one_shot_broker_binds_action_and_calls_provider_once(tmp_path):
    import base64
    import json
    from benchmarks.v02_full.provider_broker import (
        OneShotProviderBroker, ProviderInvocation,
    )

    boundary, _ = _setup(tmp_path)
    action_id = _helpers._sha("broker-action")
    calls = []

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, prompt, *, action_id, arm_config, resource_bounds,
                        timeout_seconds):
            calls.append((prompt, action_id, arm_config.model, resource_bounds, timeout_seconds))
            return ProviderInvocation(b"result", b"signed-provider-receipt")

        def cancel_action(self, action_id):
            return None

    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=action_id, arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=17, output_tokens=21,
                                wall_seconds=9, spend_microusd=50), timeout_seconds=9,
    )
    request = json.dumps({
        "schema_version": 1, "action_id": action_id,
        "prompt_base64": base64.b64encode(b"prompt").decode(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    class MemoryConnection:
        def __init__(self, request_bytes):
            self.request = request_bytes
            self.response = b""

        def recv(self, limit):
            chunk, self.request = self.request[:limit], self.request[limit:]
            return chunk

        def sendall(self, value):
            self.response += value

    connection = MemoryConnection(request)
    broker._serve_connection(connection)
    response = connection.response
    envelope = json.loads(response)
    assert base64.b64decode(envelope["output_base64"]) == b"result"
    assert broker.verify_complete() == b"signed-provider-receipt"
    assert calls[0][:3] == (b"prompt", action_id, boundary.arm_config.model)
    assert calls[0][3].input_tokens == 17
    assert calls[0][3].output_tokens == 21
    assert calls[0][3].spend_microusd == 50
    assert calls[0][4] == 9
    boundary.close()


def test_one_shot_broker_unix_socket_roundtrip_when_host_allows_it(tmp_path):
    import socket
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("host Python lacks AF_UNIX transport")
    from benchmarks.v02_full.provider_broker import (
        OneShotProviderBroker, ProviderInvocation, request_one_provider_action,
    )

    boundary, _ = _setup(tmp_path)

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, prompt, *, action_id, arm_config, resource_bounds, timeout_seconds):
            return ProviderInvocation(prompt.upper(), b"provider-signed-receipt")

        def cancel_action(self, action_id):
            return None

    action_id = _helpers._sha("socket-broker-action")
    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=action_id, arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10,
                                wall_seconds=5, spend_microusd=30), timeout_seconds=5,
    )
    try:
        broker.__enter__()
    except PermissionError:
        boundary.close()
        pytest.skip("host sandbox denies Unix socket bind")
    try:
        result = request_one_provider_action(
            broker.socket_path, action_id=action_id, prompt=b"hello", timeout_seconds=3,
        )
        assert result.output == b"HELLO"
        assert result.provider_receipt == b"provider-signed-receipt"
        assert broker.verify_complete() == b"provider-signed-receipt"
    finally:
        try:
            broker.__exit__(None, None, None)
        finally:
            boundary.close()


def test_one_shot_broker_fails_closed_when_unix_socket_is_unavailable(tmp_path, monkeypatch):
    import socket
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker, ProviderBrokerError

    boundary, _ = _setup(tmp_path)

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            raise AssertionError("provider must not be invoked")

        def cancel_action(self, action_id):
            return None

    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=_helpers._sha("missing-af-unix"),
        arm_config=boundary.arm_config, resource_bounds=_bounds(agent_calls=1),
        timeout_seconds=1,
    )
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    try:
        with pytest.raises(ProviderBrokerError, match="requires AF_UNIX"):
            broker.__enter__()
        assert broker._temp is None
    finally:
        boundary.close()


def test_one_shot_broker_rejects_wrong_action_before_provider_call(tmp_path):
    import base64
    import json
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker, ProviderBrokerError

    boundary, _ = _setup(tmp_path)
    calls = []

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            calls.append(True)
            raise AssertionError("provider must not be invoked")

        def cancel_action(self, action_id):
            return None

    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=_helpers._sha("expected-action"),
        arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=5, output_tokens=5,
                                wall_seconds=3, spend_microusd=50), timeout_seconds=3,
    )
    request = json.dumps({
        "schema_version": 1, "action_id": _helpers._sha("different-action"),
        "prompt_base64": base64.b64encode(b"prompt").decode(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    class MemoryConnection:
        def __init__(self, payload):
            self.payload = payload

        def recv(self, limit):
            chunk, self.payload = self.payload[:limit], self.payload[limit:]
            return chunk

        def sendall(self, value):
            raise AssertionError("invalid request must not receive provider output")

    with pytest.raises(ProviderBrokerError, match="another action"):
        broker._serve_connection(MemoryConnection(request))
    assert not calls
    boundary.close()


def test_provider_broker_does_not_cancel_completed_action_and_always_cleans_temp(tmp_path):
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker, ProviderBrokerError

    boundary, _ = _setup(tmp_path)
    cancelled = []

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            raise AssertionError("not invoked")

        def cancel_action(self, action_id):
            cancelled.append(action_id)

    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=_helpers._sha("completed-action"),
        arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=5, output_tokens=5,
                                wall_seconds=3, spend_microusd=50), timeout_seconds=3,
    )
    broker.provider_receipt = b"provider-complete"
    broker._calls = 1
    broker._provider_started = True
    broker._provider_finished = True

    class FinishingThread:
        def __init__(self):
            self.checks = 0

        def join(self, timeout):
            return None

        def is_alive(self):
            self.checks += 1
            return self.checks == 1

    class TempDir:
        cleaned = False

        def cleanup(self):
            self.cleaned = True

    thread, temp = FinishingThread(), TempDir()
    broker._thread, broker._temp = thread, temp
    broker.__exit__(None, None, None)
    assert not cancelled
    assert temp.cleaned
    boundary.close()


def test_provider_broker_cancels_inflight_call_and_cleans_temp_on_failure(tmp_path):
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker, ProviderBrokerError

    boundary, _ = _setup(tmp_path)
    cancelled = []

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            raise AssertionError("not invoked")

        def cancel_action(self, action_id):
            cancelled.append(action_id)

    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=_helpers._sha("inflight-action"),
        arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=5, output_tokens=5,
                                wall_seconds=3, spend_microusd=50), timeout_seconds=3,
    )
    broker._provider_started = True

    class StuckThread:
        def join(self, timeout):
            return None

        def is_alive(self):
            return True

    class TempDir:
        cleaned = False

        def cleanup(self):
            self.cleaned = True

    thread, temp = StuckThread(), TempDir()
    broker._thread, broker._temp = thread, temp
    with pytest.raises(ProviderBrokerError, match="did not stop"):
        broker.__exit__(RuntimeError, RuntimeError("worker failed"), None)
    assert cancelled == [_helpers._sha("inflight-action")]
    assert temp.cleaned
    boundary.close()


def test_provider_invocation_exception_keeps_cancellation_eligible(tmp_path):
    import base64
    import json
    from benchmarks.v02_full.provider_broker import OneShotProviderBroker

    boundary, _ = _setup(tmp_path)
    cancelled = []

    class TestOnlyProvider:
        enforces_single_call = True
        enforces_timeout = True
        enforces_provider_ceilings = True
        enforces_cancellation = True

        def invoke_once(self, *args, **kwargs):
            raise TimeoutError("provider response lost after dispatch")

        def cancel_action(self, action_id):
            cancelled.append(action_id)

    action_id = _helpers._sha("uncertain-provider-action")
    broker = OneShotProviderBroker(
        TestOnlyProvider(), action_id=action_id, arm_config=boundary.arm_config,
        resource_bounds=_bounds(agent_calls=1, input_tokens=5, output_tokens=5,
                                wall_seconds=3, spend_microusd=50), timeout_seconds=3,
    )
    request = json.dumps({
        "schema_version": 1, "action_id": action_id,
        "prompt_base64": base64.b64encode(b"prompt").decode(),
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    class MemoryConnection:
        def __init__(self, payload):
            self.payload = payload

        def recv(self, limit):
            chunk, self.payload = self.payload[:limit], self.payload[limit:]
            return chunk

        def sendall(self, value):
            raise AssertionError("uncertain provider call must not return a response")

    with pytest.raises(TimeoutError):
        broker._serve_connection(MemoryConnection(request))
    assert broker._provider_started
    assert not broker._provider_finished

    class CompletingAfterCancel:
        def __init__(self):
            self.checks = 0

        def join(self, timeout):
            return None

        def is_alive(self):
            self.checks += 1
            return self.checks == 1

    class TempDir:
        def cleanup(self):
            return None

    broker._thread = CompletingAfterCancel()
    broker._temp = TempDir()
    broker.__exit__(TimeoutError, TimeoutError("worker/provider uncertainty"), None)
    assert cancelled == [action_id]
    boundary.close()


def test_host_observed_dispatch_wall_is_included_in_the_bound(tmp_path, monkeypatch):
    import benchmarks.v02_full.dispatch as dispatch_module
    from benchmarks.v02_full.dispatch import DispatchOverBound

    boundary, _ = _setup(tmp_path)
    readings = iter((10.0, 12.1))
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: next(readings))
    with pytest.raises(DispatchOverBound, match="wall_seconds"):
        boundary.dispatch_one(
            action_id=_helpers._sha("action"), action_kind="MODEL_CALL",
            actor_id_digest=_helpers._sha("wall-actor"),
            bounds=_bounds(agent_calls=1, input_tokens=10, output_tokens=10,
                            wall_seconds=1, spend_microusd=10),
            bound_proof=b"provider-ceiling-v1",
            operation=lambda: InvocationOutput("ok", b"authoritative-provider-receipt"),
        )
    assert boundary.chain_head()[2] == "CLOSED_OVER_BOUND"
    boundary.close()
