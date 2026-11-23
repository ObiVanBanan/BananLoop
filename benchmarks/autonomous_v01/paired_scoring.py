"""Score a pair of frozen artifacts with one caller-supplied public DEV scorer."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from .runner import canonical_sha, read_json

SYSTEMS = ("codex_direct", "bananloop_full")


def _safe_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not relative.parts or relative.is_absolute() or ".." in relative.parts
            or "\\" in name or relative.as_posix() != name):
        raise ValueError("invalid relative archive path")
    path = root.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("archive path is missing or unsafe") from exc
    if resolved != path or not path.is_file():
        raise ValueError("archive path is missing or unsafe")
    return path


def _verified_bytes(root: Path, index: Mapping[str, dict], name: str) -> bytes:
    entry = index.get(name)
    if entry is None:
        raise ValueError(f"archive file is not indexed: {name}")
    path = _safe_path(root, name)
    if path.stat().st_size != entry.get("size_bytes"):
        raise ValueError(f"archive file size differs from its seal: {name}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        raise ValueError(f"archive file digest differs from its seal: {name}")
    return raw


def _capture(root: Path, index: Mapping[str, dict], arm_id: str,
             artifact: object) -> tuple[str, dict[str, bytes]]:
    if not isinstance(artifact, dict):
        return "UNKNOWN_OUTPUT", {}
    status = artifact.get("status")
    if status == "MISSING_OUTPUT":
        return "MISSING_OUTPUT", {}
    if status != "CAPTURED":
        return ("UNKNOWN_OUTPUT" if status in (None, "UNKNOWN") else "INVALID_OUTPUT"), {}
    entries = artifact.get("files")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        return "CORRUPT_OUTPUT", {}
    prefix = f"{arm_id}/final-artifacts/"
    files = {}
    try:
        total_bytes = 0
        for entry in entries:
            path = entry["path"]
            size = entry["size_bytes"]
            if (not isinstance(path, str) or path in files or type(size) is not int
                    or not 0 <= size <= 64_000_000 or total_bytes + size > 128_000_000):
                raise ValueError("malformed final artifact file record")
            relative = PurePosixPath(path)
            if relative.is_absolute() or not relative.parts or relative.as_posix() != path:
                raise ValueError("malformed final artifact file path")
            indexed = index[prefix + path]
            if indexed.get("sha256") != entry["sha256"] or indexed.get("size_bytes") != size:
                raise ValueError("final artifact receipt differs from its archive index")
            files[path] = _verified_bytes(root, index, prefix + path)
            total_bytes += size
        if {name for name in index if name.startswith(prefix)} != {prefix + name for name in files}:
            raise ValueError("indexed final artifact files differ from the receipt")
        if "entrypoint" in artifact:
            name = PurePosixPath(artifact["entrypoint"]).name
            if name not in files or hashlib.sha256(files[name]).hexdigest() != artifact.get("entrypoint_sha256"):
                raise ValueError("final entrypoint differs from its receipt")
    except (KeyError, TypeError, ValueError, OSError):
        return "CORRUPT_OUTPUT", {}
    return "READY", files


def score_frozen_pair(archive_root: Path, task_id: str,
                      scorer: Callable[[str, Mapping[str, bytes]], int | float]) -> dict[str, Any]:
    """Check both terminal receipts and load both snapshots before scoring either."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", task_id):
        raise ValueError("invalid paired task ID")
    if archive_root.is_symlink() or not archive_root.is_dir():
        raise ValueError("invalid paired archive directory")
    root = archive_root.resolve()
    seal = read_json(root / "SHA256SUMS.json")
    entries = seal.get("files")
    if (not isinstance(entries, list)
            or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) for item in entries)):
        raise ValueError("invalid paired archive index")
    index = {entry["path"]: entry for entry in entries}
    if len(index) != len(entries):
        raise ValueError("duplicate paths in paired archive index")

    manifest = json.loads(_verified_bytes(root, index, "experiment-manifest.json"))
    if not isinstance(manifest, dict):
        raise ValueError("invalid frozen series manifest")
    digest = manifest.get("manifest_sha256")
    if digest != seal.get("manifest_sha256") or digest != canonical_sha({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }):
        raise ValueError("paired archive manifest digest does not match")
    if "arm_ids" in manifest:
        arm_ids = manifest["arm_ids"]
        if not isinstance(arm_ids, list) or any(f"{task_id}--{system}" not in arm_ids for system in SYSTEMS):
            raise ValueError("pair is absent from the frozen series manifest")
    else:
        task_entries = manifest.get("tasks")
        task_entries = task_entries if isinstance(task_entries, list) else []
        pairs = [entry for entry in task_entries if isinstance(entry, dict)
                 and isinstance(entry.get("task"), dict) and entry["task"].get("task_id") == task_id]
        if (len(pairs) != 1 or not isinstance(pairs[0].get("arms"), list)
                or len(pairs[0]["arms"]) != 2 or any(not isinstance(arm, dict) for arm in pairs[0]["arms"])
                or any(sum(arm.get("system") == system for arm in pairs[0]["arms"]) != 1
                       for system in SYSTEMS)):
            raise ValueError("pair is absent from the frozen series manifest")

    rows = {}
    snapshots = {}
    for system in SYSTEMS:
        arm_id = f"{task_id}--{system}"
        record = json.loads(_verified_bytes(root, index, f"{arm_id}/result.json"))
        if (not isinstance(record, dict) or record.get("arm_id") != arm_id
                or record.get("task_id") != task_id or record.get("system") != system
                or not isinstance(record.get("result"), dict)):
            raise ValueError("invalid frozen arm receipt")
        result = record["result"]
        arm_status = result.get("status")
        if (not isinstance(arm_status, str) or not arm_status
                or arm_status in {"RUNNING", "PENDING", "READY", "STARTED",
                                  "QUEUED", "LEASED", "DISPATCHED", "IN_PROGRESS"}):
            raise ValueError("paired scoring requires two terminal arm receipts")
        status, snapshots[system] = _capture(root, index, arm_id, result.get("final_artifact"))
        usage = result.get("usage")
        rows[system] = {"status": status, "raw_score": None, "arm_status": arm_status,
                        "usage": usage if isinstance(usage, dict) and usage
                        and not result.get("usage_unknown") else "UNKNOWN"}

    for system, row in rows.items():
        if row["status"] != "READY":
            continue
        try:
            score = scorer(task_id, MappingProxyType(snapshots[system]))
            if type(score) not in (float, int) or not math.isfinite(score):
                raise ValueError("scorer must return one finite numeric metric")
        except Exception as exc:
            row.update(status="SCORER_FAILED", failure_type=type(exc).__name__)
        else:
            row.update(status="SCORED", raw_score=score)
    return {"task_id": task_id, "manifest_sha256": digest, "arms": rows}
