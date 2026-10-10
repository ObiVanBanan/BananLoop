from __future__ import annotations

from pathlib import Path
import os

from pydantic import ValidationError

from ..contracts import EvaluationResult
from ..evaluator_binding import resolve_executable
from ..process import ProcessError, parse_json_output, run_process


class EvaluatorError(RuntimeError):
    pass


class CommandEvaluator:
    def __init__(self, command: list[str]):
        self.command = command
        self.context: dict[str, str] = {}

    def evaluate(self, workspace: Path, timeout: int) -> EvaluationResult:
        try:
            command = list(self.command)
            if command:
                resolved = resolve_executable(command[0])
                if resolved is not None:
                    command[0] = resolved
            result = run_process(command, cwd=workspace, timeout=timeout, check=True, env={**os.environ, **self.context})
            data = parse_json_output(result.stdout)
            return EvaluationResult.model_validate(data)
        except (ProcessError, ValidationError) as exc:
            raise EvaluatorError(str(exc)) from exc

