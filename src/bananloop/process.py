from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessError(RuntimeError):
    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    input_text: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessError(f"process timed out after {timeout}s: {argv!r}") from exc
    except OSError as exc:
        raise ProcessError(f"cannot start process: {argv!r}: {exc}") from exc
    result = ProcessResult(completed.returncode, completed.stdout, completed.stderr)
    if check and completed.returncode != 0:
        details: list[str] = []
        if completed.stdout.strip():
            details.append("stdout:\n" + completed.stdout[-4000:].rstrip())
        if completed.stderr.strip():
            details.append("stderr:\n" + completed.stderr[-4000:].rstrip())
        suffix = "\n" + "\n".join(details) if details else ""
        raise ProcessError(
            f"process failed with exit code {completed.returncode}: {argv!r}{suffix}",
            result,
        )
    return result


def parse_json_output(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProcessError(f"expected JSON output, got: {text[:500]!r}") from exc
    if not isinstance(data, dict):
        raise ProcessError("expected top-level JSON object")
    return data

