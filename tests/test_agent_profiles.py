from pathlib import Path

import pytest

from bananloop.agent_profiles import AgentProfileError, load_agent_profiles
from bananloop.contracts import AgentConfig, BudgetSpec, CampaignSpec, Direction, MetricSpec


def test_loads_default_and_per_role_profile(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text(
        '[default]\nkind="codex"\nmodel="gpt-default"\nreasoning_effort="high"\n'
        '[roles.implement]\nmodel="gpt-coder"\n', encoding="utf-8"
    )
    profiles = load_agent_profiles(path)
    config = AgentConfig(**profiles["default"], roles=profiles["roles"])
    assert config.for_role("propose").model == "gpt-default"
    assert config.for_role("propose").reasoning_effort == "high"
    assert config.for_role("implement").model == "gpt-coder"
    assert config.for_role("implement").reasoning_effort == "high"


def test_rejects_unknown_role_and_secret_like_unknown_fields(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text('[roles.reviewer]\nmodel="x"\n', encoding="utf-8")
    with pytest.raises(AgentProfileError, match="unsupported agent roles"):
        load_agent_profiles(path)
    secret = "sk-example-secret-do-not-print"
    path.write_text(f'[default]\napi_key="{secret}"\n', encoding="utf-8")
    with pytest.raises(AgentProfileError, match="invalid agent profile") as error:
        load_agent_profiles(path)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "kind,values",
    [
        ("codex", {"model": "fixed", "extra_args": ["-m", "shadow"]}),
        ("codex", {"model": "fixed", "extra_args": ["--profile", "mutable"]}),
        ("pi", {"provider": "fixed", "extra_args": ["--provider=shadow"]}),
    ],
)
def test_rejects_raw_args_that_shadow_typed_profile(kind, values):
    with pytest.raises(ValueError, match="configured in one place|must not be shadowed"):
        AgentConfig(kind=kind, **values)


def test_empty_profile_fields_preserve_approved_hash():
    spec = CampaignSpec(
        goal="goal", source_repo=".", base_commit="abc", evaluation_command=["eval"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1,
                                  min_improvement=0.1),
        agent=AgentConfig(), budget=BudgetSpec(),
    )
    old_payload = spec.model_dump(mode="json")
    for key in ("reasoning_effort", "provider", "roles"):
        old_payload["agent"].pop(key, None)
    old_spec = CampaignSpec.model_validate(old_payload)
    assert spec.approved_hash() == old_spec.approved_hash()


def test_select_role_uses_independent_model_profile(tmp_path: Path):
    path = tmp_path / "agents.toml"
    path.write_text(
        '[default]\nkind="codex"\nmodel="gpt-default"\n'
        '[roles.select]\nmodel="gpt-selector"\n',
        encoding="utf-8",
    )
    profiles = load_agent_profiles(path)
    config = AgentConfig(**profiles["default"], roles=profiles["roles"])
    assert config.for_role("propose").model == "gpt-default"
    assert config.for_role("select").model == "gpt-selector"
