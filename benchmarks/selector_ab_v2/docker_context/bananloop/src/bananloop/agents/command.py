from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentError
from ..process import ProcessError, parse_json_output, run_process


class CommandAgentAdapter(AgentAdapter):
    def __init__(self, command: list[str]):
        self.command = command

    def probe(self) -> dict[str, Any]:
        executable = self.command[0]
        found = shutil.which(executable) if not Path(executable).is_absolute() else Path(executable).exists()
        return {"available": bool(found), "kind": "command", "command": self.command}

    def execute(self, task: dict[str, Any], workspace: Path, timeout: int) -> dict[str, Any]:
        try:
            result = run_process(self.command, cwd=workspace, timeout=timeout, input_text=json.dumps(task), check=True)
            return parse_json_output(result.stdout)
        except ProcessError as exc:
            raise AgentError(str(exc)) from exc
