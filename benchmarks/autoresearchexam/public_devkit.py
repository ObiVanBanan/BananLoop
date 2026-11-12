"""Run the public ARE development panel in a networkless, read-only container."""
from __future__ import annotations

import math
import os
import re
import select
import subprocess
import tempfile
import time
from uuid import uuid4
from pathlib import Path
from typing import Any


_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_PANEL_RESULT = re.compile(
    r"(?m)^panel mean over (?P<count>[0-9]+) cells: "
    r"(?P<score>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)$"
)
_MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
_MAX_STDOUT_BYTES = 1024 * 1024


class PublicDevkitError(RuntimeError):
    """The public development evaluator could not produce a trusted aggregate."""


def run_public_devkit(
    candidate: bytes,
    *,
    image: str,
    expected_image_id: str,
    timeout_seconds: int,
    expected_cell_count: int = 42,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    """Evaluate one candidate with the task's public devkit and no network.

    The image must be built from the pinned task's `environment/` directory.
    Only the candidate's `impute.py` is mounted into its public `/app/output`.
    The devkit process runs inside a networkless container with a read-only root.
    Only its terminal aggregate is returned; per-cell stdout is not propagated.
    """
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > _MAX_CANDIDATE_BYTES:
        raise PublicDevkitError("candidate source is empty or exceeds the file-size limit")
    if (not isinstance(image, str) or not image or any(ch.isspace() for ch in image)
            or image.startswith("-") or not _IMAGE_ID.fullmatch(expected_image_id)):
        raise PublicDevkitError("public DEV container image pin is malformed")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 3600):
        raise PublicDevkitError("public DEV timeout must be between 1 and 3600 seconds")
    if (isinstance(expected_cell_count, bool) or not isinstance(expected_cell_count, int)
            or not 1 <= expected_cell_count <= 10_000):
        raise PublicDevkitError("public DEV cell count is invalid")

    try:
        started = time.monotonic()
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicDevkitError("could not inspect pinned public DEV image") from None
    if inspected.returncode != 0 or inspected.stdout.strip() != expected_image_id:
        raise PublicDevkitError("public DEV image ID differs from the frozen protocol pin")
    container_timeout = int(timeout_seconds - (time.monotonic() - started))
    if container_timeout < 1:
        raise PublicDevkitError("public DEV time budget expired during image verification")

    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-are-dev-") as temp_name:
            temp = Path(temp_name)
            temp.chmod(0o755)
            output = temp / "output"
            output.mkdir(mode=0o755)
            candidate_path = output / "impute.py"
            candidate_path.write_bytes(candidate)
            candidate_path.chmod(0o444)
            argv = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", "bananloop-are-dev-" + uuid4().hex,
                "--pids-limit", "32", "--memory", "1g", "--cpus", "1",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                "--mount", f"type=bind,src={output},dst=/app/output,readonly",
                "--entrypoint", "python3", image,
                "/app/devkit.py", "--module", "/app/output",
            ]
            container_name = argv[argv.index("--name") + 1]
            stdout = _run_bounded_container(
                argv, docker_binary=docker_binary, container_name=container_name,
                timeout_seconds=container_timeout,
            )
            if stdout is None:
                raise PublicDevkitError("public ARE DEV container returned an error")
            matches = list(_PANEL_RESULT.finditer(stdout))
            if len(matches) != 1:
                raise PublicDevkitError("public ARE DEV output lacks one terminal panel aggregate")
            count = int(matches[0].group("count"))
            score = float(matches[0].group("score"))
            if count != expected_cell_count or not math.isfinite(score):
                raise PublicDevkitError("public ARE DEV aggregate does not match its frozen panel")
            return {"status": "GRADED", "dev_score": score, "dev_cells": count}
    except OSError:
        raise PublicDevkitError("could not prepare the isolated public DEV candidate") from None


def _run_bounded_container(
    argv: list[str], *, docker_binary: str, container_name: str,
    timeout_seconds: int,
) -> str | None:
    """Read at most one MiB; explicitly stop the named container on failure."""
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise PublicDevkitError("could not start the public ARE DEV container") from None
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    fd = process.stdout.fileno()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PublicDevkitError("public ARE DEV container timed out")
            readable, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if readable:
                chunk = os.read(fd, min(65536, _MAX_STDOUT_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_STDOUT_BYTES:
                    raise PublicDevkitError("public ARE DEV output exceeded its limit")
            elif process.poll() is not None:
                # Keep draining until EOF after the CLI has exited.
                continue
        returncode = process.wait(timeout=max(1, min(10, deadline - time.monotonic())))
        if returncode != 0:
            _stop_container(docker_binary, container_name)
            return None
        try:
            return output.decode("utf-8")
        except UnicodeDecodeError:
            raise PublicDevkitError("public ARE DEV output is not UTF-8") from None
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
    """Best-effort bounded kill/remove; timeout never leaves a running job."""
    for args in (("kill", container_name), ("rm", "-f", container_name)):
        try:
            subprocess.run(
                [docker_binary, *args], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
