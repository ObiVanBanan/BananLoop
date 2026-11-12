import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "benchmarks" / "discoverybench_boardgames" / "runner.py"

sys.path.insert(0, str(RUNNER_PATH.parent))
import runner  # noqa: E402


def test_boardgames_pilot_manifest_has_ten_distinct_available_questions():
    manifest = json.loads((RUNNER_PATH.parent / "pilot_manifest.json").read_text())
    tasks = manifest["tasks"]
    assert manifest["qualification"].endswith("not_official_discoverybench_score")
    assert len(tasks) == 10
    assert len({task["task_id"] for task in tasks}) == 10
    assert len({(task["x"], task["y"]) for task in tasks}) == 10
    assert all(task["version"] == "pilot-1.0" for task in tasks)
    assert {method["id"] for method in manifest["registered_analyses"]} == {
        "primary_association", "permutation_test", "split_half_robustness",
        "outlier_sensitivity", "data_quality_audit",
    }
    with (ROOT / manifest["source"]["data_path"]).open(newline="", encoding="utf-8") as stream:
        import csv
        reader = csv.DictReader(stream)
        rows = list(reader)
    assert len(rows) == 500 and len(reader.fieldnames) == 42
    assert all({task["x"], task["y"]} <= set(reader.fieldnames) for task in tasks)


def test_boardgames_runner_is_deterministic_and_pins_source_bytes():
    first = runner.run("bg-01-strategies-tournament-win", bootstrap_resamples=100)
    second = runner.run("bg-01-strategies-tournament-win", bootstrap_resamples=100)
    assert first == second
    assert first["data"]["sample_size"] == 500
    assert first["result"]["ci95_lower"] <= first["result"]["ci95_upper"]
    assert first["qualification"] == "operator_composed_public_diagnostic_not_official_discoverybench_score"


def test_boardgames_runner_rejects_unregistered_task_and_changed_csv(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="unknown preregistered task"):
        runner.run("bg-11-not-registered", bootstrap_resamples=100)
    modified = tmp_path / "changed.csv"
    modified.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV digest"):
        runner.run("bg-01-strategies-tournament-win", csv_path=modified,
                   bootstrap_resamples=100)


def test_boardgames_registered_robustness_analyses_are_reproducible():
    for analysis_id in ("permutation_test", "split_half_robustness",
                        "outlier_sensitivity", "data_quality_audit"):
        first = runner.run("bg-01-strategies-tournament-win", analysis_id=analysis_id,
                           bootstrap_resamples=100)
        second = runner.run("bg-01-strategies-tournament-win", analysis_id=analysis_id,
                            bootstrap_resamples=100)
        assert first == second
        assert first["method"]["analysis_id"] == analysis_id
