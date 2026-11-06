#!/usr/bin/env python3
"""Create a disposable v2 Full Exploration campaign and run two MOCK turns.

No Codex, provider network, billing, evaluator, or user home state is used.
Designed as a real, local v2 persistence/gateway smoke, NOT a model benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from bananloop.budget_ledger import BudgetLedger
from bananloop.contracts import ExplorationCampaignSpecV2, RuntimePaths, utc_now
from bananloop.graph_store import GraphRepository
from bananloop.migrations import create_v5_graph_schema
from bananloop.registry import CampaignRegistry
from bananloop.roles import RoleDefinitionV1, RoleRegistryV1
from bananloop.v2_research_runtime import V2ResearchRuntime, make_researcher_invoke


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="new, empty local directory")
    parser.add_argument("--goal", default="Find testable explanations for a toy mechanism",
                        help="public research question frozen into this disposable v2 campaign")
    args = parser.parse_args()
    if not args.goal.strip() or len(args.goal) > 4000 or "\x00" in args.goal:
        parser.error("--goal must contain 1 to 4000 non-NUL characters")

    root = args.work_dir.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("--work-dir must be a new or empty directory; existing state is never overwritten")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = root / "source"
    source.mkdir()
    (source / "TASK.md").write_text(
        f"Research question: {args.goal}\n"
        "Treat findings as unverified and design a falsifying next experiment.\n",
        encoding="utf-8",
    )
    _git(["init", "-q"], source)
    _git(["add", "TASK.md"], source)
    _git(["-c", "user.name=BananLoop mock", "-c",
          "user.email=mock@example.invalid", "commit", "-qm", "initial"], source)
    base_commit = _git(["rev-parse", "HEAD"], source)

    tools = ["read_file", "list_files", "write_artifact", "commit_candidate"]
    policy = {"policy_version": 1, "tools": tools,
              "read_paths": ["TASK.md"], "write_paths": ["**"], "backend": "LOCAL"}
    roles = RoleRegistryV1(
        registry_id="rpdi13r-demo", version=1,
        roles=(RoleDefinitionV1(
            role_id="researcher", adapter="codex", provider="openai",
            model="gpt-6-luna", model_version="offline-mock-v1",
            prompt_digest="e" * 64, capability_policy=policy,
            context_fields=("campaign_goal", "node_id", "node_phase",
                            "public_task", "evidence_refs", "dev_summary", "budget_remaining"),
            min_instances=1, max_instances=1, calls_per_instance=2,
            tokens_per_instance=200,
        ),),
    )
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION",
        "goal": args.goal,
        "source_repo": str(source), "base_commit": base_commit,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "a" * 64,
            "scope_version": 1, "capability_policy": policy,
        },
        "budget": {"max_agent_calls": 2},
        "role_registry_digest": roles.digest(),
        "evaluation": None,
    })
    registry = CampaignRegistry(
        source, paths=RuntimePaths.for_repo(source, root / "state"),
    )
    entry, state_root, created = registry.reserve(spec.goal, "rpdi13r-offline")
    if not created:
        raise RuntimeError("demo must use a fresh campaign")
    db = state_root / "campaign.sqlite3"
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("CREATE TABLE events("
                     "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                     "event_type TEXT NOT NULL,payload_json TEXT NOT NULL,"
                     "created_at TEXT NOT NULL)")
        conn.execute("BEGIN IMMEDIATE")
        create_v5_graph_schema(conn)
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,"
            "spec_json TEXT NOT NULL,spec_hash TEXT NOT NULL,"
            "status TEXT NOT NULL,created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        now = utc_now()
        conn.execute(
            "INSERT INTO campaigns VALUES(1,?,?,?,?,?)",
            (json.dumps(spec.model_dump(mode="json")),
             spec.approved_hash(), "READY", now, now),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    finally:
        conn.close()
    ledger = registry.budget_ledger()
    try:
        ledger.create_root(entry.campaign_id, {"agent_calls": 2})
    finally:
        ledger.close()

    graph = GraphRepository.open(str(db), campaign_id=entry.campaign_id)
    queue = registry.worker_queue()
    requests = []

    def mock_model(public_input, **pin):
        requests.append(public_input)
        text = f"mock observation {len(requests)}"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {
            "next_experiment": "Repeat with an independent controlled sample",
            "artifact_text": {"observation.txt": text},
            "research_findings": [{
                "claim_key": "toy:mechanism",
                "statement": f"An unverified toy observation #{len(requests)}",
                "stance": "SUPPORTS", "kind": "CAUSAL_INFERENCE",
                "confidence": 0.3,
                "evidence_refs": [f"artifact://sha256/{digest}"],
            }],
        }
    try:
        runner = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            state_root=state_root, role_registry=roles,
        )
        graph.mutate({
            "command_id": "demo-add-initial-research",
            "idempotency_key": "demo-add-initial-research",
            "expected_graph_revision": graph.graph_revision(),
            "changes": [{"type": "AddNode", "node_id": "research-initial",
                         "phase": "EXPLORATION"}],
        })
        campaign_result = runner.run_campaign(
            visible_paths=["TASK.md"], invoke=make_researcher_invoke(mock_model),
            max_iterations=2,
        )
        if campaign_result["iterations_this_call"] != 2:
            raise RuntimeError("offline campaign did not complete its two bounded iterations")
        results = campaign_result["completed"]
        inherited = requests[1]["role_context"].get("dev_summary", "")
        if "toy:mechanism" not in inherited:
            raise RuntimeError("the second model turn did not receive public causal memory")
        code_repo = Path(__file__).resolve().parents[2]
        bananloop_revision = subprocess.check_output(
            ["git", "-C", str(code_repo), "rev-parse", "HEAD"], text=True,
        ).strip()
        code_dirty = bool(subprocess.check_output(
            ["git", "-C", str(code_repo), "status", "--porcelain"], text=True,
        ).strip())
        result = {
            "status": "OFFLINE_V2_EXPLORATION_PASS",
            "live_model_called": False,
            "full_identity_only": True,
            "complete_full_pipeline_proven": False,
            "bananloop_revision": bananloop_revision,
            "bananloop_worktree_dirty": code_dirty,
            "campaign_id": entry.campaign_id,
            "goal": spec.goal,
            "source_commit": base_commit,
            "source_state": str(state_root),
            "campaign_loop_status": campaign_result["status"],
            "jobs": [x["job_id"] for x in results],
            "research_evidence_counts": [len(x["research_evidence_ids"]) for x in results],
            "second_turn_received_public_memory": True,
        }
        (state_root / "offline-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        queue.close()
        graph.close()


if __name__ == "__main__":
    main()
