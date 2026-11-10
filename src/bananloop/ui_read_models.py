"""Bounded, read-only UI projections over existing BananLoop campaign stores.

Never expose node payloads, sealed evaluator details, validation/holdout scores,
raw campaign spec, absolute paths, or model credentials to the browser.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .application import CampaignApplication
from .registry import CampaignRegistry
from .store import campaign_db_initialized

_ID_RE = re.compile(r"^(?:legacy|c-[0-9a-f]{32})$")


def list_ui_campaigns(repo: str) -> dict[str, Any]:
    registry = CampaignRegistry(Path(repo))
    items = []
    for entry, active in registry.list():
        # Do not reveal registry state paths or sensitive spec contents.
        try:
            root = registry._validate_state_path(entry.state_path, entry.origin)
            initialized = campaign_db_initialized(root / "campaign.sqlite3")
        except (ValueError, OSError):
            initialized = False
        items.append({
            "campaign_id": entry.campaign_id,
            "goal": entry.goal_summary[:240],
            "active": active,
            "initialized": initialized,
            "origin": entry.origin,
        })
    return {"schema_version": 1, "campaigns": items}


def _v2_view(app: CampaignApplication) -> dict[str, Any]:
    with app.open_graph_store() as (store, campaign_id):
        # scheduling_snapshot is the authoritative readiness/status projection.
        scheduling = store.scheduling_snapshot()
        campaign = store.conn.execute("SELECT status FROM campaigns WHERE id=1").fetchone()
        # Restrict the projection to public identity/status fields. No payload_json.
        nodes = [
            {"id": str(n["node_id"]), "phase": n["phase"], "status": n["status"],
             "ready": bool(n["ready"]), "blockers": list(n["blockers"])[:30],
             "revision": n["revision"]}
            for n in scheduling["nodes"][:300]
        ]
        node_ids = {n["id"] for n in nodes}
        edges = [
            {"source": row["src_id"], "target": row["dst_id"], "type": row["edge_type"]}
            for row in store.conn.execute(
                "SELECT src_id,dst_id,edge_type FROM research_edges ORDER BY src_id,dst_id,edge_type LIMIT 1200"
            ) if row["src_id"] in node_ids and row["dst_id"] in node_ids
        ]
        groups = []
        for row in store.conn.execute(
            "SELECT group_id,owner_node_id,kind FROM graph_groups ORDER BY group_id LIMIT 200"
        ):
            members = [r[0] for r in store.conn.execute(
                "SELECT node_id FROM graph_group_members WHERE group_id=? ORDER BY position LIMIT 300",
                (row["group_id"],),
            ) if r[0] in node_ids]
            if row["owner_node_id"] in node_ids:
                groups.append({"id": row["group_id"], "owner": row["owner_node_id"],
                               "kind": row["kind"], "members": members})
        # Graph writes are transactional and always advance the graph revision.
        # Do not combine nodes from one revision with edges from another.
        if store.graph_revision() != scheduling["graph_revision"]:
            raise RuntimeError("graph changed during UI read; retry")
        spec = store.campaign_spec()
        return {"schema_version": 1, "source_schema": 2, "campaign_id": campaign_id,
                "goal": spec.goal[:500], "status": campaign["status"] if campaign else "UNKNOWN",
                "revision": int(scheduling["graph_revision"]),
                "nodes": nodes, "edges": edges, "groups": groups,
                "partial": len(scheduling["nodes"]) > len(nodes),
                "capabilities": {"graph": True, "reasons": True, "engine_writes": False}}


def _v1_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Legacy campaigns do not have a trustworthy v2 graph/edges. We render
    # actual committed hypotheses as unconnected cards, never infer edges.
    nodes = []
    for hypothesis in (snapshot.get("hypotheses") or [])[:300]:
        if not isinstance(hypothesis, dict):
            continue
        ordinal = hypothesis.get("ordinal")
        if not isinstance(ordinal, int) or ordinal < 0:
            continue
        raw_status = hypothesis.get("status")
        nodes.append({"id": f"exp-{ordinal}", "phase": "EXPERIMENT", "status": str(raw_status or "UNKNOWN")[:40],
                      "title": str(hypothesis.get("title") or f"Experiment {ordinal}")[:240],
                      "ready": False, "blockers": [], "revision": None})
    return {"schema_version": 1, "source_schema": 1,
            "campaign_id": snapshot["campaign_id"], "goal": str(snapshot.get("goal") or "")[:500],
            "status": snapshot.get("status", "UNKNOWN"), "revision": snapshot.get("revision", 0),
            "nodes": nodes, "edges": [], "groups": [],
            "partial": len(snapshot.get("hypotheses") or []) > len(nodes),
            "capabilities": {"graph": False, "reasons": False, "engine_writes": False}}


def campaign_ui_view(repo: str, campaign_id: str) -> dict[str, Any]:
    if not _ID_RE.fullmatch(campaign_id):
        raise ValueError("invalid campaign identity")
    app = CampaignApplication(repo, campaign_id=campaign_id)
    paths, _ = app.resolve()
    if not campaign_db_initialized(paths.db):
        return {"schema_version": 1, "source_schema": 0, "campaign_id": campaign_id,
                "goal": "", "status": "RESERVED", "revision": 0,
                "nodes": [], "edges": [], "groups": [], "partial": False,
                "capabilities": {"graph": False, "reasons": False, "engine_writes": False}}
    if app._schema_version(paths.db) == 5:
        return _v2_view(app)
    return _v1_view(app.snapshot())


def codex_connection_status() -> dict[str, Any]:
    """Expose only account *mode*, never the CLI stdout or credentials."""
    executable = shutil.which("codex")
    result: dict[str, Any] = {"provider": "codex_cli", "installed": bool(executable),
                              "authenticated": False, "auth_mode": "none",
                              "chatgpt_subscription": False, "login_command": "codex login"}
    if not executable:
        return result
    try:
        proc = subprocess.run([executable, "login", "status"], capture_output=True,
                              text=True, timeout=4, check=False, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return {**result, "auth_mode": "unavailable"}
    if proc.returncode != 0:
        return result
    value = (proc.stdout + " " + proc.stderr).lower()[:2000]
    if "chatgpt" in value or "chat gpt" in value:
        return {**result, "authenticated": True, "auth_mode": "chatgpt", "chatgpt_subscription": True}
    if "api key" in value or "api_key" in value or "apikey" in value:
        return {**result, "authenticated": True, "auth_mode": "api_key"}
    return {**result, "authenticated": True, "auth_mode": "unknown"}