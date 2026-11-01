"""Minimal additive v4->v5 SQLite migration spike; not production schema code."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core import ContractError


def migrate_v4_to_v5(database_path: str | Path, backup_path: str | Path, *,
                     fail_after_schema: bool = False) -> None:
    """Back up a v4 fixture, add v0.2 tables transactionally, keep legacy rows intact."""
    source = Path(database_path)
    backup = Path(backup_path)
    if backup.exists():
        raise ContractError("BACKUP_EXISTS", "refusing to overwrite a prior backup")
    db = sqlite3.connect(source, timeout=10, isolation_level=None)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 4:
            raise ContractError("MIGRATION_VERSION", f"expected schema v4, found v{version}")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ContractError("CORRUPT_DATABASE", integrity)
        backup_db = sqlite3.connect(backup)
        try:
            db.backup(backup_db)
        finally:
            backup_db.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE graph_meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=0))")
        db.execute("INSERT INTO graph_meta VALUES(1,0)")
        db.execute("CREATE TABLE research_nodes(node_id TEXT PRIMARY KEY, phase TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE research_edges(src_id TEXT NOT NULL, dst_id TEXT NOT NULL, edge_type TEXT NOT NULL CHECK(edge_type IN ('REQUIRES','DERIVED_FROM','RELATED_TO')), PRIMARY KEY(src_id,dst_id,edge_type))")
        db.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, status TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0, request_hash TEXT NOT NULL)")
        if fail_after_schema:
            raise RuntimeError("injected migration crash before version commit")
        db.execute("PRAGMA user_version=5")
        db.execute("COMMIT")
        final_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if final_integrity != "ok":
            raise ContractError("CORRUPT_DATABASE", final_integrity)
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()

