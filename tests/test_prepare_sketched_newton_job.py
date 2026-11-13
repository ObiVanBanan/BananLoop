"""No-provider checks for the single-task Sketched Newton Harbor handoff."""
from pathlib import Path

import pytest

from benchmarks.autoresearchexam.prepare_sketched_newton_job import (
    PreparationError,
    build_configs,
)
from benchmarks.autoresearchexam.sketched_newton_runner import (
    CANDIDATE_PATH,
    PUBLIC_OUTPUT_PATH,
    TASK_ID,
)


@pytest.mark.parametrize("iterations", [1, 5])
def test_newton_job_configs_bind_existing_child_without_replay(tmp_path: Path, iterations: int):
    state = tmp_path / "state"
    child = "c-" + "a" * 32
    output = tmp_path / "prepared"
    bl, harbor = build_configs(
        repo=tmp_path / "repo",
        state_dir=state,
        campaign_id=child,
        role_registry=tmp_path / "registry.json",
        source_task=tmp_path / TASK_ID,
        mcp_contract=tmp_path / "contract.json",
        overlay=tmp_path / "safe-overlay",
        jobs_dir=tmp_path / "harbor-jobs",
        output_dir=output,
        instruction_sha256="b" * 64,
        contract_sha256="c" * 64,
        candidate_iterations=iterations,
    )
    assert bl["candidate_path"] == CANDIDATE_PATH
    assert bl["public_output_path"] == PUBLIC_OUTPUT_PATH
    assert bl["visible_path"] == bl["write_path"] == [CANDIDATE_PATH]
    assert bl["adapter_id"] == "sketched-newton-public-v1"
    assert bl["repeatability_runs"] == 1
    assert bl["model_call_limit"] == 2 * iterations
    assert bl["max_steps"] >= 3 * iterations
    assert bl["max_candidate_iterations"] == iterations
    assert bl["time_limit_seconds"] == 3600
    assert Path(bl["public_submission_ledger_path"]).is_relative_to(state)
    assert child in bl["public_submission_ledger_path"]
    assert harbor["n_attempts"] == harbor["n_concurrent_trials"] == 1
    assert harbor["retry"]["max_retries"] == 0
    assert harbor["tasks"] == [{"path": str(tmp_path / "safe-overlay")}]
    assert harbor["agents"][0]["kwargs"]["config_path"] == str(output / "bananloop.json")
    assert harbor["agents"][0]["override_timeout_sec"] == 3600
    assert "verify" not in str(harbor)
    assert not output.exists()


@pytest.mark.parametrize("iterations", [0, 6, True])
def test_newton_job_configs_refuse_invalid_iteration_count(tmp_path: Path, iterations):
    with pytest.raises(PreparationError, match="1..5"):
        build_configs(
            repo=tmp_path, state_dir=tmp_path,
            campaign_id="c-" + "a" * 32,
            role_registry=tmp_path / "roles.json",
            source_task=tmp_path / TASK_ID,
            mcp_contract=tmp_path / "mcp.json",
            overlay=tmp_path / "safe",
            jobs_dir=tmp_path / "harbor-jobs",
            output_dir=tmp_path / "new",
            instruction_sha256="b" * 64,
            contract_sha256="c" * 64,
            candidate_iterations=iterations,
        )
