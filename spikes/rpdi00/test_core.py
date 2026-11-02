import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import (
    ContractError, approve_child_creation, canonical_hash, claim_job, child_admission_ready,
    complete_exploration, complete_job, connect, persist_exploration_completion,
    create_child_saga, enqueue_job, init_campaign,
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

    def make_child_spec(self, goal):
        scope = {"allowed_actions": ["research"], "max_parallel": 1}
        digest = canonical_hash(scope)
        spec = {"schema_version": 2, "mode": "EXPLORATION", "goal": goal,
                "autonomy": {"mode": "SUPERVISED", "scope_digest": digest, "scope_version": 1}}
        return scope, digest, spec

    def approve_child(self, parent, allocation_id, child_path, child_id, amount, parent_id, scope, spec):
        return approve_child_creation(parent, allocation_id, parent_campaign_id=parent_id,
                                      child_id=child_id, child_path=child_path, amount=amount,
                                      approved_scope=scope, child_spec=spec, approved_by="trusted-parent-core")

    def test_init_campaign_upgrades_legacy_nodes_without_losing_payload(self):
        dbpath = self.root / "legacy-nodes.db"
        db = sqlite3.connect(dbpath)
        try:
            db.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT INTO nodes VALUES('legacy-node','{\"kept\":true}')")
            db.commit()
        finally:
            db.close()
        init_campaign(dbpath)
        db = connect(dbpath)
        try:
            row = db.execute("SELECT node_id,payload,phase,status,revision FROM nodes WHERE node_id='legacy-node'").fetchone()
            self.assertEqual(tuple(row), ("legacy-node", '{"kept":true}', "EXPLORATION", "READY", 0))
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
        first = mutate_graph(dbpath, command_id="c1", idempotency_key="same", expected_revision=0,
                             changes=changes, evidence_refs=["evidence:b", "evidence:a", "evidence:a"])
        self.assertEqual(first["evidence_refs"], ["evidence:a", "evidence:b"])
        self.assertEqual(mutate_graph(dbpath, command_id="c1-retry", idempotency_key="same", expected_revision=0,
                                      changes=changes, evidence_refs=["evidence:a", "evidence:b"]), first)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), 1)
        self.assertEqual(json.loads(self.scalar(dbpath, "SELECT payload FROM events"))["evidence_refs"], first["evidence_refs"])
        with self.assertRaisesRegex(ContractError, "different canonical payload"):
            mutate_graph(dbpath, command_id="c2-evidence", idempotency_key="same", expected_revision=0,
                         changes=changes, evidence_refs=["evidence:c"])
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

    def test_exploration_completion_persists_fenced_provenance_and_is_idempotent(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath, budget_cap=10)
        mutate_graph(dbpath, command_id="node", idempotency_key="node-key", expected_revision=0,
                     changes=[{"type": "AddNode", "node_id": "n1"}])
        enqueue_job(dbpath, "job1", "n1", {"prompt": "frozen evidence task"})
        lease = claim_job(dbpath, "job1", reserve=3, now=100)
        mark_dispatched(dbpath, "job1", lease["fence"], now=101)
        complete_job(dbpath, "job1", lease["fence"], output_digest="sha256:artifact-1",
                     actual_amount=2, now=102)
        enqueue_job(dbpath, "job2", "n1", {"prompt": "same artifact from second evidence job"})
        lease2 = claim_job(dbpath, "job2", reserve=3, now=110)
        mark_dispatched(dbpath, "job2", lease2["fence"], now=111)
        complete_job(dbpath, "job2", lease2["fence"], output_digest="sha256:artifact-1",
                     actual_amount=2, now=112)
        spec = {"schema_version": 2, "mode": "EXPLORATION", "goal": "explain behavior", "evaluation": None}
        args = {"completion_id": "complete1", "idempotency_key": "complete-key",
                "expected_graph_revision": 1, "node_id": "n1", "evidence_refs": ["job2", "job1"],
                "artifact_digests": ["sha256:artifact-1"], "limitations": ["single run"],
                "next_experiment": "repeat with control"}
        with self.assertRaisesRegex(RuntimeError, "before commit"):
            persist_exploration_completion(dbpath, spec, **args, fail_before_commit=True)
        self.assertEqual(self.scalar(dbpath, "SELECT revision FROM graph_meta"), 1)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM exploration_completions"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM completion_evidence"), 0)
        event_count = self.scalar(dbpath, "SELECT COUNT(*) FROM events")

        package = persist_exploration_completion(dbpath, spec, **args)
        self.assertEqual(package["phase"], "CONSOLIDATION")
        self.assertEqual(package["protocol_id"], None)
        self.assertNotIn("reward", package)
        self.assertEqual(package["source_graph_revision"], 1)
        self.assertEqual(package["graph_revision"], 2)
        self.assertEqual(package["evidence_refs"], ["job1", "job2"])
        replay_args = {**args, "completion_id": "alternate-id-on-replay"}
        self.assertEqual(persist_exploration_completion(dbpath, spec, **replay_args), package)
        self.assertEqual(self.scalar(dbpath, "SELECT revision FROM graph_meta"), 2)
        self.assertEqual(self.scalar(dbpath, "SELECT phase FROM nodes WHERE node_id='n1'"), "CONSOLIDATION")
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM nodes WHERE node_id='n1'"), "COMPLETED")
        self.assertEqual(self.scalar(dbpath, "SELECT revision FROM nodes WHERE node_id='n1'"), 1)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM exploration_completions"), 1)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM completion_evidence"), 2)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), event_count + 1)
        self.assertEqual(self.scalar(dbpath, "SELECT source_request_hash FROM completion_evidence"), lease["request_hash"])
        db = connect(dbpath)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("UPDATE exploration_completions SET package_json='{}'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("DELETE FROM completion_evidence")
        finally:
            db.close()

    def test_exploration_completion_rejects_unverified_evidence_without_mutation(self):
        dbpath = self.root / "campaign.db"
        init_campaign(dbpath)
        mutate_graph(dbpath, command_id="node", idempotency_key="node-key", expected_revision=0,
                     changes=[{"type": "AddNode", "node_id": "n1"}])
        spec = {"schema_version": 2, "mode": "EXPLORATION", "goal": "explain behavior", "evaluation": None}
        with self.assertRaisesRegex(ContractError, "not a current completed job"):
            persist_exploration_completion(
                dbpath, spec, completion_id="complete1", idempotency_key="complete-key",
                expected_graph_revision=1, node_id="n1", evidence_refs=["fabricated-job"],
                artifact_digests=["sha256:fabricated"], limitations=[], next_experiment="repeat")
        self.assertEqual(self.scalar(dbpath, "SELECT revision FROM graph_meta"), 1)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM exploration_completions"), 0)
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM events"), 1)
        enqueue_job(dbpath, "job1", "n1", {"prompt": "valid evidence"})
        lease = claim_job(dbpath, "job1", reserve=1, now=100)
        mark_dispatched(dbpath, "job1", lease["fence"], now=101)
        complete_job(dbpath, "job1", lease["fence"], output_digest="sha256:actual",
                     actual_amount=1, now=102)
        with self.assertRaisesRegex(ContractError, "must match the referenced job results"):
            persist_exploration_completion(
                dbpath, spec, completion_id="complete2", idempotency_key="different-key",
                expected_graph_revision=1, node_id="n1", evidence_refs=["job1"],
                artifact_digests=["sha256:forged"], limitations=[], next_experiment="repeat")
        self.assertEqual(self.scalar(dbpath, "SELECT revision FROM graph_meta"), 1)
        self.assertEqual(self.scalar(dbpath, "SELECT status FROM nodes WHERE node_id='n1'"), "READY")
        self.assertEqual(self.scalar(dbpath, "SELECT COUNT(*) FROM exploration_completions"), 0)

    def test_child_escrow_saga_recovers_after_intermediate_crash(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("child scope")
        child_args = {"parent_campaign_id": "parent-1", "approved_scope_digest": digest,
                      "approved_scope": scope, "child_spec": spec}
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        with self.assertRaisesRegex(RuntimeError, "injected child saga crash"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12, **child_args, crash_at="after_child_init")
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "REGISTRY_INITIALIZING")
        reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12, **child_args)
        reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12, **child_args)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(parent, "SELECT COUNT(*) FROM child_allocations"), 1)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "ACTIVE")
        self.assertEqual(self.scalar(registry, "SELECT COUNT(*) FROM children"), 1)
        self.assertEqual(self.scalar(registry, "SELECT state FROM children"), "ACTIVE")
        self.assertEqual(self.scalar(child, "SELECT approved_scope_digest FROM campaign_lineage WHERE allocation_id='alloc1'"), digest)
        self.assertEqual(self.scalar(child, "SELECT child_spec_json FROM campaign_lineage WHERE allocation_id='alloc1'"),
                         json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        with self.assertRaisesRegex(ContractError, "exceeds root cap"):
            scope2, digest2, spec2 = self.make_child_spec("too much")
            self.approve_child(parent, "alloc2", self.root / "child2.db", "child2", 9, "parent-1", scope2, spec2)
            create_child_saga(parent, registry, self.root / "child2.db", "alloc2", "child2", 9,
                              parent_campaign_id="parent-1", approved_scope_digest=digest2,
                              approved_scope=scope2, child_spec=spec2)

    def test_child_escrow_saga_recovers_at_each_committed_boundary(self):
        for crash_at in ("after_reserve", "after_registry", "during_child_init", "after_campaign_init",
                         "after_child_init", "after_registry_active", "after_parent_active"):
            with self.subTest(crash_at=crash_at):
                parent, registry, child = (self.root / f"{crash_at}-parent.db", self.root / f"{crash_at}-registry.db",
                                           self.root / f"{crash_at}-child.db")
                init_parent(parent, cap=20)
                init_registry(registry)
                scope, digest, spec = self.make_child_spec("recover")
                args = {"parent_campaign_id": "parent-2", "approved_scope_digest": digest,
                        "approved_scope": scope, "child_spec": spec}
                self.approve_child(parent, "allocation", child, "child", 12, "parent-2", scope, spec)
                with self.assertRaisesRegex(RuntimeError, "injected child saga crash"):
                    create_child_saga(parent, registry, child, "allocation", "child", 12, **args, crash_at=crash_at)
                if crash_at == "during_child_init":
                    self.assertEqual(self.scalar(child, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"), 0)
                self.assertEqual(child_admission_ready(parent, registry, child, "allocation"),
                                 crash_at == "after_parent_active")
                reconcile_child_saga(parent, registry, child, "allocation", "child", 12, **args)
                reconcile_child_saga(parent, registry, child, "allocation", "child", 12, **args)
                self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
                self.assertEqual(self.scalar(parent, "SELECT COUNT(*) FROM child_allocations"), 1)
                self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "ACTIVE")
                self.assertEqual(self.scalar(registry, "SELECT COUNT(*) FROM children"), 1)
                self.assertEqual(self.scalar(registry, "SELECT state FROM children"), "ACTIVE")
                self.assertEqual(self.scalar(child, "SELECT COUNT(*) FROM campaign_lineage"), 1)
                self.assertTrue(child_admission_ready(parent, registry, child, "allocation"))

    def test_child_scope_conflict_fails_closed_without_refunding_escrow(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("immutable")
        args = {"parent_campaign_id": "parent-3", "approved_scope_digest": digest,
                "approved_scope": scope, "child_spec": spec}
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-3", scope, spec)
        create_child_saga(parent, registry, child, "alloc1", "child1", 12, **args)
        changed_scope = {"allowed_actions": ["research", "write"], "max_parallel": 1}
        changed_digest = canonical_hash(changed_scope)
        changed_spec = {**spec, "autonomy": {**spec["autonomy"], "scope_digest": changed_digest}}
        with self.assertRaisesRegex(ContractError, "durable parent approval"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              **{**args, "approved_scope_digest": changed_digest,
                                 "approved_scope": changed_scope, "child_spec": changed_spec})
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(child, "SELECT approved_scope_digest FROM campaign_lineage"), digest)
        db = connect(child)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("UPDATE campaign_lineage SET approved_scope_digest='changed'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("DELETE FROM campaign_lineage")
        finally:
            db.close()

    def test_child_scope_digest_must_match_spec_before_escrow(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("scope mismatch")
        spec["autonomy"]["scope_digest"] = "sha256:unrelated"
        with self.assertRaisesRegex(ContractError, "must match"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              parent_campaign_id="parent-1", approved_scope_digest=digest,
                              approved_scope=scope, child_spec=spec)
        broad_scope = {"allowed_actions": ["research", "write"], "max_parallel": 10}
        broad_digest = canonical_hash(broad_scope)
        broad_spec = {"schema_version": 2, "mode": "EXPLORATION", "goal": "broadened",
                      "autonomy": {"mode": "FULL_AUTO", "scope_digest": broad_digest, "scope_version": 1}}
        with self.assertRaisesRegex(ContractError, "no durable parent approval"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              parent_campaign_id="parent-1", approved_scope_digest=broad_digest,
                              approved_scope=broad_scope, child_spec=broad_spec)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 0)
        self.assertEqual(self.scalar(parent, "SELECT COUNT(*) FROM child_allocations"), 0)

    def test_child_registry_conflict_quarantines_reserved_allocation(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("registry conflict")
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        db = connect(registry)
        try:
            db.execute("INSERT INTO children VALUES('child1','other-allocation','other-parent','/elsewhere/child.db',5,'scope:other','spec:other','INITIALIZING')")
        finally:
            db.close()
        with self.assertRaisesRegex(ContractError, "different immutable identity"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              parent_campaign_id="parent-1", approved_scope_digest=digest,
                              approved_scope=scope, child_spec=spec)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RECONCILIATION_REQUIRED")
        with self.assertRaisesRegex(ContractError, "operator reconciliation"):
            reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12,
                                 parent_campaign_id="parent-1", approved_scope_digest=digest,
                                 approved_scope=scope, child_spec=spec)
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)

    def test_unreadable_child_database_is_quarantined_without_refund(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        child.write_bytes(b"not a sqlite database")
        scope, digest, spec = self.make_child_spec("corrupt child")
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        with self.assertRaisesRegex(ContractError, "unreadable"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              parent_campaign_id="parent-1", approved_scope_digest=digest,
                              approved_scope=scope, child_spec=spec)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RECONCILIATION_REQUIRED")
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)

    def test_existing_same_cap_database_without_matching_lineage_is_quarantined(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        init_campaign(child, budget_cap=12)
        scope, digest, spec = self.make_child_spec("do not adopt existing campaign")
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        with self.assertRaisesRegex(ContractError, "refusing to attach lineage"):
            create_child_saga(parent, registry, child, "alloc1", "child1", 12,
                              parent_campaign_id="parent-1", approved_scope_digest=digest,
                              approved_scope=scope, child_spec=spec)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RECONCILIATION_REQUIRED")
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)

    def test_child_database_with_extra_lineage_is_quarantined(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("single lineage")
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        args = {"parent_campaign_id": "parent-1", "approved_scope_digest": digest,
                "approved_scope": scope, "child_spec": spec}
        create_child_saga(parent, registry, child, "alloc1", "child1", 12, **args)
        db = connect(child)
        try:
            db.execute("INSERT INTO campaign_lineage VALUES(?,?,?,?,?,?,?,?)",
                       ("foreign-allocation", "child-x", "parent-x", 1, "d", "{}", "{}", "d"))
        finally:
            db.close()
        self.assertFalse(child_admission_ready(parent, registry, child, "alloc1"))
        with self.assertRaisesRegex(ContractError, "exactly one lineage"):
            reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12, **args)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RECONCILIATION_REQUIRED")
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)

    def test_preexisting_noop_lineage_trigger_is_rejected(self):
        parent, registry, child = (self.root / "parent.db", self.root / "registry.db", self.root / "child.db")
        init_parent(parent, cap=20)
        init_registry(registry)
        scope, digest, spec = self.make_child_spec("trigger tamper")
        self.approve_child(parent, "alloc1", child, "child1", 12, "parent-1", scope, spec)
        args = {"parent_campaign_id": "parent-1", "approved_scope_digest": digest,
                "approved_scope": scope, "child_spec": spec}
        create_child_saga(parent, registry, child, "alloc1", "child1", 12, **args)
        db = connect(child)
        try:
            db.execute("DROP TRIGGER campaign_lineage_no_update")
            db.execute("CREATE TRIGGER campaign_lineage_no_update BEFORE UPDATE ON campaign_lineage BEGIN SELECT 1; END")
        finally:
            db.close()
        with self.assertRaisesRegex(ContractError, "incompatible campaign_lineage_no_update"):
            reconcile_child_saga(parent, registry, child, "alloc1", "child1", 12, **args)
        self.assertEqual(self.scalar(parent, "SELECT state FROM child_allocations"), "RECONCILIATION_REQUIRED")
        self.assertEqual(self.scalar(parent, "SELECT escrowed FROM root_budget"), 12)

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

    def test_v4_migration_backup_snapshot_precedes_blocked_concurrent_writer(self):
        dbpath, backup = self.root / "legacy-v4.db", self.root / "legacy-v4.backup.db"
        original = self.make_v4_fixture(dbpath)
        self.assertEqual(self.scalar(dbpath, "PRAGMA journal_mode"), "wal")
        lock_acquired, continue_migration = threading.Event(), threading.Event()
        writer_started, writer_finished = threading.Event(), threading.Event()

        def hold_after_writer_lock():
            lock_acquired.set()
            if not continue_migration.wait(timeout=10):
                raise TimeoutError("test did not release migration writer lock")

        def competing_writer():
            writer = sqlite3.connect(dbpath, timeout=10, isolation_level=None)
            try:
                writer_started.set()
                writer.execute("UPDATE campaigns SET spec_hash='committed-after-backup' WHERE id=1")
                writer_finished.set()
            finally:
                writer.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            migration = pool.submit(migrate_v4_to_v5, dbpath, backup, after_writer_lock=hold_after_writer_lock)
            self.assertTrue(lock_acquired.wait(timeout=5))
            writer = pool.submit(competing_writer)
            self.assertTrue(writer_started.wait(timeout=5))
            self.assertFalse(writer_finished.wait(timeout=0.1), "writer committed while migration held BEGIN IMMEDIATE")
            continue_migration.set()
            migration.result(timeout=10)
            writer.result(timeout=10)

        self.assertEqual(self.scalar(backup, "SELECT spec_hash FROM campaigns WHERE id=1"), original["spec_hash"])
        self.assertEqual(self.scalar(dbpath, "SELECT spec_hash FROM campaigns WHERE id=1"), "committed-after-backup")
        self.assertEqual(self.scalar(dbpath, "PRAGMA user_version"), 5)

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
