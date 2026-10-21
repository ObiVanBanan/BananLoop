from __future__ import annotations

from pathlib import Path
import os
import hashlib

from pydantic import ValidationError

from ..contracts import EvaluationResult
from ..process import ProcessError, parse_json_output, run_process


class EvaluatorError(RuntimeError):
    pass


class CommandEvaluator:
    def __init__(self, command: list[str]):
        self.command = command
        self.context: dict[str, str] = {}

    def evaluate(self, workspace: Path, timeout: int) -> EvaluationResult:
        try:
            result = run_process(self.command, cwd=workspace, timeout=timeout, check=True, env={**os.environ, **self.context})
            data = parse_json_output(result.stdout)
            return EvaluationResult.model_validate(data)
        except (ProcessError, ValidationError) as exc:
            raise EvaluatorError(str(exc)) from exc


class CommandCheck:
    """Run a regression command as an exit-code gate, without JSON parsing."""

    def __init__(self, command: list[str]):
        self.command = command
        self.context: dict[str, str] = {}

    def check(self, workspace: Path, timeout: int) -> dict[str, object]:
        try:
            result = run_process(
                self.command, cwd=workspace, timeout=timeout, check=False,
                env={**os.environ, **self.context},
            )
            return {
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
            }
        except ProcessError as exc:
            raise EvaluatorError(str(exc)) from exc

