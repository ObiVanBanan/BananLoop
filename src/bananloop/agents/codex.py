from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentError
from ..process import ProcessError, run_process


HYPOTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "rationale": {"type": "string"},
        "expected_effect": {"type": "string"},
        "files_or_area": {"type": "array", "items": {"type": "string"}},
        "falsification_condition": {"type": "string"},
        "mechanism_key": {"type": "string"},
        "relation": {"type": "string", "enum": ["new", "refine", "retry", "supersede"]},
        "prior_experiment_refs": {"type": "array", "items": {"type": "integer"}},
        "novelty_rationale": {"type": "string"},
    },
    "required": [
        "hypothesis",
        "rationale",
        "expected_effect",
        "files_or_area",
        "falsification_condition",
        "mechanism_key",
        "relation",
        "prior_experiment_refs",
        "novelty_rationale",
    ],
    "additionalProperties": False,
}
IMPLEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changed_paths": {"type": "array", "items": {"type": "string"}},
        "verification_notes": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "changed_paths", "verification_notes", "blockers"],
    "additionalProperties": False,
}


class CodexAdapter(AgentAdapter):
    def __init__(self, model: str | None = None, extra_args: list[str] | None = None):
        self.model = model
        self.extra_args = extra_args or []

    def probe(self) -> dict[str, Any]:
        path = shutil.which("codex")
        if not path:
            return {"available": False, "kind": "codex"}
        try:
            version = run_process(["codex", "--version"], cwd=Path.cwd(), timeout=10).stdout.strip()
        except ProcessError:
            version = "unknown"
        return {"available": True, "kind": "codex", "path": path, "version": version}

    def execute(self, task: dict[str, Any], workspace: Path, timeout: int) -> dict[str, Any]:
        role = task.get("role")
        schema = HYPOTHESIS_SCHEMA if role == "propose" else IMPLEMENT_SCHEMA
        if role == "propose":
            instruction = (
                "Propose exactly one bounded, falsifiable hypothesis for this measurable campaign. "
                "Use the supplied research_state and planning_mode. Avoid repeating a mechanism unless "
                "you explicitly mark the hypothesis as refine, retry, or supersede and reference the prior "
                "experiment ordinal. Do not modify files. Return only the requested structured result.\n\n"
            )
        else:
            instruction = (
                "Implement exactly the approved hypothesis in the current repository. "
                "Modify only the allowed editable scope. Do not modify evaluator/protected artifacts. "
                "Do not decide whether the result is better; BananLoop will evaluate it independently. "
                "Return the requested structured result after editing.\n\n"
            )
        prompt = instruction + json.dumps(task, ensure_ascii=False, indent=2)
        with tempfile.TemporaryDirectory(prefix="bananloop-codex-") as td:
            schema_path = Path(td) / "schema.json"
            result_path = Path(td) / "result.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv = ["codex", "exec", "--skip-git-repo-check", "--output-schema", str(schema_path), "-o", str(result_path)]
            if self.model:
                argv += ["-m", self.model]
            argv += self.extra_args
            argv.append(prompt)
            try:
                run_process(argv, cwd=workspace, timeout=timeout, check=True)
                if not result_path.exists():
                    raise AgentError("Codex completed without structured result file")
                data = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise AgentError("Codex structured result must be a JSON object")
                return data
            except (ProcessError, json.JSONDecodeError) as exc:
                raise AgentError(str(exc)) from exc
