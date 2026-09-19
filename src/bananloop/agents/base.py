from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class AgentError(RuntimeError):
    pass


class AgentAdapter(ABC):
    @abstractmethod
    def probe(self) -> dict[str, Any]: ...

    @abstractmethod
    def execute(self, task: dict[str, Any], workspace: Path, timeout: int) -> dict[str, Any]: ...

    def normalize_result(self, task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return result

    def cancel(self, action_id: str) -> dict[str, Any]:
        return {"action_id": action_id, "status": "not_running"}
