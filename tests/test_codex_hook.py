from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bananloop.codex_hook import main, stop_hook_decision
from bananloop.contracts import CampaignSpec, Direction, MetricSpec, RuntimePaths
from bananloop.lock import CampaignLock
from bananloop.registry import CampaignRegistry
from bananloop.store import CampaignStore


def setup_running_campaign(tmp_path: Path, monkeypatch, *, status: str = "RUNNING", stop_requested: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    paths = RuntimePaths.for_repo(repo)
    registry = CampaignRegistry(repo, paths)
    entry, state, _ = registry.reserve("Improve quality")
    spec = CampaignSpec(
        goal="Improve quality",
        source_repo=str(repo),
        base_commit="a" * 40,
        evaluation_command=["python", "evaluate.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1, min_improvement=0.1),
    )
    store = CampaignStore(state / "campaign.sqlite3")
    store.initialize(spec)
    store.update_campaign(status=status, stop_requested=stop_requested)
    store.close()
    return repo, paths, registry, entry, state


def stop_event(repo: Path, campaign_id: str | None = None, **overrides):
    message = f"BananLoop campaign: {campaign_id}" if campaign_id else "ordinary completion"
    event = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-secret-ish",
        "cwd": str(repo),
        "last_assistant_message": message,
        "stop_hook_active": False,
    }
    event.update(overrides)
    return event


def test_hook_blocks_once_and_reuses_hashed_session_binding(tmp_path, monkeypatch):
    repo, paths, registry, entry, state = setup_running_campaign(tmp_path, monkeypatch)
    result = stop_hook_decision(stop_event(repo, entry.campaign_id))
    assert result and result["decision"] == "block"
    assert f"--campaign-id {entry.campaign_id}" in result["reason"]
    assert str(repo) not in result["reason"]
    assert registry.codex_session_campaign("codex-session-secret-ish") == entry.campaign_id
    persisted = registry.path.read_bytes().decode("latin1", errors="ignore")
    assert registry.session_hash("codex-session-secret-ish") in persisted
    assert "codex-session-secret-ish" not in persisted

    resumed = stop_hook_decision(stop_event(repo))
    assert resumed and resumed["decision"] == "block"


def test_hook_binds_from_marker_in_pre_bridge_registry(tmp_path, monkeypatch):
    repo, _, registry, entry, _ = setup_running_campaign(tmp_path, monkeypatch)
    conn = sqlite3.connect(registry.path)
    conn.execute("DROP TABLE codex_sessions")
    conn.commit()
    conn.close()

    result = stop_hook_decision(stop_event(repo, entry.campaign_id))
    assert result and result["decision"] == "block"
    assert registry.codex_session_campaign("codex-session-secret-ish") == entry.campaign_id


def test_hook_fails_open_for_other_events_repeat_callback_and_missing_campaign(tmp_path, monkeypatch):
    repo, _, _, entry, _ = setup_running_campaign(tmp_path, monkeypatch)
    assert stop_hook_decision(stop_event(repo, entry.campaign_id, hook_event_name="SessionStart")) is None
    assert stop_hook_decision(stop_event(repo, entry.campaign_id, stop_hook_active=True)) is None
    assert stop_hook_decision(stop_event(repo)) is None
    assert stop_hook_decision(stop_event(repo, "c-" + "f" * 32)) is None


@pytest.mark.parametrize("status,stop_requested", [("PAUSED_INFRA", False), ("RUNNING", True), ("GOAL_MET", False)])
def test_hook_does_not_block_terminal_or_stopped_campaigns(tmp_path, monkeypatch, status, stop_requested):
    repo, _, _, entry, _ = setup_running_campaign(tmp_path, monkeypatch, status=status, stop_requested=stop_requested)
    assert stop_hook_decision(stop_event(repo, entry.campaign_id)) is None


def test_hook_does_not_retarget_existing_session_binding(tmp_path, monkeypatch):
    repo, paths, registry, entry, _ = setup_running_campaign(tmp_path, monkeypatch)
    registry.bind_codex_session("codex-session-secret-ish", entry.campaign_id)
    other, state, _ = registry.reserve("Second goal")
    spec = CampaignSpec(
        goal="Second goal", source_repo=str(repo), base_commit="a" * 40,
        evaluation_command=["python", "evaluate.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1, min_improvement=0.1),
    )
    store = CampaignStore(state / "campaign.sqlite3")
    store.initialize(spec)
    store.update_campaign(status="RUNNING")
    store.close()

    assert stop_hook_decision(stop_event(repo, other.campaign_id)) is None
    assert registry.codex_session_campaign("codex-session-secret-ish") == entry.campaign_id


def test_hook_fails_open_while_campaign_lock_is_held(tmp_path, monkeypatch):
    repo, _, _, entry, state = setup_running_campaign(tmp_path, monkeypatch)
    with CampaignLock(state / "campaign.lock"):
        assert stop_hook_decision(stop_event(repo, entry.campaign_id)) is None


def test_hook_fails_open_for_symlinked_campaign_lock(tmp_path, monkeypatch):
    repo, _, _, entry, state = setup_running_campaign(tmp_path, monkeypatch)
    lock_path = state / "campaign.lock"
    outside = tmp_path / "outside-lock-target"
    outside.write_text("preserve me", encoding="utf-8")
    try:
        lock_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"host does not permit symlink creation: {exc}")

    assert stop_hook_decision(stop_event(repo, entry.campaign_id)) is None
    assert outside.read_text(encoding="utf-8") == "preserve me"


def test_internal_cli_entrypoint_is_quiet_for_malformed_json(monkeypatch, capsys):
    import sys

    class Input:
        def read(self, _limit):
            return "not json"

    monkeypatch.setattr(sys, "stdin", Input())
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_main_discards_late_decision_at_hook_deadline(monkeypatch, capsys):
    import sys
    import bananloop.codex_hook as hook

    class Input:
        def read(self, _limit):
            return json.dumps({"hook_event_name": "Stop"})

    ticks = iter((0.0, 2.6))
    monkeypatch.setattr(sys, "stdin", Input())
    monkeypatch.setattr(hook, "stop_hook_decision", lambda _event: {"decision": "block", "reason": "late"})
    monkeypatch.setattr(hook.time, "monotonic", lambda: next(ticks))
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_cli_hidden_command_emits_stop_protocol_json(tmp_path, monkeypatch, capsys):
    import sys
    from bananloop.cli import parser

    repo, _, _, entry, _ = setup_running_campaign(tmp_path, monkeypatch)
    event = stop_event(repo, entry.campaign_id)

    class Input:
        def read(self, _limit):
            return json.dumps(event)

    monkeypatch.setattr(sys, "stdin", Input())
    args = parser().parse_args(["codex-hook"])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "block"
    assert entry.campaign_id in payload["reason"]


def test_plugin_hook_wire_contract_is_stop_only_and_windows_aware():
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / "plugins/bananloop/hooks/hooks.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "plugins/bananloop/plugin.json").read_text(encoding="utf-8"))
    legacy_manifest = json.loads((root / "plugins/bananloop/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert list(hooks["hooks"]) == ["Stop"]
    command = hooks["hooks"]["Stop"][0]["hooks"][0]
    assert command["command"] == command["commandWindows"] == "bananloop codex-hook"
    assert command["timeout"] == 3
    assert manifest["extensions"]["com.openai"]["hooks"] == "./hooks/hooks.json"
    assert legacy_manifest["hooks"] == "./hooks/hooks.json"
