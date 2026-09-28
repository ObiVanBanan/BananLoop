from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentError
from ..process import ProcessError, run_process


BOOTSTRAP_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_input"]},
        "summary": {"type": "string"},
        "project_context": {"type": "string"},
        "needs_input": {"type": "array", "items": {"type": "string"}},
        "evaluation_command": {"type": "array", "items": {"type": "string"}},
        "validation_command": {"type": "array", "items": {"type": "string"}},
        "holdout_command": {"type": "array", "items": {"type": "string"}},
        "primary_metric": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "direction": {"type": "string", "enum": ["maximize", "minimize"]},
                "target": {"type": "number"},
                "min_improvement": {"type": "number", "minimum": 0.000001}
            },
            "required": ["name", "direction", "target", "min_improvement"],
            "additionalProperties": False
        },
        "guardrails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "operator": {"type": "string", "enum": ["<=", ">=", "<", ">", "=="]},
                    "bound": {"type": "number"}
                },
                "required": ["metric", "operator", "bound"],
                "additionalProperties": False
            }
        },
        "non_regression": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "direction": {"type": "string", "enum": ["maximize", "minimize"]},
                    "tolerance": {"type": "number", "minimum": 0}
                },
                "required": ["metric", "direction", "tolerance"],
                "additionalProperties": False
            }
        },
        "mandatory_checks": {"type": "array", "items": {"type": "string"}},
        "editable_scope": {"type": "array", "items": {"type": "string"}},
        "protected_paths": {"type": "array", "items": {"type": "string"}},
        "agent_visible_metrics": {"type": "array", "items": {"type": "string"}},
        "agent_visible_diagnostics": {"type": "array", "items": {"type": "string"}},
        "paired_comparison": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "case_count": {"type": "integer", "minimum": 1},
                "repetitions": {"type": "integer", "minimum": 1},
                "metric": {"type": "string"},
                "direction": {"type": "string", "enum": ["maximize", "minimize"]},
                "min_wins": {"type": "integer", "minimum": 1},
                "worst_case_tolerance": {"type": "number", "minimum": 0}
            },
            "required": ["enabled", "case_count", "repetitions", "metric", "direction", "min_wins", "worst_case_tolerance"],
            "additionalProperties": False
        },
        "evaluator_timeout_seconds": {"type": "integer", "minimum": 1}
    },
    "required": [
        "status", "summary", "project_context", "needs_input",
        "evaluation_command", "validation_command", "holdout_command",
        "primary_metric", "guardrails", "non_regression", "mandatory_checks",
        "editable_scope", "protected_paths", "agent_visible_metrics",
        "agent_visible_diagnostics", "paired_comparison", "evaluator_timeout_seconds"
    ],
    "additionalProperties": False
}

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
FAILURE_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "capsules": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "failure_class": {"type": "string"},
                    "stage": {"type": "string"},
                    "affected_fields": {"type": "array", "items": {"type": "string"}},
                    "observation": {"type": "string"},
                    "likely_mechanism": {"type": "string"},
                    "general_rule": {"type": "string"},
                    "suggested_regression": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "title",
                    "failure_class",
                    "stage",
                    "affected_fields",
                    "observation",
                    "likely_mechanism",
                    "general_rule",
                    "suggested_regression",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["capsules"],
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

    def _resolved_command(self) -> list[str]:
        path = shutil.which("codex")
        if not path:
            raise AgentError("Codex executable not found")
        suffix = Path(path).suffix.lower()
        if os.name == "nt" and suffix in {".cmd", ".bat"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", path]
        if os.name == "nt" and suffix == ".ps1":
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        return [path]

    def probe(self) -> dict[str, Any]:
        try:
            command = self._resolved_command()
        except AgentError:
            return {"available": False, "kind": "codex"}
        try:
            version = run_process([*command, "--version"], cwd=Path.cwd(), timeout=10).stdout.strip()
        except ProcessError:
            version = "unknown"
        return {"available": True, "kind": "codex", "command": command, "version": version}

    def execute(self, task: dict[str, Any], workspace: Path, timeout: int) -> dict[str, Any]:
        role = task.get("role")
        if role == "bootstrap":
            schema = BOOTSTRAP_SCHEMA
        elif role == "propose":
            schema = HYPOTHESIS_SCHEMA
        elif role == "analyze":
            schema = FAILURE_ANALYSIS_SCHEMA
        else:
            schema = IMPLEMENT_SCHEMA
        if role == "bootstrap":
            instruction = (
                "You are the campaign bootstrapper for BananLoop. Inspect the repository and the human goal, "
                "then turn them into an executable measurement contract with minimal operator work. "
                "You MAY create or modify files only under .bananloop_eval/. Never change production code, tests, "
                "reference assets, or user configuration during bootstrap. Prefer an existing trustworthy evaluator "
                "when one already exists; otherwise create a thin evaluator adapter under .bananloop_eval/. "
                "Before returning status=ready, run a cheap baseline smoke check of the evaluator yourself and repair "
                "contract/startup errors; avoid a full expensive benchmark when a smaller smoke path is possible. "
                "The evaluator must compare the same frozen inputs/settings for baseline and candidates, emit the "
                "BananLoop JSON evaluation contract, use stable case IDs/input fingerprints, and avoid per-evaluation "
                "LLM planning or other avoidable nondeterminism. For stochastic generation, freeze cases and seeds and "
                "use paired case-level measurements. Do not invent missing reference data or credentials: when an "
                "essential input is absent, return status=needs_input with a short exact list of what the operator must "
                "provide. Keep evaluator/scoring files separate from editable production scope. project_context must "
                "summarize architecture, execution entrypoints, output path, and plausible improvement levers so later "
                "proposers can act without rediscovering the project. Return only the requested structured result.\n\n"
            )
        elif role == "propose":
            instruction = (
                "Propose exactly one bounded, falsifiable hypothesis for this measurable campaign. "
                "Use the supplied research_state and planning_mode. Avoid repeating a mechanism unless "
                "you explicitly mark the hypothesis as refine, retry, or supersede and reference the prior "
                "experiment ordinal. Do not modify files. Return only the requested structured result.\n\n"
            )
        elif role == "analyze":
            instruction = (
                "Analyze the supplied DEV-only failure signals and public DEV teacher examples against the candidate implementation. "
                "Do not modify files. Use the exact public DEV expected answers to diagnose the root cause, then "
                "distill causal, reusable lessons rather than memorizing individual benchmark cases. Do not copy "
                "case IDs, product IDs, exact expected answers, or case-specific constants into the resulting "
                "capsules. Focus on stage attribution, conflicting "
                "state/provenance, violated invariants, and general regression tests. Return only the requested "
                "structured failure capsules. If evidence is insufficient, use a low confidence value rather "
                "than fabricating a cause.\n\n"
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
            argv = [*self._resolved_command(), "exec", "--skip-git-repo-check", "--output-schema", str(schema_path), "-o", str(result_path)]
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
