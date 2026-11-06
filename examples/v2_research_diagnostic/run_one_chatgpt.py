#!/usr/bin/env python3
"""Explicitly authorized ONE subscription call through real v2 Exploration.

Opt-in only. No Code Interpreter, shell, evaluator or private files reach
the model. This is a transport smoke, not an RPDI-14 benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from bananloop.budget_ledger import BudgetLedger
from bananloop.chatgpt_plan import ChatGPTPlanAuth, SIWCError
from bananloop.chatgpt_responses import ChatGPTPlanResearcher, INSTRUCTIONS
from bananloop.contracts import ExplorationCampaignSpecV2, RuntimePaths, utc_now
from bananloop.graph_store import GraphRepository
from bananloop.migrations import create_v5_graph_schema
from bananloop.registry import CampaignRegistry
from bananloop.roles import RoleDefinitionV1, RoleRegistryV1
from bananloop.v2_research_runtime import V2ResearchRuntime, make_researcher_invoke


def _git(args: list[str], source: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=source, text=True, stderr=subprocess.PIPE
    ).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path,
                        help="new/empty work directory for safe disposable campaign")
    parser.add_argument("--model", default="gpt-6-luna",
                        help="pinned model slug returned by your ChatGPT model catalog")
    parser.add_argument("--approve-one-call", action="store_true",
                        help="explicit authorization to consume one ChatGPT plan request")
    args = parser.parse_args()
    if not args.approve_one_call:
        parser.error("NO MODEL CALL MADE: pass --approve-one-call only after reviewing quota")
    root = args.work_dir.expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        parser.error("--work-dir must be new or empty; existing campaign state is never overwritten")

    auth = ChatGPTPlanAuth()
    if not auth.status()["connected"]:
        parser.error("ChatGPT is not connected; run bananloop-chatgpt login first")
    researcher = ChatGPTPlanResearcher(auth, approved_model=args.model, timeout_seconds=120)
    try:
        researcher.check_model()
    except SIWCError as exc:
        parser.error(str(exc))

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = root / "source"
    source.mkdir(mode=0o700)
    (source / "TASK.md").write_text(
        "Research question: What controlled test would distinguish a true "
        "mechanism behind an observed association from an unmeasured "
        "confounder? State one falsifiable hypothesis, a concrete control, "
        "the expected evidence and limitations. Treat every inference as "
        "unverified; do not claim experimental data exists.\n",
        encoding="utf-8",
    )
    _git(["init", "-q"], source)
    _git(["add", "TASK.md"], source)
    _git(["-c", "user.name=BananLoop owner", "-c", "user.email=owner@example.invalid",
          "commit", "-qm", "public task"], source)
    commit = _git(["rev-parse", "HEAD"], source)
    policy = {
        "policy_version": 1,
        "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
        "read_paths": ["TASK.md"], "write_paths": ["**"], "backend": "LOCAL",
    }
    registry_roles = RoleRegistryV1(
        registry_id="rpdi13r-siwc-one-shot", version=1,
        roles=(RoleDefinitionV1(
            role_id="researcher", adapter="codex", provider="openai", model=args.model,
            model_version="siwc-responses-v1",
            prompt_digest=hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest(),
            capability_policy=policy,
            context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                            "evidence_refs", "dev_summary", "budget_remaining"),
            min_instances=1, max_instances=1, calls_per_instance=1,
            tokens_per_instance=2048,
        ),),
    )
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION",
        "goal": "Plan a falsifiable test of confounding for a public toy question",
        "source_repo": str(source), "base_commit": commit,
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": "a" * 64,
            "scope_version": 1, "capability_policy": policy,
        },
        "budget": {"max_agent_calls": 1},
        "role_registry_digest": registry_roles.digest(),
        "evaluation": None,
    })
    registry = CampaignRegistry(
        source, paths=RuntimePaths.for_repo(source, root / "state")
    )
    entry, state_root, created = registry.reserve(spec.goal, "rpdi13r-siwc-owner")
    if not created:
        raise RuntimeError("disposable campaign must not already exist")
    database = state_root / "campaign.sqlite3"
    conn = sqlite3.connect(database, isolation_level=None)
    try:
        conn.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        create_v5_graph_schema(conn)
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,"
            "spec_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        now = utc_now()
        conn.execute(
            "INSERT INTO campaigns VALUES(1,?,?,?,?,?)",
            (json.dumps(spec.model_dump(mode="json")), spec.approved_hash(),
             "READY", now, now),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    finally:
        conn.close()
    ledger = registry.budget_ledger()
    try:
        ledger.create_root(entry.campaign_id, {"agent_calls": 1})
    finally:
        ledger.close()
    graph = GraphRepository.open(str(database), campaign_id=entry.campaign_id)
    queue = registry.worker_queue()
    try:
        graph.mutate({
            "command_id": "add-siwc-research-1",
            "idempotency_key": "add-siwc-research-1",
            "expected_graph_revision": graph.graph_revision(),
            "changes": [{
                "type": "AddNode", "node_id": "siwc-research-1",
                "phase": "EXPLORATION",
            }],
        })
        runner = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            state_root=state_root, role_registry=registry_roles,
        )
        # The single subscription request happens only after the flag and preflight.
        result = runner.run_one(
            node_id="siwc-research-1", visible_paths=["TASK.md"],
            invoke=make_researcher_invoke(researcher),
        )
        print(json.dumps({
            "status": "SIWC_ONE_SHOT_EXPLORATION_COMPLETED",
            "live_model_call": True,
            "provider": "chatgpt_plan",
            "model": args.model,
            "response_request_id": researcher.last_request_id,
            "usage": researcher.last_usage if researcher.last_usage is not None else "UNKNOWN",
            "complete_full_pipeline_proven": False,
            "RPDI14_evidence": False,
            "campaign_id": entry.campaign_id,
            "job_id": result["job_id"],
            "research_evidence_ids": result["research_evidence_ids"],
            "state_dir": str(state_root),
        }, indent=2))
    finally:
        queue.close()
        graph.close()


if __name__ == "__main__":
    main()
