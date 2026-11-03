from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from bananloop.contracts import (
    AutonomyScopeV2,
    AutonomySpecV2,
    CampaignSpec,
    ExplorationCampaignSpecV2,
    OptimizationCampaignSpecV2,
    V2_FULL_FEATURES,
    parse_campaign_spec_document,
    parse_legacy_campaign_spec,
)
from bananloop.store import CampaignStore, StoreError


SHA = "a" * 64


def exploration_spec(**updates):
    spec = {
        "schema_version": 2,
        "mode": "EXPLORATION",
        "goal": "Map failure mechanisms before selecting an optimization metric",
        "source_repo": "https://example.invalid/research/project",
        "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": SHA, "scope_version": 1},
        "budget": {"max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
                   "max_evaluator_calls": 10, "deadline_seconds": 3600,
                   "plateau_experiments": 3},
        "role_registry_digest": SHA,
        "evaluation": None,
        "parent": None,
    }
    spec.update(updates)
    return spec


def optimization_spec(**updates):
    spec = exploration_spec(
        mode="OPTIMIZATION",
        evaluation={"protocol_id": "benchmark-quality", "version": 2, "content_sha256": SHA},
    )
    spec.update(updates)
    return spec


def test_v2_parser_dispatches_to_strict_mode_specific_models():
    exploration = parse_campaign_spec_document(exploration_spec())
    optimization = parse_campaign_spec_document(optimization_spec())

    assert isinstance(exploration, ExplorationCampaignSpecV2)
    assert exploration.evaluation is None
    assert "reward" not in exploration.model_dump(mode="json")
    assert isinstance(optimization, OptimizationCampaignSpecV2)
    assert optimization.evaluation.protocol_id == "benchmark-quality"
    assert optimization.evaluation.version == 2


@pytest.mark.parametrize("payload", [
    {**exploration_spec(), "schema_version": 3},
    {**exploration_spec(), "schema_version": True},
    {**exploration_spec(), "mode": "METRIC_DISCOVERY"},
    {**exploration_spec(), "unexpected": "must fail closed"},
    {**exploration_spec(), "evaluation": {"protocol_id": "not-allowed-in-exploration",
                                           "version": 1, "content_sha256": SHA}},
    {key: value for key, value in optimization_spec().items() if key != "evaluation"},
    {**optimization_spec(), "evaluation": {"protocol_id": "p", "version": 1,
                                            "content_sha256": "not-a-digest"}},
])
def test_v2_parser_rejects_unknown_versions_fields_and_mixed_modes(payload):
    with pytest.raises((ValidationError, ValueError)):
        parse_campaign_spec_document(payload)


@pytest.mark.parametrize("field,value", [
    ("mode", "EXPLORATION"),
    ("autonomy", {}),
    ("feature_flags", {}),
    ("evaluation", None),
    ("parent", None),
    ("role_registry_digest", SHA),
])
def test_missing_schema_version_rejects_v2_fields_in_otherwise_valid_v1_document(field, value):
    legacy = {
        "goal": "test",
        "source_repo": "repo",
        "base_commit": "abc",
        "evaluation_command": ["python", "evaluator.py"],
        "primary_metric": {"name": "quality", "direction": "maximize",
                           "target": 1.0, "min_improvement": 0.1},
    }
    ambiguous = {**legacy, field: value}

    with pytest.raises(ValueError, match="require an explicit schema_version=2"):
        parse_campaign_spec_document(ambiguous)


def test_v2_specs_are_frozen_and_approved_hash_is_canonical():
    spec = parse_campaign_spec_document(exploration_spec())

    assert spec.approved_hash() == parse_campaign_spec_document(exploration_spec()).approved_hash()
    assert spec.feature_flags.profile == "FULL"
    assert spec.feature_flags.enabled_features == V2_FULL_FEATURES
    assert {"budget_accounting", "durable_worker_fencing", "node_phase_scheduler"} <= set(
        spec.feature_flags.enabled_features
    )
    assert len(spec.feature_flags.flag_hash()) == 64
    with pytest.raises(ValidationError):
        spec.goal = "mutated"
    with pytest.raises(ValidationError):
        spec.budget.max_attempts = 100


def test_adding_optional_verifier_receipt_pin_preserves_old_hash_when_omitted():
    legacy_shape = optimization_spec()
    explicit_absent = optimization_spec(evaluation={
        "protocol_id": "benchmark-quality", "version": 2, "content_sha256": SHA,
        "approval_digest": None,
    })
    old_ref_hash = parse_campaign_spec_document(legacy_shape).approved_hash()
    assert parse_campaign_spec_document(explicit_absent).approved_hash() == old_ref_hash
    approved = optimization_spec(evaluation={
        "protocol_id": "benchmark-quality", "version": 2, "content_sha256": SHA,
        "approval_digest": "b" * 64,
    })
    assert parse_campaign_spec_document(approved).approved_hash() != old_ref_hash


def test_optional_capability_policy_preserves_legacy_v2_hash_and_binds_explicit_policy():
    legacy = parse_campaign_spec_document(exploration_spec())
    payload = legacy.model_dump(mode="json")
    payload["autonomy"].pop("capability_policy", None)
    payload["autonomy"].pop("scope", None)
    old_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert legacy.approved_hash() == old_hash

    explicit = parse_campaign_spec_document(exploration_spec(autonomy={
        "mode": "SUPERVISED", "scope_digest": SHA, "scope_version": 1,
        "capability_policy": {"policy_version": 1, "tools": ["read_file"],
                              "read_paths": ["src/**"], "backend": "ISOLATED"},
    }))
    assert explicit.approved_hash() != legacy.approved_hash()
    with pytest.raises(ValidationError):
        parse_campaign_spec_document(exploration_spec(autonomy={
            "mode": "SUPERVISED", "scope_digest": SHA, "scope_version": 1,
            "capability_policy": {"read_paths": ["../private/**"]},
        }))
def test_versioned_autonomy_scope_authorizes_only_bounded_full_auto_pivots():
    scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/optimizer", "tests/optimizer"),
        max_child_depth=2, max_child_campaigns=3,
    )
    full_auto = AutonomySpecV2(
        mode="FULL_AUTO", scope_digest=scope.content_digest(),
        scope_version=scope.version, scope=scope,
    )

    assert full_auto.authorize_pivot(["src/optimizer/scorer.py"]) == "AUTO_ALLOWED"
    assert full_auto.authorize_pivot(
        ["src/optimizer/scorer.py"], current_child_depth=1, current_child_count=2,
    ) == "AUTO_ALLOWED"
    assert full_auto.authorize_pivot(["docs/release.md"]) == "DENIED"
    assert full_auto.authorize_pivot(["../private/secret.py"]) == "DENIED"
    assert full_auto.authorize_pivot(
        ["src/optimizer/scorer.py"], current_child_depth=2,
    ) == "DENIED"
    assert full_auto.authorize_pivot(
        ["src/optimizer/scorer.py"], current_child_count=3,
    ) == "DENIED"
    legacy_full_auto = AutonomySpecV2(
        mode="FULL_AUTO", scope_digest=SHA, scope_version=1,
    )
    assert legacy_full_auto.authorize_pivot(["src/optimizer/scorer.py"]) == "DENIED"


@pytest.mark.parametrize("path", [
    ".git/config", ".bananloop_eval/cache", "dataset/holdout", "src/evaluator.py",
    "src/validation/cases", "config/secrets",
])
def test_autonomy_scope_rejects_protected_or_secret_paths(path):
    with pytest.raises(ValidationError, match="protected evaluator"):
        AutonomyScopeV2(version=1, allowed_paths=(path,))


def test_supervised_pivots_still_require_explicit_operator_approval():
    supervised = AutonomySpecV2(
        mode="SUPERVISED", scope_digest=SHA, scope_version=1,
    )
    assert supervised.authorize_pivot(["src/optimizer/scorer.py"]) == "APPROVAL_REQUIRED"
    assert supervised.authorize_pivot(["outside/scope.py"]) == "APPROVAL_REQUIRED"


def test_child_autonomy_scope_is_inherited_and_cannot_expand_parent_bounds():
    parent_scope = AutonomyScopeV2(
        version=1, allowed_paths=("src/optimizer", "tests/optimizer"),
        max_child_depth=3, max_child_campaigns=4,
    )
    parent = AutonomySpecV2(
        mode="FULL_AUTO", scope_digest=parent_scope.content_digest(),
        scope_version=1, scope=parent_scope,
    )
    child_scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/optimizer/model",),
        max_child_depth=2, max_child_campaigns=3,
    )
    child = AutonomySpecV2(
        mode="FULL_AUTO", scope_digest=child_scope.content_digest(),
        scope_version=2, scope=child_scope,
    )
    broader_scope = AutonomyScopeV2(
        version=2, allowed_paths=("src",),
        max_child_depth=2, max_child_campaigns=3,
    )
    broader_child = AutonomySpecV2(
        mode="FULL_AUTO", scope_digest=broader_scope.content_digest(),
        scope_version=2, scope=broader_scope,
    )
    assert child.is_narrower_child_of(parent)
    assert not broader_child.is_narrower_child_of(parent)

    supervised_parent = AutonomySpecV2(
        mode="SUPERVISED", scope_digest=parent_scope.content_digest(),
        scope_version=1, scope=parent_scope,
    )
    assert not child.is_narrower_child_of(supervised_parent)


def test_materialized_scope_is_digest_pinned_and_included_in_approved_hash():
    legacy = parse_campaign_spec_document(exploration_spec())
    scope = AutonomyScopeV2(
        version=2, allowed_paths=("src/optimizer",),
        max_child_depth=1, max_child_campaigns=2,
    )
    scoped_payload = exploration_spec(autonomy={
        "mode": "FULL_AUTO", "scope_digest": scope.content_digest(),
        "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
    })
    scoped = parse_campaign_spec_document(scoped_payload)
    assert scoped.autonomy.scope == scope
    assert scoped.approved_hash() != legacy.approved_hash()

    mismatched = exploration_spec(autonomy={
        "mode": "FULL_AUTO", "scope_digest": SHA,
        "scope_version": scope.version, "scope": scope.model_dump(mode="json"),
    })
    with pytest.raises(ValidationError, match="digest"):
        parse_campaign_spec_document(mismatched)


def test_feature_flag_profile_is_frozen_into_campaign_approved_hash():
    full = parse_campaign_spec_document(exploration_spec())
    diagnostic_payload = exploration_spec(
        feature_flags={"version": 1, "profile": "DIAGNOSTIC",
                       "enabled_features": ("exploration", "research_intelligence")}
    )
    diagnostic = parse_campaign_spec_document(diagnostic_payload)

    assert diagnostic.feature_flags.flag_hash() != full.feature_flags.flag_hash()
    assert diagnostic.approved_hash() != full.approved_hash()
    with pytest.raises(ValidationError, match="FULL profile must enable every v2 mechanism"):
        parse_campaign_spec_document(exploration_spec(
            feature_flags={"profile": "FULL", "enabled_features": ("exploration",)}
        ))


def test_pivot_child_parent_reference_binds_complete_lineage_without_changing_old_hash():
    legacy = parse_campaign_spec_document(exploration_spec(parent={
        "parent_campaign_id": "campaign-parent", "allocation_id": "allocation-1",
    }))
    payload = legacy.model_dump(mode="json")
    payload["autonomy"].pop("capability_policy", None)
    payload["autonomy"].pop("scope", None)
    payload["parent"].pop("parent_spec_hash", None)
    payload["parent"].pop("pivot_proposal_id", None)
    payload["parent"].pop("pivot_proposal_digest", None)
    payload["parent"].pop("pivot_decision_id", None)
    payload["parent"].pop("approval_request_id", None)
    expected = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    assert legacy.approved_hash() == expected

    linked = parse_campaign_spec_document(exploration_spec(parent={
        "parent_campaign_id": "campaign-parent", "allocation_id": "allocation-1",
        "parent_spec_hash": SHA, "pivot_proposal_id": "pivot-" + "b" * 24,
        "pivot_proposal_digest": "c" * 64, "pivot_decision_id": "approval-1",
        "approval_request_id": "approval-1",
    }))
    assert linked.parent.parent_spec_hash == SHA
    assert linked.approved_hash() != legacy.approved_hash()
    with pytest.raises(ValidationError, match="supplied together"):
        parse_campaign_spec_document(exploration_spec(parent={
            "parent_campaign_id": "campaign-parent", "allocation_id": "allocation-1",
            "parent_spec_hash": SHA,
        }))


def test_v1_dispatch_keeps_legacy_model_and_hash_behavior():
    legacy = {
        "schema_version": 1,
        "goal": "test",
        "source_repo": "repo",
        "base_commit": "abc",
        "evaluation_command": ["python", "evaluator.py"],
        "primary_metric": {"name": "quality", "direction": "maximize",
                           "target": 1.0, "min_improvement": 0.1},
    }
    direct = CampaignSpec.model_validate(legacy)
    dispatched = parse_campaign_spec_document(json.dumps(legacy))

    assert isinstance(dispatched, CampaignSpec)
    assert dispatched.model_dump_json() == direct.model_dump_json()
    assert dispatched.approved_hash() == direct.approved_hash()
    with pytest.raises(ValueError, match="requires the v2 campaign adapter"):
        parse_legacy_campaign_spec(exploration_spec())


def test_legacy_campaign_store_fails_closed_on_v2_spec(tmp_path):
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    try:
        v2 = parse_campaign_spec_document(exploration_spec())
        with pytest.raises(StoreError, match="only accepts schema_version=1"):
            store.initialize(v2)
        assert store.conn.execute("SELECT 1 FROM campaigns WHERE id=1").fetchone() is None

        now = "2026-11-02T00:00:00+00:00"
        payload = json.dumps(exploration_spec(), sort_keys=True, separators=(",", ":"))
        store.conn.execute(
            "INSERT INTO campaigns(id,spec_json,spec_hash,status,created_at,updated_at) VALUES(1,?,?,?, ?,?)",
            (payload, "ignored-until-v5-adapter", "READY", now, now),
        )
        with pytest.raises(StoreError, match="schema_version=2 requires the v2 campaign adapter"):
            store.spec()
    finally:
        store.close()


def test_populated_v4_database_reads_legacy_evaluator_binding_without_rewrite(tmp_path):
    path = tmp_path / "historical-v4.sqlite3"
    binding_json = '{"command":["python","evaluator.py"],"metric":"quality"}'
    store = CampaignStore(path)
    try:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        legacy_spec = CampaignSpec.model_validate({
            "schema_version": 1, "goal": "preserve legacy evaluator binding",
            "source_repo": "repo", "base_commit": "abc",
            "evaluation_command": ["python", "evaluator.py"],
            "primary_metric": {"name": "quality", "direction": "maximize",
                               "target": 1.0, "min_improvement": 0.1},
        })
        store.initialize(legacy_spec)
        legacy_hash = store.spec().approved_hash()
        store.save_evaluator_binding(
            "legacy-binding-v1",
            hashlib.sha256(binding_json.encode()).hexdigest(),
            binding_json,
        )
        before = tuple(store.conn.execute(
            "SELECT id,binding_id,binding_hash,binding_json FROM evaluator_bindings WHERE id=1"
        ).fetchone())
    finally:
        store.close()

    # Reopen the populated v4 database through the current store reader. The
    # legacy singleton is read as-is; v2 protocol onboarding does not rewrite it.
    reopened = CampaignStore(path)
    try:
        after = tuple(reopened.conn.execute(
            "SELECT id,binding_id,binding_hash,binding_json FROM evaluator_bindings WHERE id=1"
        ).fetchone())
        assert reopened.evaluator_binding()["binding_json"] == binding_json
        assert reopened.spec().approved_hash() == legacy_hash
        assert reopened.campaign()["spec_hash"] == legacy_hash
        assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert after == before
    finally:
        reopened.close()
