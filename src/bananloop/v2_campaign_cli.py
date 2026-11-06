"""Owner-facing v2 Exploration campaign creation and bounded SIWC execution."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .budget_ledger import BudgetLedger
from .chatgpt_plan import ChatGPTPlanAuth, SIWCError
from .chatgpt_responses import ChatGPTPlanResearcher, INSTRUCTIONS
from .contracts import ExplorationCampaignSpecV2, RuntimePaths, utc_now
from .graph_store import GraphRepository
from .migrations import create_v5_graph_schema
from .registry import CampaignRegistry
from .roles import RoleDefinitionV1, RoleRegistryV1
from .v2_research_runtime import V2ResearchRuntime, make_researcher_invoke
from .worker_queue import DurableWorkerQueue


class V2CampaignCLIError(ValueError):
    """A v2 campaign could not be safely prepared or advanced."""


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.PIPE, text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise V2CampaignCLIError("source repository Git operation failed") from exc


def _read_paths(repo: Path, paths: list[str]) -> tuple[str, ...]:
    if not paths or len(paths) > 16:
        raise V2CampaignCLIError("supply 1 to 16 exact --read-path values")
    normalized: list[str] = []
    for value in paths:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise V2CampaignCLIError("read paths must be nonempty POSIX-relative files")
        path = PurePosixPath(value)
        if (path.is_absolute() or PureWindowsPath(value).drive
                or any(part in {".", ".."} for part in path.parts)
                or any(part.lower() == ".git" or part.lower().startswith(".env")
                       or any(marker in part.lower() for marker in (
                           "evaluator", "evaluation", "validation", "holdout",
                           "secret", "credential",
                       )) for part in path.parts)):
            raise V2CampaignCLIError("read path is absolute, protected, or traverses its repository")
        candidate = repo.joinpath(*path.parts)
        current = repo
        contains_symlink = False
        for part in path.parts:
            current = current / part
            contains_symlink = contains_symlink or current.is_symlink()
        if (contains_symlink or not candidate.is_file()
                or not candidate.resolve().is_relative_to(repo)):
            raise V2CampaignCLIError(f"read path is not a safe repository file: {value}")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise V2CampaignCLIError("read paths must be unique")
    return tuple(sorted(normalized))


def _build_spec(repo: Path, goal: str, paths: tuple[str, ...], *,
                max_agent_calls: int, max_model_tokens: int, model: str):
    policy = {
        "policy_version": 1,
        "tools": ["read_file", "list_files", "write_artifact", "commit_candidate"],
        "read_paths": list(paths),
        "write_paths": ["**"],
        "backend": "LOCAL",
        "network_access": False,
    }
    roles = RoleRegistryV1(
        registry_id="bananloop-v2-owner-researcher", version=1,
        roles=(RoleDefinitionV1(
            role_id="researcher", adapter="codex", provider="openai", model=model,
            model_version="siwc-responses-v1",
            prompt_digest=hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest(),
            capability_policy=policy,
            context_fields=("campaign_goal", "node_id", "node_phase", "public_task",
                            "evidence_refs", "dev_summary", "budget_remaining"),
            min_instances=1, max_instances=1, calls_per_instance=max_agent_calls,
            tokens_per_instance=max_agent_calls * max_model_tokens,
        ),),
    )
    scope_digest = hashlib.sha256(json.dumps({
        "repo": str(repo), "goal": goal, "read_paths": paths,
        "model": model, "version": 1,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    spec = ExplorationCampaignSpecV2.model_validate({
        "schema_version": 2, "mode": "EXPLORATION", "goal": goal,
        "source_repo": str(repo), "base_commit": _git(repo, "rev-parse", "HEAD"),
        "autonomy": {
            "mode": "SUPERVISED", "scope_digest": scope_digest, "scope_version": 1,
            "capability_policy": policy,
        },
        "budget": {
            "max_agent_calls": max_agent_calls,
            "max_model_tokens": max_agent_calls * max_model_tokens,
        },
        "role_registry_digest": roles.digest(), "evaluation": None,
    })
    return spec, roles, policy


def _initialize_campaign(database: Path, spec: ExplorationCampaignSpecV2) -> None:
    existed = database.exists()
    conn = sqlite3.connect(database, timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )}
        if "campaigns" in names:
            rows = conn.execute("SELECT spec_json,spec_hash FROM campaigns ORDER BY id").fetchall()
            if (len(rows) != 1 or rows[0][1] != spec.approved_hash()
                    or ExplorationCampaignSpecV2.model_validate_json(rows[0][0]).approved_hash()
                    != spec.approved_hash()):
                raise V2CampaignCLIError("existing v2 campaign spec differs from this request")
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 5:
                raise V2CampaignCLIError("existing campaign schema version is not v5")
            conn.commit()
            return
        if existed and names:
            raise V2CampaignCLIError("campaign database is partially initialized; preserve it for recovery")
        conn.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        create_v5_graph_schema(conn)
        conn.execute(
            "CREATE TABLE campaigns(id INTEGER PRIMARY KEY,spec_json TEXT NOT NULL,"
            "spec_hash TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        now = utc_now()
        conn.execute(
            "INSERT INTO campaigns VALUES(1,?,?,?,?,?)",
            (json.dumps(spec.model_dump(mode="json"), sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False),
             spec.approved_hash(), "READY", now, now),
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def run_v2_explore(args: Any) -> dict[str, Any]:
    """Create/reopen one frozen Full v2 Exploration campaign and advance it."""
    goal = args.goal
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000 or "\x00" in goal:
        raise V2CampaignCLIError("--goal must contain 1 to 4000 non-NUL characters")
    if (isinstance(args.max_agent_calls, bool) or not 1 <= args.max_agent_calls <= 100
            or isinstance(args.max_model_tokens, bool) or not 1 <= args.max_model_tokens <= 100_000
            or isinstance(args.approve_up_to_calls, bool)
            or not 0 <= args.approve_up_to_calls <= args.max_agent_calls
            or isinstance(args.max_iterations, bool) or not 1 <= args.max_iterations <= 100
            or isinstance(args.timeout_seconds, bool) or not 1 <= args.timeout_seconds <= 300
            or not isinstance(args.model, str) or not args.model.strip() or len(args.model) > 200):
        raise V2CampaignCLIError("v2 call, token, or iteration bounds are invalid")
    if args.approve_up_to_calls > args.max_iterations:
        raise V2CampaignCLIError("--approve-up-to-calls cannot exceed --max-iterations")
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir() or repo.is_symlink():
        raise V2CampaignCLIError("--repo must be a real local Git repository directory")
    paths = _read_paths(repo, args.read_path)
    spec, roles, _policy = _build_spec(
        repo, goal, paths, max_agent_calls=args.max_agent_calls,
        max_model_tokens=args.max_model_tokens, model=args.model,
    )
    runtime_paths = RuntimePaths.for_repo(
        repo, Path(args.state_dir).expanduser() if args.state_dir else None,
    )
    registry = CampaignRegistry(repo, paths=runtime_paths)
    entry, state_root, _created = registry.reserve(goal, args.request_id, activate=False)
    database = state_root / "campaign.sqlite3"
    _initialize_campaign(database, spec)

    ledger = registry.budget_ledger()
    try:
        ledger.create_root(entry.campaign_id, {
            "agent_calls": args.max_agent_calls,
            "model_tokens": args.max_agent_calls * args.max_model_tokens,
        })
    finally:
        ledger.close()

    graph = GraphRepository.open(str(database), campaign_id=entry.campaign_id)
    queue = registry.worker_queue()
    try:
        if not graph.scheduling_snapshot()["nodes"]:
            graph.mutate({
                "command_id": "owner-initial-exploration-" + entry.campaign_id,
                "idempotency_key": "owner-initial-exploration-" + entry.campaign_id,
                "expected_graph_revision": graph.graph_revision(),
                "changes": [{
                    "type": "AddNode", "node_id": "explore-initial", "phase": "EXPLORATION",
                    "payload": {"hypothesis": goal, "iteration": 0},
                }],
            })
        runtime = V2ResearchRuntime(
            graph=graph, queue=queue, campaign_id=entry.campaign_id,
            state_root=state_root, role_registry=roles,
        )
        if args.approve_up_to_calls == 0:
            return {
                "status": "READY_FOR_OWNER_APPROVAL", "mode": "EXPLORATION",
                "profile": "FULL", "campaign_id": entry.campaign_id,
                "spec_hash": spec.approved_hash(), "base_commit": spec.base_commit,
                "read_paths": list(paths), "state_dir": str(state_root),
                "live_model_called": False,
            }

        auth = ChatGPTPlanAuth()
        if not auth.status()["connected"]:
            raise V2CampaignCLIError("ChatGPT is not connected; use bananloop-chatgpt status first")
        researcher = ChatGPTPlanResearcher(
            auth, approved_model=args.model, timeout_seconds=args.timeout_seconds,
        )
        try:
            researcher.check_model()
        except SIWCError as exc:
            raise V2CampaignCLIError(str(exc)) from exc
        result = runtime.run_campaign(
            visible_paths=paths, invoke=make_researcher_invoke(researcher),
            max_iterations=args.approve_up_to_calls,
        )
        return {
            **result, "mode": "EXPLORATION", "profile": "FULL",
            "spec_hash": spec.approved_hash(), "base_commit": spec.base_commit,
            "read_paths": list(paths), "state_dir": str(state_root),
            "provider": "chatgpt_plan", "model": args.model,
            "live_model_called": bool(result.get("completed")),
            "usage": researcher.last_usage if researcher.last_usage is not None else "UNKNOWN",
            "request_id": researcher.last_request_id,
        }
    finally:
        queue.close()
        graph.close()
