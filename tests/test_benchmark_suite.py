"""No-provider regression tests for the PR #141 benchmark orchestration CLI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bananloop.agent_benchmark import BenchmarkError, prepare_demo
from bananloop.benchmark_suite import prepare_roles, score_roles, stage_discoverybench


@pytest.fixture
def prepared(tmp_path):
    demo = tmp_path / "demo"
    prepare_demo(demo)
    private = tmp_path / "operator-private"
    public = tmp_path / "worker-public"
    batches = tmp_path / "worker-batches"
    result = prepare_roles(
        registry_path=str(demo / "registry.json"),
        team_path=str(demo / "team.json"),
        private_root=str(private),
        public_root=str(public),
        batches_root=str(batches))
    return tmp_path, private, public, batches, result


def test_prepare_uses_all_four_pinned_roles_and_seals_40_cases(prepared):
    _, private, public, batches, result = prepared
    assert result["status"] == "PREPARED_NO_MODEL_CALLS"
    assert result["cases"] == 40
    assert result["batch_count"] == 20
    assert result["roles"] == {
        "researcher": 10, "optimizer": 10,
        "independent_verifier": 10, "integration_verifier": 10,
    }
    assert result["splits"] == {"DEV": 8, "HOLDOUT": 24, "VALIDATION": 8}
    assert result["release_eligible"] is False
    assert (private / "authored.json").exists()
    assert (private / "oracle" / "oracle.json").exists()
    assert (private / "oracle" / "nonce.json").exists()
    assert (batches / "batch-000.json").exists()
    source = json.loads((public / "cases.json").read_text())
    assert len(source["cases"]) == 40
    assert all("oracle" not in case for case in source["cases"])
    assert not (public / "oracle.json").exists()
    assert not (public / "nonce.json").exists()
    public_plan = (public / "diagnostic-plan.json").read_text()
    assert str(private) not in public_plan


def test_output_roots_and_frozen_inputs_reject_overwrite(prepared):
    tmp_path, private, public, batches, _ = prepared
    demo = tmp_path / "demo"
    with pytest.raises(BenchmarkError, match="already exists"):
        prepare_roles(
            registry_path=str(demo / "registry.json"),
            team_path=str(demo / "team.json"),
            private_root=str(private),
            public_root=str(public), batches_root=str(batches))
    with pytest.raises(BenchmarkError, match="non-overlapping"):
        prepare_roles(
            registry_path=str(demo / "registry.json"),
            team_path=str(demo / "team.json"),
            private_root=str(tmp_path / "nested"),
            public_root=str(tmp_path / "nested" / "public"),
            batches_root=str(tmp_path / "other"))


def test_private_scoring_retains_all_missing_cases(prepared):
    tmp_path, private, public, batches, _ = prepared
    manifest = json.loads((batches / "batches.json").read_text())
    first = manifest["batches"][0]
    run = tmp_path / "recorded-public-batch"
    run.mkdir()
    (run / "run-status.json").write_text(json.dumps({
        "schema_version": 1, "suite_sha256": first["suite_sha256"],
        "status": "STOPPED_UNKNOWN_USAGE_OR_INVALID_OUTPUT", "completed_cases": 0,
    }))
    (run / "responses.json").write_text(json.dumps({
        "schema_version": 1, "responses": {},
    }))
    result = score_roles(
        private_root=str(private), public_root=str(public),
        batches_root=str(batches), run_dirs=[str(run)],
        merged_root=str(tmp_path / "merged"),
        score_root=str(tmp_path / "private-score"))
    assert result["total_cases"] == 40
    assert result["completed_cases"] == 0
    assert result["missing_cases"] == 40
    assert all(row["completed"] == 0 for row in result["splits"].values())
    assert all(row["passed"] == 0 for row in result["splits"].values())
    assert result["release_eligible"] is False


def test_discoverybench_stages_identical_public_dev_pair(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "examples" / "exploration_diagnostic_v1" / "discoverybench_boardgames"
    root = tmp_path / "paired"
    result = stage_discoverybench(source_dir=str(source), output_root=str(root))
    assert result["rows"] == 500 and result["columns"] == 42
    assert result["arm_inputs_identical"] is True
    assert result["split"] == "PUBLIC_DEV"
    assert result["release_eligible"] is False
    for filename in ("TASK.md", "data.csv"):
        a = (root / "arms" / "bananloop" / filename).read_bytes()
        b = (root / "arms" / "codex" / filename).read_bytes()
        assert a == b
        assert hashlib.sha256(a).hexdigest() == result["files_sha256"][filename]
    assert not any(root.rglob("oracle.json"))
    assert not any(root.rglob("nonce.json"))
    with pytest.raises(BenchmarkError, match="already exists"):
        stage_discoverybench(source_dir=str(source), output_root=str(root))


def test_discoverybench_rejects_modified_source_schema(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "TASK.md").write_text("diagnostic")
    (source / "data.csv").write_text("a,b\n1,2\n")
    with pytest.raises(BenchmarkError, match="schema"):
        stage_discoverybench(source_dir=str(source), output_root=str(tmp_path / "staged"))
