from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bananloop.api import create_server
from bananloop.application import CampaignApplication
from bananloop.contracts import AgentConfig, CampaignSpec, Direction, MetricSpec, RuntimePaths, parse_campaign_spec_document
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


def _request(url, *, method="GET", token=TOKEN, body=None, key=None, timeout=5,
             origin=None, csrf=None, host=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()
    if key is not None:
        headers["Idempotency-Key"] = key
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-BananLoop-CSRF"] = csrf
    if host is not None:
        headers["Host"] = host
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def test_openapi_snapshot_and_cli_service_share_safe_projection(api, capsys):
    base, campaign_id, store, _, _ = api
    with _request(base + "/openapi.json") as response:
        spec = json.loads(response.read())
    assert spec["openapi"] == "3.1.0"
    assert "/v1/campaigns/{campaign_id}/events/stream" in spec["paths"]
    assert spec["paths"]["/v1/campaigns/{campaign_id}/research"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["oneOf"]
    assert "/v1/campaigns/{campaign_id}/lifecycle" in spec["paths"]
    assert "/v1/campaigns/{campaign_id}/observability" in spec["paths"]

    with _request(f"{base}/v1/campaigns/{campaign_id}/snapshot") as response:
        remote = json.loads(response.read())
    # The CLI commands and API use the same service; compare the two projections
    # directly through the repo-scoped service.
    local = CampaignApplication(store.spec().source_repo, campaign_id=campaign_id).snapshot()
    assert remote.pop("as_of") and local.pop("as_of")
    assert remote == local
    assert "private" not in json.dumps(remote).lower()

    from bananloop.cli import cmd_operator_snapshot
    import argparse

    cmd_operator_snapshot(argparse.Namespace(repo=store.spec().source_repo, campaign_id=campaign_id,
                                            state_dir=None))
    cli_snapshot = json.loads(capsys.readouterr().out)
    assert cli_snapshot.pop("as_of")
    assert cli_snapshot == remote

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


def test_campaign_discovery_is_paginated_redacted_and_does_not_select_campaign(api):
    base, first_id, first_store, second_id, _ = api
    registry = CampaignRegistry(Path(first_store.spec().source_repo))
    before = {entry.campaign_id: active for entry, active in registry.list()}
    assert before[second_id] is True

    with _request(base + "/openapi.json") as response:
        openapi = json.loads(response.read())
    assert "/v1/campaigns" in openapi["paths"]

    with _request(base + "/v1/campaigns?limit=1") as response:
        first_page = json.loads(response.read())
    assert len(first_page["campaigns"]) == 1
    assert first_page["next_cursor"]
    assert first_page["source"] == "PERSISTED"
    assert first_page["as_of"]
    first_summary = first_page["campaigns"][0]
    assert first_summary["schema_version"] == 1
    assert first_summary["mode"] == "LEGACY"
    assert first_summary["profile"] is None
    assert first_summary["flag_hash"] is None
    assert first_summary["integrity_valid"] is True

    with _request(base + "/v1/campaigns?limit=1&cursor=" + first_page["next_cursor"]) as response:
        second_page = json.loads(response.read())
    assert len(second_page["campaigns"]) == 1
    assert second_page["next_cursor"] is None
    assert {first_summary["campaign_id"], second_page["campaigns"][0]["campaign_id"]} == {
        first_id, second_id,
    }

    encoded = json.dumps([first_page, second_page])
    for private in ("first campaign", "second campaign", "goal_fingerprint", "state_path", "request_key"):
        assert private not in encoded
    after = {entry.campaign_id: active for entry, active in registry.list()}
    assert after == before

    for query in ("limit=0", "limit=101", "limit=1&limit=2", "cursor=not-a-valid-cursor"):
        with pytest.raises(urllib.error.HTTPError) as invalid:
            _request(base + "/v1/campaigns?" + query)
        assert invalid.value.code == 400
        invalid.value.close()


def test_campaign_discovery_reports_persisted_v2_full_identity(tmp_path):
    campaign_id, store = _campaign(tmp_path, "v2 discovery campaign")
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    store.close()
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)

    from bananloop.contracts import V2_FULL_FEATURES

    digest = "a" * 64
    spec = parse_campaign_spec_document({
        "schema_version": 2,
        "mode": "EXPLORATION",
        "goal": "v2 discovery campaign",
        "source_repo": str(tmp_path),
        "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": digest, "scope_version": 1},
        "budget": {"max_experiments": 5, "max_attempts": 8, "max_agent_calls": 20,
                   "max_evaluator_calls": 10, "deadline_seconds": 3600,
                   "plateau_experiments": 3},
        "role_registry_digest": digest,
        "evaluation": None,
        "parent": None,
    })
    assert spec.feature_flags.enabled_features == V2_FULL_FEATURES
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE campaigns SET spec_json=?,spec_hash=? WHERE id=1",
            (spec.model_dump_json(), spec.approved_hash()),
        )
        connection.commit()
    finally:
        connection.close()

    page = CampaignApplication(tmp_path).list_campaigns(limit=100)
    summary = next(item for item in page["campaigns"] if item["campaign_id"] == campaign_id)
    assert summary["schema_version"] == 2
    assert summary["mode"] == "EXPLORATION"
    assert summary["profile"] == "FULL"
    assert summary["flag_hash"] == spec.feature_flags.flag_hash()
    assert summary["approved_spec_hash"] == spec.approved_hash()
    assert summary["integrity_valid"] is True

    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE campaigns SET spec_hash=? WHERE id=1", ("b" * 64,))
        connection.commit()
    finally:
        connection.close()
    corrupt_page = CampaignApplication(tmp_path).list_campaigns(limit=100)
    corrupt = next(item for item in corrupt_page["campaigns"] if item["campaign_id"] == campaign_id)
    assert corrupt["status"] == "DATA_INTEGRITY_ERROR"
    assert corrupt["integrity_valid"] is False
    assert corrupt["mode"] is None
    assert corrupt["profile"] is None
    assert corrupt["flag_hash"] is None


def test_campaign_discovery_indexes_legacy_without_writing_registry(tmp_path):
    paths = RuntimePaths.for_repo(tmp_path)
    store = CampaignStore(paths.db)
    store.initialize(CampaignSpec(
        goal="legacy campaign before registry", source_repo=str(tmp_path), base_commit="abc",
        evaluation_command=["python", "eval.py"],
        primary_metric=MetricSpec(name="quality", direction=Direction.MAXIMIZE,
                                  target=1.0, min_improvement=0.1),
        agent=AgentConfig(kind="codex"),
    ))
    store.close()

    registry = CampaignRegistry(tmp_path)
    assert not registry.path.exists()
    page = CampaignApplication(tmp_path).list_campaigns(limit=10)
    assert len(page["campaigns"]) == 1
    assert page["campaigns"][0]["campaign_id"] == "legacy"
    assert page["campaigns"][0]["active"] is False
    assert not registry.path.exists()


def test_dashboard_is_same_origin_static_and_mutations_reject_cross_origin(api):
    base, campaign_id, _, _, _ = api
    with _request(base + "/", token=None) as response:
        html = response.read().decode("utf-8")
        csp = response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "/dashboard.js" in html and "/dashboard.css" in html
    assert 'method="post" action="/"' in html
    assert "http://" not in html and "https://" not in html
    assert "localStorage" not in html
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp

    with _request(base + "/dashboard.js", token=None) as response:
        script = response.read().decode("utf-8")
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "textContent" in script
    assert "/research?" in script and "selectionGeneration" in script
    assert "/observability" in script and "/lifecycle" in script
    assert "sqlite" not in script.lower() and "indexeddb" not in script.lower()

    host = base.removeprefix("http://")
    url = f"{base}/v1/campaigns/{campaign_id}/commands/pause"
    body = {"request_id": "dashboard-pause"}
    with pytest.raises(urllib.error.HTTPError) as wrong_origin:
        _request(url, method="POST", body=body, key="dashboard-pause", origin="http://evil.example")
    assert wrong_origin.value.code == 403
    wrong_origin.value.close()
    with pytest.raises(urllib.error.HTTPError) as missing_csrf:
        _request(url, method="POST", body=body, key="dashboard-pause", origin=f"http://{host}")
    assert missing_csrf.value.code == 403
    missing_csrf.value.close()
    response = _request(url, method="POST", body=body, key="dashboard-pause",
                        origin=f"http://{host}", csrf="1")
    assert response.status == 200
    response.close()

    with pytest.raises(urllib.error.HTTPError) as bad_host:
        _request(base + "/healthz", token=None, host="evil.example")
    assert bad_host.value.code == 421
    bad_host.value.close()


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
        "changes": [
            {"type": "AddNode", "node_id": "explore-1", "phase": "EXPLORATION",
             "payload": {"private_note": "not returned by snapshot", "scheduler": {"priority": 2}}},
            {"type": "AddNode", "node_id": "explore-2", "phase": "OPTIMIZATION"},
            {"type": "AddEdge", "source_id": "explore-1", "target_id": "explore-2",
             "edge_type": "REQUIRES"},
            {"type": "AddEdge", "source_id": "explore-1", "target_id": "explore-2",
             "edge_type": "RELATED_TO"},
            {"type": "AddGroup", "group_id": "all-findings", "owner_node_id": "explore-2",
             "kind": "AND", "member_node_ids": ["explore-1"]},
        ],
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
        assert remote["nodes"] == [
            {"node_id": "explore-1", "phase": "EXPLORATION", "status": "PENDING",
             "revision": 0, "ready": True, "blockers": [], "completion_evidence_refs": []},
            {"node_id": "explore-2", "phase": "OPTIMIZATION", "status": "PENDING",
             "revision": 0, "ready": False,
             "blockers": ["requires:explore-1", "and:all-findings"],
             "completion_evidence_refs": []},
        ]
        assert remote["edges"] == [
            {"source_id": "explore-1", "target_id": "explore-2", "type": "RELATED_TO"},
            {"source_id": "explore-1", "target_id": "explore-2", "type": "REQUIRES"},
        ]
        assert remote["groups"] == [{
            "group_id": "all-findings", "owner_node_id": "explore-2",
            "kind": "AND", "member_node_ids": ["explore-1"],
        }]
        assert "not returned by snapshot" not in json.dumps(remote)

        from bananloop.cli import cmd_graph_snapshot
        import argparse

        cmd_graph_snapshot(argparse.Namespace(repo=str(tmp_path), campaign_id=campaign_id))
        local = json.loads(capsys.readouterr().out)
        # Each projection carries its own capture timestamp; compare persisted content.
        assert {key: value for key, value in local.items() if key != "as_of"} == {
            key: value for key, value in remote.items() if key != "as_of"
        }

        with _request(f"{base}/v1/campaigns/{campaign_id}/events?after=0") as response:
            events = json.loads(response.read())
        assert events["events"][-1]["type"] == "GRAPH_MUTATED"
        assert events["events"][-1]["payload"] == {"command_id": "add-node-1", "graph_revision": 1}
        assert "not returned by snapshot" not in json.dumps(events)

        with pytest.raises(urllib.error.HTTPError) as future_cursor:
            _request(f"{base}/v1/campaigns/{campaign_id}/events?after=999999")
        assert future_cursor.value.code == 409
        future_cursor.value.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_research_knowledge_read_model_is_public_bounded_and_redacted(api, tmp_path):
    base, campaign_id, store, second_id, second_store = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)

    # A read against a new v2 campaign must not bootstrap/write a research table.
    with _request(f"{base}/v1/campaigns/{campaign_id}/research?limit=10") as response:
        empty = json.loads(response.read())
    assert empty["scopes"] == [] and empty["source"] == "PERSISTED"
    conn = sqlite3.connect(database)
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_evidence'"
        ).fetchone() is None
        from bananloop.research_ledger import ResearchEvidenceV1, ResearchLedger, _digest

        ledger = ResearchLedger(conn)
        payload = {
            "campaign_id": campaign_id, "node_id": "explore-public", "node_revision": 2,
            "spec_hash": "d" * 64, "source_snapshot_id": "a" * 40,
            "protocol_digest": "b" * 64, "source_job_id": "private-job-id",
            "source_fence": 3, "source_request_hash": "e" * 64,
            "output_digest": "f" * 64, "event_cursor": 9,
            "claim_key": "retrieval.effect", "statement": "A public DEV observation.",
            "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE",
            "evidence_refs": ("artifact://sha256/" + "9" * 64,), "confidence": 0.4,
            "intervention_type": "feature_representation", "algorithmic_layer": "features",
            "affected_paths": ("src/private_module.py",), "classification": "PUBLIC_DEV",
            "verification_status": "UNVERIFIED_ROLE_FINDING",
            "causal_status": "UNVERIFIED_INFERENCE",
        }
        record = ResearchEvidenceV1(evidence_id=_digest(payload), **payload)
        conn.execute("INSERT INTO research_evidence VALUES(" + ",".join("?" for _ in ledger._row_values(record)) + ")",
                     ledger._row_values(record))
        conn.commit()
    finally:
        conn.close()

    with _request(f"{base}/v1/campaigns/{campaign_id}/research?limit=10") as response:
        scopes = json.loads(response.read())
    assert len(scopes["scopes"]) == 1
    query = "source_snapshot_id=" + "a" * 40 + "&protocol_digest=" + "b" * 64
    with _request(f"{base}/v1/campaigns/{campaign_id}/research?{query}") as response:
        projection = json.loads(response.read())
    encoded = json.dumps(projection)
    assert projection["evidence"][0]["verification_status"] == "UNVERIFIED_ROLE_FINDING"
    assert projection["evidence"][0]["causal_status"] == "UNVERIFIED_INFERENCE"
    assert "private-job-id" not in encoded and "source_request_hash" not in encoded
    assert "src/private_module.py" not in encoded and "affected_paths" not in encoded
    assert "PUBLIC_DEV" in encoded and "PERSISTED" in encoded
    assert len(projection["evidence"][0]["evidence_refs"][0]) < 100

    with pytest.raises(urllib.error.HTTPError) as invalid_scope:
        _request(f"{base}/v1/campaigns/{campaign_id}/research?source_snapshot_id=bad&protocol_digest=" + "b" * 64)
    assert invalid_scope.value.code == 400
    invalid_scope.value.close()
    with pytest.raises(urllib.error.HTTPError) as oversized_cursor:
        _request(f"{base}/v1/campaigns/{campaign_id}/research?cursor=" + "x" * 513)
    assert oversized_cursor.value.code == 400
    oversized_cursor.value.close()

    second_store.close()
    second_root, _ = registry.resolve(second_id, activate=False)
    with CampaignLock(second_root / "campaign.lock") as lock:
        migrate_v4_to_v5(second_root / "campaign.sqlite3", lock)
    with _request(f"{base}/v1/campaigns/{second_id}/research?limit=10") as response:
        isolated = json.loads(response.read())
    assert isolated["scopes"] == []


def test_observability_is_campaign_scoped_and_reconciles_budget(api, tmp_path):
    base, campaign_id, _, second_id, _ = api
    registry = CampaignRegistry(tmp_path)
    ledger = registry.budget_ledger()
    ledger.create_root(campaign_id, {"agent_calls": 10, "evaluator_calls": 4})
    ledger.create_root(second_id, {"agent_calls": 99})
    queue = registry.worker_queue()
    try:
        queue.enqueue(job_id="obs-ready-1", enqueue_key="obs-ready-1", campaign_id=campaign_id,
                      action_id="obs-action-1", resource="agent_calls", upper_bound=2,
                      request={"private_prompt": "must never be serialized"})
        queue.enqueue(job_id="obs-leased-1", enqueue_key="obs-leased-1", campaign_id=campaign_id,
                      action_id="obs-action-2", resource="agent_calls", upper_bound=2,
                      request={"private_output": "hidden"})
        queue.claim("obs-leased-1", "worker-private-name", lease_seconds=60)
        queue.enqueue(job_id="obs-expired-1", enqueue_key="obs-expired-1", campaign_id=campaign_id,
                      action_id="obs-expired-action", resource="agent_calls", upper_bound=1,
                      request={})
        queue.claim("obs-expired-1", "worker-expired", lease_seconds=1, now=100)
        queue.expire(now=102)
        queue.enqueue(job_id="obs-unknown-1", enqueue_key="obs-unknown-1", campaign_id=campaign_id,
                      action_id="obs-unknown-action", resource="agent_calls", upper_bound=1,
                      request={})
        unknown_claim = queue.claim("obs-unknown-1", "worker-unknown", lease_seconds=1, now=100)
        queue.mark_dispatched("obs-unknown-1", "worker-unknown", unknown_claim["fence"], now=100)
        queue.expire(now=102)
        queue.reconcile_unknown("obs-unknown-1", actual_amount=1,
                                receipt_digest="e" * 64, output_digest="f" * 64)
        queue.enqueue(job_id="obs-other-1", enqueue_key="obs-other-1", campaign_id=second_id,
                      action_id="obs-other-action", resource="agent_calls", upper_bound=1,
                      request={"private_prompt": "other campaign"})
        queue.conn.executemany(
            "INSERT INTO worker_events(job_id,event_type,fence,payload_json,created_at) "
            "VALUES('obs-other-1','JOB_ENQUEUED',0,'{}','2026-01-01T00:00:00Z')",
            [()] * 10001,
        )
        campaign_worker_cursor = queue.conn.execute(
            "SELECT MAX(e.event_id) FROM worker_events e JOIN worker_jobs j ON j.job_id=e.job_id "
            "WHERE j.campaign_id=?", (campaign_id,),
        ).fetchone()[0]
        project_worker_cursor = queue.conn.execute(
            "SELECT MAX(event_id) FROM worker_events"
        ).fetchone()[0]
    finally:
        queue.close()
        ledger.close()

    with _request(f"{base}/v1/campaigns/{campaign_id}/observability") as response:
        result = json.loads(response.read())
    assert result["campaign_id"] == campaign_id
    assert result["source"] == "PERSISTED" and result["event_cursor"] is not None
    assert result["queue"]["available"] is True
    assert result["queue"]["states"]["READY"] == 2
    assert result["queue"]["states"]["LEASED"] == 1
    assert result["queue"]["leases"]["active"] == 1
    assert result["queue"]["leases"]["max_fence"] == 1
    assert result["queue"]["transitions"]["JOB_ENQUEUED"] == 4
    assert result["queue"]["transitions"]["LEASE_EXPIRED_PRE_DISPATCH"] == 1
    assert result["queue"]["transitions"]["JOB_UNKNOWN"] == 1
    assert result["queue"]["transitions"]["UNKNOWN_RECONCILED"] == 1
    assert result["queue"]["states"]["UNKNOWN"] == 0
    assert result["queue"]["worker_event_cursor"] == campaign_worker_cursor
    assert campaign_worker_cursor < project_worker_cursor
    assert result["queue"]["transition_window"] == "latest 10000 campaign events"
    assert result["budget"]["dimensions"]["agent_calls"]["cap"] == 10
    assert result["budget"]["dimensions"]["agent_calls"]["spent"] == 1
    assert result["budget"]["dimensions"]["agent_calls"]["reserved"] == 2
    assert result["budget"]["dimensions"]["agent_calls"]["unknown"] == 0
    encoded = json.dumps(result)
    assert "private_prompt" not in encoded and "private_output" not in encoded
    assert "worker-private-name" not in encoded and "obs-ready-1" not in encoded

    with _request(f"{base}/v1/campaigns/{second_id}/observability") as response:
        other = json.loads(response.read())
    assert other["queue"]["states"]["READY"] == 1
    assert other["queue"]["states"]["LEASED"] == 0
    assert other["budget"]["dimensions"]["agent_calls"]["cap"] == 99
    assert other["scheduler"]["available"] is False  # legacy campaign, not a graph plan

    queue = registry.worker_queue()
    try:
        queue.conn.executemany(
            "INSERT INTO worker_events(job_id,event_type,fence,payload_json,created_at) "
            "VALUES('obs-ready-1','FUTURE_EVENT',0,'{}','2026-01-02T00:00:00Z')",
            [()] * 10001,
        )
        campaign_cursor_after_unknown_events = queue.conn.execute(
            "SELECT MAX(e.event_id) FROM worker_events e JOIN worker_jobs j ON j.job_id=e.job_id "
            "WHERE j.campaign_id=?", (campaign_id,),
        ).fetchone()[0]
    finally:
        queue.close()
    with _request(f"{base}/v1/campaigns/{campaign_id}/observability") as response:
        after_unknown_events = json.loads(response.read())
    assert after_unknown_events["queue"]["worker_event_cursor"] == campaign_cursor_after_unknown_events
    assert not any(after_unknown_events["queue"]["transitions"].values())


def test_graph_scheduler_wait_reason_is_persisted_read_only_and_campaign_scoped(api, tmp_path):
    _, campaign_id, store, _, _ = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)

    ledger = registry.budget_ledger()
    try:
        ledger.create_root(campaign_id, {"agent_calls": 1})
    finally:
        ledger.close()
    queue = registry.worker_queue()
    queue.close()
    app = CampaignApplication(tmp_path, campaign_id=campaign_id)
    app.mutate_graph({
        "command_id": "add-scheduler-node", "idempotency_key": "add-scheduler-node",
        "expected_graph_revision": 0,
        "changes": [{"type": "AddNode", "node_id": "scheduler-node", "phase": "EXPLORATION",
                     "payload": {"scheduler": {"upper_bound": 2}}}],
    }, "add-scheduler-node")

    before = sqlite3.connect(database).execute("SELECT MAX(seq) FROM events").fetchone()[0]
    result = app.observability()
    after = sqlite3.connect(database).execute("SELECT MAX(seq) FROM events").fetchone()[0]
    assert result["scheduler"] == {
        "available": True, "reason": "BUDGET_EXHAUSTED", "candidate_count": 0,
        "blocked_node_count": 0, "source": "PERSISTED", "as_of": result["as_of"],
        "event_cursor": result["event_cursor"],
        "worker_event_cursor": result["scheduler"]["worker_event_cursor"],
    }
    assert result["scheduler"]["event_cursor"] == before == after
    assert isinstance(result["scheduler"]["worker_event_cursor"], int)
    assert "scheduler-node" not in json.dumps(result)


def test_graph_scheduler_reports_waiting_for_active_campaign_worker(api, tmp_path):
    _, campaign_id, store, second_id, _ = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(state_root / "campaign.sqlite3", lock)
    ledger = registry.budget_ledger()
    ledger.create_root(campaign_id, {"agent_calls": 10})
    ledger.create_root(second_id, {"agent_calls": 10})
    ledger.close()

    app = CampaignApplication(tmp_path, campaign_id=campaign_id)
    app.mutate_graph({
        "command_id": "add-active-scheduler-node", "idempotency_key": "add-active-scheduler-node",
        "expected_graph_revision": 0,
        "changes": [{"type": "AddNode", "node_id": "active-node", "phase": "EXPLORATION"}],
    }, "add-active-scheduler-node")
    queue = registry.worker_queue()
    try:
        queue.enqueue(job_id="active-job", enqueue_key="active-job", campaign_id=campaign_id,
                      action_id="active-action", resource="agent_calls", upper_bound=1,
                      request={"node_id": "active-node", "private_prompt": "do not return"})
        queue.claim("active-job", "private-worker", lease_seconds=60)
        queue.enqueue(job_id="other-campaign-job", enqueue_key="other-campaign-job",
                      campaign_id=second_id, action_id="other-action", resource="agent_calls",
                      upper_bound=1, request={"node_id": "other-node"})
        project_worker_cursor = queue.conn.execute(
            "SELECT MAX(event_id) FROM worker_events"
        ).fetchone()[0]
    finally:
        queue.close()

    result = app.observability()
    assert result["scheduler"]["available"] is True
    assert result["scheduler"]["reason"] == "WAITING_FOR_WORKERS"
    assert result["scheduler"]["candidate_count"] == 0
    assert isinstance(result["scheduler"]["worker_event_cursor"], int)
    assert result["scheduler"]["worker_event_cursor"] < project_worker_cursor
    encoded = json.dumps(result)
    assert "active-node" not in encoded and "active-job" not in encoded
    assert "private-worker" not in encoded and "private_prompt" not in encoded


def test_graph_scheduler_missing_required_budget_dimension_is_unavailable(api, tmp_path):
    _, campaign_id, store, _, _ = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(state_root / "campaign.sqlite3", lock)
    ledger = registry.budget_ledger()
    ledger.create_root(campaign_id, {"agent_calls": 10})
    ledger.close()

    app = CampaignApplication(tmp_path, campaign_id=campaign_id)
    app.mutate_graph({
        "command_id": "add-optimization-scheduler-node",
        "idempotency_key": "add-optimization-scheduler-node",
        "expected_graph_revision": 0,
        "changes": [{"type": "AddNode", "node_id": "optimization-node", "phase": "OPTIMIZATION"}],
    }, "add-optimization-scheduler-node")
    result = app.observability()
    assert result["budget"]["available"] is True
    assert result["scheduler"]["available"] is False
    assert result["scheduler"]["reason"] is None
    assert result["scheduler"]["worker_event_cursor"] is None


def test_observability_reserved_campaign_reports_unavailable_not_server_error(api, tmp_path):
    base, _, _, _, _ = api
    entry, reserved_path, created = CampaignRegistry(tmp_path).reserve("not initialized yet", activate=False)
    assert created and not (reserved_path / "campaign.sqlite3").exists()
    with _request(f"{base}/v1/campaigns/{entry.campaign_id}/observability") as response:
        result = json.loads(response.read())
    assert result["event_cursor"] is None
    assert result["queue"]["available"] is False
    assert result["budget"]["available"] is False
    assert result["scheduler"]["available"] is False


def test_lifecycle_read_model_is_read_only_for_v2_campaign(api, tmp_path):
    base, campaign_id, store, _, _ = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)
    with _request(f"{base}/v1/campaigns/{campaign_id}/lifecycle") as response:
        result = json.loads(response.read())
    assert result["available"] is True
    assert result["phase_state"] is not None
    assert result["proposals"] == []
    assert result["verifier_receipts"] == []
    assert result["evaluator_state"] is None
    assert result["evaluator_run_receipt_count"] is None
    assert result["source"] == "PERSISTED" and result["event_cursor"] is not None
    conn = sqlite3.connect(database)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "v2_pivot_proposals" not in tables
    assert "evaluator_verifier_receipts" not in tables


def test_lifecycle_projection_redacts_pivot_scope_and_verifier_job_details(api, tmp_path):
    base, campaign_id, store, other_campaign_id, _ = api
    store.close()
    registry = CampaignRegistry(tmp_path)
    state_root, _ = registry.resolve(campaign_id, activate=False)
    database = state_root / "campaign.sqlite3"
    with CampaignLock(state_root / "campaign.lock") as lock:
        migrate_v4_to_v5(database, lock)
    conn = sqlite3.connect(database)
    try:
        spec_hash = conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0]
        from bananloop.contracts import PivotProposal
        from bananloop.evaluator_verification import EvaluatorProtocolAuthority

        proposal = PivotProposal.build(
            spec_hash=spec_hash, trigger_experiment_ordinal=2,
            current_architecture="Current", proposed_architecture="Candidate",
            rationale="A persisted test rationale.", expected_effect="Improve the DEV metric.",
            changed_scope=["src/private_module.py"],
            resources={"additional_agent_calls": 2, "additional_evaluator_calls": 1},
        )
        conn.execute("CREATE TABLE v2_pivot_proposals(proposal_id TEXT PRIMARY KEY,digest TEXT,spec_hash TEXT,proposal_json TEXT,status TEXT,created_at TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO v2_pivot_proposals VALUES(?,?,?,?,?,?,?)", (
            proposal.proposal_id, proposal.digest, spec_hash,
            json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            "PENDING", "2026-11-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00",
        ))
        conn.execute("CREATE TABLE v2_pivot_decisions(request_id TEXT,campaign_id TEXT,proposal_id TEXT,digest TEXT,decision TEXT,rejection_action TEXT,status TEXT,created_at TEXT)")
        conn.execute("INSERT INTO v2_pivot_decisions VALUES(?,?,?,?,?,?,?,?)", (
            "private-request-key", campaign_id, proposal.proposal_id, proposal.digest,
            "approve", None, "APPROVED", "2026-11-02T00:00:00+00:00",
        ))
        conn.execute("CREATE TABLE v2_pivot_sagas(idempotency_key TEXT,parent_campaign_id TEXT,child_id TEXT,state TEXT,child_spec_hash TEXT,created_at TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO v2_pivot_sagas VALUES(?,?,?,?,?,?,?)", (
            "private-saga-key", campaign_id, "c-" + "1" * 32, "CHILD_INITIALIZED",
            "a" * 64, "2026-11-02T00:00:00+00:00", "2026-11-02T00:01:00+00:00",
        ))
        EvaluatorProtocolAuthority(conn)
        conn.execute(
            "INSERT INTO evaluator_verifier_receipts(receipt_digest,proposal_digest,campaign_id,spec_hash,source_snapshot_id,protocol_digest,proposer_role_instance_id,verifier_job_id,verifier_role_id,verifier_role_instance_id,decision,approval_digest,receipt_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("b" * 64, proposal.digest, campaign_id, spec_hash, "c" * 40, "d" * 64,
             "private-proposer-role", "private-verifier-job", "verifier", "private-verifier-role",
             "APPROVE", "e" * 64, "{\"private\":true}"),
        )
        conn.execute(
            "INSERT INTO evaluator_verifier_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("1" * 64, proposal.digest, campaign_id, spec_hash, "c" * 40, "d" * 64,
             "private-proposer-role", "private-rejected-job", "verifier", "private-rejected-role",
             "REJECT", None, "{}"),
        )
        conn.execute(
            "INSERT INTO evaluator_verifier_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2" * 64, proposal.digest, other_campaign_id, spec_hash, "c" * 40, "d" * 64,
             "private-proposer-role", "private-other-campaign-job", "verifier", "private-other-role",
             "APPROVE", None, "{}"),
        )
        for digest, job_id, decision in (("3" * 64, "pass-job", "PASS"), ("4" * 64, "fail-job", "FAIL")):
            conn.execute(
                "INSERT INTO evaluator_integration_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (digest, campaign_id, spec_hash, "c" * 40, "e" * 64, "d" * 64, "a" * 40,
                 None, job_id, "private-integration-role", decision, "{}"),
            )
        conn.execute(
            "INSERT INTO evaluator_run_receipts VALUES(?,?,?,?,?,?,?,?)",
            ("f" * 64, campaign_id, spec_hash, "e" * 64, "d" * 64, "a" * 40, "9" * 64, "{}"),
        )
        conn.commit()
    finally:
        conn.close()

    with _request(f"{base}/v1/campaigns/{campaign_id}/lifecycle") as response:
        result = json.loads(response.read())
    assert result["proposals"][0]["classification"] == "UNVERIFIED_PROPOSAL"
    assert result["proposals"][0]["status"] == "PENDING"
    assert "changed_scope" not in result["proposals"][0]
    assert result["children"][0]["state"] == "CHILD_INITIALIZED"
    assert {item["decision"] for item in result["verifier_receipts"]} == {"APPROVE", "REJECT"}
    assert result["evaluator_run_receipt_count"] == 1
    assert result["evaluator_state"] == {
        "verifier": {"APPROVE": 1, "REJECT": 1},
        "integration": {"PASS": 1, "FAIL": 1},
        "run_receipts": 1,
    }
    encoded = json.dumps(result)
    for private in ("private_module.py", "private-request-key", "private-saga-key",
                    "private-verifier-job", "private-proposer-role", "private-verifier-role",
                    "private-rejected-job", "private-rejected-role",
                    "private-other-campaign-job", "private-other-role",
                    "pass-job", "fail-job", "private-integration-role"):
        assert private not in encoded


def test_event_cursor_reports_expired_and_ahead_positions(api):
    base, campaign_id, store, _, _ = api
    store.event("CURSOR_FIXTURE", {"part": 1})
    store.event("CURSOR_FIXTURE", {"part": 2})
    store.conn.commit()
    latest = int(store.conn.execute("SELECT MAX(seq) FROM events").fetchone()[0])
    store.conn.execute("DELETE FROM events WHERE seq<?", (latest,))
    store.conn.commit()
    cursor_floor = int(store.conn.execute("SELECT MIN(seq) FROM events").fetchone()[0]) - 1

    with pytest.raises(urllib.error.HTTPError) as expired:
        _request(f"{base}/v1/campaigns/{campaign_id}/events?after=0")
    assert expired.value.code == 410
    expired_payload = json.loads(expired.value.read())
    expired.value.close()
    assert expired_payload["error"] == "event_cursor_expired"
    assert expired_payload["cursor_floor"] == cursor_floor
    assert expired_payload["current_cursor"] >= 1

    with pytest.raises(urllib.error.HTTPError) as ahead:
        _request(f"{base}/v1/campaigns/{campaign_id}/events?after=999999")
    assert ahead.value.code == 409
    ahead_payload = json.loads(ahead.value.read())
    ahead.value.close()
    assert ahead_payload["error"] == "event_cursor_ahead"
    assert ahead_payload["cursor_floor"] == cursor_floor
    assert ahead_payload["current_cursor"] >= 1

    with pytest.raises(urllib.error.HTTPError) as expired_stream:
        _request(f"{base}/v1/campaigns/{campaign_id}/events/stream?after=0")
    assert expired_stream.value.code == 410
    expired_stream.value.close()
