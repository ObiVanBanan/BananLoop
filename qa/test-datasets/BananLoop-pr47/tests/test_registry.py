from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bananloop.cli import _selected_paths, cmd_campaigns_list, cmd_doctor, cmd_start, parser
from bananloop.contracts import CampaignSpec, Direction, MetricSpec, RuntimePaths
from bananloop.lock import CampaignLock, CampaignLockError
from bananloop.registry import CampaignRegistry, RegistryError, goal_fingerprint
from bananloop.store import CampaignStore, StoreError


def registry_for(repo: Path, root: Path) -> CampaignRegistry:
    repo.mkdir(parents=True, exist_ok=True)
    return CampaignRegistry(repo, RuntimePaths.for_repo(repo, root))


def test_goal_reservation_is_canonical_and_distinct_goals_get_distinct_directories(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    first, first_path, created = registry.reserve("  Improve   quality\n safely ")
    repeated, repeated_path, repeated_created = registry.reserve("improve quality safely")
    second, second_path, second_created = registry.reserve("Improve quality safely without regressions")

    assert created and not repeated_created and second_created
    assert first.campaign_id == repeated.campaign_id
    assert first_path == repeated_path
    assert first_path != second_path
    assert first_path.name == first.campaign_id
    assert all(ch in "0123456789abcdef-" for ch in first.campaign_id)
    assert repeated.goal_fingerprint == goal_fingerprint("improve quality safely")
    assert registry.resolve()[1] == second.campaign_id


def test_request_id_is_hashed_and_conflicting_goal_does_not_mutate_entry(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    entry, path, _ = registry.reserve("Improve quality", "../unsafe/request")
    assert "unsafe" not in entry.campaign_id
    assert path.parent.name == "campaigns"
    with pytest.raises(RegistryError, match="different goal"):
        registry.reserve("Improve speed", "../unsafe/request")
    rows = registry.list()
    assert len(rows) == 1
    assert rows[0][0].campaign_id == entry.campaign_id


def test_codex_session_binding_is_hashed_and_does_not_change_active_campaign(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    first, _, _ = registry.reserve("first")
    second, _, _ = registry.reserve("second")
    registry.bind_codex_session("private-session-id", first.campaign_id)

    assert registry.codex_session_campaign("private-session-id") == first.campaign_id
    assert registry.codex_session_campaign("other-session") is None
    assert registry.resolve()[1] == second.campaign_id
    conn = sqlite3.connect(registry.path)
    try:
        hashes = {row[0] for row in conn.execute("SELECT session_hash FROM codex_sessions")}
    finally:
        conn.close()
    assert hashes == {registry.session_hash("private-session-id")}
    assert "private-session-id" not in hashes


def test_codex_session_binding_rejects_unknown_campaign_and_bad_session(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    registry.reserve("known campaign")
    with pytest.raises(RegistryError, match="unknown campaign"):
        registry.bind_codex_session("session", "c-" + "0" * 32)
    with pytest.raises(RegistryError, match="invalid Codex session"):
        registry.codex_session_campaign("\x00")


def test_codex_binding_adds_table_to_pre_bridge_registry(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    entry, _, _ = registry.reserve("legacy registry")
    conn = sqlite3.connect(registry.path)
    conn.execute("DROP TABLE codex_sessions")
    conn.commit()
    conn.close()

    registry.bind_codex_session("old-registry-session", entry.campaign_id)
    assert registry.codex_session_campaign("old-registry-session") == entry.campaign_id


def test_reservation_survives_crash_before_campaign_database_creation(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    first, state, created = registry.reserve("Recover me", "request-1")
    assert created and not (state / "campaign.sqlite3").exists()

    retry, retry_state, created_again = registry.reserve("Recover me", "request-1")
    assert not created_again
    assert retry.campaign_id == first.campaign_id
    assert retry_state == state
    assert len(registry.list()) == 1


def test_concurrent_same_request_reserves_one_campaign(tmp_path):
    repo = tmp_path / "repo"
    root = tmp_path / "state"
    repo.mkdir()

    def reserve(_):
        return registry_for(repo, root).reserve("Same goal", "same-request")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len({entry.campaign_id for entry, _, _ in results}) == 1
    assert sum(created for _, _, created in results) == 1
    assert len(registry_for(repo, root).list()) == 1


def test_concurrent_different_goals_reserve_separate_campaigns(tmp_path):
    repo = tmp_path / "repo"
    root = tmp_path / "state"
    repo.mkdir()
    registry = registry_for(repo, root)
    goals = [f"goal {index}" for index in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda goal: registry.reserve(goal), goals))
    assert len({entry.campaign_id for entry, _, _ in results}) == len(goals)
    assert len({path for _, path, _ in results}) == len(goals)
    assert len(registry.list()) == len(goals)


def test_campaign_lock_rejects_a_second_writer(tmp_path):
    path = tmp_path / "state" / "campaign.lock"
    with CampaignLock(path):
        with pytest.raises(CampaignLockError, match="already locked"):
            CampaignLock(path).acquire()


def test_legacy_registry_is_lazy_and_does_not_rewrite_campaign_database(tmp_path):
    repo = tmp_path / "repo"
    root = tmp_path / "state"
    repo.mkdir()
    root.mkdir()
    legacy_db = root / "campaign.sqlite3"
    original = b"legacy database bytes must remain untouched"
    legacy_db.write_bytes(original)
    registry = registry_for(repo, root)

    assert not registry.path.exists()
    entries = registry.list()
    assert len(entries) == 1
    entry, active = entries[0]
    assert entry.campaign_id == "legacy"
    assert entry.origin == "legacy"
    assert active
    assert registry.resolve("legacy")[0] == root
    assert legacy_db.read_bytes() == original


def test_corrupt_registry_is_not_overwritten(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    registry.root.mkdir(parents=True)
    registry.path.write_bytes(b"not sqlite")
    with pytest.raises(RegistryError, match="corrupt"):
        registry.list()
    assert registry.path.read_bytes() == b"not sqlite"


def test_registry_symlink_is_rejected_before_read_or_write(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    entry, _, _ = registry.reserve("safe")
    outside = tmp_path / "outside-registry.sqlite3"
    outside.write_bytes(b"preserve registry target")
    saved_registry = registry.path.with_suffix(".saved")
    registry.path.replace(saved_registry)
    try:
        registry.path.symlink_to(outside)
    except OSError as exc:
        saved_registry.replace(registry.path)
        pytest.skip(f"host does not permit symlink creation: {exc}")

    try:
        with pytest.raises(RegistryError, match="campaign registry cannot be a symlink"):
            registry.codex_campaign_state_path(entry.campaign_id)
        with pytest.raises(RegistryError, match="campaign registry cannot be a symlink"):
            registry.bind_codex_session("session", entry.campaign_id)
        assert outside.read_bytes() == b"preserve registry target"
    finally:
        registry.path.unlink(missing_ok=True)
        saved_registry.replace(registry.path)


def test_managed_registry_path_escape_is_rejected(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    entry, _, _ = registry.reserve("safe")
    conn = sqlite3.connect(registry.path)
    conn.execute("UPDATE campaign_index SET state_path=? WHERE campaign_id=?", ("../../outside", entry.campaign_id))
    conn.commit()
    conn.close()
    with pytest.raises(RegistryError, match="invalid managed campaign path"):
        registry.resolve(entry.campaign_id)


def test_campaign_database_symlink_is_rejected_before_open(tmp_path):
    registry = registry_for(tmp_path / "repo", tmp_path / "state")
    entry, state, _ = registry.reserve("unsafe db")
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"do not open")
    database = state / "campaign.sqlite3"
    try:
        database.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"host does not permit symlink creation: {exc}")
    with pytest.raises(RegistryError, match="symlink or reparse point"):
        registry.resolve(entry.campaign_id)


def test_cli_selector_and_campaign_list_select_active(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = CampaignRegistry(repo)
    first, _, _ = registry.reserve("first")
    second, _, _ = registry.reserve("second")
    args = parser().parse_args(["status", "--repo", str(repo), "--campaign-id", first.campaign_id])
    assert _selected_paths(args).root.name == first.campaign_id

    list_args = parser().parse_args(["campaigns", "list", "--repo", str(repo), "--json"])
    assert cmd_campaigns_list(list_args) == 0
    output = capsys.readouterr().out
    assert first.campaign_id in output and second.campaign_id in output
    assert '"active": true' in output

    mixed = parser().parse_args(["status", "--repo", str(repo), "--state-dir", "custom", "--campaign-id", first.campaign_id])
    with pytest.raises(RegistryError, match="cannot be used together"):
        _selected_paths(mixed)

    unknown = parser().parse_args(["status", "--repo", str(repo), "--campaign-id", "c-" + "0" * 32])
    with pytest.raises(RegistryError, match="unknown campaign-id"):
        _selected_paths(unknown)


def test_start_reserves_before_bootstrap_and_reuses_request_id(tmp_path, monkeypatch, capsys):
    from bananloop import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def fake_bootstrap(*, repo, goal, paths, **kwargs):
        calls.append((goal, paths.root))
        return SimpleNamespace(status="needs_input", summary="missing test fixture", needs_input=["fixture"]), None

    monkeypatch.setattr(cli, "bootstrap_campaign", fake_bootstrap)
    args = parser().parse_args(["start", "--repo", str(repo), "--goal", "Improve quality", "--request-id", "run-1", "--allow-dirty"])
    assert cmd_start(args) == 3
    capsys.readouterr()
    args2 = parser().parse_args(["start", "--repo", str(repo), "--goal", "improve quality", "--request-id", "run-1", "--allow-dirty"])
    assert cmd_start(args2) == 3
    capsys.readouterr()

    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert calls[0][1].parent.name == "campaigns"
    assert len(CampaignRegistry(repo).list()) == 1

    conflict = parser().parse_args(["start", "--repo", str(repo), "--goal", "Different goal", "--request-id", "run-1", "--allow-dirty"])
    with pytest.raises(RegistryError, match="different goal"):
        cmd_start(conflict)


def _spec(repo: Path, goal: str) -> CampaignSpec:
    return CampaignSpec(
        goal=goal,
        source_repo=str(repo),
        base_commit="a" * 40,
        evaluation_command=["python", "evaluate.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE, target=1, min_improvement=0.1),
    )


def test_reservation_recovers_when_database_exists_after_interruption(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = registry_for(repo, tmp_path / "state")
    original, state, _ = registry.reserve("resume bootstrap", "crash-1")
    store = CampaignStore(state / "campaign.sqlite3")
    store.initialize(_spec(repo, "resume bootstrap"))
    store.update_campaign(status="PAUSED_INFRA", stop_reason="INTERRUPTED")
    store.close()

    recovered, recovered_state, created = registry.reserve("resume bootstrap", "crash-1")
    assert recovered.campaign_id == original.campaign_id
    assert recovered_state == state
    assert not created
    check = CampaignStore(recovered_state / "campaign.sqlite3")
    try:
        assert check.campaign()["status"] == "PAUSED_INFRA"
    finally:
        check.close()


@pytest.mark.parametrize("status", ["PAUSED_INFRA", "GOAL_MET"])
def test_legacy_goal_repeat_adopts_existing_campaign_and_routes_by_status(tmp_path, monkeypatch, capsys, status):
    from bananloop import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = RuntimePaths.for_repo(repo)
    store = CampaignStore(paths.db)
    store.initialize(_spec(repo, "Improve quality"))
    store.update_campaign(status=status, stop_reason="TEST")
    store.close()
    original_bytes = paths.db.read_bytes()
    registry = CampaignRegistry(repo)
    entry, resolved, created = registry.reserve(" improve   QUALITY ")
    assert entry.campaign_id == "legacy"
    assert resolved == paths.root
    assert not created
    assert paths.db.read_bytes() == original_bytes
    managed_dirs = list((paths.root / "campaigns").glob("c-*")) if (paths.root / "campaigns").exists() else []
    assert managed_dirs == []

    calls = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, resume=False: calls.append(resume) or 0)
    args = parser().parse_args(["start", "--repo", str(repo), "--goal", "Improve quality", "--allow-dirty"])
    result = cmd_start(args)
    capsys.readouterr()
    if status == "GOAL_MET":
        assert result == 0
        assert calls == []
    else:
        assert result == 0
        assert calls == [True]


def test_explicit_request_id_does_not_adopt_matching_legacy_goal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "state"
    legacy = CampaignStore(root / "campaign.sqlite3")
    legacy.initialize(_spec(repo, "same goal"))
    legacy.close()
    registry = registry_for(repo, root)
    entry, state, created = registry.reserve("same goal", "intentional-repeat")
    assert created
    assert entry.campaign_id != "legacy"
    assert state != root


def test_legacy_registration_uses_synthetic_key_when_managed_goal_exists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "state"
    registry = registry_for(repo, root)
    managed, managed_path, created = registry.reserve("shared goal")
    assert created

    legacy = CampaignStore(root / "campaign.sqlite3")
    legacy.initialize(_spec(repo, "shared goal"))
    legacy.close()

    entries = registry.list()
    assert {entry.campaign_id for entry, _ in entries} == {managed.campaign_id, "legacy"}
    reused, reused_path, was_created = registry.reserve("shared goal")
    assert reused.campaign_id == managed.campaign_id
    assert reused_path == managed_path
    assert not was_created


def test_start_reuses_paused_campaign_and_routes_to_resume(tmp_path, monkeypatch, capsys):
    from bananloop import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = CampaignRegistry(repo)
    entry, root, _ = registry.reserve("Improve quality", "resume-me")
    store = CampaignStore(root / "campaign.sqlite3")
    store.initialize(_spec(repo, "Improve quality"))
    store.update_campaign(status="PAUSED_INFRA", stop_reason="TEST_PAUSE")
    store.close()
    called = []
    monkeypatch.setattr(cli, "bootstrap_campaign", lambda **kwargs: pytest.fail("bootstrap must not rerun"))
    monkeypatch.setattr(cli, "cmd_run", lambda args, resume=False: called.append((args.campaign_id, resume)) or 0)

    args = parser().parse_args(["start", "--repo", str(repo), "--goal", "Improve quality", "--request-id", "resume-me", "--allow-dirty"])
    assert cmd_start(args) == 0
    capsys.readouterr()
    assert called == [(entry.campaign_id, True)]


def test_start_terminal_retry_returns_saved_status_without_running(tmp_path, monkeypatch, capsys):
    from bananloop import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    entry, root, _ = CampaignRegistry(repo).reserve("Done", "done")
    store = CampaignStore(root / "campaign.sqlite3")
    store.initialize(_spec(repo, "Done"))
    store.update_campaign(status="GOAL_MET")
    store.close()
    monkeypatch.setattr(cli, "cmd_run", lambda *args, **kwargs: pytest.fail("terminal campaign must not run again"))

    args = parser().parse_args(["start", "--repo", str(repo), "--goal", "Done", "--request-id", "done", "--allow-dirty"])
    assert cmd_start(args) == 0
    output = capsys.readouterr().out
    assert '"status": "GOAL_MET"' in output
    assert entry.campaign_id in output


def test_explicit_state_dir_keeps_existing_single_campaign_error(tmp_path, monkeypatch):
    from bananloop import cli

    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "custom-state"
    store = CampaignStore(state / "campaign.sqlite3")
    store.initialize(_spec(repo, "existing"))
    store.close()
    monkeypatch.setattr(cli, "bootstrap_campaign", lambda **kwargs: pytest.fail("must preserve explicit state-dir behavior"))
    args = parser().parse_args(["start", "--repo", str(repo), "--goal", "new goal", "--state-dir", str(state), "--allow-dirty"])
    with pytest.raises(StoreError, match="use run/resume or choose a fresh --state-dir"):
        cmd_start(args)


def test_doctor_reports_active_managed_registry_location(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    repo = tmp_path / "repo"
    repo.mkdir()
    entry, state, _ = CampaignRegistry(repo).reserve("Inspect me")
    args = parser().parse_args(["doctor", "--repo", str(repo), "--json"])
    assert cmd_doctor(args) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["state"]) == state
    assert payload["base_commit"] is None
    assert payload["active_campaign_id"] == entry.campaign_id
    assert payload["campaign_exists"] is False
    assert payload["campaign_count"] == 1
