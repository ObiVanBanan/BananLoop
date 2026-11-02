"""Explicit SQLite schema migrations for versioned campaign stores.

The v4 to v5 upgrade is deliberately separate from CampaignStore construction:
callers must hold the campaign's application lock before invoking it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Literal

from .lock import CampaignLock


class MigrationError(RuntimeError):
    """The campaign database could not be safely upgraded."""


V5MigrationFailurePoint = Literal[
    "after_backup", "during_schema", "after_schema", "before_version_commit"
]
_VALID_FAILURE_POINTS = {"after_backup", "during_schema", "after_schema", "before_version_commit"}
_EXPECTED_COLUMNS = {
    "graph_meta": {"id", "revision"},
    "research_nodes": {
        "node_id", "phase", "status", "payload_json", "revision", "created_at", "updated_at",
    },
    "research_edges": {"src_id", "dst_id", "edge_type", "constraint_json"},
    "graph_groups": {"group_id", "owner_node_id", "kind", "readiness_policy"},
    "graph_group_members": {"group_id", "node_id", "position"},
    "commands": {
        "command_id", "command_type", "idempotency_key", "payload_hash",
        "status", "response_json", "created_at",
    },
}


def create_v5_graph_schema(
    conn: sqlite3.Connection,
    *,
    after_statement: Callable[[int], None] | None = None,
) -> None:
    """Create the canonical graph tables inside the caller's transaction.

    The helper intentionally does not commit. It is reusable by the v4->v5
    migrator and by focused schema tests. `events` remains the event log.
    """
    if not conn.in_transaction:
        raise MigrationError("v5 graph schema must be created inside a transaction")

    statements = (
        """CREATE TABLE graph_meta (
            id INTEGER PRIMARY KEY CHECK(id=1),
            revision INTEGER NOT NULL CHECK(revision>=0)
        )""",
        "INSERT INTO graph_meta(id, revision) VALUES(1, 0)",
        """CREATE TABLE research_nodes (
            node_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL CHECK(phase IN (
                'EXPLORATION','METRIC_DISCOVERY','OPTIMIZATION','VERIFICATION','CONSOLIDATION'
            )),
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE research_edges (
            src_id TEXT NOT NULL REFERENCES research_nodes(node_id),
            dst_id TEXT NOT NULL REFERENCES research_nodes(node_id),
            edge_type TEXT NOT NULL CHECK(edge_type IN ('REQUIRES','DERIVED_FROM','RELATED_TO')),
            constraint_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(src_id, dst_id, edge_type)
        )""",
        "CREATE INDEX research_edges_src_type_idx ON research_edges(src_id, edge_type)",
        "CREATE INDEX research_edges_dst_type_idx ON research_edges(dst_id, edge_type)",
        """CREATE TABLE graph_groups (
            group_id TEXT PRIMARY KEY,
            owner_node_id TEXT NOT NULL REFERENCES research_nodes(node_id),
            kind TEXT NOT NULL CHECK(kind IN ('AND','OR')),
            readiness_policy TEXT NOT NULL CHECK(readiness_policy IN ('verified_completion'))
        )""",
        "CREATE INDEX graph_groups_owner_idx ON graph_groups(owner_node_id)",
        """CREATE TABLE graph_group_members (
            group_id TEXT NOT NULL REFERENCES graph_groups(group_id) ON DELETE CASCADE,
            node_id TEXT NOT NULL REFERENCES research_nodes(node_id),
            position INTEGER NOT NULL CHECK(position>=0),
            PRIMARY KEY(group_id, node_id),
            UNIQUE(group_id, position)
        )""",
        "CREATE INDEX graph_group_members_node_idx ON graph_group_members(node_id, group_id)",
        """CREATE TABLE commands (
            command_id TEXT PRIMARY KEY,
            command_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    )
    for index, statement in enumerate(statements):
        conn.execute(statement)
        if after_statement is not None:
            after_statement(index)


def _claim_backup_path(database_path: Path) -> Path:
    base = database_path.with_name(database_path.name + ".pre-rpdi-02-v5.bak")
    suffix = 0
    while True:
        candidate = base if suffix == 0 else base.with_name(f"{base.name}.{suffix}")
        try:
            # Exclusive creation reserves this name against concurrent backup
            # attempts and preserves every older backup byte for byte.
            with candidate.open("xb"):
                pass
            return candidate
        except FileExistsError:
            suffix += 1


def _make_consistent_backup(source_path: Path, backup_path: Path, *, timeout: float) -> None:
    """Copy a stable snapshot while the migrator holds BEGIN IMMEDIATE."""
    source = sqlite3.connect(source_path, timeout=timeout)
    try:
        destination = sqlite3.connect(backup_path, timeout=timeout)
        try:
            source.backup(destination)
            destination.commit()
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise MigrationError(f"created migration backup failed integrity_check: {integrity}")
            version = destination.execute("PRAGMA user_version").fetchone()[0]
            if version != 4:
                raise MigrationError(f"created migration backup has unexpected schema version {version}")
        finally:
            destination.close()
    finally:
        source.close()


def _check_integrity(conn: sqlite3.Connection, *, stage: str) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise MigrationError(f"campaign database failed integrity_check {stage}: {integrity}")
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise MigrationError(
            f"campaign database failed foreign_key_check {stage}: {foreign_key_errors[0]}"
        )


def _validate_v5_schema(conn: sqlite3.Connection) -> None:
    expected_tables = {
        "graph_meta", "research_nodes", "research_edges", "graph_groups",
        "graph_group_members", "commands",
    }
    present = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = expected_tables - present
    if missing:
        raise MigrationError(f"v5 graph schema is missing tables: {', '.join(sorted(missing))}")

    expected_indexes = {
        "research_edges_src_type_idx", "research_edges_dst_type_idx", "graph_groups_owner_idx",
        "graph_group_members_node_idx",
    }
    indexes = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    missing_indexes = expected_indexes - indexes
    if missing_indexes:
        raise MigrationError(f"v5 graph schema is missing indexes: {', '.join(sorted(missing_indexes))}")
    for table, expected_columns in _EXPECTED_COLUMNS.items():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing_columns = expected_columns - columns
        if missing_columns:
            raise MigrationError(
                f"v5 graph table {table} is missing columns: {', '.join(sorted(missing_columns))}"
            )


def migrate_v4_to_v5(
    database_path: str | Path,
    campaign_lock: CampaignLock,
    *,
    timeout: float = 10.0,
    fail_at: V5MigrationFailurePoint | None = None,
    after_writer_lock: Callable[[], None] | None = None,
) -> Path:
    """Upgrade a v4 campaign DB while its adjacent ``campaign.lock`` is held.

    A complete, collision-safe v4 backup is made only after SQLite has granted
    the writer reservation. Failures before commit leave the live DB at v4 and
    retain any completed backup. The returned path identifies that backup.

    ``after_writer_lock`` and ``fail_at`` are deterministic fault-injection
    seams for migration tests; production callers should leave them unset.
    """
    source_path = Path(database_path).resolve()
    if fail_at is not None and fail_at not in _VALID_FAILURE_POINTS:
        raise MigrationError(f"unknown migration failure point: {fail_at}")
    if not source_path.is_file():
        raise MigrationError(f"campaign database does not exist: {source_path}")

    expected_lock_path = (source_path.parent / "campaign.lock").resolve()
    if not isinstance(campaign_lock, CampaignLock):
        raise MigrationError("migration requires a CampaignLock instance")
    try:
        supplied_lock_path = Path(campaign_lock.path).resolve()
        lock_acquired = campaign_lock.acquired
    except (AttributeError, OSError) as exc:
        raise MigrationError("an acquired adjacent campaign.lock is required") from exc
    if not lock_acquired or supplied_lock_path != expected_lock_path:
        raise MigrationError(f"migration requires the acquired adjacent lock at {expected_lock_path}")

    conn = sqlite3.connect(source_path, timeout=timeout, isolation_level=None)
    backup_path: Path | None = None
    backup_complete = False
    try:
        conn.execute(f"PRAGMA busy_timeout = {max(0, int(timeout * 1000))}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        if after_writer_lock is not None:
            after_writer_lock()

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != 4:
            raise MigrationError(f"v4 to v5 migration requires schema v4, found v{version}")
        _check_integrity(conn, stage="before migration")

        backup_path = _claim_backup_path(source_path)
        try:
            # Use a separate read connection: the source handle owns the
            # BEGIN IMMEDIATE reservation, which blocks competing writers.
            _make_consistent_backup(source_path, backup_path, timeout=timeout)
            backup_complete = True
        except Exception:
            backup_path.unlink(missing_ok=True)
            backup_path = None
            raise

        if fail_at == "after_backup":
            raise MigrationError("injected migration failure after backup")

        def inject_during_schema(statement_index: int) -> None:
            if fail_at == "during_schema" and statement_index == 2:
                raise MigrationError("injected migration failure during schema creation")

        create_v5_graph_schema(conn, after_statement=inject_during_schema)
        if fail_at == "after_schema":
            raise MigrationError("injected migration failure after schema creation")
        _check_integrity(conn, stage="after schema creation")
        _validate_v5_schema(conn)
        if fail_at == "before_version_commit":
            raise MigrationError("injected migration failure before version commit")

        # This is the final schema-state change. It commits atomically with the
        # DDL so no observer can see a v5 version without its complete schema.
        conn.execute("PRAGMA user_version = 5")
        conn.execute("COMMIT")
        return backup_path
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if backup_path is not None and not backup_complete:
            backup_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
