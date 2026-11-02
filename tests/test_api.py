from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bananloop.api import create_server
from bananloop.application import CampaignApplication
from bananloop.contracts import AgentConfig, CampaignSpec, Direction, MetricSpec
from bananloop.lock import CampaignLock
from bananloop.registry import CampaignRegistry
from bananloop.store import CampaignStore
from bananloop.migrations import migrate_v4_to_v5


TOKEN = "test-api-token"


def _campaign(repo, goal: str):
    registry = CampaignRegistry(repo)
    entry, state_path, _ = registry.reserve(goal)
    store = CampaignStore(state_path / "campaign.sqlite3")
    store.initialize(CampaignSpec(
        goal=goal, source_repo=str(repo), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE,
                                  target=1.0, min_improvement=0.1),
        agent=AgentConfig(kind="codex"),
    ))
    return entry.campaign_id, store


@pytest.fixture
def api(tmp_path):
    first_id, first_store = _campaign(tmp_path, "first campaign")
    second_id, second_store = _campaign(tmp_path, "second campaign")
    server = create_server(str(tmp_path), port=0, token=TOKEN, poll_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, first_id, first_store, second_id, second_store
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        first_store.close()
        second_store.close()


def _request(url, *, method="GET", token=TOKEN, body=None, key=None, timeout=5):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()
    if key is not None:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def test_openapi_snapshot_and_cli_service_share_safe_projection(api, capsys):
    base, campaign_id, store, _, _ = api
    with _request(base + "/openapi.json") as response:
        spec = json.loads(response.read())
    assert spec["openapi"] == "3.1.0"
    assert "/v1/campaigns/{campaign_id}/events/stream" in spec["paths"]

    with _request(f"{base}/v1/campaigns/{campaign_id}/snapshot") as response:
        remote = json.loads(response.read())
    # The CLI commands and API use the same service; compare the two projections
    # directly through the repo-scoped service.
    local = CampaignApplication(store.spec().source_repo, campaign_id=campaign_id).snapshot()
    assert remote == local
    assert "private" not in json.dumps(remote).lower()

    from bananloop.cli import cmd_operator_snapshot
    import argparse

    cmd_operator_snapshot(argparse.Namespace(repo=store.spec().source_repo, campaign_id=campaign_id,
                                            state_dir=None))
    assert json.loads(capsys.readouterr().out) == remote

    from bananloop.cli import cmd_operator_events

    store.event("PRIVATE_EVALUATOR_RESULT", {"holdout": "never-return-this"})
    store.conn.commit()
    args = argparse.Namespace(repo=store.spec().source_repo, campaign_id=campaign_id,
                              state_dir=None, after=0, limit=100)
    assert cmd_operator_events(args) == 0
    cli_page = json.loads(capsys.readouterr().out)
    with _request(f"{base}/v1/campaigns/{campaign_id}/events?after=0&limit=100") as response:
        api_page = json.loads(response.read())
    assert cli_page == api_page
    assert "never-return-this" not in json.dumps(api_page)


def test_api_commands_are_idempotent_scoped_and_redacted(api):
    base, campaign_id, store, other_id, _ = api
    url = f"{base}/v1/campaigns/{campaign_id}/commands/pause"
    response = _request(url, method="POST", body={"request_id": "pause-1"}, key="pause-1")
    first = json.loads(response.read())
    response.close()
    response = _request(url, method="POST", body={"request_id": "pause-1"}, key="pause-1")
    assert json.loads(response.read()) == first
    response.close()

    with pytest.raises(urllib.error.HTTPError) as mismatch:
        _request(f"{base}/v1/campaigns/{campaign_id}/commands/stop", method="POST",
                 body={"request_id": "pause-1"}, key="pause-1")
    assert mismatch.value.code == 409
    mismatch.value.close()

    other_store = api[4]
    response = _request(f"{base}/v1/campaigns/{other_id}/commands/pause", method="POST",
                        body={"request_id": "pause-other"}, key="pause-other")
    assert response.status == 200
    response.close()
    assert other_store.control_request("pause-other") is not None
    assert store.control_request("pause-other") is None
    assert store.control_request("pause-1")["status"] == first["status"]


def test_sse_reconnect_uses_campaign_cursor_and_allowlist(api):
    base, campaign_id, store, _, _ = api
    before = int(store.conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
    store.event("STAGE_ENTERED", {"stage": "PROPOSE", "attempt_id": 17, "secret": "hidden"})
    store.conn.commit()
    url = f"{base}/v1/campaigns/{campaign_id}/events/stream?after={before}"
    with _request(url, timeout=2) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        lines = []
        while len(lines) < 5:
            line = response.readline().decode("utf-8").rstrip("\n")
            lines.append(line)
            if line.startswith("data: "):
                break
    event_id = next(line for line in lines if line.startswith("id: "))
    data_line = next(line for line in lines if line.startswith("data: "))
    assert event_id.startswith("id: ")
    data = json.loads(data_line[6:])
    assert data["payload"] == {"stage": "PROPOSE", "attempt_id": 17}
    assert "hidden" not in data_line

    with _request(f"{base}/v1/campaigns/{campaign_id}/events?after={int(event_id[4:])}") as response:
        replay = json.loads(response.read())
    assert replay["events"] == []
    assert replay["cursor"] == int(event_id[4:])


def test_auth_required_and_explicit_campaign_reads_do_not_change_active_campaign(api):
    base, first_id, first_store, second_id, _ = api
    with pytest.raises(urllib.error.HTTPError) as unauthorized:
        _request(f"{base}/v1/campaigns/{first_id}/snapshot", token=None)
    assert unauthorized.value.code == 401
    unauthorized.value.close()
    repo = first_store.spec().source_repo
    registry = CampaignRegistry(Path(repo))
    active_before = registry.resolve()[1]
    assert active_before == second_id
    CampaignApplication(repo, campaign_id=first_id).snapshot()
    assert registry.resolve()[1] == second_id


def test_non_loopback_is_rejected_and_body_size_is_bounded(tmp_path, api):
    with pytest.raises(ValueError, match="restricted to loopback"):
        create_server(str(tmp_path), host="0.0.0.0", port=0, token=TOKEN)
    base, campaign_id, _, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as too_large:
        _request(f"{base}/v1/campaigns/{campaign_id}/commands/pause", method="POST",
                 body={"request_id": "x", "unused": "y" * (65 * 1024)}, key="large")
    assert too_large.value.code == 413
    too_large.value.close()


def test_v2_graph_api_and_cli_share_idempotent_redacted_state(tmp_path, capsys):
    campaign_id, legacy_store = _campaign(tmp_path, "v2 graph campaign")
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    legacy_store.close()
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)

    server = create_server(str(tmp_path), port=0, token=TOKEN, poll_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    command = {
        "command_id": "add-node-1", "idempotency_key": "graph-1",
        "expected_graph_revision": 0,
        "changes": [{"type": "AddNode", "node_id": "explore-1", "phase": "EXPLORATION",
                     "payload": {"private_note": "not returned by snapshot", "scheduler": {"priority": 2}}}],
    }
    try:
        url = f"{base}/v1/campaigns/{campaign_id}/graph/commands"
        first_response = _request(url, method="POST", body=command, key="graph-1")
        first = json.loads(first_response.read())
        first_response.close()
        replay_response = _request(url, method="POST", body=command, key="graph-1")
        assert json.loads(replay_response.read()) == first
        replay_response.close()

        with pytest.raises(urllib.error.HTTPError) as mismatch:
            _request(url, method="POST", body={**command, "changes": [
                {"type": "AddNode", "node_id": "other", "phase": "EXPLORATION"},
            ]}, key="graph-1")
        assert mismatch.value.code == 409
        mismatch.value.close()

        with _request(f"{base}/v1/campaigns/{campaign_id}/graph") as response:
            remote = json.loads(response.read())
        assert remote["graph_revision"] == 1
        assert remote["nodes"] == [{"node_id": "explore-1", "phase": "EXPLORATION",
                                     "status": "PENDING", "revision": 0, "ready": True,
                                     "blockers": []}]
        assert "not returned by snapshot" not in json.dumps(remote)

        from bananloop.cli import cmd_graph_snapshot
        import argparse

        cmd_graph_snapshot(argparse.Namespace(repo=str(tmp_path), campaign_id=campaign_id))
        assert json.loads(capsys.readouterr().out) == remote

        with _request(f"{base}/v1/campaigns/{campaign_id}/events?after=0") as response:
            events = json.loads(response.read())
        assert events["events"][-1]["type"] == "GRAPH_MUTATED"
        assert events["events"][-1]["payload"] == {"command_id": "add-node-1", "graph_revision": 1}
        assert "not returned by snapshot" not in json.dumps(events)

        with pytest.raises(urllib.error.HTTPError) as future_cursor:
            _request(f"{base}/v1/campaigns/{campaign_id}/events?after=999999")
        assert future_cursor.value.code == 400
        future_cursor.value.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
