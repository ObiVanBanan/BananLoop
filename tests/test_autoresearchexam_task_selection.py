import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "benchmarks" / "autoresearchexam" / "task-selection.json"


def test_autoresearchexam_shortlist_is_ten_pinned_v11_cpu_tasks():
    data = json.loads(SELECTION.read_text(encoding="utf-8"))
    tasks = data["tasks"]
    assert data["benchmark"] == "AutoResearchExam-v1.1"
    assert data["source_revision"] == "23c0d92e11af1dea821f972b5f0a739060958002"
    assert data["agent_time_limit_seconds"] == 3600
    assert data["iterations_limit"] == 5
    assert data["concurrency"] == 1
    assert len(tasks) == 10
    assert len({task["task_id"] for task in tasks}) == 10
    assert all(task["version"] == "1.1.0" for task in tasks)
    assert all(task["resources"]["gpus"] == 0 for task in tasks)
    assert all(task["resources"]["cpus"] == 8 for task in tasks)
    assert all(task["resources"]["memory_mb"] == 14336 for task in tasks)
    assert all(task["resources"]["storage_mb"] == 153600 for task in tasks)
