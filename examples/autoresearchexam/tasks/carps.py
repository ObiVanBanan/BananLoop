"""Carps star-discrepancy task broker.

The caller owns and retains the case data. ``evaluate_case`` serializes exactly
one points cloud and its public call arguments to the candidate process. No
baseline, labels, other cases, scorer state, or evaluator paths are serialized.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from ..sandbox import CandidateSandbox, SandboxError, run_candidate, stage_candidate_artifact


UPSTREAM_REPOSITORY = "bespokelabsai/AutoResearchExam"
UPSTREAM_COMMIT = "23c0d92e11af1dea821f972b5f0a739060958002"
INSTRUCTION_SHA256 = "83960625fccf0d0287a88a9a4def0828d242f136"
SCORER_SOURCE_SHA256 = "f29fb50a95b3951a5bc97a195544de429e27a07f"
TIME_BUDGET_SECONDS = 10.0
INVALID_DISCREPANCY = 1.0
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class CarpsError(ValueError):
    """Malformed task input or evaluator result."""


@dataclass(frozen=True)
class CarpsCase:
    case_id: str
    points: Any
    k: int
    seed: int


@dataclass(frozen=True)
class CarpsEvaluation:
    case_id: str
    discrepancy: float
    valid: bool
    reason: str | None
    call_seconds: float | None
    returncode: int | None


@dataclass(frozen=True)
class CarpsPanelSummary:
    mean_discrepancy: float
    case_count: int
    valid_runs: int


def _as_points(value: Any) -> list[list[float]]:
    """Validate and copy one cloud into ordinary JSON-compatible numbers."""
    try:
        import numpy as np
    except ImportError as exc:
        raise CarpsError("Carps evaluation requires NumPy in the trusted evaluator") from exc
    points = np.asarray(value)
    if points.dtype != np.float64 or points.ndim != 2 or points.shape[1] != 3:
        raise CarpsError("points must be a float64 array with shape (n, 3)")
    if not points.flags.c_contiguous:
        raise CarpsError("points must be C-contiguous")
    if not 800 <= points.shape[0] <= 4000:
        raise CarpsError("Carps cloud size must be between 800 and 4000")
    if not np.isfinite(points).all() or points.min() < 0.0 or points.max() > 1.0:
        raise CarpsError("Carps coordinates must be finite values in [0, 1]")
    return points.tolist()


def _validate_case(case: CarpsCase) -> tuple[list[list[float]], int, int]:
    if not _CASE_ID.fullmatch(case.case_id):
        raise CarpsError("case_id must be a bounded opaque identifier")
    if isinstance(case.k, bool) or not isinstance(case.k, int) or case.k not in {30, 50, 65}:
        raise CarpsError("k must be one of 30, 50, or 65")
    if isinstance(case.seed, bool) or not isinstance(case.seed, int):
        raise CarpsError("seed must be an integer")
    points = _as_points(case.points)
    if case.k >= len(points):
        raise CarpsError("k must be smaller than the cloud size")
    return points, case.k, case.seed


def validate_indices(raw: Any, *, n: int, k: int) -> list[int]:
    """Apply the upstream grader's output contract without trusting JSON types."""
    if not isinstance(raw, list) or len(raw) != k:
        raise CarpsError(f"candidate must return a flat sequence of exactly {k} indices")
    indices: list[int] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CarpsError(f"index {index} is not an integer")
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            raise CarpsError(f"index {index} is not integral")
        integer = int(value)
        if integer < 0 or integer >= n:
            raise CarpsError(f"index {index} is outside [0, {n})")
        indices.append(integer)
    if len(set(indices)) != k:
        raise CarpsError("candidate returned duplicate indices")
    return indices


def star_discrepancy_3d(points: Any) -> float:
    """Exact L-infinity star discrepancy from upstream grader_core.py.

    Apache-2.0 attribution: bespokelabsai/AutoResearchExam, pinned at
    ``UPSTREAM_COMMIT``; adapted locally to keep the scorer in the trusted
    evaluator process. Source blob SHA: ``SCORER_SOURCE_SHA256``.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise CarpsError("Carps scoring requires NumPy in the trusted evaluator") from exc
    pts = np.ascontiguousarray(np.asarray(points, dtype=np.float64))
    if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
        raise CarpsError("expected a non-empty (k, 3) selected-point array")
    if not np.isfinite(pts).all() or pts.min() < 0.0 or pts.max() > 1.0:
        raise CarpsError("selected points must lie in [0,1]^3")
    k = pts.shape[0]
    grids: list[Any] = []
    indices: list[Any] = []
    for dimension in range(3):
        grid = np.unique(np.concatenate([pts[:, dimension], np.array([1.0])]))
        grids.append(grid)
        indices.append(np.searchsorted(grid, pts[:, dimension]))
    shape = tuple(len(grid) for grid in grids)
    histogram = np.zeros(shape, dtype=np.int64)
    np.add.at(histogram, (indices[0], indices[1], indices[2]), 1)
    closed = histogram.cumsum(0).cumsum(1).cumsum(2)
    open_box = np.zeros(shape, dtype=np.int64)
    open_box[1:, 1:, 1:] = closed[:-1, :-1, :-1]
    volume = grids[0][:, None, None] * grids[1][None, :, None] * grids[2][None, None, :]
    over = closed / k - volume
    under = volume - open_box / k
    return float(max(over.max(), under.max()))


def random_subset_baseline(case: CarpsCase) -> float:
    """Score the pinned uniform-random subset reference for one case."""
    points, k, seed = _validate_case(case)
    import numpy as np
    array = np.asarray(points, dtype=np.float64)
    indices = np.random.default_rng(seed).choice(len(array), k, replace=False)
    return star_discrepancy_3d(array[indices])


def summarize_panel(
    expected_case_ids: tuple[str, ...],
    results: tuple[CarpsEvaluation, ...],
) -> CarpsPanelSummary:
    """Aggregate a frozen task tier; reject missing, duplicate, or extra cases."""
    if not expected_case_ids or len(set(expected_case_ids)) != len(expected_case_ids):
        raise CarpsError("expected Carps panel IDs must be non-empty and unique")
    result_ids = [row.case_id for row in results]
    if len(set(result_ids)) != len(result_ids) or set(result_ids) != set(expected_case_ids):
        raise CarpsError("Carps result IDs do not exactly match the frozen panel")
    values: list[float] = []
    valid_runs = 0
    for result in results:
        if not math.isfinite(result.discrepancy) or not 0.0 <= result.discrepancy <= 1.0:
            raise CarpsError("Carps panel contains an invalid discrepancy")
        if result.valid:
            valid_runs += 1
        elif result.discrepancy != INVALID_DISCREPANCY:
            raise CarpsError("invalid Carps results must carry the worst metric penalty")
        values.append(result.discrepancy)
    return CarpsPanelSummary(sum(values) / len(values), len(values), valid_runs)


def evaluate_case(
    sandbox: CandidateSandbox,
    case: CarpsCase,
    *,
    evaluator_root: Path,
    candidate_root: Path,
) -> CarpsEvaluation:
    """Run one case and score it outside Docker; failures receive worst metric 1.0.

    ``evaluator_root`` must be a private, non-agent-visible directory. The
    candidate artifact must already be frozen and the agent stopped before this
    function is called. This adapter does not prove those host-level facts.
    """
    points, k, seed = _validate_case(case)
    payload = json.dumps(
        {"points": points, "k": k, "seed": seed, "time_budget_s": TIME_BUDGET_SECONDS},
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > sandbox.input_limit_bytes:
        raise CarpsError("serialized one-case input exceeds the sandbox limit")

    root = evaluator_root.resolve(strict=True)
    if not root.is_dir() or evaluator_root.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(evaluator_root)
    ):
        raise CarpsError("evaluator_root must be a real private directory")
    candidate_source = candidate_root.resolve(strict=True)
    try:
        root.relative_to(candidate_source)
    except ValueError:
        pass
    else:
        raise CarpsError("evaluator_root cannot be inside candidate_root")
    try:
        candidate_source.relative_to(root)
    except ValueError:
        pass
    else:
        raise CarpsError("candidate_root cannot be inside evaluator_root")
    if not candidate_source.is_dir() or candidate_root.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(candidate_root)
    ):
        raise CarpsError("candidate_root must be a real directory")
    if sandbox.command != ("python", "-I", "/opt/are/carps_runner.py"):
        raise CarpsError("Carps runtime must use the pinned image's trusted runner entry point")
    artifact_path = Path(tempfile.mkdtemp(prefix="carps-case-", dir=root)) / "candidate"
    try:
        staged = stage_candidate_artifact(candidate_root, artifact_path, ("select_subset.py",))
    except (OSError, SandboxError) as exc:
        _make_tree_writable(artifact_path.parent)
        shutil.rmtree(artifact_path.parent, ignore_errors=True)
        return CarpsEvaluation(case.case_id, INVALID_DISCREPANCY, False, f"candidate staging failed: {exc}"[:240], None, None)
    case_sandbox = replace(
        sandbox,
        artifact_dir=staged,
        timeout_seconds=TIME_BUDGET_SECONDS + 5,
        cpu_count=1.0,
        input_limit_bytes=min(sandbox.input_limit_bytes, 16 * 1024**2),
    )
    try:
        # The independent scorer runs only after the candidate container exits.
        try:
            result = run_candidate(case_sandbox, case_input=payload)
        except SandboxError as exc:
            return CarpsEvaluation(case.case_id, INVALID_DISCREPANCY, False, str(exc)[:240], None, None)
        if result.returncode != 0:
            return CarpsEvaluation(case.case_id, INVALID_DISCREPANCY, False, "candidate process failed", None, result.returncode)
        try:
            response = json.loads(result.stdout.decode("utf-8"))
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise CarpsError("candidate runner returned no successful result")
            if isinstance(response.get("call_s"), bool):
                raise CarpsError("candidate runner returned invalid call duration")
            call_seconds = float(response.get("call_s"))
            if not math.isfinite(call_seconds) or call_seconds < 0.0 or call_seconds > TIME_BUDGET_SECONDS:
                raise CarpsError("candidate exceeded the 10 second function budget")
            indices = validate_indices(response.get("result"), n=len(points), k=k)
            import numpy as np
            selected = np.asarray(points, dtype=np.float64)[indices]
            discrepancy = star_discrepancy_3d(selected)
        except (CarpsError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError) as exc:
            return CarpsEvaluation(case.case_id, INVALID_DISCREPANCY, False, str(exc)[:240], None, result.returncode)
        return CarpsEvaluation(case.case_id, discrepancy, True, None, call_seconds, result.returncode)
    finally:
        _make_tree_writable(artifact_path.parent)
        shutil.rmtree(artifact_path.parent, ignore_errors=True)


def _make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    for directory, _children, _files in os.walk(root):
        try:
            Path(directory).chmod(0o700)
        except OSError:
            pass
        for child in Path(directory).iterdir():
            try:
                if child.is_file() and not child.is_symlink():
                    child.chmod(0o600)
            except OSError:
                pass


def write_candidate_input(points: list[list[float]], k: int, seed: int) -> bytes:
    """Canonical per-case serializer for the image-side runner and unit tests."""
    if len(points) > 4000 or len(points) < 800 or k not in {30, 50, 65} or k >= len(points):
        raise CarpsError("invalid Carps case dimensions")
    return json.dumps(
        {"points": points, "k": k, "seed": seed, "time_budget_s": TIME_BUDGET_SECONDS},
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
