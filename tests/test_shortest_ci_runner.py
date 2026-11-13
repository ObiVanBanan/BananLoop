import json

from benchmarks.autoresearchexam.build_shortest_ci_fixture import (
    VALIDATION_METRIC_SEMANTICS,
    _task_file_hashes,
)
from benchmarks.autoresearchexam.public_shortest_ci_dev import (
    EXPECTED_SETTINGS,
    TASK_ID,
    TASK_VERSION,
)
from benchmarks.autoresearchexam.shortest_ci_runner import (
    AREPublicShortestCIEvaluatorRunner,
)


def test_shortest_ci_protocol_uses_task_raw_lower_is_better_metric():
    runner = AREPublicShortestCIEvaluatorRunner
    assert runner.metric_id == "relative_interval_length"
    assert runner.metric_field == "metric"
    assert runner.validation_metric_semantics == (
        "public_are.validation_score=relative_interval_length.lower_is_better.v1"
    )
    assert VALIDATION_METRIC_SEMANTICS == runner.validation_metric_semantics

    common = {
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "contract_sha256": "a" * 64,
        "dev_image_id": runner.default_dev_image_id,
        "dev_cell_count": EXPECTED_SETTINGS,
    }
    dev = json.loads(runner.aggregate_case_input(tier="dev", **common))
    validation = json.loads(runner.aggregate_case_input(tier="validation", **common))
    for case in (dev, validation):
        assert case["metric_id"] == "relative_interval_length"
        assert case["metric_field"] == "metric"
        assert case["direction"] == "MINIMIZE"
    assert dev["public_metric_direction"] == "MINIMIZE"
    assert "coverage is below 0.88" in dev["measurement"]
    assert "higher-is-better" not in validation["measurement"]


def test_shortest_ci_fixture_pins_task_toml_and_public_docker_inputs():
    import hashlib

    values = {
        "task.toml": b"[task]\nversion = '1.1.0'\n",
        "task-Dockerfile": b"FROM python:3.12\n",
        "task-requirements.txt": b"numpy\nscipy\n",
    }
    actual = _task_file_hashes(
        values["task.toml"],
        {key: value for key, value in values.items() if key != "task.toml"},
    )
    assert actual == {key: hashlib.sha256(value).hexdigest() for key, value in values.items()}
