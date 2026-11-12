"""Harbor BaseAgent bridge that runs one frozen BananLoop v2 Optimization child.

This is an execution adapter, not a direct-writing benchmark agent. The child
campaign, role registry, and independently approved protocol must already be
prepared. A separate task driver is responsible for creating the next child
and stopping after five candidate snapshots or the persisted one-hour deadline.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shlex
import tempfile
from argparse import Namespace
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

from bananloop.trusted_evaluator import TrustedEvaluatorError
from bananloop.v2_optimization_cli import run_v2_optimize

from .optimization_runner import AREPublicEvaluatorRunner
from .public_grader import PublicAREGrader
from .public_devkit import run_public_devkit


_MCP_CLIENT = Path(__file__).with_name("are_public_mcp_client.py")
_REMOTE_MCP_CLIENT = "/tmp/bananloop_are_public_mcp_client.py"
_PUBLIC_OUTPUT = "/app/output/impute.py"
_CANDIDATE_PATH = "benchmarks/autoresearchexam/solutions/impute.py"


class AREBridgeOptions(AgentOptions):
    config_path: str = Field(description="Host-side frozen BananLoop task/campaign config JSON")


class AREBananLoopOptimizationAgent(BaseAgent):
    """Harbor custom agent binding Core Optimization to the task public grader."""

    options_model = AREBridgeOptions
    # Harbor merges task-level MCP servers into custom agents and rejects the
    # trial unless the agent explicitly declares that it can use them. The
    # adapter calls only the task's public submit/result tools through the
    # environment bridge; it never exposes private verifier services.
    capabilities = AgentCapabilities(mcp_servers=True)

    def __init__(self, *args: Any, config_path: str | None = None, **kwargs: Any):
        # Preserve the Harbor custom-agent option so BaseAgent validates it
        # against options_model instead of reporting the required field missing.
        super().__init__(*args, config_path=config_path, **kwargs)
        self.config_path = Path(config_path).expanduser().resolve() if config_path else None
        self._mcp_state: dict[str, Any] = {"session_id": None, "request_id": 1}

    @staticmethod
    def name() -> str:
        return "bananloop-v2-are-public"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        if self.config_path is None or not self.config_path.is_file() or self.config_path.is_symlink():
            raise RuntimeError("Harbor bridge requires a local nonsymlink config_path")
        if not _MCP_CLIENT.is_file() or _MCP_CLIENT.is_symlink():
            raise RuntimeError("public ARE MCP client script is missing or unsafe")
        await environment.upload_file(_MCP_CLIENT, _REMOTE_MCP_CLIENT)
        check = await environment.exec(
            "test -d /app/output && test ! -L /app/output/impute.py",
            timeout_sec=10,
        )
        if check.return_code != 0:
            raise RuntimeError("ARE output directory or starter file is unavailable")

    def _load_config(self, instruction: str) -> dict[str, Any]:
        assert self.config_path is not None
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RuntimeError("Harbor bridge config must be a JSON object")
        required = {
            "repo", "state_dir", "campaign_id", "role_registry", "task_id",
            "task_version", "task_instruction_sha256", "mcp_contract_sha256",
            "mcp_contract_path", "model_call_limit", "timeout_seconds",
            "visible_path", "write_path", "public_dev_image", "public_dev_image_id",
        }
        if required - set(raw):
            raise RuntimeError("Harbor bridge config is missing required pinned fields")
        repo = Path(raw["repo"]).expanduser().resolve()
        selection_path = repo / "benchmarks/autoresearchexam/task-selection.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        matches = [task for task in selection.get("tasks", [])
                   if task.get("task_id") == raw["task_id"]]
        if len(matches) != 1:
            raise RuntimeError("task ID is not uniquely pinned in the AutoResearchExam shortlist")
        task = matches[0]
        instruction_digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        if (task.get("version") != raw["task_version"]
                or task.get("instruction_sha256") != raw["task_instruction_sha256"]
                or instruction_digest != raw["task_instruction_sha256"]):
            raise RuntimeError("Harbor task instruction or version differs from the pinned shortlist")
        contract_path = Path(raw["mcp_contract_path"]).expanduser().resolve()
        if not contract_path.is_file() or contract_path.is_symlink():
            raise RuntimeError("public ARE MCP contract file is missing or unsafe")
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        discovered_names = {
            item.get("name") for item in contract.get("discovered_tools", [])
            if isinstance(item, dict)
        }
        if (contract.get("contract_sha256") != raw["mcp_contract_sha256"]
                or contract.get("task_id") != raw["task_id"]
                or contract.get("task_version") != raw["task_version"]
                or contract.get("task_instruction_sha256") != raw["task_instruction_sha256"]
                or contract.get("public_endpoint") != "http://grader:8000/mcp"
                or not {"submit", "result"} <= discovered_names):
            raise RuntimeError("public MCP contract does not match the pinned task or tool allowlist")
        for field in ("model_call_limit", "timeout_seconds"):
            value = raw[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise RuntimeError(f"{field} must be an integer")
        if not 0 <= raw["model_call_limit"] <= 20 or not 1 <= raw["timeout_seconds"] <= 300:
            raise RuntimeError("model call or request timeout limit is outside the supported range")
        time_limit = raw.get("time_limit_seconds", 3600)
        if isinstance(time_limit, bool) or not isinstance(time_limit, int) or not 1 <= time_limit <= 3600:
            raise RuntimeError("task time limit must be from 1 to 3600 seconds")
        if raw.get("max_candidate_iterations", 5) != 5:
            raise RuntimeError("ARE task driver must enforce exactly five candidate iterations")
        if not isinstance(raw["visible_path"], list) or not isinstance(raw["write_path"], list):
            raise RuntimeError("visible_path and write_path must be path arrays")
        if not isinstance(raw.get("status_probe_only", False), bool):
            raise RuntimeError("status_probe_only must be a boolean")
        if raw.get("status_probe_only") is True and raw["model_call_limit"] != 0:
            raise RuntimeError("status-only probe must have a zero model-call limit")
        if any(not isinstance(path, str) for path in (*raw["visible_path"], *raw["write_path"])):
            raise RuntimeError("visible_path and write_path entries must be strings")
        if raw.get("candidate_path", _CANDIDATE_PATH) != _CANDIDATE_PATH:
            raise RuntimeError("this bridge configuration currently pins the imputation task output")
        if raw.get("public_output_path", _PUBLIC_OUTPUT) != _PUBLIC_OUTPUT:
            raise RuntimeError("this bridge configuration currently pins /app/output/impute.py")
        if (raw["public_dev_image"] != AREPublicEvaluatorRunner.default_dev_image
                or raw["public_dev_image_id"] != AREPublicEvaluatorRunner.default_dev_image_id
                or raw.get("public_dev_cell_count", 42) != AREPublicEvaluatorRunner.default_dev_cell_count):
            raise RuntimeError("public DEV image or panel differs from the reviewed task pin")
        if (raw.get("max_candidate_iterations", 5) != 5
                or raw.get("time_limit_seconds", 3600) > 3600):
            raise RuntimeError("ARE bridge may run at most five candidates and one hour")
        servers = [item for item in self.mcp_servers if item.name == "are"]
        if (len(servers) != 1 or servers[0].transport != "streamable-http"
                or servers[0].url != "http://grader:8000/mcp"):
            raise RuntimeError("Harbor task lacks the pinned public ARE MCP endpoint")
        raw["repo"] = str(repo)
        raw["state_dir"] = str(Path(raw["state_dir"]).expanduser().resolve())
        return raw

    async def _call_public_tool(
        self, environment: BaseEnvironment, tool: str, arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if tool not in {"submit", "result", "status"}:
            raise RuntimeError("ARE bridge only permits public submit/result and read-only status MCP tools")
        remote_request = f"/tmp/bananloop_are_request_{uuid4().hex}.json"
        request_body = {
            "tool": tool,
            "arguments": dict(arguments),
            "session_id": self._mcp_state["session_id"],
            "request_id": self._mcp_state["request_id"],
        }
        encoded_request = base64.b64encode(
            json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ).decode("ascii")
        # Create the request file as the task's agent user. Harbor upload_file
        # preserved NamedTemporaryFile's host mode 0600 but materialized it
        # under another UID, making MCP requests unreadable inside the task.
        write_script = (
            "import base64,os,sys; "
            "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); "
            "os.write(fd, base64.b64decode(sys.argv[2], validate=True)); os.close(fd)"
        )
        command = (
            f"python3 -c {shlex.quote(write_script)} {shlex.quote(remote_request)} "
            f"{shlex.quote(encoded_request)} && "
            f"python3 {shlex.quote(_REMOTE_MCP_CLIENT)} {shlex.quote(remote_request)}"
        )
        try:
            result = await environment.exec(command, timeout_sec=125)
            if result.return_code != 0 or not result.stdout:
                error = RuntimeError("ARE public MCP client exited without a sanitized response")
                error.safe_category = "client_exit"
                raise error
            try:
                response = json.loads(result.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                error = RuntimeError("ARE public MCP client response was not valid JSON")
                error.safe_category = "malformed_client_output"
                raise error from None
            if not isinstance(response, dict) or response.get("transport_error"):
                category = response.get("transport_category") if isinstance(response, dict) else None
                if category not in {
                    "http_client_error", "http_server_error", "http_timeout", "network_error",
                    "local_io_error", "oversized_response", "malformed_response", "jsonrpc_error",
                    "tool_error", "missing_structured_result", "invalid_request", "initialize_error",
                    "empty_tool_response", "initialize_client_error", "notification_client_error",
                    "tool_client_error", "request_file_error", "request_shape_error", "tool_not_allowed",
                    "arguments_shape_error", "session_shape_error", "request_id_error",
                    "client_internal_error", "request_file_missing", "request_file_permission",
                    "request_file_encoding_error", "request_json_invalid",
                }:
                    category = "unknown_transport_error"
                error = RuntimeError(f"ARE public MCP call failed ({category})")
                error.safe_category = category
                raise error
            self._mcp_state = {
                "session_id": response.get("session_id"),
                "request_id": response.get("request_id", 1),
            }
            payload = response.get("result")
            if not isinstance(payload, dict):
                raise RuntimeError("ARE public MCP call returned no sanitized payload")
            return payload
        finally:
            await environment.exec(f"rm -f {shlex.quote(remote_request)}", timeout_sec=5)

    async def _publish_candidate(self, environment: BaseEnvironment, data: bytes) -> None:
        check = await environment.exec(
            "test -d /app/output && test ! -L /app/output/impute.py",
            timeout_sec=10,
        )
        if check.return_code != 0:
            raise RuntimeError("ARE candidate output path changed or is unsafe")
        with tempfile.NamedTemporaryFile("wb", suffix=".py") as stream:
            stream.write(data)
            stream.flush()
            await environment.upload_file(stream.name, _PUBLIC_OUTPUT)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext,
    ) -> None:
        raw = self._load_config(instruction)
        if raw.get("status_probe_only") is True:
            status = await self._call_public_tool(environment, "status", {})
            context.metadata = {
                **(context.metadata or {}),
                "bananloop_are_status_probe": {
                    key: status[key] for key in (
                        "status", "submissions", "queued_or_grading",
                        "time_remaining_seconds", "total_budget_seconds",
                    ) if key in status
                },
            }
            return
        loop = asyncio.get_running_loop()
        state_dir = Path(raw["state_dir"])
        state_dir.mkdir(parents=True, exist_ok=True)
        grader = PublicAREGrader(
            state_dir / "autoresearchexam-public-submissions.sqlite3",
            task_id=raw["task_id"],
            contract_sha256=raw["mcp_contract_sha256"],
            repeatability_runs=int(raw.get("repeatability_runs", 2)),
            time_limit_seconds=raw.get("time_limit_seconds", 3600),
            call_tool=lambda name, args: asyncio.run_coroutine_threadsafe(
                self._call_public_tool(environment, name, args), loop,
            ).result(timeout=130),
        )

        def publish_candidate(data: bytes) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._publish_candidate(environment, data), loop,
            )
            try:
                future.result(timeout=60)
            except FutureTimeout:
                # Upload outcome is not a public grading dispatch. Do not hide
                # the uncertainty, but a new task runner may safely reconcile
                # the current output hash before any submit is attempted.
                raise RuntimeError("candidate upload did not finish within 60 seconds") from None

        args = Namespace(
            repo=raw["repo"], state_dir=raw["state_dir"],
            campaign_id=raw["campaign_id"], role_registry=raw["role_registry"],
            visible_path=raw["visible_path"], write_path=raw["write_path"],
            approve_up_to_calls=raw["model_call_limit"],
            max_steps=raw.get("max_steps", 20), timeout_seconds=raw["timeout_seconds"],
            task_deadline_epoch=grader.deadline_epoch,
        )

        def runner_factory(protocol: Any) -> AREPublicEvaluatorRunner:
            if protocol.execution_backend_id != AREPublicEvaluatorRunner.backend_id:
                raise TrustedEvaluatorError("approved protocol is not pinned to the ARE public backend")
            if protocol.repeatability_runs != grader.repeatability_runs:
                raise TrustedEvaluatorError("approved protocol repeatability differs from the durable ARE ledger")
            def evaluate_dev(candidate: bytes, timeout_seconds: int) -> Mapping[str, Any]:
                return run_public_devkit(
                    candidate,
                    image=raw["public_dev_image"],
                    expected_image_id=raw["public_dev_image_id"],
                    timeout_seconds=timeout_seconds,
                    expected_cell_count=raw.get("public_dev_cell_count", 42),
                )

            return AREPublicEvaluatorRunner(
                grader=grader,
                publish_candidate=publish_candidate,
                task_id=raw["task_id"],
                task_version=raw["task_version"],
                contract_sha256=raw["mcp_contract_sha256"],
                candidate_path=raw.get("candidate_path", _CANDIDATE_PATH),
                dev_image=raw["public_dev_image"],
                dev_image_id=raw["public_dev_image_id"],
                dev_cell_count=raw.get("public_dev_cell_count", 42),
                evaluate_dev=evaluate_dev,
            )

        report = await asyncio.to_thread(
            run_v2_optimize, args, evaluator_runner_factory=runner_factory,
        )
        context.metadata = {
            **(context.metadata or {}),
            "bananloop_are": {
                "task_id": raw["task_id"],
                "task_version": raw["task_version"],
                "campaign_id": raw["campaign_id"],
                "status": report.get("status"),
                "live_model_called": report.get("live_model_called", "UNKNOWN"),
                "provider_call_attempts": report.get("provider_call_attempts", "UNKNOWN"),
                "usage": report.get("usage", "UNKNOWN"),
                "task_started_epoch": grader.started_epoch,
                "task_deadline_epoch": grader.deadline_epoch,
                "candidate_iterations_used": grader.candidate_iterations_used,
                "public_submissions": grader.submission_summary(),
                "public_mcp_failure_category": getattr(grader, "last_transport_failure_category", None),
                "report_path": report.get("report_path"),
            },
        }
