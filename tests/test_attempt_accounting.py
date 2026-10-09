from __future__ import annotations

import json
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from bananloop.report import build_report, render_markdown
from bananloop.cli import cmd_explain, cmd_inspect
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
        assert report["resources"]["by_stage"]["PROPOSE"]["agent_calls"] == 3
        assert report["experiments"][0]["causal_record"]["scientific_outcome"] == "DEV_ACCEPTED"
        assert report["experiments"][0]["trace_action_ids"]
        assert report["experiments"][0]["causal_record"]["evidence_ids"] == report["experiments"][0]["trace_action_ids"]
        assert len(report["experiments"][0]["causal_record"]["diff_signature"]) == 64
        selection_events = [event for event in store.events() if event["event_type"] == "HYPOTHESIS_SELECTION"]
        assert selection_events
        explain_out = StringIO()
        with redirect_stdout(explain_out):
            cmd_explain(argparse.Namespace(
                repo=str(engine.workspace.source_repo),
                state_dir=str(store.path.parent), campaign_id=None, json=True,
            ))
        explanation = json.loads(explain_out.getvalue())
        assert explanation["hypotheses"]
        assert explanation["interpretation"] is None
        inspect_out = StringIO()
        with redirect_stdout(inspect_out):
            cmd_inspect(argparse.Namespace(
                repo=str(engine.workspace.source_repo),
                state_dir=str(store.path.parent), campaign_id=None,
                experiment=1, json=True,
            ))
        inspected = json.loads(inspect_out.getvalue())
        assert inspected["experiment"]["ordinal"] == 1
        assert inspected["actions"]
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
