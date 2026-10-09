from __future__ import annotations

import json
from pathlib import Path

from bananloop.report import build_report, render_markdown
from bananloop.research import attempt_bucket
from test_engine import make_engine


def test_report_attempt_buckets_are_disjoint_and_scientific_count_is_durable(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.run()
        report = build_report(store)
        accounting = report["attempt_accounting"]
        buckets = [
            value
            for key, value in accounting.items()
            if key
            not in {
                "total_attempts",
                "durable_experiment_counter",
                "counter_matches_classification",
            }
        ]

        assert accounting["total_attempts"] == store.campaign()["attempt_count"]
        assert sum(buckets) == accounting["total_attempts"]
        assert accounting["scientific_trials"] == store.campaign()["experiment_count"]
        assert accounting["durable_experiment_counter"] == accounting["scientific_trials"]
        assert accounting["counter_matches_classification"] is True
        assert accounting["scientific_trials"] == 3
        assert "## Attempt accounting" in render_markdown(report)
    finally:
        store.close()


def test_legacy_invalid_measurement_is_reported_as_infrastructure():
    assert attempt_bucket(
        {
            "status": "REJECTED",
            "failure_category": "INVALID_VALIDATION",
            "evaluation_json": '{"metrics": {"quality": 1}}',
            "decision_json": '{"accepted": false, "reasons": ["INVALID_VALIDATION"]}',
        }
    ) == "infra_failures"
    assert attempt_bucket(
        {
            "status": "REJECTED",
            "failure_category": "REJECTED",
            "evaluation_json": '{"metrics": {"quality": 1}}',
            "decision_json": '{"accepted": false, "reasons": ["MIN_IMPROVEMENT_NOT_MET"]}',
        }
    ) == "scientific_trials"


def test_legacy_accepted_result_ignores_stale_operational_failure_category():
    assert attempt_bucket(
        {
            "status": "ACCEPTED",
            "failure_category": "AGENT_PROTOCOL_ERROR",
            "evaluation_json": '{"status": "ok", "metrics": {"quality": 1}}',
            "decision_json": '{"accepted": true, "reasons": ["ACCEPTED"]}',
        }
    ) == "scientific_trials"


def test_root_plugin_manifest_is_portable_and_keeps_codex_metadata_extension():
    root = Path(__file__).parents[1] / "plugins" / "bananloop"
    portable = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    legacy = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert all(portable.get(key) for key in ("name", "version", "description"))
    assert portable["name"] == legacy["name"]
    assert portable["version"] == legacy["version"]
    assert portable["description"] == legacy["description"]
    assert portable["extensions"]["com.openai"]["interface"]["shortDescription"] == legacy["interface"]["shortDescription"]
    assert portable["extensions"]["com.openai"]["interface"]["longDescription"] == legacy["interface"]["longDescription"]
    assert "interface" in portable["extensions"]["com.openai"]
    assert (root / "skills" / "bananloop-goal" / "SKILL.md").is_file()
