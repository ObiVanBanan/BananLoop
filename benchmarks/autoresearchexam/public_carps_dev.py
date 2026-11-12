"""Network-isolated public DEV evaluator for CARPS star-discrepancy selection.

This evaluator only reads the task's eight public example clouds and one frozen
candidate source file. It never contacts the ARE grader. The candidate runs in a
caller-pinned, networkless Docker image; only one cloud and the candidate are
mounted into each short-lived container. The caller must build/pin a clean task
runtime image that contains Python, NumPy, and the task's allowed packages, but
does not contain grader or hidden-task assets.

The reported metric is the exact (up to IEEE-754 arithmetic) L-infinity star
discrepancy for each selected subset, averaged over the eight public examples.
Lower is better. It is a public diagnostic score, not an official ARE score.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import select
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np


TASK_ID = "carps-star-discrepancy-subset-select"
TASK_VERSION = "1.1.0"
TASK_TOML_SHA256 = "e149dd3b1c959c445e2d06f25ad2753124837bae6011e540ec9e516fdf5c1482"
INSTRUCTION_SHA256 = "77138bdffa578f1211e19fc2c1e74a6fec18323a91dfb97bb7808e5b26427987"
EXAMPLES_MANIFEST_SHA256 = "a2270976daefdc079e3f7bc6477f9f3043e1ae00090b325c989b021c734a96b6"
EXAMPLE_CLOUD_SHA256 = {
    "cloud_00.npy": "e3c113b089b7b7f13429593eebcbdfb71f0374bc9b9a1b4f8e910709911d5b35",
    "cloud_01.npy": "9dae1df7b994f2d75958439cafa6843725d845ab72d164b8c543d8d95c6fa356",
    "cloud_02.npy": "61145848214161145ef956a5ba9cbfb5700f6110d2ba38b225fe338826bc18da",
    "cloud_03.npy": "a4e9c7089ed94a809b406e81d3362701dc8dd8e6f076eae7ac68428590d0dc55",
    "cloud_04.npy": "d1941a6fac230b157ed295c2a6322918bc7f36c87401a837bbc9e81cfbdd0709",
    "cloud_05.npy": "611bd182922e738836f09b4050e96693fbecb30f56c243520194acca646d17c2",
    "cloud_06.npy": "139339b1a17b118221fa5afede47e5c6809e2ae682d3a48d89e5e3db5353bc9b",
    "cloud_07.npy": "6b8095ee6ebe9f5e61a78b18c5d499b4ba5044dbc1955843c59599f18852594c",
}

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$")
_MAX_CANDIDATE_BYTES = 1_000_000
_MAX_STDOUT_BYTES = 256_000
_MAX_PANEL_COUNT = 8
_CANDIDATE_PATH = "select_subset.py"
_PANEL = (
    ("cloud_00.npy", 2436, 30),
    ("cloud_01.npy", 3217, 65),
    ("cloud_02.npy", 1149, 30),
    ("cloud_03.npy", 1374, 30),
    ("cloud_04.npy", 3621, 50),
    ("cloud_05.npy", 872, 65),
    ("cloud_06.npy", 2457, 50),
    ("cloud_07.npy", 2990, 65),
)


class PublicCARPSEvaluatorError(RuntimeError):
    """Public CARPS DEV evaluation failed without a trusted aggregate."""


_CONTAINER_RUNNER = r'''import importlib.util, json, math, sys, time
import numpy as np

module_path, cloud_path, task_id, task_version = sys.argv[1:5]
if task_id != "carps-star-discrepancy-subset-select" or task_version != "1.1.0":
    raise SystemExit(31)
spec = importlib.util.spec_from_file_location("carps_candidate", module_path)
if spec is None or spec.loader is None:
    raise SystemExit(32)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
select = getattr(module, "select", None)
if not callable(select):
    raise SystemExit(33)
points = np.load(cloud_path, allow_pickle=False)
if not isinstance(points, np.ndarray) or points.ndim != 2 or points.shape[1] != 3:
    raise SystemExit(34)
n = int(points.shape[0])
name = cloud_path.rsplit("/", 1)[-1]
expected = {
    "cloud_00.npy": (2436, 30), "cloud_01.npy": (3217, 65),
    "cloud_02.npy": (1149, 30), "cloud_03.npy": (1374, 30),
    "cloud_04.npy": (3621, 50), "cloud_05.npy": (872, 65),
    "cloud_06.npy": (2457, 50), "cloud_07.npy": (2990, 65),
}
if name not in expected or n != expected[name][0] or points.dtype != np.float64:
    raise SystemExit(35)
k = expected[name][1]
if not np.isfinite(points).all() or np.any(points < 0.0) or np.any(points > 1.0):
    raise SystemExit(36)
started = time.monotonic()
result = select(points, k, 0, 10.0)
if time.monotonic() - started > 10.0:
    raise SystemExit(37)
if isinstance(result, (str, bytes, bytearray)):
    raise SystemExit(38)
try:
    iterator = iter(result)
except TypeError:
    raise SystemExit(39)
indices = []
for _ in range(k + 1):
    try:
        item = next(iterator)
    except StopIteration:
        break
    if isinstance(item, (bool, np.bool_)):
        raise SystemExit(40)
    if isinstance(item, (int, np.integer)):
        value = int(item)
    elif isinstance(item, (float, np.floating)):
        number = float(item)
        if not math.isfinite(number) or not number.is_integer():
            raise SystemExit(41)
        value = int(number)
    else:
        raise SystemExit(42)
    indices.append(value)
if len(indices) != k or len(set(indices)) != k or any(i < 0 or i >= n for i in indices):
    raise SystemExit(43)
if time.monotonic() - started > 10.0:
    raise SystemExit(44)
print("CARPS_PUBLIC_RESULT=" + json.dumps({"indices": indices}, separators=(",", ":")))
'''


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_pinned_file(path: Path, expected_sha256: str, *, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PublicCARPSEvaluatorError("pinned public input is missing or unsafe")
    if path.stat().st_size > max_bytes:
        raise PublicCARPSEvaluatorError("pinned public input exceeds its size limit")
    data = path.read_bytes()
    if _sha256_bytes(data) != expected_sha256:
        raise PublicCARPSEvaluatorError("pinned public input digest mismatch")
    return data


def _load_public_panel(task_root: Path) -> tuple[bytes, list[tuple[str, int, int, bytes, np.ndarray]]]:
    """Read only the selected task's public metadata and eight public example clouds."""
    if task_root.is_symlink() or not task_root.is_dir():
        raise PublicCARPSEvaluatorError("public task root is missing or unsafe")
    task_root = task_root.resolve(strict=True)
    task_toml = _read_pinned_file(task_root / "task.toml", TASK_TOML_SHA256, max_bytes=64_000)
    instruction = _read_pinned_file(
        task_root / "instruction.md", INSTRUCTION_SHA256, max_bytes=128_000,
    )
    try:
        import tomllib
        task_config = tomllib.loads(task_toml.decode("utf-8"))
    except (ImportError, UnicodeDecodeError, ValueError):
        raise PublicCARPSEvaluatorError("pinned public task metadata could not be parsed") from None
    task = task_config.get("task", {})
    if task.get("version") != TASK_VERSION or task.get("name") != f"bespokelabs/{TASK_ID}":
        raise PublicCARPSEvaluatorError("public task ID or version does not match the evaluator pin")
    if not instruction.startswith(b"# Low-discrepancy subset selection\n"):
        raise PublicCARPSEvaluatorError("public task instruction does not match the pinned task")

    examples_dir = task_root
    for component in ("environment", "app", "data", "examples"):
        examples_dir = examples_dir / component
        if examples_dir.is_symlink():
            raise PublicCARPSEvaluatorError("public example path contains an unsafe symlink")
    if examples_dir.is_symlink() or not examples_dir.is_dir():
        raise PublicCARPSEvaluatorError("public example directory is missing or unsafe")
    manifest_path = examples_dir / "manifest.json"
    manifest_bytes = _read_pinned_file(
        manifest_path, EXAMPLES_MANIFEST_SHA256, max_bytes=32_000,
    )
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PublicCARPSEvaluatorError("pinned public example manifest is invalid") from None
    records = manifest.get("instances")
    expected_records = [
        {"id": name.removesuffix(".npy"), "file": name, "n": n, "k": k}
        for name, n, k in _PANEL
    ]
    if manifest.get("dim") != 3 or records != expected_records:
        raise PublicCARPSEvaluatorError("public example panel differs from the frozen eight-cloud panel")

    panel = []
    for filename, expected_n, k in _PANEL:
        raw = _read_pinned_file(
            examples_dir / filename, EXAMPLE_CLOUD_SHA256[filename], max_bytes=256_000,
        )
        try:
            points = np.load(__import__("io").BytesIO(raw), allow_pickle=False)
        except (ValueError, OSError):
            raise PublicCARPSEvaluatorError("pinned public example cloud could not be loaded") from None
        if (not isinstance(points, np.ndarray) or points.dtype != np.float64
                or points.shape != (expected_n, 3) or not np.isfinite(points).all()
                or np.any(points < 0.0) or np.any(points > 1.0)):
            raise PublicCARPSEvaluatorError("pinned public example cloud has an invalid schema")
        panel.append((filename, expected_n, k, raw, points))
    return manifest_bytes, panel


def exact_star_discrepancy(points: np.ndarray, selected_indices: list[int] | tuple[int, ...]) -> float:
    """Return exact 3-D L-infinity star discrepancy by enumerating cell corners.

    At a data coordinate, both the value immediately at the coordinate (strict
    `<`) and the right-hand limit (inclusive `<=`) are included. At q=1 only the
    strict state is valid; at q=0 both the exact point and right-hand limit are
    included. These are all extrema of the piecewise-constant count minus the
    continuous box volume over the coordinate cells.
    """
    selected = np.asarray(points[np.asarray(selected_indices, dtype=np.int64)], dtype=np.float64)
    k = int(selected.shape[0])
    if k <= 0 or selected.shape[1:] != (3,):
        raise ValueError("star discrepancy requires a nonempty 3-D point subset")

    axes = [np.unique(np.concatenate(([0.0], selected[:, dim], [1.0]))) for dim in range(3)]
    bins = [np.searchsorted(axes[dim], selected[:, dim], side="left") for dim in range(3)]
    histogram = np.zeros(tuple(len(axis) for axis in axes), dtype=np.int64)
    np.add.at(histogram, tuple(bins), 1)
    prefix = histogram.cumsum(axis=0).cumsum(axis=1).cumsum(axis=2)
    padded = np.pad(prefix, ((1, 0), (1, 0), (1, 0)))

    states: list[tuple[np.ndarray, np.ndarray]] = []
    for axis in axes:
        queries: list[float] = []
        counts: list[int] = []
        for q_value in axis:
            q = float(q_value)
            queries.append(q)
            counts.append(int(np.searchsorted(axis, q, side="left")))
            if q < 1.0:
                queries.append(q)
                counts.append(int(np.searchsorted(axis, q, side="right")))
        states.append((np.asarray(queries, dtype=np.float64), np.asarray(counts, dtype=np.int64)))

    q0, i0 = states[0]
    q1, i1 = states[1]
    q2, i2 = states[2]
    counts = padded[np.ix_(i0, i1, i2)]
    volumes = q0[:, None, None] * q1[None, :, None] * q2[None, None, :]
    discrepancy = np.max(np.abs(counts.astype(np.float64) / k - volumes))
    value = float(discrepancy)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("computed star discrepancy is invalid")
    return value


def _run_one_cloud(
    *, candidate: bytes, cloud_bytes: bytes, filename: str,
    image: str, expected_image_id: str, docker_binary: str,
    timeout_seconds: int,
) -> list[int]:
    n, k = next((n, k) for name, n, k in _PANEL if name == filename)
    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-carps-public-dev-") as temp_name:
            temp = Path(temp_name)
            temp.chmod(0o755)
            output_dir = temp / "output"
            data_dir = temp / "examples"
            runner_dir = temp / "runner"
            output_dir.mkdir(mode=0o755)
            data_dir.mkdir(mode=0o755)
            runner_dir.mkdir(mode=0o755)
            candidate_path = output_dir / _CANDIDATE_PATH
            candidate_path.write_bytes(candidate)
            candidate_path.chmod(0o444)
            cloud_path = data_dir / filename
            cloud_path.write_bytes(cloud_bytes)
            cloud_path.chmod(0o444)
            runner_path = runner_dir / "run_candidate.py"
            runner_path.write_text(_CONTAINER_RUNNER, encoding="utf-8")
            runner_path.chmod(0o444)
            container_name = "bananloop-carps-dev-" + uuid4().hex
            argv = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", container_name, "--user", "65534:65534",
                "--pids-limit", "32", "--memory", "1g", "--cpus", "1",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
                "--env", "PYTHONDONTWRITEBYTECODE=1",
                "--mount", f"type=bind,src={output_dir},dst=/app/output,readonly",
                "--mount", f"type=bind,src={data_dir},dst=/app/data/examples,readonly",
                "--mount", f"type=bind,src={runner_dir},dst=/bananloop-runner,readonly",
                "--entrypoint", "python3", image,
                "-B", "/bananloop-runner/run_candidate.py",
                f"/app/output/{_CANDIDATE_PATH}", f"/app/data/examples/{filename}",
                TASK_ID, TASK_VERSION,
            ]
            stdout = _run_bounded_docker(
                argv, docker_binary=docker_binary, container_name=container_name,
                timeout_seconds=timeout_seconds,
            )
            if stdout is None:
                raise PublicCARPSEvaluatorError("candidate execution failed or exceeded its time limit")
            marker = "CARPS_PUBLIC_RESULT="
            results = [line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)]
            if len(results) != 1:
                raise PublicCARPSEvaluatorError("candidate execution returned no unique result record")
            value = json.loads(results[0])
            indices = value.get("indices") if isinstance(value, dict) else None
            if not isinstance(indices, list) or len(indices) != k:
                raise PublicCARPSEvaluatorError("candidate returned an invalid number of indices")
            if any(type(index) is not int for index in indices):
                raise PublicCARPSEvaluatorError("candidate returned a non-integer index")
            if len(set(indices)) != k or any(index < 0 or index >= n for index in indices):
                raise PublicCARPSEvaluatorError("candidate returned duplicate or out-of-range indices")
            return indices
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise PublicCARPSEvaluatorError("public candidate container could not be prepared") from None


def _run_bounded_docker(
    argv: list[str], *, docker_binary: str, container_name: str, timeout_seconds: int,
) -> str | None:
    """Capture bounded stdout and kill/remove a container on timeout or overflow."""
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise PublicCARPSEvaluatorError("could not start the pinned public DEV container") from None
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    fd = process.stdout.fileno()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PublicCARPSEvaluatorError("candidate execution exceeded the per-cloud runtime cap")
            readable, _, _ = select.select([fd], [], [], min(remaining, 0.2))
            if readable:
                chunk = os.read(fd, min(65536, _MAX_STDOUT_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_STDOUT_BYTES:
                    raise PublicCARPSEvaluatorError("candidate output exceeded the capture limit")
            elif process.poll() is not None:
                continue
        return_code = process.wait(timeout=max(1, min(5, deadline - time.monotonic())))
        if return_code != 0:
            _stop_container(docker_binary, container_name)
            return None
        try:
            return output.decode("utf-8")
        except UnicodeDecodeError:
            raise PublicCARPSEvaluatorError("candidate result was not UTF-8") from None
    except BaseException:
        _stop_container(docker_binary, container_name)
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except OSError:
            pass
        raise
    finally:
        process.stdout.close()


def _stop_container(docker_binary: str, container_name: str) -> None:
    for args in (("kill", container_name), ("rm", "-f", container_name)):
        try:
            subprocess.run(
                [docker_binary, *args], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue


def evaluate_carps_public_dev(
    candidate: bytes,
    *,
    public_task_root: Path,
    image: str,
    expected_image_id: str,
    docker_binary: str = "docker",
    total_timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Evaluate a frozen candidate on the eight pinned public example clouds.

    `public_task_root` must point directly to the public
    `carps-star-discrepancy-subset-select` task directory. `image` may be a tag
    or immutable reference, but its local Docker image ID must exactly match
    `expected_image_id`. The image should be a minimal public runtime image;
    it must not contain grader, hidden-task, or unrelated benchmark assets.

    The candidate's `select` function receives `time_budget_s=10.0`. Each call
    gets a 15-second hard wall cap, matching the task's 10-second budget plus
    its documented 5-second process allowance. The whole panel is capped by
    `total_timeout_seconds` (maximum 300 seconds).
    """
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > _MAX_CANDIDATE_BYTES:
        raise ValueError("candidate source is empty or exceeds the size limit")
    if not isinstance(public_task_root, Path):
        raise TypeError("public_task_root must be a pathlib.Path")
    if not isinstance(image, str) or not _IMAGE_REF_RE.fullmatch(image) or image.startswith("-"):
        raise ValueError("Docker image reference is malformed")
    if not isinstance(expected_image_id, str) or not _IMAGE_ID_RE.fullmatch(expected_image_id):
        raise ValueError("expected_image_id must be a pinned Docker SHA-256 image ID")
    if (isinstance(total_timeout_seconds, bool) or not isinstance(total_timeout_seconds, int)
            or not 1 <= total_timeout_seconds <= 300):
        raise ValueError("total_timeout_seconds must be from 1 to 300")
    if not isinstance(docker_binary, str) or not docker_binary or docker_binary.startswith("-"):
        raise ValueError("docker_binary is malformed")

    candidate_digest = _sha256_bytes(candidate)
    panel_manifest, panel = _load_public_panel(public_task_root)
    panel_digest = _sha256_bytes(panel_manifest + b"\n" + b"\n".join(
        f"{name}:{EXAMPLE_CLOUD_SHA256[name]}".encode("ascii") for name, _, _, _, _ in panel
    ))
    started = time.monotonic()
    try:
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(15, total_timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "FAILED", "failure": "PUBLIC_DEV_IMAGE_UNAVAILABLE"}
    if inspected.returncode != 0 or inspected.stdout.strip() != expected_image_id:
        return {"status": "FAILED", "failure": "PUBLIC_DEV_IMAGE_PIN_MISMATCH"}

    metrics = []
    try:
        for filename, _, _, cloud_bytes, points in panel:
            remaining = total_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0.25:
                return {"status": "FAILED", "failure": "PUBLIC_DEV_TOTAL_TIMEOUT"}
            call_cap = min(15, max(1, int(remaining)))
            indices = _run_one_cloud(
                candidate=candidate, cloud_bytes=cloud_bytes, filename=filename,
                image=image,
                expected_image_id=expected_image_id, docker_binary=docker_binary,
                timeout_seconds=call_cap,
            )
            metric = exact_star_discrepancy(points, indices)
            if not math.isfinite(metric):
                return {"status": "FAILED", "failure": "PUBLIC_DEV_INVALID_METRIC"}
            metrics.append(metric)
    except PublicCARPSEvaluatorError:
        return {"status": "FAILED", "failure": "PUBLIC_DEV_CANDIDATE_OR_PANEL_ERROR"}

    if len(metrics) != _MAX_PANEL_COUNT:
        return {"status": "FAILED", "failure": "PUBLIC_DEV_PANEL_INCOMPLETE"}
    aggregate = float(math.fsum(metrics) / len(metrics))
    if not math.isfinite(aggregate):
        return {"status": "FAILED", "failure": "PUBLIC_DEV_INVALID_METRIC"}
    return {
        "status": "GRADED",
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "candidate_sha256": candidate_digest,
        "dev_image_id": expected_image_id,
        "public_panel_sha256": panel_digest,
        "panel_count": len(metrics),
        "metric_name": "mean_exact_linf_star_discrepancy",
        "metric_direction": "lower_is_better",
        "dev_metric": aggregate,
    }
