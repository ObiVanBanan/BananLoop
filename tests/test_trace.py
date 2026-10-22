import json
import hashlib
from pathlib import Path
import sys

import pytest

from bananloop.cli import cmd_trace_export, parser
from bananloop.evaluator_binding import canonical_hash, make_binding, runtime_descriptor
from bananloop.store import StoreError
from bananloop.trace import _runtime_projection, export_trace
from test_engine import make_engine


def _file_bytes(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def test_shareable_trace_is_deterministic_and_omits_sensitive_payloads(tmp_path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.run()
        spec = store.spec().model_copy(update={
            "goal": "Improve quality for customer api_key=sk-secret GOLD_CANARY",
        })
        store.update_campaign(spec_json=spec.model_dump_json())
        hidden_result = json.loads(store.campaign()["baseline_json"])
        hidden_result["metrics"]["GOLD_CANARY"] = {"value": 0.999}
        store.record_evaluation(None, store.campaign()["champion_snapshot"], hidden_result, tier="holdout")
        store.event("ACTION_FAILED", {"action_id": "exp:1:implement", "error": "private traceback GOLD_CANARY sk-secret"})
        store.event("FINAL_HOLDOUT_RECORDED", {"snapshot": store.campaign()["champion_snapshot"],
                                                  "target_met": False, "label": "GOLD_CANARY"})
        store.event("CANDIDATE_PREFLIGHT_FAILED", {"experiment_id": 1, "errors": ["GOLD_CANARY secret detail"]})
        store.event("GOLD_CANARY", {"secret": "GOLD_CANARY"})

        output = tmp_path / "trace"
        export_trace(store, "campaign-test", output)
        first = _file_bytes(output)
        export_trace(store, "campaign-test", output)
        assert _file_bytes(output) == first

        text = "\n".join(value.decode("utf-8") for value in first.values())
        assert "GOLD_CANARY" not in text
        assert "sk-secret" not in text
        events = [json.loads(line) for line in first["events.jsonl"].decode().splitlines()]
        assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
        assert all({"schema_version", "campaign_id", "event_id", "seq", "timestamp", "event_type",
                    "stage", "experiment_id", "action_id", "receipt_sha256"}.issubset(event)
                   for event in events)
        assert all("error" not in event and "label" not in event and "errors" not in event for event in events)
        summary = json.loads(first["summary.json"])
        assert summary["schema_version"] == 2
        assert summary["goal"]["text"] == "omitted from shareable trace"
        assert summary["evaluation_receipts"]
        runtime = summary["runtime"]
        assert runtime["status"] == "VERIFIED"
        assert runtime["binding_hash"] == store.evaluator_binding()["binding_hash"]
        assert len(runtime["runtime_fingerprint"]) == 64
        assert runtime["tiers"]["dev"]["status"] == "VERIFIED"
        assert len(runtime["tiers"]["dev"]["dependency_fingerprint"]) == 64
        assert "resolved_executable" not in json.dumps(runtime)
        assert str(Path(sys.executable).resolve().parent) not in json.dumps(runtime)
        assert all("cases" not in json.dumps(summary) for _ in [0])
        manifest = json.loads(first["manifest.json"])
        assert manifest["format"] == "bananloop-shareable-trace-v2"
        assert set(manifest["files"]) == {"events.jsonl", "summary.json", "report.md"}
        assert all(hashlib.sha256(first[name]).hexdigest() == digest
                   for name, digest in manifest["files"].items())
    finally:
        store.close()


def test_trace_rejects_unowned_directory_and_restores_previous_bundle_on_swap_failure(tmp_path, monkeypatch):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.run()
        output = tmp_path / "trace"
        output.mkdir()
        (output / "user-data.txt").write_text("keep", encoding="utf-8")
        with pytest.raises(StoreError, match="refusing to replace"):
            export_trace(store, "campaign-test", output)

        (output / "user-data.txt").unlink()
        export_trace(store, "campaign-test", output)
        previous = _file_bytes(output)
        (output / "user-note.txt").write_text("keep", encoding="utf-8")
        with pytest.raises(StoreError, match="files not generated"):
            export_trace(store, "campaign-test", output)
        (output / "user-note.txt").unlink()
        store.event("STOP_REQUESTED", {})
        original_rename = Path.rename

        def fail_temp_swap(self, target):
            if ".trace.tmp-" in self.name:
                raise OSError("simulated directory swap failure")
            return original_rename(self, target)

        monkeypatch.setattr(Path, "rename", fail_temp_swap)
        with pytest.raises(OSError, match="simulated directory swap failure"):
            export_trace(store, "campaign-test", output)
        assert _file_bytes(output) == previous
    finally:
        store.close()


def test_trace_export_cli_parses_repo_campaign_and_output(tmp_path):
    args = parser().parse_args([
        "trace", "export", "--repo", str(tmp_path), "--campaign-id", "c-test",
        "--output", str(tmp_path / "export"),
    ])
    assert args.trace_command == "export"
    assert args.campaign_id == "c-test"
    assert args.output == str(tmp_path / "export")


def test_trace_export_command_reads_campaign_and_writes_bundle(tmp_path, capsys):
    store, engine, repo, _ = make_engine(tmp_path)
    state_dir = store.path.parent
    try:
        engine.run()
    finally:
        store.close()
    output = tmp_path / "trace-output"
    args = parser().parse_args([
        "trace", "export", "--repo", str(repo), "--state-dir", str(state_dir),
        "--output", str(output),
    ])
    assert cmd_trace_export(args) == 0
    assert "Shareable campaign trace exported" in capsys.readouterr().out
    assert {path.name for path in output.iterdir()} == {
        "events.jsonl", "summary.json", "report.md", "manifest.json",
    }


def test_trace_refreshes_owned_v1_bundle_to_current_schema(tmp_path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        engine.run()
        output = tmp_path / "trace-v1"
        export_trace(store, "campaign-test", output)
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["format"] = "bananloop-shareable-trace-v1"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        export_trace(store, "campaign-test", output)

        upgraded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert upgraded["format"] == "bananloop-shareable-trace-v2"
        assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["schema_version"] == 2
    finally:
        store.close()


def test_trace_marks_wrapper_runtime_partial_instead_of_inferencing_python(tmp_path):
    store, engine, _, _ = make_engine(tmp_path)
    try:
        runtime = runtime_descriptor({"dev": ["sh", "-c", "python evaluator.py"]})
        binding = make_binding(engine.spec, runtime)
        encoded = binding.model_dump_json()
        store.save_evaluator_binding(
            binding.binding_id,
            canonical_hash(binding.model_dump(mode="json")),
            encoded,
        )

        projected = _runtime_projection(store)

        assert projected["status"] == "PARTIAL"
        assert projected["tiers"]["dev"]["configured"] is True
        assert projected["tiers"]["dev"]["status"] == "UNKNOWN"
        assert projected["tiers"]["dev"]["dependency_fingerprint"] is None
        assert "/" not in projected["tiers"]["dev"]["executable_name"]
    finally:
        store.close()
