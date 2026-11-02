from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bananloop.contracts import (
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
