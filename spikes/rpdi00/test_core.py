import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import (
    ContractError, claim_job, complete_exploration, connect, create_child_saga, enqueue_job, init_campaign,
    init_parent, init_registry, mark_dispatched, mutate_graph, reconcile_child_saga,
    record_unknown, validate_spec,
)


class RPDI00ContractSpike(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def scalar(self, path, sql, args=()):
        db = connect(path)
        try:
            return db.execute(sql, args).fetchone()[0]
        finally:
            db.close()

    def test_graph_state_command_and_event_commit_atomically(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath)
        mutate_graph(dbpath, command_id="c1", idempotency_key="k1", expected_revision=0,
                     changes=[{"type": "AddNode", "node_id": "a"}, {"type": "AddNode", "node_id": "b"}])
        before = (self.scalar(dbpath, "SELECT revision FROM graph_meta"), self.scalar(dbpath, "SELECT COUNT(*) FROM events"))
        with self.assertRaisesRegex(ContractError, "acyclic"):
            mutate_graph(dbpath, command_id="c2", idempotency_key="k2", expected_revision=1,
                         changes=[{"type": "AddEdge", "src": "a", "dst": "b", "kind": "REQUIRES"},
                                  {"type": "AddEdge", "src": "b", "dst": "a", "kind": "REQUIRES"}])
        after = (self.scalar(dbpath, "SELECT revision FROM graph_meta"), self.scalar(dbpath, "SELECT COUNT(*) FROM events"))
        self.assertEqual(after, before)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM edges"), 0)

    def test_graph_idempotency_and_revision_conflict(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath)
        changes = [{"type": "AddNode", "node_id": "n1"}]
        first = mutate_graph(dbpath, command_id="c1", idempotency_key="same", expected_revision=0, changes=changes)
        self.assertEqual(mutate_graph(dbpath, command_id="c1-retry", idempotency_key="same", expected_revision=0, changes=changes), first)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), 1)
        with self.assertRaisesRegex(ContractError, "different canonical payload"):
            mutate_graph(dbpath, command_id="c2", idempotency_key="same", expected_revision=0,
                         changes=[{"type": "AddNode", "node_id": "n2"}])
        with self.assertRaisesRegex(ContractError, "command ID reused"):
            mutate_graph(dbpath, command_id="c1", idempotency_key="another", expected_revision=0,
                         changes=[{"type": "AddNode", "node_id": "n2"}])
        with self.assertRaisesRegex(ContractError, "current revision"):
            mutate_graph(dbpath, command_id="c3", idempotency_key="new", expected_revision=0,
                         changes=[{"type": "AddNode", "node_id": "n3"}])

    def test_job_reservation_and_lease_are_atomic_and_unknown_is_held(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        enqueue_job(dbpath, "j1", "n1", {"prompt": "frozen"})
        with self.assertRaisesRegex(RuntimeError, "before claim commit"):
            claim_job(dbpath, "j1", reserve=8, fail_before_commit=True)
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs"), "READY")
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM reservations"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), 0)
        lease = claim_job(dbpath, "j1", reserve=8)
        self.assertEqual(lease["fence"], 1)
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 8)
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs"), "LEASED")
        mark_dispatched(dbpath, "j1", lease["fence"])
        record_unknown(dbpath, "j1", lease["fence"])
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT unknown FROM budget"), 8)
        enqueue_job(dbpath, "j2", "n2", {"prompt": "other"})
        with self.assertRaisesRegex(ContractError, "exceeds campaign cap"):
            claim_job(dbpath, "j2", reserve=3)
        with self.assertRaisesRegex(ContractError, "must be positive"):
            claim_job(dbpath, "j2", reserve=-1)
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs WHERE job_id='j2'"), "READY")
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM reservations"), 1)

    def test_parallel_job_claims_never_over_reserve(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        for i in range(4):
            enqueue_job(dbpath, f"j{i}", f"n{i}", {"i": i})

        def claim(i):
            try:
                claim_job(dbpath, f"j{i}", 4)
                return True
            except ContractError as exc:
                self.assertEqual(exc.code, "BUDGET_EXHAUSTED")
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))
        self.assertEqual(sum(results), 2)
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 8)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM reservations"), 2)

    def test_exploration_spec_is_valid_without_synthetic_evaluator(self):
        spec = {"schema_version": 2, "mode": "EXPLORATION", "goal": "explain behavior", "evaluation": None}
        validate_spec(spec)
        package = complete_exploration(spec, node_id="n1", evidence_refs=["ev1"],
                                      artifact_digests=["sha256:abc"], limitations=["small sample"],
                                      next_experiment="repeat with control")
        self.assertEqual(package["phase"], "CONSOLIDATION")
        self.assertEqual(package["protocol_id"], None)
        self.assertNotIn("reward", package)
        with self.assertRaisesRegex(ContractError, "evidence, artifact digests"):
            complete_exploration(spec, node_id="n3", evidence_refs=[], artifact_digests=[],
                                 limitations=[], next_experiment="")
        with self.assertRaisesRegex(ContractError, "requires an exploration spec"):
            complete_exploration({"schema_version": 2, "mode": "OPTIMIZATION", "goal": "improve",
                                  "evaluation": {"protocol_id": "p1"}}, node_id="n2", evidence_refs=["ev1"],
                                 artifact_digests=["sha256:abc"], limitations=[], next_experiment="next")
        with self.assertRaisesRegex(ContractError, "requires a frozen evaluator"):
            validate_spec({"schema_version": 2, "mode": "OPTIMIZATION", "goal": "improve", "evaluation": None})
        with self.assertRaisesRegex(ContractError, "does not accept an implicit score"):
            validate_spec({"schema_version": 2, "mode": "EXPLORATION", "goal": "study", "evaluation": {"reward": 0}})

    def test_child_escrow_saga_recovers_after_intermediate_crash(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12, crash_after_child_init=True)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RESERVED")
        reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(parent, "SELECT COUNT(*) FROM child_allocations"), 1)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "ACTIVE")
        self.assertEqual(self.scalar(registry, "SELECT COUNT(*) FROM children"), 1)
        self.assertEqual(self.scalar(registry, "SELECT state FROM children"), "ACTIVE")
        with self.assertRaisesRegex(ContractError, "exceeds root cap"):
            create_child_saga(parent, registry, self.root / "child2.db", "alloc2", "child2", 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
