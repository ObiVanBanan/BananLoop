from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bananloop.contracts import (
    CampaignSpec,
    Direction,
    ExplorationCampaignSpecV2,
    MetricSpec,
    parse_campaign_spec_document,
)
from bananloop.graph_store import V2CampaignStore
from bananloop.lock import CampaignLock
from bananloop.migrations import MigrationError, migrate_v4_to_v5
from bananloop.store import CampaignStore, StoreError


def _create_v4_campaign(path: Path) -> tuple[str, str]:
    store = CampaignStore(path)
    spec = CampaignSpec(
        goal="preserve this v4 campaign through migration",
        source_repo=str(path.parent),
        base_commit="legacy-base",
        evaluation_command=["python", "evaluate.py"],
        primary_metric=MetricSpec(
            name="quality", direction=Direction.MAXIMIZE, target=1, min_improvement=0.1,
        ),
    )
    store.initialize(spec)
    event_payload = '{"marker":"before-v5","opaque":"preserve-bytes"}'
    store.conn.execute(
        "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
        ("MIGRATION_SENTINEL", event_payload, "2026-11-02T00:00:00Z"),
    )
    created_at = "2026-11-02T00:00:00Z"
    store.conn.execute(
        """INSERT INTO actions
           (id,experiment_id,stage,kind,status,request_hash,request_json,response_json,error,created_at,updated_at)
           VALUES(?,NULL,?,?,?,?,?,?,NULL,?,?)""",
        (
            "migration-action", "PROPOSE", "agent", "SUCCEEDED", "c" * 64,
            '{"prompt":"preserve request bytes"}', '{"answer":"preserve response bytes"}',
            created_at, created_at,
        ),
    )
    store.conn.execute(
        """INSERT INTO reservations
           (action_id,resource,amount,status,quality,reserved_at,settled_at,duration_ms)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            "migration-action", "model_tokens", 17, "SETTLED", "MEASURED",
            created_at, created_at, 123.5,
        ),
    )
    store.conn.execute(
        """INSERT INTO evaluator_bindings(id,binding_id,binding_hash,binding_json,created_at)
           VALUES(1,?,?,?,?)""",
        ("evaluator-v1", "d" * 64, '{"command":["python","evaluate.py"]}', created_at),
    )
    store.conn.execute(
        """INSERT INTO evaluation_receipts(action_id,receipt_hash,receipt_json,created_at)
           VALUES(?,?,?,?)""",
        (
            "migration-action", "e" * 64,
            '{"action_id":"migration-action","receipt":"preserve exact bytes"}', created_at,
        ),
    )
    store.conn.commit()
    spec_hash = store.campaign()["spec_hash"]
    store.close()
    return spec_hash, event_payload


def _campaign_lock(path: Path) -> CampaignLock:
    return CampaignLock(path.parent / "campaign.lock")


def _event_payload(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT payload_json FROM events WHERE event_type='MIGRATION_SENTINEL'"
        ).fetchone()[0]


def _legacy_row_snapshot(path: Path) -> tuple:
    with sqlite3.connect(path) as conn:
        queries = (
            ("SELECT spec_hash,spec_json FROM campaigns WHERE id=1", ()),
            ("SELECT id,request_hash,request_json,response_json FROM actions WHERE id=?", ("migration-action",)),
            ("SELECT action_id,resource,amount,status,quality,reserved_at,settled_at,duration_ms "
             "FROM reservations WHERE action_id=?", ("migration-action",)),
            ("SELECT id,binding_id,binding_hash,binding_json,created_at FROM evaluator_bindings WHERE id=1", ()),
            ("SELECT action_id,receipt_hash,receipt_json,created_at FROM evaluation_receipts WHERE action_id=?",
             ("migration-action",)),
            ("SELECT event_type,payload_json,created_at FROM events WHERE event_type='MIGRATION_SENTINEL'", ()),
        )
        return tuple(conn.execute(sql, params).fetchall()[0] for sql, params in queries)


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def test_v4_migration_rejects_unlocked_call_without_mutating_database(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    original_bytes = path.read_bytes()
    lock = _campaign_lock(path)

    assert not lock.acquired
    with pytest.raises(MigrationError, match="acquired adjacent lock"):
        migrate_v4_to_v5(path, lock)

    assert path.read_bytes() == original_bytes
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not any(path.parent.glob(path.name + ".pre-rpdi-02-v5.bak*"))


def test_v4_migration_requires_the_lock_adjacent_to_database(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    original_bytes = path.read_bytes()
    wrong_lock = CampaignLock(tmp_path / "other" / "campaign.lock")

    with wrong_lock:
        with pytest.raises(MigrationError, match="adjacent lock"):
            migrate_v4_to_v5(path, wrong_lock)

    assert path.read_bytes() == original_bytes
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_v4_migration_requires_an_exact_v4_source(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        original_spec_hash = conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0]

    with _campaign_lock(path) as lock:
        with pytest.raises(MigrationError, match="requires schema v4"):
            migrate_v4_to_v5(path, lock)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0] == original_spec_hash
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_meta'"
        ).fetchone() is None
    assert not any(path.parent.glob(path.name + ".pre-rpdi-02-v5.bak*"))


def test_v4_migration_creates_graph_schema_and_consistent_backup_under_lock(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    spec_hash, event_payload = _create_v4_campaign(path)
    legacy_rows = _legacy_row_snapshot(path)
    assert _campaign_lock(path).acquired is False
    with _campaign_lock(path) as lock:
        assert lock.acquired
        backup = migrate_v4_to_v5(path, lock)
        assert lock.acquired
    assert not lock.acquired

    assert backup.is_file()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0] == spec_hash
        assert conn.execute(
            "SELECT payload_json FROM events WHERE event_type='MIGRATION_SENTINEL'"
        ).fetchone()[0] == event_payload
        assert conn.execute("SELECT revision FROM graph_meta WHERE id=1").fetchone()[0] == 0

    expected_graph_tables = {
        "graph_meta", "research_nodes", "research_edges", "graph_groups",
        "graph_group_members", "commands",
    }
    assert expected_graph_tables <= _table_names(path)
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0] == spec_hash
        assert conn.execute(
            "SELECT payload_json FROM events WHERE event_type='MIGRATION_SENTINEL'"
        ).fetchone()[0] == event_payload
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_meta'"
        ).fetchone() is None
    assert _legacy_row_snapshot(path) == legacy_rows
    assert _legacy_row_snapshot(backup) == legacy_rows


@pytest.mark.parametrize(
    "fail_at",
    ["after_backup", "during_schema", "after_schema", "before_version_commit"],
)
def test_v4_migration_fault_rolls_back_schema_and_preserves_backup(tmp_path, fail_at):
    path = tmp_path / "campaign.sqlite3"
    spec_hash, event_payload = _create_v4_campaign(path)
    legacy_rows = _legacy_row_snapshot(path)
    with _campaign_lock(path) as lock:
        with pytest.raises(MigrationError, match="injected migration failure"):
            migrate_v4_to_v5(path, lock, fail_at=fail_at)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0] == spec_hash
        assert conn.execute(
            "SELECT payload_json FROM events WHERE event_type='MIGRATION_SENTINEL'"
        ).fetchone()[0] == event_payload
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_meta'"
        ).fetchone() is None

    backups = list(path.parent.glob(path.name + ".pre-rpdi-02-v5.bak*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT spec_hash FROM campaigns WHERE id=1").fetchone()[0] == spec_hash
        assert conn.execute(
            "SELECT payload_json FROM events WHERE event_type='MIGRATION_SENTINEL'"
        ).fetchone()[0] == event_payload
    assert _legacy_row_snapshot(path) == legacy_rows
    assert _legacy_row_snapshot(backups[0]) == legacy_rows


def test_v4_migration_uses_a_new_backup_name_when_one_exists(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    prior_backup = path.with_name(path.name + ".pre-rpdi-02-v5.bak")
    prior_backup.write_bytes(b"preserve existing backup")

    with _campaign_lock(path) as lock:
        backup = migrate_v4_to_v5(path, lock)

    assert backup == prior_backup.with_name(prior_backup.name + ".1")
    assert prior_backup.read_bytes() == b"preserve existing backup"
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_is_taken_after_writer_reservation(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    lock = _campaign_lock(path)
    lock_acquired = threading.Event()
    release_migration = threading.Event()
    writer_started = threading.Event()
    writer_finished = threading.Event()

    def pause_after_reservation():
        lock_acquired.set()
        if not release_migration.wait(timeout=10):
            raise TimeoutError("migration writer reservation was not released by the test")

    def competing_writer():
        writer = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            writer_started.set()
            writer.execute("UPDATE events SET payload_json=? WHERE event_type=?",
                           ('{"marker":"after-v5"}', "MIGRATION_SENTINEL"))
            writer_finished.set()
        finally:
            writer.close()

    with lock:
        with ThreadPoolExecutor(max_workers=2) as pool:
            migration = pool.submit(
                migrate_v4_to_v5, path, lock, after_writer_lock=pause_after_reservation,
            )
            assert lock_acquired.wait(timeout=5)
            writer = pool.submit(competing_writer)
            assert writer_started.wait(timeout=5)
            assert not writer_finished.wait(timeout=0.1)
            release_migration.set()
            backup = migration.result(timeout=10)
            writer.result(timeout=10)

    assert _event_payload(backup) == '{"marker":"before-v5","opaque":"preserve-bytes"}'
    assert _event_payload(path) == '{"marker":"after-v5"}'
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_legacy_store_open_racing_v5_migration_never_downgrades_schema(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    lock = _campaign_lock(path)
    migration_paused = threading.Event()
    release_migration = threading.Event()
    open_started = threading.Event()

    def pause_after_reservation():
        migration_paused.set()
        if not release_migration.wait(timeout=10):
            raise TimeoutError("test did not release the v5 migrator")

    def open_legacy():
        open_started.set()
        return CampaignStore(path)

    with lock:
        with ThreadPoolExecutor(max_workers=2) as pool:
            migration = pool.submit(
                migrate_v4_to_v5, path, lock, after_writer_lock=pause_after_reservation,
            )
            assert migration_paused.wait(timeout=5)
            legacy_open = pool.submit(open_legacy)
            assert open_started.wait(timeout=5)
            time.sleep(0.1)
            release_migration.set()
            migration.result(timeout=10)
            try:
                unexpected_store = legacy_open.result(timeout=10)
            except (StoreError, sqlite3.OperationalError):
                unexpected_store = None

    if unexpected_store is not None:
        unexpected_store.close()
        pytest.fail("legacy CampaignStore opened a v5 database")
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_meta'"
        ).fetchone() is not None


def test_legacy_campaign_store_rejects_v5_without_modification(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    with _campaign_lock(path) as lock:
        migrate_v4_to_v5(path, lock)
    tables_before = _table_names(path)

    with pytest.raises(StoreError, match="newer than this BananLoop version"):
        CampaignStore(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert _table_names(path) == tables_before


def test_v2_adapter_reopens_v5_and_dispatches_exploration_spec(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    _create_v4_campaign(path)
    v2_document = {
        "schema_version": 2,
        "mode": "EXPLORATION",
        "goal": "inspect a system before choosing an optimization metric",
        "source_repo": "https://example.invalid/research/system",
        "base_commit": "0123456789abcdef",
        "autonomy": {"mode": "SUPERVISED", "scope_digest": "a" * 64, "scope_version": 1},
        "budget": {},
        "role_registry_digest": "b" * 64,
        "evaluation": None,
        "parent": None,
    }
    spec = parse_campaign_spec_document(v2_document)
    assert isinstance(spec, ExplorationCampaignSpecV2)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE campaigns SET spec_json=?,spec_hash=? WHERE id=1",
            (spec.model_dump_json(), spec.approved_hash()),
        )

    with _campaign_lock(path) as lock:
        migrate_v4_to_v5(path, lock)

    store = V2CampaignStore.open(str(path))
    try:
        reopened = store.spec()
        assert isinstance(reopened, ExplorationCampaignSpecV2)
        assert reopened.approved_hash() == spec.approved_hash()
        assert store.graph_revision() == 0
    finally:
        store.close()


def test_v2_adapter_can_still_read_preserved_legacy_v1_campaign(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    spec_hash, _ = _create_v4_campaign(path)
    with _campaign_lock(path) as lock:
        migrate_v4_to_v5(path, lock)

    store = V2CampaignStore.open(str(path))
    try:
        spec = store.spec()
        assert isinstance(spec, CampaignSpec)
        assert spec.approved_hash() == spec_hash
    finally:
        store.close()
