"""Durable, public-only submission ledger for AutoResearchExam MCP grading.

This module deliberately accepts a transport callback instead of importing
Harbor or an MCP SDK. The Harbor adapter supplies the callback from inside the
task environment, where ``http://grader:8000/mcp`` is reachable. Submission is
at-most-once: the intent is committed before the network call, and an
ambiguous outcome is never retried automatically.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping


class PublicGraderError(RuntimeError):
    """The public AutoResearchExam grading exchange could not be trusted."""


class PublicAREGrader:
    """At-most-once submission and sanitized public-result persistence."""

    def __init__(self, database: str | Path, *, task_id: str,
                 contract_sha256: str,
                 call_tool: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
                 repeatability_runs: int | None = None,
                 time_limit_seconds: int = 3600,
                 clock: Callable[[], float] = time.time):
        if not task_id or len(task_id) > 200:
            raise ValueError("task_id must be nonempty and bounded")
        if repeatability_runs is None:
            repeatability_runs = (
                4 if task_id == "sopcc-online-chance-constrained-policy" else 2
            )
        if (len(contract_sha256) != 64
                or any(char not in "0123456789abcdef" for char in contract_sha256)):
            raise ValueError("contract_sha256 must be a lowercase SHA-256 digest")
        if not callable(call_tool):
            raise TypeError("call_tool must be callable")
        if (isinstance(repeatability_runs, bool) or not isinstance(repeatability_runs, int)
                or not 1 <= repeatability_runs <= 10):
            raise ValueError("repeatability_runs must be an integer from 1 to 10")
        if (isinstance(time_limit_seconds, bool) or not isinstance(time_limit_seconds, int)
                or not 1 <= time_limit_seconds <= 3600):
            raise ValueError("time_limit_seconds must be an integer from 1 to 3600")
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.task_id = task_id
        self.contract_sha256 = contract_sha256
        self.call_tool = call_tool
        self.repeatability_runs = repeatability_runs
        self.time_limit_seconds = time_limit_seconds
        self.clock = clock
        # Safe transport classification only. Never retain server text or
        # grader output; callers may include this category in owner reports.
        self.last_transport_failure_category: str | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
            CREATE TABLE IF NOT EXISTS are_public_grader_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL,
                    repeatability_runs INTEGER NOT NULL CHECK (repeatability_runs BETWEEN 1 AND 10),
                    time_limit_seconds INTEGER NOT NULL CHECK (time_limit_seconds BETWEEN 1 AND 3600),
                    started_epoch REAL NOT NULL,
                    deadline_epoch REAL NOT NULL,
                    max_candidate_iterations INTEGER NOT NULL CHECK (max_candidate_iterations = 5)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS are_public_submissions (
                    candidate_iteration INTEGER NOT NULL CHECK (candidate_iteration BETWEEN 0 AND 5),
                    repetition INTEGER NOT NULL CHECK (repetition BETWEEN 1 AND 10),
                    candidate_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'SUBMITTING', 'SUBMISSION_UNKNOWN', 'QUEUED',
                        'GRADING', 'GRADED', 'FAILED'
                    )),
                    submission_id INTEGER UNIQUE,
                    started_epoch REAL NOT NULL,
                    updated_epoch REAL NOT NULL,
                    public_result_json TEXT,
                    PRIMARY KEY (candidate_iteration, repetition)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS are_candidate_iterations (
                    candidate_snapshot_id TEXT PRIMARY KEY,
                    candidate_iteration INTEGER NOT NULL UNIQUE
                        CHECK (candidate_iteration BETWEEN 0 AND 5),
                    candidate_sha256 TEXT NOT NULL
                )
            """)
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(are_public_grader_meta)"
            ).fetchall()}
            existing = None
            if "started_epoch" in columns:
                existing = connection.execute(
                    "SELECT schema_version, task_id, contract_sha256, repeatability_runs, "
                    "time_limit_seconds, started_epoch, deadline_epoch, max_candidate_iterations "
                    "FROM are_public_grader_meta WHERE singleton=1"
                ).fetchone()
            elif connection.execute(
                "SELECT 1 FROM are_public_grader_meta WHERE singleton=1"
            ).fetchone() is not None:
                raise PublicGraderError(
                    "existing ledger has no durable deadline; refusing to reset its task budget"
                )
            if existing is None:
                started = float(self.clock())
                connection.execute(
                    "INSERT INTO are_public_grader_meta "
                    "(singleton, schema_version, task_id, contract_sha256, repeatability_runs, "
                    "time_limit_seconds, started_epoch, deadline_epoch, max_candidate_iterations) "
                    "VALUES (1, 3, ?, ?, ?, ?, ?, ?, 5)",
                    (self.task_id, self.contract_sha256, self.repeatability_runs,
                     self.time_limit_seconds, started, started + self.time_limit_seconds),
                )
            elif (existing["schema_version"] != 3 or existing["task_id"] != self.task_id
                  or existing["contract_sha256"] != self.contract_sha256
                  or existing["repeatability_runs"] != self.repeatability_runs
                  or existing["time_limit_seconds"] != self.time_limit_seconds
                  or existing["max_candidate_iterations"] != 5
                  or existing["deadline_epoch"] != existing["started_epoch"] + self.time_limit_seconds):
                raise PublicGraderError(
                    "submission ledger is pinned to another task, contract, repeatability, or time limit"
                )

    @property
    def deadline_epoch(self) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT deadline_epoch FROM are_public_grader_meta WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise PublicGraderError("task deadline record is missing")
        return float(row["deadline_epoch"])

    @property
    def started_epoch(self) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT started_epoch FROM are_public_grader_meta WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise PublicGraderError("task start record is missing")
        return float(row["started_epoch"])

    @property
    def candidate_iterations_used(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM are_candidate_iterations WHERE candidate_iteration > 0"
            ).fetchone()
        return int(row[0]) if row else 0

    def submission_summary(self) -> list[dict[str, Any]]:
        """Export sanitized public accounting without retaining grader text."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.candidate_iteration, s.repetition, s.candidate_sha256, "
                "s.state, s.submission_id, s.started_epoch, s.updated_epoch, "
                "s.public_result_json, i.candidate_snapshot_id "
                "FROM are_public_submissions AS s "
                "LEFT JOIN are_candidate_iterations AS i USING (candidate_iteration) "
                "ORDER BY s.candidate_iteration, s.repetition"
            ).fetchall()
        output = []
        for row in rows:
            item: dict[str, Any] = {
                "candidate_iteration": row["candidate_iteration"],
                "phase": "baseline" if row["candidate_iteration"] == 0 else "candidate",
                "repetition": row["repetition"],
                "candidate_sha256": row["candidate_sha256"],
                "candidate_snapshot_id": row["candidate_snapshot_id"],
                "state": row["state"],
                "submission_id": row["submission_id"],
                "started_epoch": row["started_epoch"],
                "updated_epoch": row["updated_epoch"],
            }
            if row["public_result_json"]:
                item["public_result"] = json.loads(row["public_result_json"])
            output.append(item)
        return output

    @staticmethod
    def _candidate_digest(candidate: bytes | str) -> str:
        raw = candidate.encode("utf-8") if isinstance(candidate, str) else candidate
        if not isinstance(raw, bytes) or not raw:
            raise ValueError("candidate must be nonempty bytes or text")
        return hashlib.sha256(raw).hexdigest()

    def submit_once(self, *, candidate_iteration: int, repetition: int,
                    candidate: bytes | str,
                    note: str = "") -> dict[str, Any]:
        """Submit baseline (iteration 0) or one of five candidates, at most once."""
        if (isinstance(candidate_iteration, bool) or not isinstance(candidate_iteration, int)
                or not 0 <= candidate_iteration <= 5):
            raise ValueError("candidate_iteration must be an integer from 0 to 5")
        if (isinstance(repetition, bool) or not isinstance(repetition, int)
                or not 1 <= repetition <= self.repeatability_runs):
            raise ValueError(f"repetition must be an integer from 1 to {self.repeatability_runs}")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("note must be a string of at most 500 characters")
        digest = self._candidate_digest(candidate)
        now = float(self.clock())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT candidate_sha256, state, submission_id FROM are_public_submissions "
                "WHERE candidate_iteration=? AND repetition=?",
                (candidate_iteration, repetition),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                if existing["candidate_sha256"] != digest:
                    raise PublicGraderError("iteration already has a different frozen candidate")
                raise PublicGraderError(
                    f"candidate evaluation already has a durable submission state ({existing['state']}); refusing resubmit"
                )
            latest = connection.execute(
                "SELECT candidate_iteration, repetition, state FROM are_public_submissions "
                "ORDER BY candidate_iteration DESC, repetition DESC LIMIT 1"
            ).fetchone()
            expected = (0, 1) if latest is None else self._next_slot(
                latest["candidate_iteration"], latest["repetition"], latest["state"],
            )
            if (candidate_iteration, repetition) != expected:
                connection.rollback()
                raise PublicGraderError("submissions must be strictly sequential and prior results graded")
            same_iteration = connection.execute(
                "SELECT candidate_sha256 FROM are_public_submissions "
                "WHERE candidate_iteration=? LIMIT 1", (candidate_iteration,),
            ).fetchone()
            if same_iteration is not None and same_iteration["candidate_sha256"] != digest:
                connection.rollback()
                raise PublicGraderError("repeatability submissions must use the same frozen candidate")
            connection.execute(
                "INSERT INTO are_public_submissions "
                "(candidate_iteration, repetition, candidate_sha256, state, started_epoch, updated_epoch) "
                "VALUES (?, ?, ?, 'SUBMITTING', ?, ?)",
                (candidate_iteration, repetition, digest, now, now),
            )
            connection.commit()
        finally:
            connection.close()

        try:
            self.last_transport_failure_category = None
            response = self.call_tool("submit", {"note": note})
            submission_id = self._valid_submission_id(response)
            state = "QUEUED"
        except Exception as exc:
            category = getattr(exc, "safe_category", None)
            self.last_transport_failure_category = (
                category if isinstance(category, str) and len(category) <= 64
                else "unknown_transport_error"
            )
            self._set_state(candidate_iteration, repetition, "SUBMISSION_UNKNOWN")
            # Do not preserve transport or server text: it may contain grader output.
            raise PublicGraderError(
                "submit outcome is uncertain; it is durably marked and will not be retried"
            ) from None

        with self._connect() as connection:
            connection.execute(
                "UPDATE are_public_submissions SET state=?, submission_id=?, updated_epoch=? "
                "WHERE candidate_iteration=? AND repetition=? AND state='SUBMITTING'",
                (state, submission_id, float(self.clock()), candidate_iteration, repetition),
            )
        return self.get(candidate_iteration, repetition)

    def _next_slot(self, candidate_iteration: int, repetition: int, state: str) -> tuple[int, int]:
        if state != "GRADED":
            raise PublicGraderError("previous submission is not graded; refusing another submission")
        if repetition < self.repeatability_runs:
            return candidate_iteration, repetition + 1
        if candidate_iteration < 5:
            return candidate_iteration + 1, 1
        raise PublicGraderError("five candidate iterations are already graded")

    def submit_snapshot_once(self, *, candidate_snapshot_id: str,
                             source_snapshot_id: str, candidate: bytes | str,
                             note: str = "") -> dict[str, Any]:
        """Map immutable Core snapshot IDs to baseline/candidate iterations."""
        import re

        git_oid = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
        if not git_oid.fullmatch(candidate_snapshot_id) or not git_oid.fullmatch(source_snapshot_id):
            raise ValueError("candidate and source snapshot IDs must be full Git object IDs")
        digest = self._candidate_digest(candidate)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT candidate_iteration, candidate_sha256 FROM are_candidate_iterations "
                "WHERE candidate_snapshot_id=?", (source_snapshot_id,),
            ).fetchone()
            if source is None:
                connection.execute(
                    "INSERT INTO are_candidate_iterations "
                    "(candidate_snapshot_id, candidate_iteration, candidate_sha256) VALUES (?, 0, ?)",
                    (source_snapshot_id, "0" * 64),
                )
            elif source["candidate_iteration"] != 0:
                connection.rollback()
                raise PublicGraderError("protocol source snapshot is not pinned as the baseline")
            existing = connection.execute(
                "SELECT candidate_iteration, candidate_sha256 FROM are_candidate_iterations "
                "WHERE candidate_snapshot_id=?", (candidate_snapshot_id,),
            ).fetchone()
            if existing is None:
                latest = connection.execute(
                    "SELECT MAX(candidate_iteration) FROM are_candidate_iterations"
                ).fetchone()[0]
                iteration = int(latest or 0) + (1 if candidate_snapshot_id != source_snapshot_id else 0)
                if iteration > 5:
                    connection.rollback()
                    raise PublicGraderError("five candidate iterations are already pinned")
                latest_submission = connection.execute(
                    "SELECT candidate_iteration, repetition, state FROM are_public_submissions "
                    "ORDER BY candidate_iteration DESC, repetition DESC LIMIT 1"
                ).fetchone()
                expected = (0, 1) if latest_submission is None else self._next_slot(
                    latest_submission["candidate_iteration"],
                    latest_submission["repetition"], latest_submission["state"],
                )
                if expected != (iteration, 1):
                    connection.rollback()
                    raise PublicGraderError("previous public evaluation is incomplete; candidate was not pinned")
                connection.execute(
                    "INSERT INTO are_candidate_iterations "
                    "(candidate_snapshot_id, candidate_iteration, candidate_sha256) VALUES (?, ?, ?)",
                    (candidate_snapshot_id, iteration, digest),
                )
            else:
                iteration = int(existing["candidate_iteration"])
                if iteration == 0:
                    # The canonical baseline digest is captured by the first actual submission.
                    old_digest = connection.execute(
                        "SELECT candidate_sha256 FROM are_public_submissions "
                        "WHERE candidate_iteration=0 LIMIT 1"
                    ).fetchone()
                    if old_digest is not None and old_digest["candidate_sha256"] != digest:
                        connection.rollback()
                        raise PublicGraderError("baseline source snapshot bytes changed")
                elif existing["candidate_sha256"] != digest:
                    connection.rollback()
                    raise PublicGraderError("candidate snapshot bytes changed after pinning")
            next_rep = connection.execute(
                "SELECT MAX(repetition) FROM are_public_submissions WHERE candidate_iteration=?",
                (iteration,),
            ).fetchone()[0]
            repetition = int(next_rep or 0) + 1
            if repetition > self.repeatability_runs:
                connection.rollback()
                raise PublicGraderError("all repeatability submissions for this candidate are complete")
            connection.commit()
        finally:
            connection.close()
        return self.submit_once(
            candidate_iteration=iteration, repetition=repetition, candidate=candidate,
            note=note,
        )

    def pending_snapshot(self, candidate_snapshot_id: str) -> dict[str, Any] | None:
        """Return an identified in-flight submission for resume without resending."""
        with self._connect() as connection:
            mapping = connection.execute(
                "SELECT candidate_iteration FROM are_candidate_iterations WHERE candidate_snapshot_id=?",
                (candidate_snapshot_id,),
            ).fetchone()
            if mapping is None:
                return None
            row = connection.execute(
                "SELECT repetition, state FROM are_public_submissions "
                "WHERE candidate_iteration=? ORDER BY repetition DESC LIMIT 1",
                (mapping["candidate_iteration"],),
            ).fetchone()
        if row is None or row["state"] == "GRADED":
            return None
        if row["state"] in {"SUBMITTING", "SUBMISSION_UNKNOWN"}:
            raise PublicGraderError("submission identity is uncertain; automatic retry is forbidden")
        return self.get(int(mapping["candidate_iteration"]), int(row["repetition"]))

    def completed_snapshot_result(
        self, candidate_snapshot_id: str, candidate: bytes | str,
    ) -> dict[str, Any] | None:
        """Reuse a completed, repeatable public score for the identical snapshot."""
        digest = self._candidate_digest(candidate)
        with self._connect() as connection:
            mapping = connection.execute(
                "SELECT candidate_iteration, candidate_sha256 FROM are_candidate_iterations "
                "WHERE candidate_snapshot_id=?", (candidate_snapshot_id,),
            ).fetchone()
            if mapping is None:
                return None
            iteration = int(mapping["candidate_iteration"])
            rows = connection.execute(
                "SELECT repetition, candidate_sha256, state, public_result_json "
                "FROM are_public_submissions WHERE candidate_iteration=? ORDER BY repetition",
                (iteration,),
            ).fetchall()
        if iteration == 0:
            # Baseline hash is intentionally recorded with the first submission.
            if rows and mapping["candidate_sha256"] != "0" * 64:
                if mapping["candidate_sha256"] != digest:
                    raise PublicGraderError("baseline source snapshot bytes changed")
        elif mapping["candidate_sha256"] != digest:
            raise PublicGraderError("candidate snapshot bytes changed after pinning")
        if len(rows) != self.repeatability_runs or any(
            row["state"] != "GRADED" or row["candidate_sha256"] != digest
            or not row["public_result_json"] for row in rows
        ):
            return None
        scores = [json.loads(row["public_result_json"]).get("validation_score") for row in rows]
        if (any(isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) for score in scores)
                or len(set(float(score) for score in scores)) != 1):
            raise PublicGraderError(
                "repeatability scores differ or are invalid; cached evaluation is not admissible"
            )
        return {
            "candidate_iteration": iteration,
            "status": "GRADED",
            "validation_score": float(scores[0]),
            "repetitions": self.repeatability_runs,
            "cached": True,
        }

    @staticmethod
    def _valid_submission_id(response: Any) -> int:
        if (not isinstance(response, Mapping)
                or isinstance(response.get("id"), bool)
                or not isinstance(response.get("id"), int)
                or response["id"] < 0
                or response.get("status") not in {"queued", "grading", "graded"}):
            raise PublicGraderError("public submit response lacks a valid id and status")
        return response["id"]

    def poll(self, candidate_iteration: int, repetition: int) -> dict[str, Any]:
        """Poll an existing public submission; grader output is discarded."""
        row = self._row(candidate_iteration, repetition)
        if row is None:
            raise PublicGraderError("iteration has no submission record")
        if row["state"] in {"SUBMITTING", "SUBMISSION_UNKNOWN"}:
            raise PublicGraderError("submission identity is uncertain; automatic retry is forbidden")
        if row["state"] == "GRADED":
            return self.get(candidate_iteration, repetition)
        submission_id = row["submission_id"]
        try:
            response = self.call_tool("result", {"id": submission_id})
            public_result, state = self._sanitize_result(response, submission_id)
        except PublicGraderError:
            raise
        except Exception:
            # Poll is read-only and safe to repeat; preserve its last known state.
            raise PublicGraderError("public result polling failed; submission identity remains pinned") from None
        self._set_state(candidate_iteration, repetition, state,
                        public_result=public_result if state == "GRADED" else None)
        return self.get(candidate_iteration, repetition)

    @staticmethod
    def _sanitize_result(response: Any, submission_id: int) -> tuple[dict[str, Any], str]:
        if (not isinstance(response, Mapping) or isinstance(response.get("id"), bool)
                or not isinstance(response.get("id"), int) or response.get("id") != submission_id):
            raise PublicGraderError("public result response has an unexpected submission identity")
        status = response.get("status")
        if status in {"queued", "grading"}:
            return {}, status.upper()
        if status != "graded":
            raise PublicGraderError("public grader returned an unrecognized result state")
        score = response.get("validation_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise PublicGraderError("graded public result lacks a finite validation score")
        grading_seconds = response.get("grading_seconds")
        if (isinstance(grading_seconds, bool) or not isinstance(grading_seconds, (int, float))
                or not math.isfinite(grading_seconds) or grading_seconds < 0):
            raise PublicGraderError("graded public result lacks valid grading time")
        return ({
            "submission_id": submission_id,
            "status": "GRADED",
            "validation_score": float(score),
            "grading_seconds": float(grading_seconds),
            "grader_error": bool(response.get("error")),
        }, "GRADED")

    def _set_state(self, candidate_iteration: int, repetition: int, state: str,
                   *, public_result: Mapping[str, Any] | None = None) -> None:
        if state not in {"SUBMISSION_UNKNOWN", "QUEUED", "GRADING", "GRADED", "FAILED"}:
            raise ValueError("invalid public submission state")
        payload = (json.dumps(dict(public_result), sort_keys=True, separators=(",", ":"))
                   if public_result is not None else None)
        with self._connect() as connection:
            connection.execute(
                "UPDATE are_public_submissions SET state=?, updated_epoch=?, public_result_json=? "
                "WHERE candidate_iteration=? AND repetition=?",
                (state, float(self.clock()), payload, candidate_iteration, repetition),
            )

    def _row(self, candidate_iteration: int, repetition: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM are_public_submissions WHERE candidate_iteration=? AND repetition=?",
                (candidate_iteration, repetition),
            ).fetchone()

    def get(self, candidate_iteration: int, repetition: int) -> dict[str, Any]:
        row = self._row(candidate_iteration, repetition)
        if row is None:
            raise PublicGraderError("iteration has no submission record")
        with self._connect() as connection:
            snapshot = connection.execute(
                "SELECT candidate_snapshot_id FROM are_candidate_iterations WHERE candidate_iteration=?",
                (candidate_iteration,),
            ).fetchone()
        result = {
            "task_id": self.task_id,
            "candidate_iteration": candidate_iteration,
            "phase": "baseline" if candidate_iteration == 0 else "candidate",
            "candidate_snapshot_id": snapshot["candidate_snapshot_id"] if snapshot else None,
            "repetition": repetition,
            "candidate_sha256": row["candidate_sha256"],
            "state": row["state"],
            "submission_id": row["submission_id"],
            "started_epoch": row["started_epoch"],
            "updated_epoch": row["updated_epoch"],
        }
        if row["public_result_json"]:
            result["public_result"] = json.loads(row["public_result_json"])
        return result
