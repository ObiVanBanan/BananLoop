"""Deterministic hybrid planning over the canonical graph and existing budgets.

The scheduler chooses work and an existing execution lane. DurableWorkerQueue
still owns leases/fences and BudgetLedger remains the sole spending authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Literal

from .graph_store import GraphRepository, GraphStoreError


Phase = Literal["EXPLORATION", "METRIC_DISCOVERY", "OPTIMIZATION", "VERIFICATION"]
StopReason = Literal[
    "CONTINUE", "WAITING_FOR_WORKERS", "ALL_NODES_COMPLETE", "BUDGET_EXHAUSTED",
    "DEPENDENCIES_BLOCKED", "NO_REMAINING_WORK", "STOP_REQUESTED",
]

_LANES: dict[str, tuple[str, str]] = {
    "EXPLORATION": ("EVIDENCE_WORKER", "agent_calls"),
    "METRIC_DISCOVERY": ("METRIC_DISCOVERY_WORKER", "agent_calls"),
    # Every v2 Optimization node must be handled by an explicit v2 adapter.
    # The old v1 Engine is never a scheduler fallback for a v2 graph.
    "OPTIMIZATION": ("OPTIMIZATION_WORKER", "experiments"),
    "VERIFICATION": ("INDEPENDENT_VERIFIER", "evaluator_calls"),
}
_PHASE_ORDER = {"EXPLORATION": 0, "METRIC_DISCOVERY": 1, "VERIFICATION": 2, "OPTIMIZATION": 3}


@dataclass(frozen=True)
class ScheduledNode:
    node_id: str
    phase: Phase
    lane: str
    resource: str
    upper_bound: int
    priority: int
    node_revision: int
    graph_revision: int


@dataclass(frozen=True)
class SchedulerPlan:
    candidates: tuple[ScheduledNode, ...]
    reason: StopReason
    graph_revision: int
    blocked_nodes: tuple[tuple[str, tuple[str, ...]], ...] = ()


class HybridScheduler:
    """Plan eligible nodes without introducing a second queue or budget ledger.

    Nodes may run in different scientific phases at the same time. Candidates
    are sorted by descending explicit priority, phase policy, smallest reserve,
    then node ID. The budget snapshot is advisory planning input; actual
    reservations still occur atomically in ``DurableWorkerQueue.claim``.
    """

    def __init__(self, graph: GraphRepository):
        self.graph = graph

    def mark_ready(self, node_id: str, *, expected_node_revision: int | None = None,
                   expected_phase: str | None = None) -> dict[str, Any]:
        """Durably mark one currently eligible node ready for queue admission."""
        snapshot = self.graph.scheduling_snapshot()
        matches = [node for node in snapshot["nodes"] if node["node_id"] == node_id]
        if not matches:
            raise GraphStoreError(f"unknown scheduler node {node_id!r}")
        node = matches[0]
        if expected_node_revision is not None and int(node["revision"]) != expected_node_revision:
            raise GraphStoreError("node changed while the scheduler was preparing it")
        if expected_phase is not None and node["phase"] != expected_phase:
            raise GraphStoreError("node phase changed while the scheduler was preparing it")
        if node["status"] == "READY":
            if not node["ready"]:
                raise GraphStoreError("READY node is blocked by graph prerequisites")
            return {"graph_revision": snapshot["graph_revision"], "node_revision": node["revision"],
                    "status": "READY", "replayed": True}
        if node["status"] != "PENDING" or not node["ready"]:
            raise GraphStoreError("only an unblocked PENDING node can become READY")
        command_digest = hashlib.sha256(f"{node_id}\0{node['revision']}\0ready".encode()).hexdigest()[:32]
        request = {
            "command_id": f"scheduler-ready-{command_digest}",
            "idempotency_key": f"scheduler-ready-{command_digest}",
            "expected_graph_revision": snapshot["graph_revision"],
            "changes": [{"type": "TransitionNodeStatus", "node_id": node_id,
                         "expected_node_revision": node["revision"], "expected_status": "PENDING",
                         "to_status": "READY", "guard": "SCHEDULER_READY", "actor": "CORE"}],
        }
        response = self.graph.mutate(request)
        row = self.graph.conn.execute("SELECT revision FROM research_nodes WHERE node_id=?", (node_id,)).fetchone()
        return {"graph_revision": response["graph_revision"], "node_revision": int(row[0]),
                "status": "READY", "replayed": False}

    def plan(self, *, budget: dict[str, dict[str, int]], active_node_ids: set[str] | None = None,
             max_candidates: int | None = None, stop_requested: bool = False) -> SchedulerPlan:
        if not isinstance(budget, dict):
            raise GraphStoreError("scheduler requires a budget snapshot")
        if max_candidates is not None and (isinstance(max_candidates, bool) or not isinstance(max_candidates, int)
                                           or max_candidates < 1):
            raise GraphStoreError("max_candidates must be a positive integer")
        active = active_node_ids or set()
        if any(not isinstance(item, str) or not item for item in active):
            raise GraphStoreError("active node IDs must be nonempty strings")
        snapshot = self.graph.scheduling_snapshot()
        revision = int(snapshot["graph_revision"])
        if stop_requested:
            return SchedulerPlan((), "STOP_REQUESTED", revision)

        candidates: list[ScheduledNode] = []
        blocked: list[tuple[str, tuple[str, ...]]] = []
        remaining_budget: dict[str, int] = {}
        budget_blocked = False
        pending: list[dict[str, Any]] = []
        for node in snapshot["nodes"]:
            if node["status"] not in {"PENDING", "READY"}:
                continue
            pending.append(node)
            if node["node_id"] in active:
                continue
            if node["phase"] == "CONSOLIDATION":
                blocked.append((node["node_id"], ("core-consolidation-required",)))
                continue
            if not node["ready"]:
                blocked.append((node["node_id"], tuple(node["blockers"])))
                continue
            lane, resource = _LANES[node["phase"]]
            priority, upper_bound = self._hints(node["payload"])
            account = budget.get(resource)
            if not isinstance(account, dict):
                budget_blocked = True
                continue
            cap, spent, reserved, unknown = (account.get(key) for key in ("cap", "spent", "reserved", "unknown"))
            values = (cap, spent, reserved, unknown)
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
                raise GraphStoreError(f"invalid {resource} budget snapshot")
            remaining_budget.setdefault(resource, cap - spent - reserved - unknown)
            candidates.append(ScheduledNode(node_id=node["node_id"], phase=node["phase"], lane=lane,
                                            resource=resource, upper_bound=upper_bound, priority=priority,
                                            node_revision=int(node["revision"]), graph_revision=revision))

        candidates.sort(key=lambda item: (-item.priority, _PHASE_ORDER[item.phase], item.upper_bound, item.node_id))
        selected: list[ScheduledNode] = []
        for candidate in candidates:
            available = remaining_budget[candidate.resource]
            if candidate.upper_bound > available:
                budget_blocked = True
                continue
            selected.append(candidate)
            remaining_budget[candidate.resource] = available - candidate.upper_bound
        if max_candidates is not None:
            selected = selected[:max_candidates]
        candidates = selected
        if candidates:
            reason: StopReason = "CONTINUE"
        elif active:
            reason = "WAITING_FOR_WORKERS"
        elif budget_blocked:
            reason = "BUDGET_EXHAUSTED"
        elif blocked or pending:
            reason = "DEPENDENCIES_BLOCKED"
        elif snapshot["nodes"] and all(node["status"] == "COMPLETED" for node in snapshot["nodes"]):
            reason = "ALL_NODES_COMPLETE"
        else:
            reason = "NO_REMAINING_WORK"
        return SchedulerPlan(tuple(candidates), reason, revision, tuple(blocked))

    @staticmethod
    def _hints(payload: dict[str, Any]) -> tuple[int, int]:
        hints = payload.get("scheduler", {})
        if not isinstance(hints, dict) or set(hints) - {"priority", "upper_bound"}:
            raise GraphStoreError("node scheduler hints must contain only priority and upper_bound")
        priority = hints.get("priority", 0)
        upper_bound = hints.get("upper_bound", 1)
        if isinstance(priority, bool) or not isinstance(priority, int) or not -1000 <= priority <= 1000:
            raise GraphStoreError("node scheduler priority must be an integer from -1000 to 1000")
        if isinstance(upper_bound, bool) or not isinstance(upper_bound, int) or not 1 <= upper_bound <= 1_000_000:
            raise GraphStoreError("node scheduler upper_bound must be an integer from 1 to 1000000")
        return priority, upper_bound
