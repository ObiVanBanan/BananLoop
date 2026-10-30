from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .contracts import RuntimePaths, utc_now
from .lock import CampaignLock


class RegistryError(RuntimeError):
    pass


class _RegistryLock:
    """Blocking project lock: concurrent registry lookups queue and recheck state."""

    def __init__(self, path: Path):
        self.path = path
        self.file: BinaryIO | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.file is None:
            return
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS campaign_index (
  campaign_id TEXT PRIMARY KEY,
  request_key TEXT NOT NULL UNIQUE,
  goal_fingerprint TEXT NOT NULL,
  goal_summary TEXT NOT NULL,
  state_path TEXT NOT NULL,
  origin TEXT NOT NULL CHECK(origin IN ('managed','legacy')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_meta (
  id INTEGER PRIMARY KEY CHECK(id=1),
  active_campaign_id TEXT
);
INSERT OR IGNORE INTO project_meta(id, active_campaign_id) VALUES(1, NULL);
CREATE TABLE IF NOT EXISTS codex_sessions (
  session_hash TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def assert_safe_local_path(path: Path, label: str = "database") -> None:
    """Reject a local symlink/reparse point before opening a sensitive path."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(info, "st_file_attributes", 0)
    if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
        raise RegistryError(f"{label} cannot be a symlink or reparse point: {path}")


def assert_safe_campaign_db(path: Path) -> None:
    """Reject a campaign DB symlink/reparse point before sqlite can follow it."""
    assert_safe_local_path(path, "campaign database")


def normalize_goal(goal: str) -> str:
    return " ".join(goal.split()).casefold()


def goal_fingerprint(goal: str) -> str:
    return hashlib.sha256(normalize_goal(goal).encode("utf-8")).hexdigest()


def request_key(goal: str, request_id: str | None = None) -> str:
    if request_id is None:
        return "goal:" + goal_fingerprint(goal)
    normalized = request_id.strip()
    if not normalized or len(normalized) > 512 or "\x00" in normalized:
        raise RegistryError("request-id must contain 1..512 non-NUL characters")
    return "request:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CampaignEntry:
    campaign_id: str
    request_key: str
    goal_fingerprint: str
    goal_summary: str
    state_path: str
    origin: str
    created_at: str
    updated_at: str


class CampaignRegistry:
    def __init__(self, repo: Path, paths: RuntimePaths | None = None):
        self.repo = repo.resolve()
        base = paths or RuntimePaths.for_repo(self.repo)
        # Registry shares the repo's hashed project root, alongside campaign state.
        self.root = base.root
        self.path = self.root / "campaign-registry.sqlite3"
        self.lock_path = self.root / "campaign-registry.lock"
        self.legacy_db = base.db

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        # Never silently replace a corrupt registry; sqlite errors are surfaced with path.
        assert_safe_local_path(self.path, "campaign registry")
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise RegistryError(f"campaign registry is corrupt at {self.path}; preserve a backup") from exc
        return conn

    def _validate_state_path(self, relative: str, origin: str) -> Path:
        if origin == "legacy":
            target = self.legacy_db.parent.resolve()
            if relative != ".":
                raise RegistryError("invalid legacy registry path")
            assert_safe_local_path(self.legacy_db.parent, "legacy campaign state directory")
            assert_safe_campaign_db(self.legacy_db)
            return target
        pure = Path(relative)
        if pure.is_absolute() or len(pure.parts) != 2 or pure.parts[0] != "campaigns" or not re.fullmatch(r"c-[0-9a-f]{32}", pure.parts[1]):
            raise RegistryError("invalid managed campaign path in registry")
        raw_target = self.root / pure
        assert_safe_local_path(self.root, "project state root")
        assert_safe_local_path(self.root / "campaigns", "managed campaigns directory")
        assert_safe_local_path(raw_target, "managed campaign directory")
        target = raw_target.resolve()
        root_resolved = self.root.resolve()
        campaigns_root = (self.root / "campaigns").resolve()
        if campaigns_root.parent != root_resolved or target.parent != campaigns_root:
            raise RegistryError("managed campaign path escapes project state root")
        assert_safe_campaign_db(target / "campaign.sqlite3")
        return target

    def _legacy_goal(self) -> str | None:
        """Read only the frozen goal column; never initialize or migrate legacy state."""
        assert_safe_campaign_db(self.legacy_db)
        if not self.legacy_db.exists():
            return None
        try:
            uri = self.legacy_db.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                row = conn.execute("SELECT spec_json FROM campaigns WHERE id=1").fetchone()
            finally:
                conn.close()
            if not row:
                return None
            import json

            spec = json.loads(row[0])
            goal = spec.get("goal")
            return goal if isinstance(goal, str) and normalize_goal(goal) else None
        except (sqlite3.DatabaseError, ValueError, TypeError):
            return None

    def ensure_legacy(self) -> None:
        if not self.legacy_db.exists():
            return
        goal = self._legacy_goal()
        fingerprint = goal_fingerprint(goal) if goal else ""
        with _RegistryLock(self.lock_path):
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT campaign_id FROM campaign_index WHERE campaign_id='legacy'").fetchone()
                if row is None:
                    now = utc_now()
                    conn.execute("INSERT INTO campaign_index VALUES(?,?,?,?,?,?,?,?)", (
                        "legacy", "legacy", fingerprint, " ".join(goal.split())[:240] if goal else "Legacy campaign", ".", "legacy", now, now,
                    ))
                    conn.execute("UPDATE project_meta SET active_campaign_id='legacy' WHERE id=1 AND active_campaign_id IS NULL")
                conn.commit()
            finally:
                conn.close()

    def reserve(self, goal: str, request_id: str | None = None) -> tuple[CampaignEntry, Path, bool]:
        key = request_key(goal, request_id)
        fingerprint = goal_fingerprint(goal)
        self.ensure_legacy()
        with _RegistryLock(self.lock_path):
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM campaign_index WHERE request_key=?", (key,)).fetchone()
                if row:
                    if row["goal_fingerprint"] != fingerprint:
                        raise RegistryError("request-id already belongs to a different goal")
                    entry = CampaignEntry(**dict(row))
                    conn.execute("UPDATE project_meta SET active_campaign_id=? WHERE id=1", (entry.campaign_id,))
                    conn.commit()
                    return entry, self._validate_state_path(entry.state_path, entry.origin), False
                if request_id is None:
                    legacy = conn.execute("SELECT * FROM campaign_index WHERE campaign_id='legacy'").fetchone()
                    legacy_goal = (
                        self._legacy_goal()
                        if legacy and not legacy["goal_fingerprint"]
                        else None
                    )
                    legacy_fingerprint = (
                        goal_fingerprint(legacy_goal)
                        if legacy_goal
                        else (legacy["goal_fingerprint"] if legacy else "")
                    )
                    if legacy and legacy_fingerprint == fingerprint:
                        now = utc_now()
                        conn.execute(
                            "UPDATE campaign_index SET request_key=?,goal_fingerprint=?,goal_summary=?,updated_at=? WHERE campaign_id='legacy'",
                            (key, fingerprint, " ".join((legacy_goal or legacy["goal_summary"]).split())[:240], now),
                        )
                        conn.execute("UPDATE project_meta SET active_campaign_id='legacy' WHERE id=1")
                        adopted = conn.execute("SELECT * FROM campaign_index WHERE campaign_id='legacy'").fetchone()
                        conn.commit()
                        entry = CampaignEntry(**dict(adopted))
                        return entry, self._validate_state_path(entry.state_path, entry.origin), False
                campaign_id = "c-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
                relative = f"campaigns/{campaign_id}"
                state = self._validate_state_path(relative, "managed")
                state.mkdir(parents=True, exist_ok=True)
                now = utc_now()
                summary = " ".join(goal.split())[:240]
                conn.execute("INSERT INTO campaign_index VALUES(?,?,?,?,?,?,?,?)", (
                    campaign_id, key, fingerprint, summary, relative, "managed", now, now,
                ))
                conn.execute("UPDATE project_meta SET active_campaign_id=? WHERE id=1", (campaign_id,))
                conn.commit()
                return CampaignEntry(campaign_id, key, fingerprint, summary, relative, "managed", now, now), state, True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def resolve(self, campaign_id: str | None = None) -> tuple[Path, str | None]:
        self.ensure_legacy()
        with _RegistryLock(self.lock_path):
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if campaign_id is None:
                    meta = conn.execute("SELECT active_campaign_id FROM project_meta WHERE id=1").fetchone()
                    campaign_id = meta[0] if meta and meta[0] else None
                if campaign_id is None:
                    conn.commit()
                    return self.legacy_db.parent, None
                row = conn.execute("SELECT * FROM campaign_index WHERE campaign_id=?", (campaign_id,)).fetchone()
                if row is None:
                    raise RegistryError(f"unknown campaign-id: {campaign_id}")
                if campaign_id != "legacy":
                    conn.execute("UPDATE project_meta SET active_campaign_id=? WHERE id=1", (campaign_id,))
                conn.commit()
                return self._validate_state_path(row["state_path"], row["origin"]), campaign_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def list(self) -> list[tuple[CampaignEntry, bool]]:
        self.ensure_legacy()
        conn = self._connect()
        try:
            active_row = conn.execute("SELECT active_campaign_id FROM project_meta WHERE id=1").fetchone()
            active = active_row[0] if active_row else None
            rows = conn.execute("SELECT * FROM campaign_index ORDER BY created_at,campaign_id").fetchall()
            return [(CampaignEntry(**dict(row)), row["campaign_id"] == active) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise RegistryError(f"campaign registry is corrupt at {self.path}; preserve a backup") from exc
        finally:
            conn.close()

    @staticmethod
    def session_hash(session_id: str) -> str:
        if not session_id or len(session_id) > 512 or "\x00" in session_id:
            raise RegistryError("invalid Codex session ID")
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def bind_codex_session(self, session_id: str, campaign_id: str, *, timeout: float = 0.15) -> None:
        """Add/update a session binding with a short lock wait suitable for hooks."""
        digest = self.session_hash(session_id)
        if campaign_id != "legacy" and not re.fullmatch(r"c-[0-9a-f]{32}", campaign_id):
            raise RegistryError("invalid campaign ID for Codex session binding")
        if not self.path.exists():
            raise RegistryError("campaign registry does not exist")
        assert_safe_local_path(self.path, "campaign registry")
        conn = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        try:
            # Upgrade registries created before the optional Codex bridge without
            # opening the campaign DB or blocking the Stop hook on the project lock.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS codex_sessions ("
                "session_hash TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM campaign_index WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            if not exists:
                raise RegistryError("unknown campaign ID for Codex session binding")
            conn.execute(
                "INSERT INTO codex_sessions(session_hash,campaign_id,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(session_hash) DO UPDATE SET campaign_id=excluded.campaign_id,updated_at=excluded.updated_at",
                (digest, campaign_id, utc_now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def codex_session_campaign(self, session_id: str, *, timeout: float = 0.15) -> str | None:
        digest = self.session_hash(session_id)
        if not self.path.exists():
            return None
        assert_safe_local_path(self.path, "campaign registry")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            row = conn.execute(
                "SELECT campaign_id FROM codex_sessions WHERE session_hash=?", (digest,)
            ).fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError as exc:
            # A registry from before the optional bridge has no binding table yet.
            # Treat it as an unbound session; the validated bind path adds the table.
            if "no such table: codex_sessions" in str(exc).lower():
                return None
            raise
        finally:
            conn.close()

    def codex_campaign_state_path(self, campaign_id: str, *, timeout: float = 0.15) -> Path | None:
        """Resolve one known campaign without changing the active campaign pointer."""
        if not self.path.exists():
            return None
        assert_safe_local_path(self.path, "campaign registry")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            row = conn.execute(
                "SELECT state_path,origin FROM campaign_index WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if not row:
                return None
            return self._validate_state_path(row[0], row[1])
        finally:
            conn.close()
