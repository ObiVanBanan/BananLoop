from __future__ import annotations

import tomllib

import pytest

from benchmarks.autoresearchexam.prepare_safe_harbor_task import (
    OverlayError,
    local_public_dev_compose,
    prepare_local_public_dev_task_toml,
    strip_grader_collection,
)


SOURCE_TASK_TOML = '''\
artifacts = [
    { source = "/var/lib/are", service = "grader" },
]

[task]
name = "bespokelabs/budgeted-imputation-mcar50"
version = "1.1.0"

[verifier]
timeout_sec = 600.0
environment_mode = "separate"

[[verifier.collect]]
service = "grader"
command = "/opt/are/venv/bin/python /opt/are/drain.py"
timeout_sec = 3600.0

[verifier.environment]
network_mode = "no-network"
'''


def test_safe_overlay_removes_only_pinned_artifact_and_collect_hook():
    transformed, record = strip_grader_collection(SOURCE_TASK_TOML)
    config = tomllib.loads(transformed)

    assert "artifacts" not in config
    assert config["verifier"].get("collect") is None
    assert config["verifier"]["environment"]["network_mode"] == "no-network"
    assert config["task"]["version"] == "1.1.0"
    assert record["verification_must_remain_disabled"] is True
    assert record["removed_artifacts"] == [{"source": "/var/lib/are", "service": "grader"}]


def test_safe_overlay_accepts_only_task_specific_reviewed_collect_timeout():
    transformed, record = strip_grader_collection(
        SOURCE_TASK_TOML.replace("3600.0", "4000.0"), expected_timeout_sec=4000.0,
    )
    assert "verifier.collect" not in transformed
    assert record["removed_verifier_collect"][0]["timeout_sec"] == 4000.0
    with pytest.raises(OverlayError):
        strip_grader_collection(
            SOURCE_TASK_TOML.replace("3600.0", "4000.0"), expected_timeout_sec=3600.0,
        )


@pytest.mark.parametrize(
    "text",
    [
        SOURCE_TASK_TOML.replace("/var/lib/are", "/tmp/other"),
        SOURCE_TASK_TOML.replace("/opt/are/drain.py", "/tmp/custom-drain.py"),
        SOURCE_TASK_TOML.replace("[verifier.environment]", "[verifier.agent]"),
    ],
)
def test_safe_overlay_fails_closed_for_changed_artifacts_or_unhandled_layout(text):
    with pytest.raises(OverlayError):
        strip_grader_collection(text)


def test_local_public_dev_task_toml_removes_grader_mcp_and_host():
    source = SOURCE_TASK_TOML + '''\
[agent]
allowed_hosts = [
    "api.openai.com",
    "grader",
]

[environment]
mcp_servers = [
    { name = "are", transport = "streamable-http", url = "http://grader:8000/mcp" },
]
'''
    transformed, report = prepare_local_public_dev_task_toml(
        source, expected_timeout_sec=3600.0,
    )
    parsed = tomllib.loads(transformed)
    assert "artifacts" not in parsed
    assert parsed["verifier"].get("collect") is None
    assert "mcp_servers" not in parsed["environment"]
    assert "grader" not in parsed["agent"]["allowed_hosts"]
    assert report["private_grader_enabled"] is False


def test_local_public_dev_compose_keeps_only_main_and_deliverable_volume():
    compose = '''\
services:
  main:
    depends_on:
      grader:
        condition: service_healthy
    volumes:
      - are-deliverable-0:/app/output
  grader:
    build:
      context: ./are-grader-ctx
      dockerfile: Dockerfile.are
  test-grader:
    build:
      context: ./are-grader-ctx
      dockerfile: Dockerfile.are
volumes:
  are-deliverable-0: {}
  are-secret: {}
networks:
  are-private:
    internal: true
'''
    transformed = local_public_dev_compose(compose, expected_output="/app/output")
    assert "main:" in transformed
    assert "are-deliverable-0:/app/output" in transformed
    assert "grader" not in transformed
    assert "are-grader-ctx" not in transformed
    assert "are-secret" not in transformed
