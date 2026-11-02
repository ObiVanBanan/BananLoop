import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import (
    ContractError, claim_job, complete_exploration, complete_job, connect, create_child_saga, enqueue_job, init_campaign,
    init_parent, init_registry, mark_dispatched, mutate_graph, reconcile_child_saga,
    recover_expired_jobs, record_unknown, validate_spec,
)
from migration import migrate_v4_to_v5


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

    def rows(self, path, table):
        db = connect(path)
        try:
            return [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        finally:
            db.close()

    def make_v4_fixture(self, path):
        """Build a representative v4 database using the real legacy store."""
        from bananloop.contracts import AgentConfig, CampaignSpec, Direction, MetricSpec
        from bananloop.store import CampaignStore

        store = CampaignStore(path)
        try:
            store.initialize(CampaignSpec(
                goal="preserve legacy v4 campaign",
                source_repo=str(self.root),
                base_commit="legacy-base",
                evaluation_command=["python", "evaluator.py"],
                primary_metric=MetricSpec(
                    name="quality", direction=Direction.MAXIMIZE, target=1.0, min_improvement=0.1,
                ),
                agent=AgentConfig(kind="codex"),
            ))
            store.save_evaluator_binding("legacy-v1", "legacy-protocol-sha256", '{"protocol":"legacy-v1"}')
            store.start_action("legacy-action", None, "BASELINE", "evaluator", "legacy-request-hash")
            store.finish_action("legacy-action", {
                "status": "ok",
                "_evaluation_receipt": {"receipt_hash": "legacy-receipt-sha256", "score": 0.75},
            })
            campaign = store.campaign()
            return {
                "spec_hash": campaign["spec_hash"],
                "binding_hash": store.evaluator_binding()["binding_hash"],
                "rows": {
                    table: [tuple(row) for row in store.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("campaigns", "evaluator_bindings", "evaluation_receipts", "actions",
                                  "reservations", "events", "experiments", "attempts", "evaluations", "decisions")
                },
            }
        finally:
            store.close()

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

    def test_expired_pre_dispatch_lease_releases_once_and_reclaims_with_new_fence(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        enqueue_job(dbpath, "j1", "n1", {"prompt": "frozen"})
        first = claim_job(dbpath, "j1", reserve=8, lease_seconds=5, now=1000)
        with self.assertRaisesRegex(ContractError, "expired"):
            mark_dispatched(dbpath, "j1", first["fence"], now=1005)
        self.assertEqual(recover_expired_jobs(dbpath, now=1005), {"reclaimed": 1, "unknown": 0})
        self.assertEqual(recover_expired_jobs(dbpath, now=1005), {"reclaimed": 0, "unknown": 0})
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT state FROM reservations WHERE action_id=?", (first["action_id"],)), "RELEASED")
        self.assertEqual(self.scalar(dbpath, "SELECT fence FROM jobs WHERE job_id='j1'"), first["fence"] + 1)
        with self.assertRaisesRegex(ContractError, "unexpired dispatched lease"):
            complete_job(dbpath, "j1", first["fence"], output_digest="sha256:stale", actual_amount=1, now=1005)
        second = claim_job(dbpath, "j1", reserve=6, lease_seconds=5, now=1006)
        self.assertGreater(second["fence"], first["fence"])
        self.assertNotEqual(second["action_id"], first["action_id"])
        with self.assertRaisesRegex(ContractError, "stale or expired"):
            mark_dispatched(dbpath, "j1", first["fence"], now=1006)
        mark_dispatched(dbpath, "j1", second["fence"], now=1007)
        with self.assertRaisesRegex(ContractError, "unexpired dispatched lease"):
            complete_job(dbpath, "j1", first["fence"], output_digest="sha256:stale", actual_amount=1, now=1007)
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 6)

    def test_expired_dispatched_lease_recovers_to_unknown_without_retry(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        enqueue_job(dbpath, "j1", "n1", {"prompt": "frozen"})
        lease = claim_job(dbpath, "j1", reserve=8, lease_seconds=5, now=1000)
        mark_dispatched(dbpath, "j1", lease["fence"], now=1001)
        with self.assertRaisesRegex(ContractError, "unexpired dispatched lease"):
            complete_job(dbpath, "j1", lease["fence"], output_digest="sha256:late", actual_amount=4, now=1005)
        self.assertEqual(recover_expired_jobs(dbpath, now=1005), {"reclaimed": 0, "unknown": 1})
        self.assertEqual(recover_expired_jobs(dbpath, now=1006), {"reclaimed": 0, "unknown": 0})
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs WHERE job_id='j1'"), "UNKNOWN")
        self.assertEqual(self.scalar(dbpath, "SELECT unknown FROM budget"), 8)
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        with self.assertRaisesRegex(ContractError, "not claimable"):
            claim_job(dbpath, "j1", reserve=1, now=1006)
        with self.assertRaisesRegex(ContractError, "unexpired dispatched lease"):
            complete_job(dbpath, "j1", lease["fence"], output_digest="sha256:late", actual_amount=4, now=1006)

    def test_result_commit_is_fenced_and_budget_settlement_is_idempotent(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        enqueue_job(dbpath, "j1", "n1", {"prompt": "frozen"})
        lease = claim_job(dbpath, "j1", reserve=8, lease_seconds=10, now=1000)
        mark_dispatched(dbpath, "j1", lease["fence"], now=1001)
        result = complete_job(dbpath, "j1", lease["fence"], output_digest="sha256:result", actual_amount=3, now=1002)
        self.assertEqual(complete_job(dbpath, "j1", lease["fence"], output_digest="sha256:result", actual_amount=3, now=1003), result)
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs WHERE job_id='j1'"), "COMPLETED")
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT spent FROM budget"), 3)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events WHERE kind='JOB_COMPLETED'"), 1)
        with self.assertRaisesRegex(ContractError, "different committed result"):
            complete_job(dbpath, "j1", lease["fence"], output_digest="sha256:other", actual_amount=3, now=1003)

    def test_budget_invariant_failure_rolls_back_unknown_transition(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        enqueue_job(dbpath, "j1", "n1", {"prompt": "frozen"})
        lease = claim_job(dbpath, "j1", reserve=8, lease_seconds=10, now=1000)
        mark_dispatched(dbpath, "j1", lease["fence"], now=1001)
        db = connect(dbpath)
        try:
            db.execute("UPDATE budget SET reserved=0 WHERE id=1")
        finally:
            db.close()
        events_before = self.scalar(dbpath, "SELECT COUNT(*) FROM events")
        with self.assertRaisesRegex(ContractError, "cannot cover dispatched reservation"):
            record_unknown(dbpath, "j1", lease["fence"])
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM jobs WHERE job_id='j1'"), "DISPATCHED")
        self.assertEqual(self.scalar(dbpath, "SELECT state FROM reservations WHERE action_id=?", (lease["action_id"],)), "DISPATCHED")
        self.assertEqual(self.scalar(dbpath, "SELECT reserved FROM budget"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), events_before)

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

    def test_v4_migration_adds_v5_schema_and_preserves_legacy_hashes_and_receipts(self):
        dbpath, backup = self.root / "legacy-v4.db", self.root / "legacy-v4.backup.db"
        original = self.make_v4_fixture(dbpath)
        migrate_v4_to_v5(dbpath, backup)
        self.assertEqual(self.scalar(dbpath, "PRAGMA user_version"), 5)
        self.assertEqual(self.scalar(dbpath, "PRAGMA integrity_check"), "ok")
        for table, expected_rows in original["rows"].items():
            self.assertEqual(self.rows(dbpath, table), expected_rows, table)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM pragma_foreign_key_check"), 0)
        self.assertEqual(self.scalar(backup, "PRAGMA user_version"), 4)
        for table, expected_rows in original["rows"].items():
            self.assertEqual(self.rows(backup, table), expected_rows, f"backup {table}")
        db = connect(dbpath)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO research_edges VALUES('missing-src','missing-dst','REQUIRES')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO jobs VALUES('invalid-job','missing-node','READY',0,'hash')")
        finally:
            db.close()
        from bananloop.store import CampaignStore, StoreError
        with self.assertRaisesRegex(StoreError, "newer than this BananLoop version"):
            CampaignStore(dbpath)

    def test_v4_migration_rolls_back_partial_schema_on_injected_crash(self):
        dbpath, backup = self.root / "legacy-v4.db", self.root / "legacy-v4.backup.db"
        original = self.make_v4_fixture(dbpath)
        with self.assertRaisesRegex(RuntimeError, "injected migration crash"):
            migrate_v4_to_v5(dbpath, backup, fail_after_schema=True)
        self.assertEqual(self.scalar(dbpath, "PRAGMA user_version"), 4)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='graph_meta'"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT spec_hash FROM campaigns WHERE id=1"), original["spec_hash"])
        self.assertEqual(self.scalar(backup, "PRAGMA user_version"), 4)

    def test_v4_migration_never_overwrites_existing_backup(self):
        dbpath, backup = self.root / "legacy-v4.db", self.root / "legacy-v4.backup.db"
        self.make_v4_fixture(dbpath)
        backup.write_bytes(b"preserve prior backup")
        with self.assertRaisesRegex(ContractError, "prior backup"):
            migrate_v4_to_v5(dbpath, backup)
        self.assertEqual(backup.read_bytes(), b"preserve prior backup")
        self.assertEqual(self.scalar(dbpath, "PRAGMA user_version"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
