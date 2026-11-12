"""Run the public SOPCC development evaluator in a bounded networkless container.

Only the candidate policy is mounted into the task image. The task's own public
dev_eval.py enforces per-instance CPU/wall/address-space limits. The adapter
returns aggregate metrics only and never reads the private grader context.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .public_devkit import PublicDevkitError, _run_bounded_container, _stop_container


_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_METRIC = re.compile(
    r"(?m)^pooled metric (?P<metric>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s+"
    r"\(R=(?P<reward>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)), "
    r"F=(?P<failure>[0-9]+(?:\.[0-9]*)?), "
    r"per-episode delivered sd=(?P<sd>[0-9]+(?:\.[0-9]*)?)\)$"
)
_SUMMARY = re.compile(
    r"(?m)^(?P<count>[0-9]+) instances x (?P<episodes>[0-9]+) episodes "
    r"= (?P<total>[0-9]+) episodes$"
)
_MAX_CANDIDATE_BYTES = 2 * 1024 * 1024


class PublicSOPCCError(RuntimeError):
    """The public SOPCC DEV evaluator could not produce a trusted aggregate."""


def run_public_sopcc_dev(
    candidate: bytes,
    *,
    image: str,
    expected_image_id: str,
    timeout_seconds: int,
    draws: tuple[int, ...] = (0,),
    instances_per_draw: int = 1,
    episodes_per_instance: int = 4,
    workers: int = 1,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    """Evaluate a SOPCC ``policy.py`` with the task's public DEV harness.

    The default is deliberately a smoke panel. More expensive public DEV runs
    must be explicitly requested by the prepared evaluator protocol.
    """
    if not isinstance(candidate, bytes) or not candidate or len(candidate) > _MAX_CANDIDATE_BYTES:
        raise PublicSOPCCError("candidate policy is empty or exceeds the file-size limit")
    if (not isinstance(image, str) or not image or any(ch.isspace() for ch in image)
            or image.startswith("-") or not _IMAGE_ID.fullmatch(expected_image_id)):
        raise PublicSOPCCError("public SOPCC DEV image pin is malformed")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 3600):
        raise PublicSOPCCError("public SOPCC DEV timeout must be between 1 and 3600 seconds")
    if (not isinstance(draws, tuple) or not draws or len(draws) > 4
            or any(type(draw) is not int or not 0 <= draw <= 3 for draw in draws)
            or len(set(draws)) != len(draws)):
        raise PublicSOPCCError("draws must be a nonempty tuple of distinct IDs from 0 to 3")
    if (type(instances_per_draw) is not int or not 1 <= instances_per_draw <= 50
            or type(episodes_per_instance) is not int or not 1 <= episodes_per_instance <= 32
            or type(workers) is not int or not 1 <= workers <= 8):
        raise PublicSOPCCError("public SOPCC DEV panel dimensions are out of range")
    if len(draws) * instances_per_draw * episodes_per_instance > 6400:
        raise PublicSOPCCError("public SOPCC DEV panel exceeds the public evaluation panel")

    started = time.monotonic()
    try:
        inspected = subprocess.run(
            [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=min(20, timeout_seconds), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicSOPCCError("could not inspect pinned public SOPCC DEV image") from None
    if inspected.returncode != 0 or inspected.stdout.strip() != expected_image_id:
        raise PublicSOPCCError("public SOPCC DEV image ID differs from the protocol pin")

    left = int(timeout_seconds - (time.monotonic() - started))
    if left < 1:
        raise PublicSOPCCError("public SOPCC DEV budget expired during image verification")
    try:
        with tempfile.TemporaryDirectory(prefix="bananloop-are-sopcc-") as temp_name:
            temp = Path(temp_name)
            temp.chmod(0o755)
            solution = temp / "solution"
            solution.mkdir(mode=0o755)
            policy_path = solution / "policy.py"
            policy_path.write_bytes(candidate)
            policy_path.chmod(0o444)
            name = "bananloop-are-sopcc-" + uuid4().hex
            argv = [
                docker_binary, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--name", name, "--pids-limit", "256", "--memory", "4g", "--cpus", "2",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=768m,mode=1777",
                "--mount", f"type=bind,src={solution},dst=/app/solution,readonly",
                "--entrypoint", "python3", image, "/app/harness/dev_eval.py",
                "--solution", "/app/solution", "--draws", ",".join(map(str, draws)),
                "--instances", str(instances_per_draw), "--episodes", str(episodes_per_instance),
                "--workers", str(workers),
            ]
            stdout = _run_bounded_container(
                argv, docker_binary=docker_binary, container_name=name,
                timeout_seconds=left,
            )
            if stdout is None:
                raise PublicSOPCCError("public SOPCC DEV container returned an error")
            metric_matches = list(_METRIC.finditer(stdout))
            summary_matches = list(_SUMMARY.finditer(stdout))
            if len(metric_matches) != 1 or len(summary_matches) != 1:
                raise PublicSOPCCError("public SOPCC DEV output lacks one terminal aggregate")
            metric_match, summary_match = metric_matches[0], summary_matches[0]
            total = int(summary_match.group("total"))
            expected = len(draws) * instances_per_draw * episodes_per_instance
            if (int(summary_match.group("count")) != len(draws) * instances_per_draw
                    or int(summary_match.group("episodes")) != episodes_per_instance
                    or total != expected):
                raise PublicSOPCCError("public SOPCC DEV output does not match the frozen panel")
            result = {
                "status": "GRADED",
                "metric": float(metric_match.group("metric")),
                "mean_delivered_reward": float(metric_match.group("reward")),
                "failure_rate": float(metric_match.group("failure")),
                "delivered_reward_sd": float(metric_match.group("sd")),
                "draws": list(draws),
                "instances_per_draw": instances_per_draw,
                "episodes_per_instance": episodes_per_instance,
                "episodes": total,
            }
            if (not all(math.isfinite(result[key]) for key in
                        ("metric", "mean_delivered_reward", "failure_rate", "delivered_reward_sd"))
                    or not 0 <= result["failure_rate"] <= 1):
                raise PublicSOPCCError("public SOPCC DEV aggregate is not finite")
            return result
    except OSError:
        raise PublicSOPCCError("could not prepare isolated public SOPCC DEV candidate") from None
    except PublicDevkitError as exc:
        raise PublicSOPCCError(str(exc)) from None

