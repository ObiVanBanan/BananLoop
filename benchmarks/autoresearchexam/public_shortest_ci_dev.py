"""Bounded public DEV evaluation for AutoResearchExam's shortest-valid-CI task.

The evaluator uses only the task's public ``panel.py`` and ``dev_panel.json``.
The candidate is evaluated in a pinned task image with no network and a
read-only root. It returns the task's exact aggregate score over a fixed,
bounded seeded subset of the public development settings; this is a pilot
estimate, not the official 2,000-replication score.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


TASK_ID = "shortest-valid-ci-l2-ece"
TASK_VERSION = "1.1.0"
TASK_SHA256 = "5c6344e36db8db95f7a89aca961ba14de65874b27ca61de299ac4e2f54530ba3"
INSTRUCTION_SHA256 = "be11f61a026312478f96b11af65a2872d7108682970b6a1d686fdf7996739726"
PANEL_SOURCE_SHA256 = "9632aaea88db6983db037cbf546b8d79983b757fc68d6d9760a210c264a6297c"
DEV_PANEL_SHA256 = "584b0d154d072140ad893f53487287d9e75bdfe4861846d5e6f1587f6178d2a9"
ROOT_SEED = 20261113
DEFAULT_REPLICATIONS_PER_SETTING = 32
EXPECTED_SETTINGS = 24
ALPHA = 0.10
PER_CALL_TIMEOUT_SECONDS = 2.0
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
RESULT_PREFIX = "PUBLIC_SHORTEST_CI_RESULT="


class PublicShortestCIError(RuntimeError):
    """The public shortest-CI DEV evaluator could not return a valid score."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_public_inputs(app_root: Path) -> tuple[dict[str, Any], Any]:
    panel_path = app_root / "panel.py"
    data_path = app_root / "data" / "dev_panel.json"
    if (panel_path.is_symlink() or data_path.is_symlink()
            or not panel_path.is_file() or not data_path.is_file()):
        raise PublicShortestCIError("pinned public shortest-CI panel inputs are missing or unsafe")
    if (_sha256_file(panel_path) != PANEL_SOURCE_SHA256
            or _sha256_file(data_path) != DEV_PANEL_SHA256):
        raise PublicShortestCIError("public shortest-CI panel inputs differ from their frozen digests")
    try:
        panel_data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise PublicShortestCIError("public shortest-CI DEV panel is malformed") from None
    if not isinstance(panel_data, dict) or set(panel_data) != {"draws"}:
        raise PublicShortestCIError("public shortest-CI DEV panel has an unsupported schema")
    draws = panel_data.get("draws")
    if not isinstance(draws, list) or len(draws) != 3:
        raise PublicShortestCIError("public shortest-CI DEV panel does not contain its pinned draws")
    settings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for draw in draws:
        if not isinstance(draw, dict) or type(draw.get("draw")) is not int:
            raise PublicShortestCIError("public shortest-CI DEV draw is malformed")
        draw_settings = draw.get("settings")
        if not isinstance(draw_settings, list) or len(draw_settings) != 8:
            raise PublicShortestCIError("public shortest-CI DEV draw has the wrong setting count")
        for spec in draw_settings:
            if (not isinstance(spec, dict) or not isinstance(spec.get("id"), str)
                    or spec["id"] in seen or type(spec.get("K")) is not int
                    or type(spec.get("k")) is not int or type(spec.get("n")) is not int
                    or not 1 <= spec["k"] <= 3 or not spec["k"] < spec["K"]
                    or spec["n"] not in {200, 500, 2000, 10000}
                    or not isinstance(spec.get("theta_grid"), list)
                    or not spec["theta_grid"]):
                raise PublicShortestCIError("public shortest-CI DEV setting is malformed")
            seen.add(spec["id"])
            settings.append({"draw": draw["draw"], "spec": spec})
    if len(settings) != EXPECTED_SETTINGS:
        raise PublicShortestCIError("public shortest-CI DEV panel setting count changed")
    spec = importlib.util.spec_from_file_location("bananloop_public_ci_panel", panel_path)
    if spec is None or spec.loader is None:
        raise PublicShortestCIError("public shortest-CI generator could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise PublicShortestCIError("public shortest-CI generator failed to load") from None
    for function in ("theta_for", "draw_replication"):
        if not callable(getattr(module, function, None)):
            raise PublicShortestCIError("public shortest-CI generator lacks a required entrypoint")
    return {"settings": settings}, module


def _candidate_worker(candidate_path: Path, request_path: Path, seed: int) -> int:
    """Run one candidate call in a child process so its code cannot alter scoring."""
    try:
        import numpy as np

        if (candidate_path.is_symlink() or not candidate_path.is_file()
                or candidate_path.stat().st_size > MAX_CANDIDATE_BYTES
                or request_path.is_symlink() or not request_path.is_file()):
            return 2
        with np.load(request_path, allow_pickle=False) as archive:
            if set(archive.files) != {"Z", "Y", "k"}:
                return 2
            Z = np.asarray(archive["Z"], dtype=np.float64)
            Y = np.asarray(archive["Y"], dtype=np.int64)
            k = int(archive["k"])
        module_spec = importlib.util.spec_from_file_location("shortest_ci_candidate", candidate_path)
        if module_spec is None or module_spec.loader is None:
            return 2
        module = importlib.util.module_from_spec(module_spec)
        sys.path.insert(0, str(candidate_path.parent))
        # Candidate stdout/stderr must not be able to forge or flood the
        # evaluator's one-line aggregate protocol.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                module_spec.loader.exec_module(module)
                function = getattr(module, "ece2_interval", None)
                if not callable(function):
                    return 2
                result = function(Z, Y, k, ALPHA, np.random.default_rng(seed))
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            return 2
        low, high = result
        if (isinstance(low, (bool, np.bool_)) or isinstance(high, (bool, np.bool_))
                or not isinstance(low, (int, float, np.number))
                or not isinstance(high, (int, float, np.number))):
            return 2
        low, high = float(low), float(high)
        if (not math.isfinite(low) or not math.isfinite(high)
                or low < 0.0 or high < low or high > float(k)):
            return 2
        sys.stdout.write(json.dumps({"lo": low, "hi": high}, separators=(",", ":")) + "\n")
        return 0
    except BaseException:
        return 2


def evaluate_public_panel(
    *, candidate_path: Path, app_root: Path,
    replications_per_setting: int = DEFAULT_REPLICATIONS_PER_SETTING,
) -> dict[str, Any]:
    """Evaluate the exact public task metric over a fixed bounded seed panel."""
    if (type(replications_per_setting) is not int
            or replications_per_setting != DEFAULT_REPLICATIONS_PER_SETTING):
        raise PublicShortestCIError("this protocol pins exactly 32 replications per public setting")
    candidate_path, app_root = candidate_path.resolve(), app_root.resolve()
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise PublicShortestCIError("candidate interval module is missing or unsafe")
    if candidate_path.stat().st_size > MAX_CANDIDATE_BYTES:
        raise PublicShortestCIError("candidate interval module exceeds its file-size limit")
    panel, generator = _verify_public_inputs(app_root)
    try:
        import numpy as np
    except ImportError:
        raise PublicShortestCIError("numpy is required by the public task image") from None

    root = Path(tempfile.gettempdir())
    request_path = root / ("bananloop-shortest-ci-request-" + uuid4().hex + ".npz")
    score_sum = 0.0
    total_replications = 0
    try:
        with request_path.open("xb") as request_stream:
            request_path.chmod(0o600)
            request_stream.close()
        setting_scores: list[float] = []
        for setting_index, entry in enumerate(panel["settings"]):
            spec = entry["spec"]
            covered = 0
            relative_lengths: list[float] = []
            for repetition in range(replications_per_setting):
                seed_seq = np.random.SeedSequence(
                    [ROOT_SEED, int(entry["draw"]), setting_index, repetition],
                )
                theta_seq, data_seq, candidate_seq = seed_seq.spawn(3)
                theta = float(generator.theta_for(spec, np.random.default_rng(theta_seq)))
                Z, Y = generator.draw_replication(spec, theta, np.random.default_rng(data_seq))
                k = int(spec["k"])
                with request_path.open("wb") as stream:
                    np.savez(stream, Z=Z, Y=Y, k=np.asarray(k, dtype=np.int64))
                candidate_seed = int(candidate_seq.generate_state(1, dtype=np.uint64)[0])
                try:
                    result = subprocess.run(
                        [sys.executable, "-I", str(Path(__file__).resolve()),
                         "--candidate-worker", str(candidate_path), str(request_path),
                         str(candidate_seed)],
                        stdin=subprocess.DEVNULL, capture_output=True, timeout=PER_CALL_TIMEOUT_SECONDS,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    raise PublicShortestCIError("public shortest-CI candidate call failed or timed out") from None
                if result.returncode != 0 or len(result.stdout) > 4096:
                    raise PublicShortestCIError("public shortest-CI candidate returned an invalid interval")
                try:
                    response = json.loads(result.stdout.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise PublicShortestCIError("public shortest-CI candidate returned malformed output") from None
                if not isinstance(response, dict) or set(response) != {"lo", "hi"}:
                    raise PublicShortestCIError("public shortest-CI candidate returned malformed output")
                lo, hi = response["lo"], response["hi"]
                if (isinstance(lo, bool) or isinstance(hi, bool)
                        or not isinstance(lo, (int, float)) or not isinstance(hi, (int, float))
                        or not math.isfinite(lo) or not math.isfinite(hi)
                        or lo < 0.0 or hi < lo or hi > k):
                    raise PublicShortestCIError("public shortest-CI candidate returned an invalid interval")
                covered += int(lo <= theta <= hi)
                relative_lengths.append((hi - lo) / k)
                total_replications += 1
            coverage = covered / replications_per_setting
            setting_score = (
                sum(relative_lengths) / replications_per_setting if coverage >= 0.88 else 1.0
            )
            score_sum += setting_score
            setting_scores.append(setting_score)
        metric = score_sum / len(setting_scores)
        if not math.isfinite(metric) or not 0.0 <= metric <= 1.0:
            raise PublicShortestCIError("public shortest-CI aggregate metric is invalid")
        return {
            "status": "GRADED",
            "task_id": TASK_ID,
            "task_version": TASK_VERSION,
            "metric": metric,
            "panel_count": len(setting_scores),
            "replications_per_setting": replications_per_setting,
            "total_replications": total_replications,
            "root_seed": ROOT_SEED,
            "task_sha256": TASK_SHA256,
            "instruction_sha256": INSTRUCTION_SHA256,
            "panel_source_sha256": PANEL_SOURCE_SHA256,
            "dev_panel_sha256": DEV_PANEL_SHA256,
        }
    finally:
        try:
            request_path.unlink()
        except FileNotFoundError:
            pass


def run_public_shortest_ci_dev(
    candidate: bytes,
    *,
    image: str,
    expected_image_id: str,
    timeout_seconds: int,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    """Run the bounded public panel in a networkless, read-only task container."""
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > MAX_CANDIDATE_BYTES:
        raise PublicShortestCIError("candidate interval module is empty or exceeds the file-size limit")
    if (not isinstance(image, str) or not image or any(ch.isspace() for ch in image)
            or image.startswith("-") or not IMAGE_ID.fullmatch(expected_image_id)):
        raise PublicShortestCIError("public shortest-CI DEV image pin is malformed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise PublicShortestCIError("public shortest-CI DEV timeout must be between 1 and 3600 seconds")
    try:
        started = time.monotonic()
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicShortestCIError("could not inspect pinned public shortest-CI image") from None
    if inspected.returncode != 0 or inspected.stdout.strip() != expected_image_id:
        raise PublicShortestCIError("public shortest-CI image ID differs from its frozen pin")
    remaining = int(timeout_seconds - (time.monotonic() - started))
    if remaining < 1:
        raise PublicShortestCIError("public shortest-CI DEV budget expired during image verification")

    # Reuse the shared bounded Docker process supervisor, without changing or
    # broadening the shared Harbor bridge.
    from .public_devkit import PublicDevkitError, _run_bounded_container

    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-are-ci-") as temp_name:
            temp = Path(temp_name)
            temp.chmod(0o755)
            output = temp / "output"
            output.mkdir(mode=0o755)
            candidate_path = output / "interval.py"
            candidate_path.write_bytes(candidate)
            candidate_path.chmod(0o444)
            evaluator_path = temp / "public_shortest_ci_dev.py"
            evaluator_path.write_bytes(Path(__file__).read_bytes())
            evaluator_path.chmod(0o444)
            container_name = "bananloop-are-ci-" + uuid4().hex
            argv = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", container_name, "--pids-limit", "64", "--memory", "4g", "--cpus", "1",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                "--mount", f"type=bind,src={output},dst=/app/output,readonly",
                "--mount", f"type=bind,src={evaluator_path},dst=/tmp/public_shortest_ci_dev.py,readonly",
                "--entrypoint", "python3", image,
                "/tmp/public_shortest_ci_dev.py", "--evaluate-public-panel",
                "--candidate", "/app/output/interval.py", "--app-root", "/app",
                "--replications-per-setting", str(DEFAULT_REPLICATIONS_PER_SETTING),
            ]
            try:
                stdout = _run_bounded_container(
                    argv, docker_binary=docker_binary, container_name=container_name,
                    timeout_seconds=remaining,
                )
            except PublicDevkitError:
                raise PublicShortestCIError("public shortest-CI DEV container failed or timed out") from None
            if stdout is None:
                raise PublicShortestCIError("public shortest-CI DEV container returned an error")
            matches = [line[len(RESULT_PREFIX):] for line in stdout.splitlines()
                       if line.startswith(RESULT_PREFIX)]
            if len(matches) != 1:
                raise PublicShortestCIError("public shortest-CI output lacks one terminal aggregate")
            try:
                result = json.loads(matches[0])
            except json.JSONDecodeError:
                raise PublicShortestCIError("public shortest-CI aggregate is malformed") from None
            expected_fields = {
                "status", "task_id", "task_version", "metric", "panel_count",
                "replications_per_setting", "total_replications", "root_seed",
                "task_sha256", "instruction_sha256", "panel_source_sha256", "dev_panel_sha256",
            }
            if (not isinstance(result, dict) or set(result) != expected_fields
                    or result.get("status") != "GRADED" or result.get("task_id") != TASK_ID
                    or result.get("task_version") != TASK_VERSION
                    or result.get("panel_count") != EXPECTED_SETTINGS
                    or result.get("replications_per_setting") != DEFAULT_REPLICATIONS_PER_SETTING
                    or result.get("total_replications") != EXPECTED_SETTINGS * DEFAULT_REPLICATIONS_PER_SETTING
                    or result.get("root_seed") != ROOT_SEED
                    or result.get("task_sha256") != TASK_SHA256
                    or result.get("instruction_sha256") != INSTRUCTION_SHA256
                    or result.get("panel_source_sha256") != PANEL_SOURCE_SHA256
                    or result.get("dev_panel_sha256") != DEV_PANEL_SHA256
                    or isinstance(result.get("metric"), bool)
                    or not isinstance(result.get("metric"), (int, float))
                    or not math.isfinite(result["metric"])
                    or not 0.0 <= result["metric"] <= 1.0):
                raise PublicShortestCIError("public shortest-CI aggregate does not match its frozen scope")
            return result
    except OSError:
        raise PublicShortestCIError("could not prepare isolated public shortest-CI DEV candidate") from None


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--candidate-worker":
        return _candidate_worker(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    if (len(sys.argv) == 8 and sys.argv[1] == "--evaluate-public-panel"
            and sys.argv[2] == "--candidate" and sys.argv[4] == "--app-root"
            and sys.argv[6] == "--replications-per-setting"):
        try:
            result = evaluate_public_panel(
                candidate_path=Path(sys.argv[3]), app_root=Path(sys.argv[5]),
                replications_per_setting=int(sys.argv[7]),
            )
        except (PublicShortestCIError, ValueError, OSError):
            return 2
        sys.stdout.write(RESULT_PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
