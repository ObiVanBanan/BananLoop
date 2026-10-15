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
        "smoke_command": {"type": "array", "items": {"type": "string"}},
        "validation_command": {"type": "array", "items": {"type": "string"}},
        "regression_command": {"type": "array", "items": {"type": "string"}},
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
        "signal_sanity": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["configured", "unavailable"]},
                "evidence_kind": {"type": ["string", "null"], "enum": ["known_examples", "controlled_degradation", "metamorphic", "invariants", None]},
                "metric": {"type": ["string", "null"]},
                "positive_case_ids": {"type": "array", "items": {"type": "string"}},
                "negative_case_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]}
            },
            "required": ["status", "evidence_kind", "metric", "positive_case_ids", "negative_case_ids", "reason"],
            "additionalProperties": False
        },
        "evaluator_timeout_seconds": {"type": "integer", "minimum": 1}
    },
    "required": [
        "status", "summary", "project_context", "needs_input",
        "evaluation_command", "smoke_command", "validation_command", "regression_command", "holdout_command",
        "primary_metric", "guardrails", "non_regression", "mandatory_checks",
        "editable_scope", "protected_paths", "agent_visible_metrics",
        "agent_visible_diagnostics", "paired_comparison", "signal_sanity", "evaluator_timeout_seconds"
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
        "diagnostic_plan": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "evidence_to_collect": {"type": "string"},
                "decision_rule": {"type": "string"},
            },
            "additionalProperties": {"type": "string"},
        },
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
    def __init__(self, model: str | None = None, extra_args: list[str] | None = None,
                 reasoning_effort: str | None = None):
        self.model = model
        self.extra_args = extra_args or []
        self.reasoning_effort = reasoning_effort

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
        return {"available": True, "kind": "codex", "command": command, "version": version,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort}

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
                "The top-level goal field is the authoritative human intent. Never request a repository-side "
                "goal/fix/brief file merely to confirm or choose that intent; in particular, do not require files "
                "such as .hermes/fix.md when goal is already present. If multiple existing evaluators conflict, "
                "select the one whose semantics best match the explicit goal and explain that choice in summary. "
                "You MAY create or modify files only under .bananloop_eval/. Never change production code, tests, "
                "reference assets, or user configuration during bootstrap. Prefer an existing trustworthy evaluator "
                "when one already exists; otherwise create a thin evaluator adapter under .bananloop_eval/. "
                "Use repository.detected_python_runtimes before declaring a Python environment missing; probe those "
                "executables directly for required packages and versions. Only request a runtime if no discovered "
                "candidate can satisfy the project requirement. Return a cheap smoke_command whenever possible; "
                "it should exercise the real evaluator contract on a small real subset without changing semantics. "
                "Do not claim readiness based on your own smoke result: BananLoop core will execute the command "
                "three times on the same snapshot, validate required metrics/checks/case and protocol identities, "
                "and compare the empirical A/A noise bound with min_improvement before READY. The evaluation "
                "result must include a stable protocol_sha256 identifying its measurement protocol. "
                "The primary metric must directly measure the supplied goal outcome with explicit goal-specific "
                "behavioral probes; test-suite pass/fail is a secondary regression check, not a substitute for "
                "measuring the goal. Every probe must import and execute code from the candidate workspace being "
                "evaluated, never an installed host checkout; for a Python src layout, set PYTHONPATH to that "
                "workspace's src directory. "
                "Also provide signal_sanity. Configure it only when the repository/evaluator exposes defensible, "
                "explicitly identified positive and negative examples or controls. Use evidence_kind known_examples, "
                "controlled_degradation, metamorphic, or invariants; list their exact case_ids and set metric to the "
                "primary metric. Never invent labels or infer gold labels from model output. If no such evidence exists, "
                "set status=unavailable, leave evidence_kind/metric/case lists null or empty, and explain why. The core "
                "will verify separation independently on every A/A run. If the core returns a "
                "repair.readiness_error, diagnose and repair only the measurement package, then return an "
                "updated plan. Avoid a full expensive benchmark when a smaller smoke path is possible. "
                "The evaluator must compare the same frozen inputs/settings for baseline and candidates, emit the "
                "BananLoop JSON evaluation contract, use stable case IDs/input fingerprints, and avoid per-evaluation "
                "LLM planning or other avoidable nondeterminism. For stochastic generation, freeze cases and seeds and "
                "use paired case-level measurements. Put ordinary test-suite commands in regression_command; "
                "validation_command is an additional BananLoop JSON evaluator and must emit the evaluation contract. "
                "Do not use pytest as validation_command. Do not invent missing reference data or credentials: when an "
                "essential input is absent, return status=needs_input with a short exact list of what the operator must "
                "provide. Do not infer that a runtime service is unavailable from a connectivity probe made inside "
                "your own sandbox: local services such as Qdrant may be reachable by the BananLoop core evaluator "
                "but intentionally blocked from the bootstrap agent. When the repository already configures the "
                "service and required credentials are present, return a ready plan and let the core-owned readiness "
                "run determine whether the evaluator can reach it. Request operator input for service access only "
                "when the core-owned readiness run itself fails and its diagnostics identify a missing external "
                "prerequisite. Keep evaluator/scoring files separate from editable production scope. project_context must "
                "summarize architecture, execution entrypoints, output path, and plausible improvement levers so later "
                "proposers can act without rediscovering the project. If repair.validation_errors is present, "
                "correct the prior plan to satisfy those schema requirements without asking the operator. "
                "Return only the requested structured result.\n\n"
            )
        elif role == "propose":
            instruction = (
                "Propose exactly one bounded, falsifiable hypothesis for this measurable campaign. "
                "Use the supplied research_state and planning_mode. When repeated_failure_diagnosis is present, "
                "include diagnostic_plan with a concrete question, evidence_to_collect, and decision_rule; "
                "the next experiment must distinguish the stated unknown causes before another ranking-only change. "
                "Avoid repeating a mechanism unless you explicitly mark the hypothesis as refine, retry, or supersede "
                "and reference the prior experiment ordinal. Do not modify files. Return only the requested structured result.\n\n"
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
                "The supervisor runs configured evaluator and validation commands independently. Do not mark "
                "the implementation blocked only because optional local tooling for those checks is unavailable; "
                "complete the change and report the local verification limit in verification_notes. Use blockers "
                "only when you cannot implement the approved change or a real constraint prevents it. "
                "Return the requested structured result after editing.\n\n"
            )
        instruction += (
            "Before returning the structured response, remove only transient cache artifacts you created "
            "in this disposable workspace (for example .pytest_cache, __pycache__, and *.pyc). Do not "
            "remove or alter tracked project files. This lets the supervisor safely dispose of the copy.\n\n"
        )
        prompt = instruction + json.dumps(task, ensure_ascii=False, indent=2)
        with tempfile.TemporaryDirectory(prefix="bananloop-codex-") as td:
            schema_path = Path(td) / "schema.json"
            result_path = Path(td) / "result.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv = [*self._resolved_command(), "exec", "--skip-git-repo-check", "--output-schema", str(schema_path), "-o", str(result_path)]
            if self.model:
                argv += ["-m", self.model]
            if self.reasoning_effort:
                argv += ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
            argv += self.extra_args
            # Keep generated campaign context off the Windows command line.
            # It can exceed CreateProcess' command-line limit after DEV history
            # grows; Codex's `-` prompt sentinel reads the same text from stdin.
            argv.append("-")
            try:
                run_process(
                    argv,
                    cwd=workspace,
                    timeout=timeout,
                    check=True,
                    input_text=prompt,
                )
                if not result_path.exists():
                    raise AgentError("Codex completed without structured result file")
                data = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise AgentError("Codex structured result must be a JSON object")
                return data
            except (ProcessError, json.JSONDecodeError) as exc:
                raise AgentError(str(exc)) from exc
