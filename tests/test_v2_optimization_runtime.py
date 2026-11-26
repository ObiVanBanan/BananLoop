from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bananloop.v2_optimization_runtime import (
    OptimizationCandidateProposalV1,
    V2OptimizationError,
    _safe_path,
    make_optimizer_invoke,
)


@pytest.mark.parametrize("path", [
    "../outside.py", "/absolute.py", "C:\\outside.py", ".git/config",
    "src/secrets.py", "src/evaluation_helper.py", "src/my_credentials.py",
    "src/.env.local", "src\\windows.py",
])
def test_optimizer_path_scope_rejects_traversal_and_private_material(path):
    with pytest.raises(V2OptimizationError):
        _safe_path(path)


def test_optimizer_proposal_is_bounded_and_rejects_empty_replacements():
    with pytest.raises(ValueError):
        OptimizationCandidateProposalV1(
            hypothesis_id="h1", rationale="bounded change", changes={"src/app.py": ""},
        )
    with pytest.raises(ValueError):
        OptimizationCandidateProposalV1(
            hypothesis_id="h1", rationale="bounded change",
            changes={f"src/file-{index}.py": "x" for index in range(17)},
        )


def test_optimizer_proposal_normalizes_one_string_limitation_losslessly():
    proposal = OptimizationCandidateProposalV1.model_validate({
        "hypothesis_id": "h1", "rationale": "bounded change",
        "changes": {"src/app.py": "candidate\n"},
        "limitations": "Needs follow-up testing.",
    })
    assert proposal.limitations == ("Needs follow-up testing.",)


def test_final_optimization_report_resolves_parent_from_custom_registry_root(tmp_path, monkeypatch):
    from bananloop.contracts import RuntimePaths
    from bananloop.registry import CampaignRegistry
    from bananloop.v2_optimization_runtime import V2OptimizationRuntime

    repo = tmp_path / "source"
    repo.mkdir()
    custom_state = tmp_path / "custom-state"
    registry = CampaignRegistry(repo, paths=RuntimePaths.for_repo(repo, custom_state))
    parent, parent_state, _ = registry.reserve("parent goal", "parent-request")
    child, child_state, _ = registry.reserve("child goal", "child-request")
    parent_report_dir = parent_state / "reports"
    parent_report_dir.mkdir(parents=True)
    (parent_report_dir / f"v2-exploration-{parent.campaign_id}.json").write_text(
        json.dumps({"campaign_id": parent.campaign_id, "status": "PERSISTED_GRAPH_SNAPSHOT"}),
        encoding="utf-8",
    )
    parent_db = sqlite3.connect(parent_state / "campaign.sqlite3")
    try:
        parent_db.executescript("""
            CREATE TABLE campaigns (id INTEGER PRIMARY KEY, spec_hash TEXT, status TEXT);
            CREATE TABLE events (seq INTEGER PRIMARY KEY, event_type TEXT, payload_json TEXT);
            CREATE TABLE evaluator_verifier_receipts (
                proposal_digest TEXT, protocol_digest TEXT, approval_digest TEXT,
                decision TEXT, receipt_digest TEXT, campaign_id TEXT
            );
        """)
        parent_db.execute("INSERT INTO campaigns VALUES(1,'parent-spec','PAUSED_NEEDS_INPUT')")
        for role, request_id in (("researcher", "research-request"),
                                 ("independent_verifier", "protocol-review-request")):
            parent_db.execute(
                "INSERT INTO events VALUES(NULL,'V2_PROVIDER_CALL',?)",
                (json.dumps({"role_id": role, "request_id": request_id,
                             "status": "COMPLETED", "dispatched": True,
                             "usage": {"input_tokens": 10, "output_tokens": 5,
                                       "total_tokens": 15}}),),
            )
        parent_db.execute(
            "INSERT INTO evaluator_verifier_receipts VALUES(?,?,?,?,?,?)",
            ("proposal", "protocol", "approval", "APPROVE", "verifier-receipt",
             parent.campaign_id),
        )
        parent_db.commit()
    finally:
        parent_db.close()

    class ParentRef:
        parent_campaign_id = parent.campaign_id
        allocation_id = "allocation-1"

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"parent_campaign_id": self.parent_campaign_id,
                    "allocation_id": self.allocation_id}

    class Spec:
        goal = "child goal"
        source_repo = str(repo)
        parent = ParentRef()

    class Graph:
        campaign_id = child.campaign_id

        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.conn.execute(
                "CREATE TABLE events (seq INTEGER PRIMARY KEY, event_type TEXT, payload_json TEXT)"
            )
            self.conn.execute(
                "CREATE TABLE evaluator_run_receipts (receipt_digest TEXT, "
                "candidate_snapshot_id TEXT, receipt_json TEXT, campaign_id TEXT)"
            )
            self.conn.execute(
                "CREATE TABLE evaluator_integration_evidence (evidence_digest TEXT, "
                "decision TEXT, baseline_run_receipt_digest TEXT, "
                "candidate_run_receipt_digest TEXT, campaign_id TEXT)"
            )

        def scheduling_snapshot(self):
            return {"nodes": [], "graph_revision": 0}

        def campaign_spec(self):
            return Spec()

    class NoChampion:
        def __init__(self, *_args):
            pass

        def current(self):
            return {"campaign_id": child.campaign_id, "snapshot_id": "candidate"}

    monkeypatch.setattr("bananloop.v2_optimization_runtime.EvaluatorProtocolAuthority", lambda _conn: object())
    monkeypatch.setattr("bananloop.champion_promotion.ChampionPromotionService", NoChampion)
    queue = registry.worker_queue()
    graph = Graph()
    for role, request_id in (("optimizer", "optimizer-request"),
                             ("integration_verifier", "integration-request")):
        graph.conn.execute(
            "INSERT INTO events(event_type,payload_json) VALUES('V2_PROVIDER_CALL',?)",
            (json.dumps({"role_id": role, "request_id": request_id,
                         "status": "COMPLETED", "dispatched": True,
                         "usage": {"input_tokens": 20, "output_tokens": 5,
                                   "total_tokens": 25}}),),
        )
    runtime = object.__new__(V2OptimizationRuntime)
    runtime.graph = graph
    runtime.queue = queue
    runtime.campaign_id = child.campaign_id
    runtime.state_root = child_state
    runtime._build_baseline_candidate_delta = lambda _champion: {
        "status": "INCOMPLETE", "comparison_table": [],
    }
    try:
        result = runtime._finish_campaign_run({
            "status": "ALL_NODES_COMPLETE", "steps": [], "holdout": {"status": "VERIFIED"},
        })
        final_report = json.loads(Path(result["final_report_path"]).read_text(encoding="utf-8"))
        assert final_report["campaign_id"] == parent.campaign_id
        assert final_report["optimization_campaign_id"] == child.campaign_id
        assert final_report["exploration_report"]["status"] == "PERSISTED_GRAPH_SNAPSHOT"
        assert [call["role_id"] for call in final_report["provider_calls"]] == [
            "researcher", "independent_verifier", "optimizer", "integration_verifier",
        ]
        rendered = Path(result["final_report_markdown_path"]).read_text(encoding="utf-8")
        for role in ("researcher", "independent_verifier", "optimizer", "integration_verifier"):
            assert f"| {role} |" in rendered
    finally:
        graph.conn.close()
        queue.close()


def test_optimizer_model_bridge_exposes_only_allowlisted_text_and_gateway_writes():
    class Gateway:
        def __init__(self):
            self.writes = {}

        def read_file(self, path):
            assert path == "src/app.py"
            return "base\n"

        def write_file(self, path, content):
            self.writes[path] = content

    seen = {}

    def model_reply(public_input, **pinned):
        seen["input"] = public_input
        seen["pinned"] = pinned
        return {
            "hypothesis_id": "h1", "rationale": "improve ranker",
            "changes": {"src/app.py": "candidate\n"},
        }

    model_reply.last_usage = {"total_tokens": 12}
    gateway = Gateway()
    invoke = make_optimizer_invoke(model_reply)
    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {"visible_paths": ["src/app.py"]},
        "role_context": {"public_task": "improve the ranker"},
        "private_path": "/home/user/.ssh/id_ed25519",
        "evaluator_secret": "must-not-be-forwarded",
    }
    result = invoke(
        request, gateway, adapter="codex", provider="openai", model="gpt-6-luna",
        model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500,
    )

    assert seen["input"]["visible_documents"] == {"src/app.py": "base\n"}
    assert "private_path" not in seen["input"] and "evaluator_secret" not in seen["input"]
    assert gateway.writes == {"src/app.py": "candidate\n"}
    assert result["model_tokens"] == 12


def test_optimizer_can_read_training_data_without_write_capability():
    class Gateway:
        def __init__(self):
            self.writes = {}

        def read_file(self, path):
            return {"data/train.csv": "sex,survived\nfemale,1\n",
                    "src/model.json": '{"schema":"titanic-linear-v1"}'}[path]

        def write_file(self, path, content):
            self.writes[path] = content

    seen = {}

    def model_reply(public_input, **_pinned):
        seen["input"] = public_input
        return {
            "hypothesis_id": "h1", "rationale": "fit bounded model weights",
            "changes": {"src/model.json": '{"schema":"titanic-linear-v1","weights":{}}'},
        }

    gateway = Gateway()
    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {
            "visible_paths": ["data/train.csv", "src/model.json"],
            "write_paths": ["src/model.json"],
        },
        "role_context": {"public_task": "improve Titanic accuracy"},
    }
    invoke = make_optimizer_invoke(model_reply)
    invoke(request, gateway, adapter="codex", provider="openai", model="gpt-6-luna",
           model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500)

    assert "female,1" in seen["input"]["visible_documents"]["data/train.csv"]
    assert seen["input"]["writable_paths"] == ["src/model.json"]
    assert gateway.writes == {
        "src/model.json": '{"schema":"titanic-linear-v1","weights":{}}',
    }


def test_optimizer_cannot_write_read_only_training_data():
    class Gateway:
        def read_file(self, _path):
            return "training data"

        def write_file(self, *_args):
            pytest.fail("read-only training data must not reach write_file")

    def model_reply(_public_input, **_pinned):
        return {
            "hypothesis_id": "h1", "rationale": "try to tamper with training data",
            "changes": {"data/train.csv": "changed\n"},
        }

    request = {
        "hypothesis_id": "h1",
        "role_binding": {
            "role_id": "optimizer", "adapter": "codex", "provider": "openai",
            "model": "gpt-6-luna", "model_version": "pin-1",
            "prompt_digest": "a" * 64,
        },
        "capability_manifest": {
            "visible_paths": ["data/train.csv", "src/model.json"],
            "write_paths": ["src/model.json"],
        },
        "role_context": {"public_task": "improve Titanic accuracy"},
    }
    invoke = make_optimizer_invoke(model_reply)
    with pytest.raises(V2OptimizationError, match="write allowlist"):
        invoke(request, Gateway(), adapter="codex", provider="openai", model="gpt-6-luna",
               model_version="pin-1", prompt_digest="a" * 64, max_model_tokens=500)


def test_optimizer_model_bridge_rejects_changed_role_or_unapproved_path():
    def model_reply(*_args, **_kwargs):
        return {"hypothesis_id": "h1", "rationale": "x", "changes": {"src/app.py": "y"}}

    invoke = make_optimizer_invoke(model_reply)
    request = {
        "hypothesis_id": "h1", "role_binding": {"role_id": "optimizer"},
        "capability_manifest": {"visible_paths": ["src/app.py"]},
        "role_context": {},
    }
    with pytest.raises(V2OptimizationError, match="pinned OpenAI role"):
        invoke(request, object(), adapter="legacy", provider="openai", model="x",
               model_version="v", prompt_digest="a" * 64, max_model_tokens=10)

