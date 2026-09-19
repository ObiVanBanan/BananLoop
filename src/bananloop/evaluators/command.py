from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ..contracts import EvaluationResult
from ..process import ProcessError, parse_json_output, run_process


class EvaluatorError(RuntimeError):
    pass


class CommandEvaluator:
    def __init__(self, command: list[str]):
        self.command = command

    def evaluate(self, workspace: Path, timeout: int) -> EvaluationResult:
        try:
            result = run_process(self.command, cwd=workspace, timeout=timeout, check=True)
            data = parse_json_output(result.stdout)
            return EvaluationResult.model_validate(data)
        except (ProcessError, ValidationError) as exc:
            raise EvaluatorError(str(exc)) from exc
