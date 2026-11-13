from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

from bananloop.contracts import CapabilityPolicyV2
from bananloop.budget_ledger import BudgetLedger
from bananloop.roles import (
    RoleContractError,
    PinnedRoleExecutorV1,
    RoleContextSourceV1,
    RoleDefinitionV1,
    RoleRegistryV1,
    RoleSpecializationV1,
    TeamFactoryV1,
    TeamManifestAuthority,
    TeamRequestV1,
    build_role_context,
)
from bananloop.worker_queue import DurableWorkerQueue, WorkerBudgetError


SHA = "a" * 64


def role(role_id: str = "researcher", *, priority: int = 10, calls: int = 3, tokens: int = 200) -> RoleDefinitionV1:
    return RoleDefinitionV1(
        role_id=role_id,
        adapter="codex",
        provider="openai",
        model="gpt-6-luna",
        model_version="pin-2026-11-03",
        prompt_digest=SHA,
        capability_policy=CapabilityPolicyV2(
            tools=("read_file", "list_files", "write_artifact"),
            read_paths=("src/**", "docs/**"),
            write_paths=("artifacts/**",),
            backend="ISOLATED",
            network_access=False,
        ),
        context_fields=("campaign_goal", "node_id", "public_task", "evidence_refs", "dev_summary"),
        priority=priority,
        min_instances=1,
        max_instances=4,
        calls_per_instance=calls,
        tokens_per_instance=tokens,
    )


def registry(*roles: RoleDefinitionV1) -> RoleRegistryV1:
    return RoleRegistryV1(registry_id="test-registry", version=1, roles=tuple(roles))


def request(*, desired: int = 1, specializations=(), calls: int = 100, tokens: int = 10000) -> TeamRequestV1:
    return TeamRequestV1(
        campaign_id="campaign-1",
        spec_hash="b" * 64,
        requested_instances={"researcher": desired},
        specializations=tuple(specializations),
        available_agent_calls=calls,
        available_model_tokens=tokens,
    )


def test_registry_digest_is_canonical_and_campaign_pin_is_required():
    first = registry(role("z_role", priority=20), role("researcher", priority=10))
    second = registry(role("researcher", priority=10), role("z_role", priority=20))
    assert first.digest() == second.digest()
    with pytest.raises(RoleContractError, match="registry digest"):
        TeamFactoryV1.build(first, request(), expected_registry_digest="c" * 64)


def test_team_manifest_is_stable_and_pins_model_provider_context_and_permissions():
    pinned = registry(role())
    first = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    replay = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    assert first.manifest_digest == replay.manifest_digest
    assert first.members == replay.members
    binding = first.job_binding(
        first.members[0].instance_id,
        action_id="action-1",
        context_digest=hashlib.sha256(b"context").hexdigest(),
    )
    assert binding["team_manifest_digest"] == first.manifest_digest
    assert binding["registry_digest"] if "registry_digest" in binding else binding["role_registry_digest"] == pinned.digest()
    assert binding["provider"] == "openai"
    assert binding["model"] == "gpt-6-luna"
    assert binding["model_version"] == "pin-2026-11-03"


def test_team_factory_scales_deterministically_to_call_and_token_budgets():
    pinned = registry(role())
    small = request(desired=4, calls=7, tokens=500)
    team1 = TeamFactoryV1.build(pinned, small, expected_registry_digest=pinned.digest())
    team2 = TeamFactoryV1.build(pinned, small, expected_registry_digest=pinned.digest())
    assert len(team1.members) == 2
    assert team1.manifest_digest == team2.manifest_digest
    assert team1.allocated_agent_calls <= small.available_agent_calls
    assert team1.allocated_model_tokens <= small.available_model_tokens
    with pytest.raises(RoleContractError, match="minimum team"):
        TeamFactoryV1.build(pinned, request(calls=2), expected_registry_digest=pinned.digest())


def test_specialization_only_removes_capability_context_and_budget():
    pinned = registry(role())
    variant = RoleSpecializationV1(
        specialization_id="read_only_researcher",
        base_role_id="researcher",
        remove_tools=("write_artifact",),
        remove_read_paths=("docs/**",),
        disable_network=True,
        require_isolation=True,
        calls_per_instance=1,
        tokens_per_instance=50,
        context_fields=("campaign_goal", "public_task"),
    )
    team = TeamFactoryV1.build(
        pinned,
        request(desired=2, specializations=(variant,)),
        expected_registry_digest=pinned.digest(),
    )
    specialized = next(member.role for member in team.members if member.role.specialization_id)
    assert specialized.capability_policy.tools == ("list_files", "read_file")
    assert specialized.capability_policy.read_paths == ("src/**",)
    assert specialized.context_fields == ("campaign_goal", "public_task")
    assert specialized.calls_per_instance == 1
    assert specialized.tokens_per_instance == 50
    assert specialized.model == "gpt-6-luna"


@pytest.mark.parametrize(
    "change",
    [
        {"remove_tools": ("run_command",)},
        {"remove_read_paths": ("outside/**",)},
        {"context_fields": ("campaign_goal", "holdout_labels")},
        {"model": "replacement-model"},
        {"provider": "replacement-provider"},
    ],
)
def test_specialization_cannot_widen_policy_context_or_swap_model(change):
    pinned = registry(role())
    payload = {
        "specialization_id": "unsafe_variant", "base_role_id": "researcher", **change,
    }
    if "model" in change or "provider" in change:
        with pytest.raises(ValidationError):
            RoleSpecializationV1.model_validate(payload)
        return
    variant = RoleSpecializationV1.model_validate(payload)
    with pytest.raises(RoleContractError):
        TeamFactoryV1.build(
            pinned,
            request(desired=2, specializations=(variant,)),
            expected_registry_digest=pinned.digest(),
        )


def test_context_is_fresh_role_scoped_and_discards_unapproved_fields():
    pinned = registry(role())
    team = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    member = team.members[0]
    source = RoleContextSourceV1(
        campaign_goal={"value": "Improve the project", "classification": "PUBLIC_SPEC",
                       "source_ref": "campaign://spec/goal"},
        fields={
            "node_id": {"value": "node-a", "classification": "PUBLIC_NODE",
                        "source_ref": "graph://nodes/node-a"},
            "public_task": {"value": "HOLDOUT secret payload", "classification": "HOLDOUT",
                            "source_ref": "evidence://holdout/case-1"},
            "evidence_refs": {"value": ["evidence:1"], "classification": "DEV_AGGREGATE",
                              "source_ref": "research://evidence/dev-1"},
            "dev_summary": {"value": "DEV-only aggregate", "classification": "DEV_AGGREGATE",
                            "source_ref": "research://summary/dev"},
            "validation_labels": {"value": ["hidden"], "classification": "VALIDATION",
                                  "source_ref": "evidence://validation/1"},
            "holdout_result": {"value": {"score": 1.0}, "classification": "HOLDOUT",
                               "source_ref": "evidence://holdout/1"},
            "other_role_private_notes": {"value": "secret", "classification": "SECRET",
                                         "source_ref": "role://private/notes"},
        },
    )
    one = build_role_context(member, source)
    two = build_role_context(member, source)
    assert one is not two
    assert one.model_dump(mode="json") == two.model_dump(mode="json")
    encoded = one.model_dump_json()
    assert "hidden" not in encoded
    assert "HOLDOUT secret payload" not in encoded
    assert "holdout_result" not in encoded
    assert "secret" not in encoded
    assert one.public_task is None
    with pytest.raises(RoleContractError, match="typed Core provenance"):
        build_role_context(member, {"campaign_goal": "raw caller value"})


def test_public_task_context_supports_bounded_full_protocol_review_material():
    from bananloop.roles import RoleContextV1

    review = RoleContextV1(
        role_id="independent_verifier", campaign_goal="Review the public evaluator.",
        public_task="x" * 153_947,
    )
    assert len(review.public_task) == 153_947
    with pytest.raises(ValidationError):
        RoleContextV1(
            role_id="independent_verifier", campaign_goal="Review the public evaluator.",
            public_task="x" * 180_001,
        )


def test_pinned_executor_supplies_manifest_config_and_rejects_request_tampering():
    pinned = registry(role())
    manifest = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    member = manifest.members[0]
    context_digest = hashlib.sha256(b"context").hexdigest()
    binding = manifest.job_binding(member.instance_id, action_id="action-1", context_digest=context_digest)
    observed = {}

    def invoke(_request, _gateway, **pin):
        observed.update(pin)
        return {"ok": True}

    executor = PinnedRoleExecutorV1.from_manifest(manifest, member.instance_id, invoke)
    assert executor.dispatch({"role_binding": binding}, object()) == {"ok": True}
    assert observed == {
        "adapter": "codex", "provider": "openai", "model": "gpt-6-luna",
        "model_version": "pin-2026-11-03", "prompt_digest": SHA,
        "max_model_tokens": 66,
    }
    with pytest.raises(RoleContractError, match="pinned role executor"):
        executor.dispatch({"role_binding": {**binding, "model": "fallback-model"}}, object())


def test_manifest_pin_is_idempotent_and_refuses_replacement_or_resume_mismatch(tmp_path):
    pinned = registry(role())
    manifest = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    authority = TeamManifestAuthority(tmp_path / "registry.sqlite3")
    try:
        authority.pin(manifest)
        authority.pin(manifest)
    finally:
        authority.close()
    resumed = TeamManifestAuthority(tmp_path / "registry.sqlite3")
    try:
        assert resumed.load("campaign-1", spec_hash="b" * 64,
                            registry_digest=pinned.digest()).manifest_digest == manifest.manifest_digest
        replacement = TeamFactoryV1.build(
            pinned,
            TeamRequestV1(**{
                **request(desired=2).model_dump(),
                "available_agent_calls": 100,
                "available_model_tokens": 10000,
            }),
            expected_registry_digest=pinned.digest(),
        )
        with pytest.raises(RoleContractError, match="different pinned"):
            resumed.pin(replacement)
        with pytest.raises(RoleContractError, match="does not match"):
            resumed.load("campaign-1", spec_hash="c" * 64,
                         registry_digest=pinned.digest())
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            resumed.conn.execute(
                "UPDATE team_role_call_allocations SET calls_limit=99 WHERE campaign_id='campaign-1'",
            )
    finally:
        resumed.close()


def test_durable_queue_counts_role_instance_allocations_and_replays_once(tmp_path):
    pinned = registry(role(calls=2, tokens=4))
    manifest = TeamFactoryV1.build(pinned, request(), expected_registry_digest=pinned.digest())
    member = manifest.members[0]
    context_digest = hashlib.sha256(b"context").hexdigest()
    binding = manifest.job_binding(member.instance_id, action_id="job-one", context_digest=context_digest)
    db_path = tmp_path / "registry.sqlite3"
    ledger = BudgetLedger(db_path)
    ledger.create_root("campaign-1", {"agent_calls": 10})
    ledger.close()
    authority = TeamManifestAuthority(db_path)
    authority.pin(manifest)
    authority.close()
    queue = DurableWorkerQueue(db_path)
    allocation = {
        "manifest_digest": manifest.manifest_digest,
        "role_instance_id": member.instance_id,
        "role_id": member.role.role_id,
        "calls_limit": member.role.calls_per_instance,
        "tokens_limit": member.role.tokens_per_instance,
        "token_upper_bound": member.role.tokens_per_instance // member.role.calls_per_instance,
    }
    request_body = {"role_binding": binding}
    try:
        first = queue.enqueue(job_id="role-job-one", enqueue_key="role-job-one", campaign_id="campaign-1",
                              action_id="job-one", resource="agent_calls", upper_bound=1,
                              request=request_body, role_allocation=allocation)
        replay = queue.enqueue(job_id="role-job-one", enqueue_key="role-job-one", campaign_id="campaign-1",
                               action_id="job-one", resource="agent_calls", upper_bound=1,
                               request=request_body, role_allocation=allocation)
        assert replay["request_hash"] == first["request_hash"]
        next_binding = manifest.job_binding(member.instance_id, action_id="job-two",
                                            context_digest=hashlib.sha256(b"next context").hexdigest())
        second = queue.enqueue(job_id="role-job-two", enqueue_key="role-job-two", campaign_id="campaign-1",
                               action_id="job-two", resource="agent_calls", upper_bound=1,
                               request={"role_binding": next_binding}, role_allocation=allocation)
        assert second["state"] == "READY"
        assert queue.cancel("role-job-one", reason="cancelled before dispatch") == "CANCELLED"
        third_binding = manifest.job_binding(member.instance_id, action_id="job-three",
                                             context_digest=hashlib.sha256(b"third context").hexdigest())
        third = queue.enqueue(job_id="role-job-three", enqueue_key="role-job-three", campaign_id="campaign-1",
                              action_id="job-three", resource="agent_calls", upper_bound=1,
                              request={"role_binding": third_binding}, role_allocation=allocation)
        assert third["state"] == "READY"
        fourth_binding = manifest.job_binding(member.instance_id, action_id="job-four",
                                              context_digest=hashlib.sha256(b"fourth context").hexdigest())
        with pytest.raises(WorkerBudgetError, match="role instance"):
            queue.enqueue(job_id="role-job-four", enqueue_key="role-job-four", campaign_id="campaign-1",
                          action_id="job-four", resource="agent_calls", upper_bound=1,
                          request={"role_binding": fourth_binding}, role_allocation=allocation)
    finally:
        queue.close()


def test_registry_loader_checks_explicit_digest_and_rejects_unknown_file_type(tmp_path):
    pinned = registry(role())
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(pinned.model_dump(mode="json")), encoding="utf-8")
    assert RoleRegistryV1.load(path, expected_digest=pinned.digest()).digest() == pinned.digest()
    with pytest.raises(RoleContractError, match="registry digest"):
        RoleRegistryV1.load(path, expected_digest="c" * 64)
    invalid_path = tmp_path / "registry.txt"
    invalid_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RoleContractError, match=".json or .toml"):
        RoleRegistryV1.load(invalid_path)
