"""Offline unit checks for the diagnostic reader only, not live qualification."""
import json
import sqlite3

from benchmarks.autonomous_v01.live_e2e import diagnose, read_db


def test_empty_archive_is_not_live_pass(tmp_path):
    result = diagnose(tmp_path)
    assert result["worker_status"] == "MISSING_OUTPUT"
    assert result["scenario_results"]["full"] == "NOT_PASSED"
    assert result["scenario_results"]["multi"] == "NOT_PASSED"
    assert result["scenario_results"]["verifier"] == "NOT_OBSERVED"


def test_read_db_reports_real_state_shape_without_mutation(tmp_path):
    db_path = tmp_path / "registry.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        CREATE TABLE worker_jobs(job_id, campaign_id, action_id, resource, state, updated_at);
        CREATE TABLE worker_events(event_id INTEGER PRIMARY KEY, job_id, event_type, payload_json);
        CREATE TABLE team_role_call_jobs(job_id, role_id);
        """)
        db.execute("INSERT INTO worker_jobs VALUES(?,?,?,?,?,?)",
                   ("integration-1", "c-1", "a-1", "agent_calls", "UNKNOWN", "now"))
        db.execute("INSERT INTO team_role_call_jobs VALUES(?,?)",
                   ("integration-1", "integration_verifier"))
        db.execute("INSERT INTO worker_events VALUES(?,?,?,?)",
                   (1, "integration-1", "JOB_UNKNOWN",
                    json.dumps({"failure_stage": "provider call", "raw_prompt": "private"})))
    original = db_path.read_bytes()
    result = read_db(db_path)
    assert result["read_errors"] == []
    assert result["jobs"][0]["role_id"] == "integration_verifier"
    assert result["jobs"][0]["diagnostics"] == {"failure_stage": "provider call"}
    assert db_path.read_bytes() == original
    report = diagnose(tmp_path, state=tmp_path)
    assert report["scenario_results"]["verifier"] == "OBSERVED_REVIEW_REQUIRED"
    assert report["scenario_results"]["full"] == "NOT_PASSED"
