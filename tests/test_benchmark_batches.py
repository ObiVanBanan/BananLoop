"""Public-only batch staging/merging must never open or export hidden oracles."""
from pathlib import Path

import pytest

from bananloop.agent_benchmark import BenchmarkError, _write, read_json
from bananloop.benchmark_batches import stage_batches, merge_recorded
from bananloop.benchmark_sealed import seal_suite, score_sealed
from test_live_sealed_benchmark import _author_from_demo


def _sealed(tmp_path):
    source, _ = _author_from_demo(tmp_path / "seed")
    public, private = tmp_path / "public", tmp_path / "private"
    seal_suite(authored_suite_path=source, registry_path=source.parent / "registry.json",
               team_path=source.parent / "team.json", public_dir=public, private_dir=private)
    return public, private


def test_stage_public_batches_and_score_missing_as_incomplete(tmp_path):
    public, private = _sealed(tmp_path)
    count = stage_batches(public_dir=public, output_dir=tmp_path / "batches", batch_size=3)
    assert count["batch_count"] == 3
    stage = read_json(tmp_path / "batches" / "batches.json")
    assert sum(len(batch["case_ids"]) for batch in stage["batches"]) == 8
    assert "expected_output" not in "\n".join(
        (tmp_path / "batches" / batch["path"]).read_text() for batch in stage["batches"])
    # Simulate only the first batch completing; the remaining five are not silently dropped.
    first = stage["batches"][0]
    first_case_id = first["case_ids"][0]
    run = tmp_path / "run-000"
    run.mkdir()
    _write(run / "run-status.json", {
        "suite_sha256": first["suite_sha256"], "completed_cases": 1,
        "status": "STOPPED_UNKNOWN_USAGE_OR_INVALID_OUTPUT",
    })
    _write(run / "responses.json", {
        "schema_version": 1, "responses": {first_case_id: {"selected": "alternative"}}})
    report = merge_recorded(public_dir=public, batches_dir=tmp_path / "batches",
                            run_dirs=[str(run)], output_dir=tmp_path / "merged")
    assert report["completed_cases"] == 1
    assert report["missing_cases"] == 7
    scorer = score_sealed(public_dir=public, private_dir=private,
                          responses_path=tmp_path / "merged" / "responses.json",
                          output_dir=tmp_path / "scores")
    assert sum(x["total"] for x in scorer["splits"].values()) == 8
    assert sum(x["completed"] for x in scorer["splits"].values()) == 1
    statuses = read_json(tmp_path / "scores" / "results.json")["results"]
    assert sum(case["status"] == "INCOMPLETE" for case in statuses) == 7


def test_refuses_unknown_or_repeated_model_run_batch(tmp_path):
    public, _ = _sealed(tmp_path)
    stage_batches(public_dir=public, output_dir=tmp_path / "batches", batch_size=2)
    root = tmp_path / "run"
    root.mkdir()
    _write(root / "run-status.json", {"suite_sha256": "a" * 64, "completed_cases": 0})
    _write(root / "responses.json", {"schema_version": 1, "responses": {}})
    with pytest.raises(BenchmarkError, match="unknown or repeated"):
        merge_recorded(public_dir=public, batches_dir=tmp_path / "batches",
                       run_dirs=[str(root)], output_dir=tmp_path / "merged")
