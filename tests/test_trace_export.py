from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import bananloop.trace_export as trace_export
from bananloop.cli import parser
from bananloop.store import CampaignStore, StoreError
from bananloop.trace_export import export_trace
from test_engine import make_engine


def _package_bytes(path: Path) -> dict[str, bytes]:
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


def test_trace_export_is_deterministic_read_only_and_redacts_untrusted_payloads(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    output = tmp_path / "share" / "trace"
    try:
        engine.run()
        # Arbitrary event text and hidden labels must never pass through the projection.
        store.event("FINAL_HOLDOUT_RECORDED", {
            "case_id": "GOLD_CANARY",
            "label": "PRIVATE_PROMPT_CANARY",
            "reason": "api_key=SECRET_CANARY",
        })
        store.conn.commit()
        before = store.campaign()["updated_at"]
        snapshot = CampaignStore(store.path, readonly=True)
        try:
            snapshot.conn.execute("BEGIN")
            export_trace(snapshot, output, Path(snapshot.spec().source_repo))
            first = _package_bytes(output)
            export_trace(snapshot, output, Path(snapshot.spec().source_repo))
            second = _package_bytes(output)
        finally:
            snapshot.close()

        assert first == second
        assert set(first) == {"events.jsonl", "summary.json", "report.md", "manifest.json", "manifest.sha256"}
        assert store.campaign()["updated_at"] == before
        text = b"\n".join(first.values()).decode("utf-8")
        for secret in ("GOLD_CANARY", "PRIVATE_PROMPT_CANARY", "SECRET_CANARY", "api_key"):
            assert secret not in text

        events = [json.loads(line) for line in first["events.jsonl"].decode().splitlines()]
        assert events
        assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
        assert all(event["event_id"] and event["campaign_id"] for event in events)
        summary = json.loads(first["summary.json"])
        assert summary["evidence"]["raw_prompts_included"] is False
        manifest = json.loads(first["manifest.json"])
        assert all(hashlib.sha256(first[name]).hexdigest() == entry["sha256"] for name, entry in manifest["files"].items())
        assert hashlib.sha256(first["manifest.json"]).hexdigest() + "\n" == first["manifest.sha256"].decode()
    finally:
        store.close()


def test_trace_export_staging_failure_preserves_prior_complete_package(tmp_path: Path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    output = tmp_path / "share" / "trace"
    try:
        engine.run()
        export_trace(store, output, Path(store.spec().source_repo))
        original = _package_bytes(output)
        write_file = trace_export._write_file
        calls = 0

        def fail_on_second_write(path: Path, data: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            write_file(path, data)

        monkeypatch.setattr(trace_export, "_write_file", fail_on_second_write)
        with pytest.raises(OSError, match="injected write failure"):
            export_trace(store, output, Path(store.spec().source_repo))
        assert _package_bytes(output) == original
        assert not list(output.parent.glob(".trace.stage-*"))
    finally:
        store.close()


def test_trace_export_reads_legacy_schema_without_migrating_campaign_db(tmp_path: Path):
    store, engine, _, _ = make_engine(tmp_path)
    output = tmp_path / "legacy-share" / "trace"
    try:
        engine.run()
        store.conn.execute("DROP TABLE evaluation_receipts")
        store.conn.execute("DROP TABLE evaluator_bindings")
        store.conn.execute("ALTER TABLE evaluations DROP COLUMN tier")
        store.conn.execute("ALTER TABLE actions DROP COLUMN request_json")
        store.conn.commit()
        before = {
            table: [row["name"] for row in store.conn.execute(f"PRAGMA table_info({table})")]
            for table in ("evaluations", "actions")
        }

        readonly = CampaignStore(store.path, readonly=True)
        try:
            readonly.conn.execute("BEGIN")
            export_trace(readonly, output, Path(readonly.spec().source_repo))
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            assert summary["evidence"]["receipt_sha256s"] == []
            assert summary["evaluations"]
        finally:
            readonly.close()

        after = {
            table: [row["name"] for row in store.conn.execute(f"PRAGMA table_info({table})")]
            for table in ("evaluations", "actions")
        }
        assert after == before
    finally:
        store.close()


def test_trace_export_cli_is_registered_and_in_repo_path_is_constrained(tmp_path: Path):
    args = parser().parse_args(["trace", "export", "--repo", str(tmp_path), "--state-dir", str(tmp_path / "state"), "--output", str(tmp_path / "out")])
    assert args.trace_command == "export"
    assert args.func.__name__ == "cmd_trace_export"

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    store, _, _, _ = make_engine(fixture_root)
    try:
        with pytest.raises(StoreError, match="under .bananloop/exports"):
            export_trace(store, Path(store.spec().source_repo) / "arbitrary-export", Path(store.spec().source_repo))
    finally:
        store.close()
