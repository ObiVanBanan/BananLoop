"""Narrow Docker cleanup for one already-verified Compose project.

This primitive only establishes Docker resource ownership. It does not correlate
containers with Harbor sessions or benchmark trials.
"""
from __future__ import annotations

import re
import subprocess
from typing import Callable

PROJECT_LABEL = "com.docker.compose.project"
_PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-fA-F]{12,64}\Z")


class CleanupUnknown(RuntimeError):
    """Cleanup could not be proven safe or complete."""


def cleanup_compose_project(
    project_id: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    """Remove only containers with the exact Compose project label.

    `project_id` must be a previously authenticated project ID. Every candidate
    is independently inspected immediately before removal, and removal is only
    reported as complete after Docker confirms that exact container is absent.
    Any command/parse/ownership uncertainty returns UNKNOWN without proceeding.
    """
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        return {"status": "UNKNOWN", "removed": [], "error": "invalid project ID"}

    def docker(*args: str) -> subprocess.CompletedProcess[str]:
        result = run(["docker", *args], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise CleanupUnknown((result.stderr or "docker command failed").strip())
        return result

    removed: list[str] = []
    try:
        listed = docker("ps", "-aq", "--filter", f"label={PROJECT_LABEL}={project_id}")
        ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if any(not _CONTAINER_ID.fullmatch(container_id) for container_id in ids):
            raise CleanupUnknown("docker returned an invalid container ID")

        for container_id in ids:
            # Re-read ownership immediately before acting; list filters alone
            # are not trusted as authorization to delete.
            inspected = docker(
                "inspect", "--format",
                f'{{{{ index .Config.Labels "{PROJECT_LABEL}" }}}}', container_id,
            )
            if inspected.stdout.strip() != project_id:
                raise CleanupUnknown(f"container {container_id} has a different project label")
            docker("rm", "-f", container_id)
            # Exact object inspection must fail specifically because it is gone.
            check = run(["docker", "inspect", container_id], capture_output=True, text=True, check=False)
            if check.returncode == 0:
                raise CleanupUnknown(f"container {container_id} remains after removal")
            message = (check.stderr or "").strip()
            absent = re.fullmatch(
                rf"(?:error:\s*)?no such object:\s*{re.escape(container_id)}",
                message,
                flags=re.IGNORECASE,
            )
            if absent is None:
                raise CleanupUnknown("could not verify container absence")
            removed.append(container_id)
        remaining = docker("ps", "-aq", "--filter", f"label={PROJECT_LABEL}={project_id}")
        remaining_ids = [line.strip() for line in remaining.stdout.splitlines() if line.strip()]
        if remaining_ids:
            raise CleanupUnknown("project containers remain after cleanup")
        return {"status": "CLEANED", "removed": removed}
    except Exception as exc:
        return {"status": "UNKNOWN", "removed": removed, "error": str(exc) or type(exc).__name__}
