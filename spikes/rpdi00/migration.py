"""Minimal additive v4->v5 SQLite migration spike; not production schema code."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from core import ContractError


def migrate_v4_to_v5(database_path: str | Path, backup_path: str | Path, *,
                     fail_after_schema: bool = False,
                     after_writer_lock: Callable[[], None] | None = None) -> None:
    """Back up a v4 fixture, add v0.2 tables transactionally, keep legacy rows intact."""
    source = Path(database_path)
    backup = Path(backup_path)
    db = sqlite3.connect(source, timeout=10, isolation_level=None)
    backup_created = False
    backup_complete = False
    try:
        # Hold SQLite's single-writer reservation before inspecting or backing
        # up the source. A second connection performs the backup because the
        # backup API can block when its source connection owns a write txn.
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if after_writer_lock is not None:
            after_writer_lock()
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 4:
            raise ContractError("MIGRATION_VERSION", f"expected schema v4, found v{version}")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ContractError("CORRUPT_DATABASE", integrity)
        try:
            with backup.open("xb"):
                pass
            backup_created = True
        except FileExistsError as exc:
            raise ContractError("BACKUP_EXISTS", "refusing to overwrite a prior backup") from exc
        backup_source = sqlite3.connect(source, timeout=10)
        try:
            backup_db = sqlite3.connect(backup)
            try:
                backup_source.backup(backup_db)
            finally:
                backup_db.close()
        finally:
            backup_source.close()
        backup_complete = True
        db.execute("CREATE TABLE graph_meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=0))")
        db.execute("INSERT INTO graph_meta VALUES(1,0)")
        db.execute("CREATE TABLE research_nodes(node_id TEXT PRIMARY KEY, phase TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE research_edges(src_id TEXT NOT NULL REFERENCES research_nodes(node_id), dst_id TEXT NOT NULL REFERENCES research_nodes(node_id), edge_type TEXT NOT NULL CHECK(edge_type IN ('REQUIRES','DERIVED_FROM','RELATED_TO')), PRIMARY KEY(src_id,dst_id,edge_type))")
        db.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES research_nodes(node_id), status TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0, request_hash TEXT NOT NULL)")
        if fail_after_schema:
            raise RuntimeError("injected migration crash before version commit")
        db.execute("PRAGMA user_version=5")
        final_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if final_integrity != "ok":
            raise ContractError("CORRUPT_DATABASE", final_integrity)
        foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise ContractError("FOREIGN_KEY_VIOLATION", str(foreign_key_errors[0]))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        if backup_created and not backup_complete:
            backup.unlink(missing_ok=True)
        raise
    finally:
        db.close()

