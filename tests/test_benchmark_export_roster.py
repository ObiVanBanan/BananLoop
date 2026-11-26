"""Real campaign roster extraction: no model, no guessed roles, no silent DB writes."""
import json
import sqlite3

import pytest

from bananloop.benchmark_export_roster import RosterExportError, export_campaign_roster
from bananloop.contracts import ExplorationCampaignSpecV2, RuntimePaths
from bananloop.registry import CampaignRegistry
from bananloop.roles import RoleRegistryV1, TeamFactoryV1, TeamRequestV1, TeamManifestAuthority
from test_exploration_adapter import _role_registry


def _campaign_with_frozen_registry(tmp_path, *, pin: bool):
    source = tmp_path / "source"
    source.mkdir()
    roles = _role_registry(calls_per_instance=2)
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": "map evidence",
        "source_repo": str(source), "base_commit": "HEAD",
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1,
            "capability_policy": {
                "policy_version": 1,
                "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
                "read_paths": ["README.md"], "write_paths": ["**"], "backend": "LOCAL",
            },
        },
        "budget": {"max_agent_calls": 4, "max_model_tokens": 400},
        "role_registry_digest": roles.digest(), "evaluation": None,
    })
    paths = RuntimePaths.for_repo(source, tmp_path / "managed")
    registry = CampaignRegistry(source, paths=paths)
    entry, state, _created = registry.reserve(spec.goal, "role-export", activate=False)
    with sqlite3.connect(state / "campaign.sqlite3") as conn:
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY, spec_json TEXT NOT NULL, "
            "spec_hash TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO campaigns VALUES(1, ?, ?)", (
            json.dumps(spec.model_dump(mode="json")), spec.approved_hash(),
        ))
    (state / "role-registry.json").write_text(roles.model_dump_json())
    if pin:
        request = TeamRequestV1(
            campaign_id=entry.campaign_id, spec_hash=spec.approved_hash(),
            requested_instances={"researcher": 1},
            available_agent_calls=spec.budget.max_agent_calls,
            available_model_tokens=spec.budget.max_model_tokens,
        )
        manifest = TeamFactoryV1.build(
            roles, request, expected_registry_digest=roles.digest(),
        )
        authority = TeamManifestAuthority(registry.path)
        try:
            authority.pin(manifest)
        finally:
            authority.close()
    return source, paths.root, entry.campaign_id, roles


def test_export_recovers_exact_frozen_registry_and_team_without_db_mutation(tmp_path):
    repo, state, campaign_id, roles = _campaign_with_frozen_registry(tmp_path, pin=True)
    with sqlite3.connect(state / "campaign-registry.sqlite3") as conn:
        initial_rows = conn.execute("SELECT manifest_digest,manifest_json FROM team_manifest_pins").fetchall()
    result = export_campaign_roster(
        repo=repo, campaign_id=campaign_id, state_dir=state,
        output_dir=tmp_path / "export",
    )
    assert result["origin"] == "EXISTING_DURABLE_PIN"
    assert result["live_model_called"] is False
    assert result["registry_sha256"] == roles.digest()
    restored = RoleRegistryV1.load(tmp_path / "export" / "registry.json")
    assert restored.digest() == roles.digest()
    exported_team = json.loads((tmp_path / "export" / "team.json").read_text())
    assert exported_team["manifest_digest"] == result["team_manifest_sha256"]
    assert [member["role"]["role_id"] for member in exported_team["members"]] == ["researcher"]
    with sqlite3.connect(state / "campaign-registry.sqlite3") as conn:
        assert conn.execute("SELECT manifest_digest,manifest_json FROM team_manifest_pins").fetchall() == initial_rows


def test_missing_pin_refused_by_default_with_no_output(tmp_path):
    repo, state, campaign_id, _ = _campaign_with_frozen_registry(tmp_path, pin=False)
    with pytest.raises(RosterExportError, match="NO_DURABLE_TEAM_PIN"):
        export_campaign_roster(
            repo=repo, campaign_id=campaign_id, state_dir=state,
            output_dir=tmp_path / "not-created",
        )
    assert not (tmp_path / "not-created").exists()
    with sqlite3.connect(state / "campaign-registry.sqlite3") as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='team_manifest_pins'"
        ).fetchone() is None


def test_explicit_runtime_composer_pins_only_frozen_campaign_roles(tmp_path):
    repo, state, campaign_id, roles = _campaign_with_frozen_registry(tmp_path, pin=False)
    first = export_campaign_roster(
        repo=repo, campaign_id=campaign_id, state_dir=state,
        output_dir=tmp_path / "first", compose_and_pin_if_missing=True,
    )
    assert first["origin"] == "EXPLICIT_DETERMINISTIC_RUNTIME_PIN_CREATED"
    assert first["role_ids_in_team"] == ["researcher"]
    second = export_campaign_roster(
        repo=repo, campaign_id=campaign_id, state_dir=state,
        output_dir=tmp_path / "second",
    )
    assert second["origin"] == "EXISTING_DURABLE_PIN"
    assert second["team_manifest_sha256"] == first["team_manifest_sha256"]


def test_registry_drift_fails_closed_even_if_team_is_pinned(tmp_path):
    repo, state, campaign_id, roles = _campaign_with_frozen_registry(tmp_path, pin=True)
    path = state / "campaigns" / campaign_id / "role-registry.json"
    bad = json.loads(path.read_text())
    bad["registry_id"] = "different"
    path.write_text(json.dumps(bad))
    with pytest.raises(RosterExportError, match="registry JSON"):
        export_campaign_roster(
            repo=repo, campaign_id=campaign_id, state_dir=state,
            output_dir=tmp_path / "no-export",
        )
    assert not (tmp_path / "no-export").exists()


def test_wrong_campaign_id_and_export_to_checkout_are_rejected(tmp_path):
    repo, state, campaign_id, _ = _campaign_with_frozen_registry(tmp_path, pin=True)
    with pytest.raises(RosterExportError, match="campaign"):
        export_campaign_roster(repo=repo, campaign_id="c-" + "0" * 32,
                               state_dir=state, output_dir=tmp_path / "wrong")
    with pytest.raises(RosterExportError, match="tracked repository"):
        export_campaign_roster(repo=repo, campaign_id=campaign_id,
                               state_dir=state, output_dir=repo / "roster")
