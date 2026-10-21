from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Mapping

from pydantic import ValidationError

from ..contracts import EvaluationResult
from ..process import ProcessError, parse_json_output, run_process


class EvaluatorError(RuntimeError):
    pass


_ENV_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)


def _split_command_environment(command: list[str]) -> tuple[dict[str, str], list[str]]:
    """Normalize shell-style leading env assignments without invoking a shell."""
    tokens = list(command)
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
    updates: dict[str, str] = {}
    while tokens:
        match = _ENV_ASSIGNMENT.fullmatch(tokens[0])
        if not match:
            break
        updates[match.group(1)] = match.group(2)
        tokens.pop(0)
    if not tokens:
        raise EvaluatorError("evaluation command contains environment assignments but no executable")
    return updates, tokens


class CommandEvaluator:
    def __init__(
        self,
        command: list[str],
        *,
        base_env: Mapping[str, str] | None = None,
    ):
        self.command = list(command)
        self.command_env, self.argv = _split_command_environment(self.command)
        self.base_env = dict(base_env) if base_env is not None else None
        self.context: dict[str, str] = {}

    def evaluate(self, workspace: Path, timeout: int) -> EvaluationResult:
        try:
            inherited = dict(os.environ) if self.base_env is None else dict(self.base_env)
            result = run_process(
                self.argv,
                cwd=workspace,
                timeout=timeout,
                check=True,
                env={**inherited, **self.command_env, **self.context},
            )
            data = parse_json_output(result.stdout)
            return EvaluationResult.model_validate(data)
        except (ProcessError, ValidationError) as exc:
            raise EvaluatorError(str(exc)) from exc
