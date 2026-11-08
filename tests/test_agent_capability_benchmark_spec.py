"""Offline-only compatibility checks for the PR #125 -> PR #135 benchmark adaptation.

No provider, task assignment, evaluator or scorer is invoked.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bananloop.roles import RoleDefinitionV1, RoleRegistryV1, TeamManifestV1

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "agent_capability_v0.2"
ACTIVE = {"researcher", "optimizer", "independent_verifier", "integration_verifier"}


def _catalog_role_ids() -> set[str]:
    text = (BENCH / "role-catalog.yaml").read_text(encoding="utf-8")
    assert 'status: "DESIGN_ONLY_NOT_RUN"' in text
    active_section = text.split("\nroles:\n", 1)[1].split("\nlegacy_capabilities:\n", 1)[0]
    role_ids = re.findall(r"^  - role_id: ([a-z][a-z0-9_]{0,63})$", active_section, re.MULTILINE)
    assert len(role_ids) == len(set(role_ids)), "duplicate catalog role ID"
    return set(role_ids)


def test_catalog_matches_exercised_integrated_roles() -> None:
    assert _catalog_role_ids() == ACTIVE
    integration = (ROOT / "tests" / "test_rpdi13_integrated_acceptance.py").read_text(encoding="utf-8")
    researcher_runtime = (ROOT / "src" / "bananloop" / "v2_research_runtime.py").read_text(encoding="utf-8")
    optimizer_runtime = (ROOT / "src" / "bananloop" / "v2_optimization_runtime.py").read_text(encoding="utf-8")
    for role in ("optimizer", "independent_verifier", "integration_verifier"):
        assert f'"{role}"' in integration
    assert '"researcher"' in researcher_runtime
    assert '"optimizer"' in optimizer_runtime


def test_catalog_is_not_a_runtime_global_registry() -> None:
    assert "role_id" in RoleDefinitionV1.model_fields
    assert "prompt_digest" in RoleDefinitionV1.model_fields
    assert "model_version" in RoleDefinitionV1.model_fields
    assert "capability_policy" in RoleDefinitionV1.model_fields
    assert "version" in RoleRegistryV1.model_fields
    assert "registry_digest" in TeamManifestV1.model_fields
    assert "manifest_digest" in TeamManifestV1.model_fields
    assert "role_version" not in RoleDefinitionV1.model_fields


@pytest.mark.parametrize("name", ["case", "result"])
def test_public_schema_is_parseable_and_fail_closed(name: str) -> None:
    schema = json.loads((BENCH / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["required"]


def test_result_contract_requires_immutable_role_definition_digest() -> None:
    schema = json.loads((BENCH / "schemas" / "result.schema.json").read_text(encoding="utf-8"))
    role = schema["properties"]["role_identity"]
    assert "role_definition_sha256" in role["required"]
    assert role["properties"]["role_definition_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert role["properties"]["role_version"]["type"] == ["string", "null"]


def test_design_documents_do_not_authorize_scored_runs() -> None:
    readme = (BENCH / "README.md").read_text(encoding="utf-8")
    pilot = (BENCH / "pilot-plan.md").read_text(encoding="utf-8")
    assert "No independent scored benchmark has been run" in readme
    assert "No paid runs are authorized" in readme
    assert "not a launch approval" in pilot
