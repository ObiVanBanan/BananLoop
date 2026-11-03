"""Core-owned, conditional promotion of a verified Optimization candidate.

Node completion and integration PASS are evidence for promotion, not promotion
itself. The champion pointer changes only through this explicit CAS operation.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .evaluator_verification import EvaluatorProtocolAuthority, EvaluatorVerificationError


class ChampionPromotionError(RuntimeError):
    """A candidate is ineligible or lost the champion compare-and-swap."""


class ChampionPromotionService:
    """Campaign-scoped champion pointer guarded by a trusted integration PASS."""

    def __init__(self, connection: sqlite3.Connection, authority: EvaluatorProtocolAuthority):
        self.conn = connection
        self.authority = authority
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS champion_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                campaign_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 0)
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS champion_promotion_history (
                revision INTEGER PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                previous_snapshot_id TEXT NOT NULL,
                promoted_snapshot_id TEXT NOT NULL,
                integration_receipt_digest TEXT NOT NULL UNIQUE
            )"""
        )
        self.conn.execute(
            """CREATE TRIGGER IF NOT EXISTS champion_history_no_update
               BEFORE UPDATE ON champion_promotion_history BEGIN
               SELECT RAISE(ABORT,'champion promotion history is immutable'); END"""
        )
        self.conn.execute(
            """CREATE TRIGGER IF NOT EXISTS champion_history_no_delete
               BEFORE DELETE ON champion_promotion_history BEGIN
               SELECT RAISE(ABORT,'champion promotion history is immutable'); END"""
        )

    def initialize(self, *, campaign_id: str, source_snapshot_id: str) -> dict[str, Any]:
        """Pin the initial champion to the campaign's approved source snapshot."""
        if not campaign_id or not source_snapshot_id:
            raise ChampionPromotionError("initial champion requires campaign and source identities")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT campaign_id,snapshot_id,revision FROM champion_state WHERE id=1").fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO champion_state(id,campaign_id,snapshot_id,revision) VALUES(1,?,?,0)",
                    (campaign_id, source_snapshot_id),
                )
            elif row[0] != campaign_id:
                raise ChampionPromotionError("champion is already initialized to a different campaign")
            result = self.current()
            self.conn.commit()
            return result
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def current(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT campaign_id,snapshot_id,revision FROM champion_state WHERE id=1"
        ).fetchone()
        if row is None:
            raise ChampionPromotionError("champion has not been initialized")
        return {"campaign_id": row[0], "snapshot_id": row[1], "revision": int(row[2])}

    def promote(
        self, *, campaign_id: str, expected_snapshot_id: str, expected_revision: int,
        candidate_snapshot_id: str, integration_receipt_digest: str,
        spec_hash: str, source_snapshot_id: str, approval_digest: str,
        protocol_digest: str,
    ) -> dict[str, Any]:
        """Promote only the exact candidate covered by a durable trusted PASS receipt."""
        try:
            self.authority.require_integration_decision(
                integration_receipt_digest, decision="PASS", campaign_id=campaign_id,
                approval_digest=approval_digest, spec_hash=spec_hash,
                source_snapshot_id=source_snapshot_id, protocol_digest=protocol_digest,
                candidate_snapshot_id=candidate_snapshot_id,
            )
        except EvaluatorVerificationError as exc:
            raise ChampionPromotionError("promotion requires an exact trusted integration PASS") from exc
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.conn.execute(
                """UPDATE champion_state SET snapshot_id=?, revision=revision+1
                   WHERE id=1 AND campaign_id=? AND snapshot_id=? AND revision=?""",
                (candidate_snapshot_id, campaign_id, expected_snapshot_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ChampionPromotionError("stale champion expectation; candidate must be reconsidered")
            self.conn.execute(
                """INSERT INTO champion_promotion_history(
                    revision,campaign_id,previous_snapshot_id,promoted_snapshot_id,
                    integration_receipt_digest
                ) VALUES(?,?,?,?,?)""",
                (expected_revision + 1, campaign_id, expected_snapshot_id,
                 candidate_snapshot_id, integration_receipt_digest),
            )
            result = self.current()
            self.conn.commit()
            return result
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
