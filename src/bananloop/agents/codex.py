from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentError
from ..process import ProcessError, run_process
from ..token_budget import parse_codex_usage


BOOTSTRAP_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_input"]},
        "summary": {"type": "string"},
        "project_context": {"type": "string"},
        "needs_input": {"type": "array", "items": {"type": "string"}},
        "evaluation_command": {"type": "array", "items": {"type": "string"}},
        "smoke_command": {"type": "array", "items": {"type": "string"}},
        "protocol_manifest_commands": {
            "type": "object",
            "properties": {
                "dev": {"type": "array", "items": {"type": "string"}},
                "validation": {"type": "array", "items": {"type": "string"}},
                "holdout": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["dev", "validation", "holdout"],
            "additionalProperties": False,
        },
        "agent_visible_training_paths": {"type": "array", "items": {"type": "string"}},
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
        "mandatory_checks": {
            "type": "array",
            "description": (
                "Names of boolean check keys emitted by the evaluator's checks object. "
                "Do not put prose requirements, commands, metrics, or protocol instructions here. "
                "Use an empty array when no applicable evaluator check key is known."
            ),
            "items": {"type": "string"},
        },
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
        "evaluation_command", "smoke_command", "protocol_manifest_commands", "agent_visible_training_paths", "validation_command", "holdout_command",
        "primary_metric", "guardrails", "non_regression", "mandatory_checks",
        "editable_scope", "protected_paths", "agent_visible_metrics",
        "agent_visible_diagnostics", "paired_comparison", "signal_sanity", "evaluator_timeout_seconds"
    ],
    "additionalProperties": False
}

LEGACY_HYPOTHESIS_SCHEMA = {
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
        "target_failure_signal": {"type": "string"},
        "failure_mechanism": {"type": "string"},
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
        "target_failure_signal",
        "failure_mechanism",
    ],
    "additionalProperties": False,
}

HYPOTHESIS_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1, "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]{0,79}$"},
        "hypothesis": LEGACY_HYPOTHESIS_SCHEMA,
        "evidence_refs": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "suspected_failure_mode": {"type": "string", "maxLength": 300},
        "predicted_observable": {"type": "string", "minLength": 1, "maxLength": 500},
        "discriminating_test": {"type": "string", "minLength": 1, "maxLength": 500},
        "scope_eligibility": {"type": "boolean"},
        "estimated_agent_calls": {"type": "integer", "minimum": 0, "maximum": 4},
        "estimated_evaluator_calls": {"type": "integer", "minimum": 0, "maximum": 4},
        "estimated_work_units": {"type": ["number", "null"], "minimum": 0, "maximum": 1000000},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
        "uncertainty": {"type": "string", "minLength": 1, "maxLength": 500},
        "inference": {"type": "string", "enum": ["agent_inference"]},
    },
    "required": ["candidate_id", "hypothesis", "evidence_refs", "suspected_failure_mode",
                 "predicted_observable", "discriminating_test", "scope_eligibility",
                 "estimated_agent_calls", "estimated_evaluator_calls", "estimated_work_units",
                 "rationale", "uncertainty", "inference"],
    "additionalProperties": False,
}

PORTFOLIO_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "minItems": 1, "maxItems": 4,
                                    "items": HYPOTHESIS_CANDIDATE_SCHEMA}},
    "required": ["candidates"],
    "additionalProperties": False,
}

# Codex structured output requires an object schema without top-level unions.
# Keep both wire shapes in one strict object: portfolio output sets the legacy
# fields to null; legacy output sets candidates to null.
def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = dict(schema)
    schema_type = result["type"]
    result["type"] = [schema_type, "null"]
    if "enum" in result:
        result["enum"] = [*result["enum"], None]
    return result


HYPOTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            **PORTFOLIO_SCHEMA["properties"]["candidates"],
            "type": ["array", "null"],
        },
        **{name: _nullable_schema(schema)
           for name, schema in LEGACY_HYPOTHESIS_SCHEMA["properties"].items()},
    },
    "required": ["candidates", *LEGACY_HYPOTHESIS_SCHEMA["required"]],
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
        self.meter_tokens = False
        self.last_token_usage: dict[str, int] | None = None

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
        self.last_token_usage = None
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
                "Use repository.preferred_python_runtime first when it is project-local (for example .venv/bin/python "
                "or .venv/Scripts/python.exe); do not use a system Python failure to infer that project dependencies "
                "are missing. Probe discovered runtimes directly for required packages and versions. Only request a "
                "runtime if no discovered candidate can satisfy the project requirement. BananLoop core injects values "
                "from discovered .env files into evaluator processes without exposing them to you; if a required key "
                "name is present in repository.detected_resources.env_keys, do not request that credential merely "
                "because it is absent from your own process environment. A failed localhost/Qdrant/network probe from "
                "your Codex sandbox is advisory, not proof that the service is unavailable: construct the evaluator and "
                "let the core-owned smoke run outside the agent sandbox establish service reachability. If the goal "
                "refers to the same/frozen/existing benchmark, inspect repository experiment/history/journal evidence "
                "and the evaluator CLI. When that evidence documents a deterministic sample count and seed, reproduce "
                "that sample through the evaluator flags instead of asking for a separately materialized case-list file. "
                "A verify-only mode that checks dataset presence is not a quality evaluation unless it emits the required "
                "goal metrics. Return a cheap smoke_command whenever possible; "
                "it should exercise the real evaluator contract on a small real subset without changing semantics. "
                "Do not claim readiness based on your own smoke result: BananLoop core will execute the command "
                "three times on the same snapshot, validate required metrics/checks/case and protocol identities, "
                "When BANANLOOP_EVALUATION_TIER=bootstrap_smoke, evaluate only the requested smoke subset. "
                "Scientific mandatory checks, guardrail metrics, non-regression metrics, and full-dataset "
                "size/count invariants are enforced by BananLoop during normal DEV/VALIDATION/HOLDOUT evaluation, "
                "not by the readiness smoke. Readiness requires the primary metric plus protocol/determinism evidence. "
                "Secondary scientific metrics/checks may be absent or false in smoke output when the small subset "
                "cannot compute them; do not weaken or delete the full-evaluation contract just to make readiness pass. "
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
                "BananLoop JSON evaluation contract as one top-level object with status, metrics, checks, "
                "protocol_sha256 when cases are emitted, and optional cases; metrics may be numbers or {value: number}. "
                "For each configured tier whose inputs can be enumerated cheaply, create a protocol manifest command "
                "that emits protocol_sha256 and the full case_id/repetition/input_sha256 list without scoring. The full "
                "evaluator must read BANANLOOP_PROTOCOL_MANIFEST and emit exactly those identities; readiness may score "
                "only a subset. For bootstrap_smoke, return a nonempty subset of exact manifest case identities and keep "
                "protocol_sha256 equal to the manifest protocol hash. Choose a subset that preserves the primary metric's "
                "semantics (for example, a balanced-accuracy subset must contain every required label); if no valid small "
                "subset can be formed, score all manifest cases. Never invent, rename, or re-hash case identities. Keep "
                "separate manifests for DEV, VALIDATION, and HOLDOUT. Do not enable paired statistical "
                "acceptance just because inputs are fixed. "
                "If discovery found training resources that implementation should use, list only those paths in "
                "agent_visible_training_paths. Core permits only discovered paths clearly named as training data; "
                "never include evaluation, test, validation, holdout, GOLD, or reference resources. "
                "Never encode shell environment assignments as the executable name; BananLoop can normalize leading "
                "NAME=value argv tokens, but prefer a plain executable argv and read runtime values from the process environment. "
                "Use stable case IDs/input fingerprints, and avoid per-evaluation "
                "LLM planning or other avoidable nondeterminism. For stochastic generation, freeze cases and seeds and "
                "use paired case-level measurements. Do not invent missing reference data or credentials: when an "
                "essential input is absent, return status=needs_input with a short exact list of what the operator must "
                "provide. Keep evaluator/scoring files separate from editable production scope. project_context must "
                "summarize architecture, execution entrypoints, output path, and plausible improvement levers so later "
                "proposers can act without rediscovering the project. If repair.validation_errors is present, "
                "correct the prior plan to satisfy those schema requirements without asking the operator. "
                "Return only the requested structured result.\n\n"
            )
        elif role == "propose":
            instruction = (
                "Propose a bounded portfolio of 2-3 materially different, falsifiable hypotheses (never more than 4) for this measurable campaign. "
                "Use the supplied research_state, its aggregate failure_signals and experiment_evidence, "
                "and planning_mode. If failure_signals exist, target one supplied signal_key and explain "
                "the causal failure_mechanism; use empty strings for both fields otherwise. "
                "When experiment_evidence exists, cite a prior ordinal and explain in novelty_rationale "
                "what its measured result changes. Distinguish a partial DEV gain from a failed mechanism. "
                "For each candidate return candidate_id, nested hypothesis (the existing Hypothesis fields), evidence_refs, "
                "suspected_failure_mode, predicted_observable, discriminating_test, scope_eligibility, "
                "estimated_agent_calls, estimated_evaluator_calls, estimated_work_units (or null), rationale, "
                "uncertainty, and inference=agent_inference. Candidate IDs must be stable mechanism slugs. "
                "Estimate only remaining implementation/evaluation calls after the current proposal call. "
                "Use only supplied DEV evidence references or "
                "INSUFFICIENT_EVIDENCE; distinguish observed facts from your inference. Do not invent gains, causes, "
                "USD costs, or evidence. The core selects one candidate and enforces all budgets and scope. "
                "For a portfolio, set every legacy top-level Hypothesis field to null. For the legacy single-Hypothesis fallback, set candidates to null. "
                "Avoid repeating a mechanism unless "
                "you explicitly mark the hypothesis as refine, retry, or supersede and reference the prior "
                "experiment ordinal. Do not modify files. Return {candidates:[...]} or legacy single Hypothesis JSON.\n\n"
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
            sandbox = "workspace-write" if role in {"bootstrap", "implement"} else "read-only"
            argv = [
                *self._resolved_command(),
                "exec",
                "--ephemeral",
                *(["--json"] if self.meter_tokens else []),
                "--sandbox",
                sandbox,
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "-o",
                str(result_path),
            ]
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
                executed = run_process(
                    argv,
                    cwd=workspace,
                    timeout=timeout,
                    check=True,
                    input_text=prompt,
                )
                if self.meter_tokens:
                    self.last_token_usage = parse_codex_usage(executed.stdout)
                    if self.last_token_usage is None:
                        raise AgentError("Codex did not provide a final turn.completed token usage receipt")
                if not result_path.exists():
                    raise AgentError("Codex completed without structured result file")
                data = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise AgentError("Codex structured result must be a JSON object")
                return data
            except (ProcessError, json.JSONDecodeError) as exc:
                if self.meter_tokens and isinstance(exc, ProcessError) and exc.result is not None:
                    self.last_token_usage = parse_codex_usage(exc.result.stdout)
                raise AgentError(str(exc)) from exc
