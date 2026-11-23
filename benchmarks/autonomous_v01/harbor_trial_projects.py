"""Fail-closed derivation of Harbor Compose project names from trial configs.

This module deliberately reports only project names it can derive from a
complete, stable config file. A directory scan is not an inventory of Docker,
so callers must never treat an empty result as proof that no other projects
exist.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import os
from pathlib import Path
from typing import Any

_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,254}\Z")
_SESSION_LIMIT = 63


def _safe_session_id(raw: str) -> str:
    """Harbor 0.25 DockerEnvironment._sanitize_docker_compose_project_name."""
    name = raw.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


def _verifier_session(trial_name: str, key: str) -> str:
    """Harbor 0.25 Trial._separate_verifier_session_id behavior."""
    safe = "".join(c if c.isalnum() or c in "-._" else "_"
                   for c in f"{trial_name}__verifier__{key}")
    if len(safe) <= _SESSION_LIMIT:
        return safe
    suffix = "__" + hashlib.sha1(safe.encode()).hexdigest()[:8]
    return safe[:_SESSION_LIMIT - len(suffix)].rstrip("-._") + suffix


def _projects(config: dict[str, Any]) -> list[str]:
    trial_name = config.get("trial_name")
    if not isinstance(trial_name, str) or not trial_name or len(trial_name) > 4096:
        raise ValueError("missing or invalid trial_name")
    environment = config.get("environment")
    if not isinstance(environment, dict) or environment.get("type") != "docker":
        raise ValueError("trial config does not prove a Docker environment")
    sessions = [f"{trial_name}__agent", f"{trial_name}__env"]
    # Harbor 0.25 keys separate verifier sessions by task or step name.
    task = config.get("task")
    if not isinstance(task, dict):
        raise ValueError("missing task config")
    # TrialConfig.json stores only a task reference (path/name/git URL). The
    # task's verifier and step definitions live in task.yaml and are not
    # represented there. Treat absent fields as unknown rather than assuming
    # no separate verifier environments exist.
    if "steps" not in task or "verifier" not in task:
        raise ValueError("trial config does not contain complete task verifier metadata")
    steps = task.get("steps")
    if not isinstance(steps, list):
        raise ValueError("invalid task.steps")
    verifier = task.get("verifier", {}) or {}
    if not isinstance(verifier, dict):
        raise ValueError("invalid task.verifier")
    mode = verifier.get("environment_mode")
    if mode not in (None, "shared", "separate"):
        raise ValueError("invalid task verifier environment mode")
    task_separate = mode == "separate" or (
        mode is None and verifier.get("environment") is not None
    )
    if task_separate and not steps:
        name = task.get("name") or Path(str(task.get("path", "task"))).name
        if not isinstance(name, str) or not name:
            raise ValueError("invalid task verifier name")
        sessions.append(_verifier_session(trial_name, name))
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("invalid task step")
        name = step.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("invalid task step name")
        step_verifier = step.get("verifier", {}) or {}
        if not isinstance(step_verifier, dict):
            raise ValueError("invalid step verifier")
        step_mode = step_verifier.get("environment_mode")
        if step_mode not in (None, "shared", "separate"):
            raise ValueError("invalid step verifier environment mode")
        step_separate = step_mode == "separate" or (
            step_mode is None and step_verifier.get("environment") is not None
        )
        if step_separate or (step_mode is None and task_separate):
            sessions.append(_verifier_session(trial_name, name))
    projects = sorted({_safe_session_id(session) for session in sessions})
    if not all(_PROJECT.fullmatch(project) for project in projects):
        raise ValueError("derived invalid Compose project")
    return projects


def identify_trial_compose_projects(jobs_root: str | Path) -> dict[str, object]:
    """Return provable Compose project names for configs directly under root.

    Config discovery is intentionally one directory deep: ``<jobs_root>/<job>/config.json``.
    Symlinked roots, job directories, configs, malformed/incomplete JSON, and
    files changing while read produce UNKNOWN and no project authorization.
    A successful scan has ``inventory_complete=False`` because configs cannot
    establish that Docker has no projects absent from the scanned tree.
    """
    root = Path(jobs_root)
    root_fd: int | None = None
    try:
        root_lstat = root.lstat()
        if stat.S_ISLNK(root_lstat.st_mode) or not stat.S_ISDIR(root_lstat.st_mode):
            raise ValueError("jobs_root must be a real directory")
        root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                          | getattr(os, "O_NOFOLLOW", 0))
        root_info = os.fstat(root_fd)
        if (root_info.st_dev, root_info.st_ino) != (root_lstat.st_dev, root_lstat.st_ino):
            raise ValueError("jobs_root changed before open")
        found: set[str] = set()
        configs_seen = 0
        for entry_name in sorted(os.listdir(root_fd)):
            info = os.stat(entry_name, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("symlink in jobs_root")
            if not stat.S_ISDIR(info.st_mode):
                continue
            # Pin the job directory so replacing it with a symlink between
            # lstat and config open cannot redirect the read outside root.
            job_fd = os.open(entry_name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                             | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
            try:
                job_info = os.fstat(job_fd)
                if (job_info.st_dev, job_info.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("job directory changed before open")
                try:
                    cfg_info = os.stat("config.json", dir_fd=job_fd, follow_symlinks=False)
                except FileNotFoundError:
                    # Generation may still be writing this trial; do not infer
                    # that the trial has no Compose project.
                    raise ValueError("trial config missing")
                if stat.S_ISLNK(cfg_info.st_mode) or not stat.S_ISREG(cfg_info.st_mode):
                    raise ValueError("config is not a regular file")
                before = (cfg_info.st_dev, cfg_info.st_ino, cfg_info.st_size,
                          cfg_info.st_mtime_ns)
                # O_NOFOLLOW plus dir_fd prevents both symlink traversal and
                # path-component replacement during open.
                fd = os.open("config.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                             dir_fd=job_fd)
                try:
                    opened_info = os.fstat(fd)
                    opened = (opened_info.st_dev, opened_info.st_ino, opened_info.st_size,
                              opened_info.st_mtime_ns)
                    if opened != before or not stat.S_ISREG(opened_info.st_mode):
                        raise ValueError("config changed before open")
                    with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
                        raw = stream.read(2_000_001)
                finally:
                    os.close(fd)
                after_info = os.stat("config.json", dir_fd=job_fd, follow_symlinks=False)
            finally:
                os.close(job_fd)
            after = (after_info.st_dev, after_info.st_ino, after_info.st_size, after_info.st_mtime_ns)
            if before != after or len(raw) > 2_000_000:
                raise ValueError("config changed during read or exceeds size limit")
            config = json.loads(raw)
            if not isinstance(config, dict):
                raise ValueError("config must be a JSON object")
            found.update(_projects(config))
            configs_seen += 1
        if configs_seen == 0:
            raise ValueError("no trial configs found")
        return {"status": "KNOWN", "projects": sorted(found), "configs_seen": configs_seen,
                "inventory_complete": False}
    except Exception as exc:
        return {"status": "UNKNOWN", "projects": [], "configs_seen": 0,
                "inventory_complete": False, "error": str(exc) or type(exc).__name__}
    finally:
        if root_fd is not None:
            os.close(root_fd)
