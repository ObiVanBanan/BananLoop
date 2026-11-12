"""Read-only Harbor agent that captures the public ARE MCP contract."""
from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

from .public_mcp_contract import make_public_mcp_contract


_CLIENT = Path(__file__).with_name("are_public_mcp_client.py")
_REMOTE_CLIENT = "/tmp/bananloop_are_public_mcp_client.py"


class PublicContractProbeOptions(AgentOptions):
    task_id: str = Field(min_length=1, max_length=200)
    task_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AREPublicMCPContractProbeAgent(BaseAgent):
    """Probe tools/list and read-only status; it cannot submit a candidate."""

    options_model = PublicContractProbeOptions
    capabilities = AgentCapabilities(mcp_servers=True)

    def __init__(self, *args: Any, task_id: str | None = None,
                 task_version: str | None = None, task_sha256: str | None = None,
                 task_instruction_sha256: str | None = None, **kwargs: Any):
        super().__init__(
            *args, task_id=task_id, task_version=task_version,
            task_sha256=task_sha256,
            task_instruction_sha256=task_instruction_sha256, **kwargs,
        )
        self.task_id = task_id
        self.task_version = task_version
        self.task_sha256 = task_sha256
        self.task_instruction_sha256 = task_instruction_sha256

    @staticmethod
    def name() -> str:
        return "bananloop-are-public-contract-probe"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        if not _CLIENT.is_file() or _CLIENT.is_symlink():
            raise RuntimeError("public ARE MCP client script is missing or unsafe")
        servers = [item for item in self.mcp_servers if item.name == "are"]
        if (len(servers) != 1 or servers[0].transport != "streamable-http"
                or servers[0].url != "http://grader:8000/mcp"):
            raise RuntimeError("Harbor task lacks the pinned public ARE MCP endpoint")
        await environment.upload_file(_CLIENT, _REMOTE_CLIENT)

    async def _call(self, environment: BaseEnvironment, *, action: str,
                    session_id: str | None, request_id: int) -> dict[str, Any]:
        request_path = "/tmp/bananloop_are_probe_" + uuid4().hex + ".json"
        body = json.dumps({
            "tool": action, "arguments": {},
            "session_id": session_id, "request_id": request_id,
        }, sort_keys=True, separators=(",", ":")).encode()
        encoded = base64.b64encode(body).decode("ascii")
        writer = (
            "import base64,os,sys; fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); "
            "os.write(fd,base64.b64decode(sys.argv[2],validate=True)); os.close(fd)"
        )
        command = (
            f"python3 -c {shlex.quote(writer)} {shlex.quote(request_path)} {shlex.quote(encoded)} && "
            f"python3 {shlex.quote(_REMOTE_CLIENT)} {shlex.quote(request_path)}"
        )
        try:
            result = await environment.exec(command, timeout_sec=130)
            if result.return_code != 0 or not result.stdout:
                raise RuntimeError("public MCP probe returned no sanitized response")
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            if not isinstance(payload, dict) or payload.get("transport_error"):
                category = payload.get("transport_category") if isinstance(payload, dict) else None
                raise RuntimeError(f"public MCP probe failed ({category or 'unknown'})")
            return payload
        finally:
            await environment.exec(f"rm -f {shlex.quote(request_path)}", timeout_sec=5)

    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        listing = await self._call(
            environment, action="discover_contract", session_id=None, request_id=1,
        )
        result = listing.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        server_info = listing.get("server_info")
        protocol_version = listing.get("protocol_version")
        if (not isinstance(tools, list) or not isinstance(server_info, Mapping)
                or protocol_version != "2024-11-05"):
            raise RuntimeError("public MCP contract probe returned incomplete metadata")
        status = await self._call(
            environment, action="status", session_id=listing.get("session_id"),
            request_id=listing.get("request_id", 2),
        )
        status_result = status.get("result")
        if not isinstance(status_result, dict):
            raise RuntimeError("read-only public grader status probe did not return a recognized state")
        contract = make_public_mcp_contract(
            task_id=self.task_id, task_version=self.task_version,
            task_sha256=self.task_sha256,
            task_instruction_sha256=self.task_instruction_sha256,
            endpoint="http://grader:8000/mcp", protocol_version=protocol_version,
            server_info=server_info, discovered_tools=tools,
        )
        context.metadata = {
            **(context.metadata or {}),
            "public_mcp_contract": contract,
            "status_probe": {
                key: status_result[key] for key in (
                    "status", "submissions", "queued_or_grading",
                    "time_remaining_seconds", "total_budget_seconds",
                ) if key in status_result
            },
        }
