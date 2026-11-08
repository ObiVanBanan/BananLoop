"""Opt-in real Codex CLI role diagnostics with observed (not provider-signed) usage.

A Codex subscription event stream is NOT an RPDI-14 provider receipt, an
enforceable token cap, a billing statement, or a security isolation boundary.
Never mount evaluator/oracle material on a Codex worker host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any, Callable

from .agent_benchmark import BenchmarkError, canonical, digest, read_json, _write
from .roles import RoleRegistryV1, TeamManifestV1

MAX_CASES = 20
MAX_EVENT_BYTES = 3_000_000
MAX_PROMPT_BYTES = 150_000
MAX_RESPONSE_BYTES = 128_000
_ROLES = {"researcher", "optimizer", "independent_verifier", "integration_verifier"}
_BAD_KEYS = {"oracle", "answer_key", "expected_output", "gold_answer", "holdout_label", "private_label"}
# A post-hoc JSONL check is useful for detecting CLI regressions, but is not a
# control: by then a requested tool may already have run. Disable every
# user-facing execution/integration feature supported by Codex CLI before the
# model turn. User/project MCP configuration is independently excluded below
# with --ignore-user-config, and web search is opt-in (we never pass --search).
_DISABLED_CODEX_FEATURES = (
    "shell_tool", "apps", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "code_mode_host",
)


class LiveBenchmarkError(BenchmarkError):
    """Live diagnostic dispatch is not safely authorized or verifiable."""


def _classify_codex_failure(stderr: bytes, stdout: bytes = b"") -> str:
    """Classify common CLI failures without persisting potentially sensitive text."""
    message = (stderr + b"\n" + stdout).decode("utf-8", errors="replace").casefold()
    # Structured CLI error codes commonly use snake_case; normalize separators
    # so they map to the same allowlisted categories as human-readable errors.
    message = re.sub(r"[_-]+", " ", message)
    patterns = (
        ("AUTHENTICATION_ERROR", ("not logged in", "unauthorized", "authentication failed", "sign in")),
        ("QUOTA_OR_RATE_LIMIT", ("usage limit", "rate limit", "quota exceeded", "too many requests")),
        ("MODEL_UNAVAILABLE", ("model not found", "unknown model", "model is not available", "model unavailable")),
        ("CONFIG_OR_FEATURE_ERROR", ("unknown feature", "unknown config", "invalid config", "unrecognized option")),
        ("SCHEMA_ERROR", ("output schema", "json schema", "schema validation")),
        ("NETWORK_OR_PROXY_ERROR", ("connection refused", "connection reset", "dns", "proxy", "tls", "certificate", "network error")),
    )
    for code, needles in patterns:
        if any(needle in message for needle in needles):
            return code
    _, event_types = _codex_stdout_metadata(stdout)
    if "error" in event_types:
        return "CODEX_JSONL_ERROR_EVENT"
    if "turn.failed" in event_types:
        return "CODEX_JSONL_TURN_FAILED_EVENT"
    return "CODEX_EXIT_NONZERO_UNCLASSIFIED"


def _codex_stdout_metadata(stdout: bytes) -> tuple[str, list[str]]:
    """Summarize JSONL shape and allowlisted event names without retaining content."""
    if not stdout:
        return "EMPTY", []
    known = {"error", "turn.failed", "turn.completed", "turn.started", "turn.cancelled",
             "thread.started", "item.started", "item.completed", "item.updated"}
    lines = [line for line in stdout.splitlines() if line.strip()]
    parsed = 0
    event_types = []
    for line in lines[:MAX_EVENT_BYTES // 2]:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            parsed += 1
            name = event["type"] if event["type"] in known else "OTHER"
            if name not in event_types and len(event_types) < 8:
                event_types.append(name)
    if parsed == 0:
        kind = "TEXT"
    elif parsed == len(lines):
        kind = "JSONL"
    else:
        kind = "MIXED"
    return kind, event_types


class CodexExecFailure(LiveBenchmarkError):
    """Safe, non-content diagnostics for an unsuccessful Codex CLI process."""

    def __init__(self, *, failure_code: str, returncode: int | None,
                 stderr: bytes, stdout: bytes = b""):
        self.failure_code = failure_code
        self.cli_exit_code = returncode
        self.stderr_bytes = len(stderr)
        self.stderr_sha256 = hashlib.sha256(stderr).hexdigest()
        self.stdout_bytes = len(stdout)
        self.stdout_sha256 = hashlib.sha256(stdout).hexdigest()
        self.stdout_format, self.stdout_event_types = _codex_stdout_metadata(stdout)
        super().__init__(f"Codex CLI stopped ({failure_code}); settlement is unknown; no retry")


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(k).lower() in _BAD_KEYS or _contains_secret_key(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def role_output_model(role_id: str):
    # These are the actual frozen v0.2 *model reply* schemas; the whole Core
    # transaction/worker/gateway is intentionally not simulated by this runner.
    if role_id == "researcher":
        from .v2_research_runtime import ResearcherReplyV1
        return ResearcherReplyV1
    if role_id == "optimizer":
        from .v2_optimization_runtime import OptimizationCandidateProposalV1
        return OptimizationCandidateProposalV1
    if role_id == "independent_verifier":
        from .evaluator_verification import ProtocolVerifierOutputV1
        return ProtocolVerifierOutputV1
    if role_id == "integration_verifier":
        from .evaluator_verification import IntegrationVerifierOutputV1
        return IntegrationVerifierOutputV1
    raise LiveBenchmarkError("case requests an unregistered v0.2 benchmark role")


def validate_role_reply(role: str, raw: Any, task: dict) -> dict:
    try:
        validated = role_output_model(role).model_validate(raw)
        if role == "optimizer":
            from .v2_optimization_runtime import _safe_path
            writable = task.get("writable_paths")
            if (not isinstance(writable, list) or not writable
                    or any(not isinstance(v, str) for v in writable)):
                raise LiveBenchmarkError("optimizer task lacks its exact writable path allowlist")
            if any(_safe_path(path) not in writable for path in validated.changes):
                raise LiveBenchmarkError("optimizer attempted an ungranted file replacement")
            if validated.hypothesis_id != task.get("hypothesis_id"):
                raise LiveBenchmarkError("optimizer output changed the selected hypothesis")
        elif role == "independent_verifier":
            if validated.proposal_digest != task.get("proposal_digest"):
                raise LiveBenchmarkError("independent verifier answered for another proposal digest")
        elif role == "integration_verifier":
            if (validated.candidate_snapshot_id != task.get("candidate_snapshot_id")
                    or validated.protocol_digest != task.get("protocol_digest")):
                raise LiveBenchmarkError("integration verifier answered for a different candidate/protocol")
        return validated.model_dump(mode="json")
    except LiveBenchmarkError:
        raise
    except Exception as exc:
        raise LiveBenchmarkError(f"role response fails actual v0.2 schema: {type(exc).__name__}") from None


def parse_codex_events(output: bytes) -> tuple[dict, dict]:
    """Reject missing/ambiguous CLI usage or any shell, file or web tool action."""
    if len(output) > MAX_EVENT_BYTES:
        raise LiveBenchmarkError("Codex event stream is oversized")
    completed, answer, finished = [], [], 0
    for line in output.splitlines():
        if len(line) > MAX_RESPONSE_BYTES:
            raise LiveBenchmarkError("oversized Codex CLI event")
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LiveBenchmarkError("Codex CLI emitted malformed JSONL") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise LiveBenchmarkError("Codex CLI event missing type")
        kind = event["type"]
        if kind in {"error", "turn.failed"}:
            raise LiveBenchmarkError("Codex reported a failed or ambiguous turn")
        if kind == "turn.completed":
            finished += 1
            usage = event.get("usage")
            if not isinstance(usage, dict):
                raise LiveBenchmarkError("completed Codex turn has no usage object")
            keys = ("input_tokens", "cached_input_tokens", "output_tokens")
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys):
                raise LiveBenchmarkError("Codex token usage is absent or malformed")
            if usage["cached_input_tokens"] > usage["input_tokens"]:
                raise LiveBenchmarkError("Codex cached input exceeds total input tokens")
            if "reasoning_output_tokens" in usage and (
                    type(usage["reasoning_output_tokens"]) is not int
                    or not 0 <= usage["reasoning_output_tokens"] <= usage["output_tokens"]):
                raise LiveBenchmarkError("Codex reasoning usage is inconsistent")
            completed.append({k: usage[k] for k in keys})
        if kind.startswith("item."):
            item = event.get("item", {})
            if not isinstance(item, dict):
                raise LiveBenchmarkError("Codex item event is malformed")
            if item.get("type") in {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_agent_tool_call"}:
                raise LiveBenchmarkError("Codex attempted tool use in a tool-free role benchmark")
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                    raise LiveBenchmarkError("Codex answer is absent or oversized")
                try:
                    answer.append(json.loads(text))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise LiveBenchmarkError("Codex answer was not valid JSON") from exc
    if finished != 1 or len(answer) != 1 or len(completed) != 1 or not isinstance(answer[0], dict):
        raise LiveBenchmarkError("Codex output requires exactly one final JSON reply and one metered turn")
    return answer[0], completed[0]


def _safe_cli_env() -> dict[str, str]:
    # Keep subscription auth accessible, not arbitrary secrets from the host env.
    names = ("PATH", "HOME", "USER", "CODEX_HOME", "XDG_CONFIG_HOME",
             "XDG_DATA_HOME", "LANG", "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
    return {name: os.environ[name] for name in names if name in os.environ}


def _codex_command(codex_executable: str, model_id: str) -> list[str]:
    cmd = [
        codex_executable, "exec", "--json", "--ephemeral",
        "--ignore-user-config", "--ignore-rules",
        "--sandbox", "read-only", "--skip-git-repo-check",
    ]
    for feature in _DISABLED_CODEX_FEATURES:
        cmd.extend(("--disable", feature))
    cmd.extend(("--model", model_id, "-"))
    return cmd


def run_codex_case(case: dict, model_id: str, *, codex_executable: str,
                   timeout_seconds: int) -> tuple[dict, dict]:
    """One fresh Codex exec; no retry, no host repo checkout in its cwd."""
    if os.name != "posix":
        raise LiveBenchmarkError("live Codex diagnostic needs a reviewed POSIX execution host")
    if not shutil.which(codex_executable):
        raise LiveBenchmarkError("Codex CLI executable not available")
    model = role_output_model(case["role_id"])
    public_prompt = {
        "role": case["role_id"],
        "role_protocol": "v0.2-pinned-model-reply-diagnostic",
        "task": case["agent_input"],
        "response_schema": model.model_json_schema(),
        "instruction": ("Return exactly one JSON object satisfying the role output schema. "
                        "Do not call tools, browse, execute commands, read files, "
                        "or claim unprovided evaluator outcomes. Use only the public task."),
    }
    raw_prompt = canonical(public_prompt)
    if len(raw_prompt) > MAX_PROMPT_BYTES:
        raise LiveBenchmarkError("public role prompt exceeds hard byte ceiling")
    with tempfile.TemporaryDirectory(prefix="bananloop-codex-public-") as root:
        cwd = Path(root)
        cmd = _codex_command(codex_executable, model_id)
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=_safe_cli_env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            output, stderr = proc.communicate(input=raw_prompt, timeout=timeout_seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                output, stderr = proc.communicate()
            except Exception:
                output, stderr = b"", b""
            raise CodexExecFailure(
                failure_code="CODEX_TIMEOUT_OR_INTERRUPTED",
                returncode=proc.returncode, stderr=stderr or b"", stdout=output or b"",
            ) from None
        if proc.returncode != 0:
            raise CodexExecFailure(
                failure_code=_classify_codex_failure(stderr or b"", output or b""),
                returncode=proc.returncode, stderr=stderr or b"", stdout=output or b"",
            )
    try:
        answer, observed = parse_codex_events(output)
    except LiveBenchmarkError:
        raise CodexExecFailure(
            failure_code="CODEX_EVENT_STREAM_INVALID_OR_UNMETERED",
            returncode=proc.returncode, stderr=stderr or b"", stdout=output or b"",
        ) from None
    observed["cli_event_stream_sha256"] = hashlib.sha256(output).hexdigest()
    return answer, observed


def _preflight(public: dict, registry: RoleRegistryV1, team: TeamManifestV1,
               authorization: dict) -> list[dict]:
    if (not isinstance(public, dict) or set(public) != {"schema_version", "cases"}
            or public["schema_version"] != 1 or not isinstance(public["cases"], list)):
        raise LiveBenchmarkError("invalid public case suite")
    cases = public["cases"]
    if not 1 <= len(cases) <= MAX_CASES or team.registry_digest != registry.digest():
        raise LiveBenchmarkError("case count or role registry identity differs from frozen team")
    members = {member.role.role_id: member.role for member in team.members}
    if len(members) != len(team.members):
        raise LiveBenchmarkError("each pinned role must have one unambiguous team member")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or _contains_secret_key(case):
            raise LiveBenchmarkError("case is malformed or contains forbidden oracle labels")
        cid, role_id = case.get("case_id"), case.get("role_id")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", cid) or cid in seen:
            raise LiveBenchmarkError("duplicate or malformed case ID")
        seen.add(cid)
        if case.get("suite") != "role_capability" or case.get("split") not in {"DEV", "VALIDATION", "HOLDOUT"}:
            raise LiveBenchmarkError("live runner only accepts frozen role-capability cases")
        if role_id not in _ROLES or role_id not in members:
            raise LiveBenchmarkError("case role is not an admitted v0.2 role")
        defined, member = registry.role(role_id), members[role_id]
        if (defined.adapter != "codex" or defined.provider != "openai"
                or defined.model != member.model or defined.prompt_digest != member.prompt_digest
                or defined.model_version != member.model_version or member.adapter != "codex"
                or member.provider != "openai"):
            raise LiveBenchmarkError("live case role is not a pinned OpenAI Codex role")
        # The model-only diagnostic grants NO tools even if a production
        # researcher/optimizer role has wider Core-gated capabilities.
        # Pin the original profile rather than silently rewriting its role ID.
        if member.capability_policy.network_access:
            raise LiveBenchmarkError("role benchmark refuses an independently network-enabled role")
        if role_id in {"independent_verifier", "integration_verifier"} and (
                member.capability_policy.tools or member.capability_policy.read_paths
                or member.capability_policy.write_paths):
            raise LiveBenchmarkError("independent verifier roles must be tool-free")
        data = case.get("agent_input")
        if not isinstance(data, dict) or digest(data) != case.get("provenance", {}).get("content_sha256"):
            raise LiveBenchmarkError("public input provenance mismatch")
        if case.get("provenance", {}).get("license_status") not in {"APPROVED", "INTERNAL_SANITIZED"}:
            raise LiveBenchmarkError("task rights not approved")
        limits = case.get("resource_budget")
        if not isinstance(limits, dict) or type(limits.get("wall_clock_seconds")) is not int or not 1 <= limits["wall_clock_seconds"] <= 1200:
            raise LiveBenchmarkError("case timeout is not a bounded positive integer")
        if limits.get("max_agent_calls") != 1 or limits.get("max_tool_calls") != 0:
            raise LiveBenchmarkError("case must authorize precisely one model call and no tools")
    required = {"schema_version", "mode", "suite_sha256", "registry_sha256",
                "team_manifest_sha256", "max_cases", "max_observed_input_tokens",
                "max_observed_output_tokens", "max_wall_seconds_per_call",
                "acknowledge_subscription_usage_unverified", "acknowledge_token_caps_observational"}
    if (not isinstance(authorization, dict) or set(authorization) != required
            or authorization["schema_version"] != 1
            or authorization["mode"] != "CODEX_SUBSCRIPTION_NONQUALIFYING_DIAGNOSTIC"
            or authorization["suite_sha256"] != digest(public)
            or authorization["registry_sha256"] != registry.digest()
            or authorization["team_manifest_sha256"] != team.manifest_digest
            or authorization["acknowledge_subscription_usage_unverified"] is not True
            or authorization["acknowledge_token_caps_observational"] is not True):
        raise LiveBenchmarkError("separate exact-SHA owner diagnostic authorization is missing")
    for key in ("max_cases", "max_observed_input_tokens", "max_observed_output_tokens", "max_wall_seconds_per_call"):
        if type(authorization[key]) is not int or authorization[key] < 1:
            raise LiveBenchmarkError("authorization resource budget must be a positive integer")
    if (len(cases) > authorization["max_cases"]
            or any(c["resource_budget"]["wall_clock_seconds"] > authorization["max_wall_seconds_per_call"] for c in cases)):
        raise LiveBenchmarkError("case suite exceeds independently approved call/time budget")
    return cases


def run_live(*, cases_path: str, registry_path: str, team_path: str,
             authorization_path: str, output_dir: str,
             codex_executable: str = "codex",
             execute: Callable[..., tuple[dict, dict]] = run_codex_case) -> dict:
    """Dispatch only a separately approved, bounded, public-task Codex diagnostic.

    The oracle cannot be passed to this function; scoring belongs to a second
    machine/operator. Token counts are CLI observations, not signed receipts.
    """
    public, auth = read_json(cases_path), read_json(authorization_path)
    registry = RoleRegistryV1.model_validate(read_json(registry_path))
    team = TeamManifestV1.model_validate(read_json(team_path))
    cases = _preflight(public, registry, team, auth)
    output = Path(output_dir)
    if output.exists():
        raise LiveBenchmarkError("cannot overwrite an existing live benchmark output")
    output.mkdir(parents=True, mode=0o700)
    responses = {}
    events = []
    chain_head = "0" * 64
    used_input = used_output = 0
    state = "IN_PROGRESS"
    try:
        for case in cases:
            entry = {"case_id": case["case_id"], "role_id": case["role_id"],
                     "model_id": registry.role(case["role_id"]).model,
                     "provider": "openai", "meter_source": "UNSIGNED_CODEX_CLI_EVENT",
                     "provider_cost_usd": None, "provider_receipt_verified": False,
                     "status": "STARTED", "previous_sha256": chain_head}
            # Write-before-dispatch so an interrupted/unknown call is never retried implicitly.
            events.append(entry)
            _write(output / "usage.observed.json", {"schema_version": 1, "records": events})
            try:
                reply, usage = execute(
                    case, entry["model_id"], codex_executable=codex_executable,
                    timeout_seconds=case["resource_budget"]["wall_clock_seconds"],
                )
                if (not isinstance(usage, dict) or
                        any(type(usage.get(k)) is not int or usage[k] < 0 for k in
                            ("input_tokens", "cached_input_tokens", "output_tokens"))):
                    raise LiveBenchmarkError("model transport omitted observable token usage")
                entry["usage"] = usage
                used_input += usage["input_tokens"]
                used_output += usage["output_tokens"]
                result = validate_role_reply(case["role_id"], reply, case["agent_input"])
                entry["status"] = "COMPLETED"
                entry["response_sha256"] = digest(result)
                responses[case["case_id"]] = result
                if used_input > auth["max_observed_input_tokens"] or used_output > auth["max_observed_output_tokens"]:
                    entry["status"] = "OVER_OBSERVED_BUDGET"
                    state = "STOPPED_OVER_BUDGET"
                    break
            except Exception as exc:
                entry["status"] = ("AGENT_ERROR_WITH_CLI_USAGE"
                                   if "usage" in entry else "UNKNOWN_SETTLEMENT_STOP")
                entry["error_type"] = type(exc).__name__
                if isinstance(exc, CodexExecFailure):
                    for key in ("failure_code", "cli_exit_code", "stderr_bytes", "stderr_sha256",
                                "stdout_bytes", "stdout_sha256", "stdout_format", "stdout_event_types"):
                        entry[key] = getattr(exc, key)
                elif "usage" not in entry and isinstance(exc, LiveBenchmarkError):
                    entry["failure_code"] = "RUNNER_PREFLIGHT_OR_EVENT_VALIDATION_ERROR"
                state = ("STOPPED_METERED_AGENT_ERROR" if "usage" in entry
                         else "STOPPED_UNKNOWN_USAGE_OR_INVALID_OUTPUT")
                break
            finally:
                entry["entry_sha256"] = digest({k: v for k, v in entry.items() if k != "entry_sha256"})
                chain_head = entry["entry_sha256"]
                _write(output / "usage.observed.json", {
                    "schema_version": 1, "chain_head_sha256": chain_head, "records": events,
                    "usage_authority": "UNSIGNED_OBSERVATION_NOT_PROVIDER_RECEIPT",
                })
                _write(output / "responses.json", {"schema_version": 1, "responses": responses})
        else:
            state = "COMPLETED_DIAGNOSTIC"
    finally:
        _write(output / "run-status.json", {
            "schema_version": 1, "status": state,
            "suite_sha256": digest(public), "registry_sha256": registry.digest(),
            "team_manifest_sha256": team.manifest_digest,
            "completed_cases": len(responses),
            "attempted_cases": len(events),
            "observed_input_tokens": used_input,
            "observed_output_tokens": used_output,
            "actual_provider_cost_usd": None,
            "release_eligible": False,
            "rpdi14_independent_benchmark": False,
        })
    return read_json(output / "run-status.json")


def verify_codex_observation(path: str) -> dict:
    """Check local hash-chain integrity, NOT upstream signature or actual billing."""
    document = read_json(path)
    records = document.get("records") if isinstance(document, dict) else None
    if (not isinstance(records, list)
            or document.get("usage_authority") != "UNSIGNED_OBSERVATION_NOT_PROVIDER_RECEIPT"):
        raise LiveBenchmarkError("Codex usage ledger is not a completed observation archive")
    previous = "0" * 64
    seen = set()
    observed_input = observed_output = unknown = 0
    for entry in records:
        if not isinstance(entry, dict) or entry.get("previous_sha256") != previous:
            raise LiveBenchmarkError("Codex usage chain is incomplete or reordered")
        sha = entry.get("entry_sha256")
        if sha != digest({k: v for k, v in entry.items() if k != "entry_sha256"}):
            raise LiveBenchmarkError("Codex observation digest mismatch")
        if entry.get("case_id") in seen:
            raise LiveBenchmarkError("Codex meter repeats a case identity")
        seen.add(entry.get("case_id"))
        usage = entry.get("usage")
        if usage is None:
            unknown += 1
        else:
            if any(type(usage.get(key)) is not int or usage[key] < 0
                   for key in ("input_tokens", "output_tokens", "cached_input_tokens")):
                raise LiveBenchmarkError("Codex observed token counts are malformed")
            observed_input += usage["input_tokens"]
            observed_output += usage["output_tokens"]
        previous = sha
    if document.get("chain_head_sha256") != previous:
        raise LiveBenchmarkError("Codex observation head digest differs")
    return {"observed_calls": len(records), "known_token_calls": len(records) - unknown,
            "unknown_token_calls": unknown,
            "observed_input_tokens": observed_input, "observed_output_tokens": observed_output,
            "operator_signed": False, "provider_signed": False,
            "upstream_billing_verified": False, "tamper_evident_without_external_anchor": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in Codex CLI (non-qualifying) v0.2 role diagnostic")
    for arg in ("cases", "registry", "team", "authorization", "out"):
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--i-authorize-codex-exec", action="store_true")
    args = parser.parse_args(argv)
    if not args.i_authorize_codex_exec:
        parser.error("explicit --i-authorize-codex-exec is required (this consumes subscription quota)")
    try:
        result = run_live(cases_path=args.cases, registry_path=args.registry,
                          team_path=args.team, authorization_path=args.authorization,
                          output_dir=args.out, codex_executable=args.codex_executable)
    except (LiveBenchmarkError, BenchmarkError, ValueError) as exc:
        parser.exit(2, f"live benchmark refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "COMPLETED_DIAGNOSTIC" else 3


if __name__ == "__main__":
    raise SystemExit(main())
