"""Canonical owner-side representation of an observed public ARE MCP contract."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def make_public_mcp_contract(
    *, task_id: str, task_version: str, task_sha256: str,
    task_instruction_sha256: str, endpoint: str, protocol_version: str,
    server_info: Mapping[str, Any], discovered_tools: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if endpoint != "http://grader:8000/mcp" or protocol_version != "2024-11-05":
        raise ValueError("public MCP endpoint or protocol version is not pinned")
    if (not isinstance(task_id, str) or not task_id
            or not isinstance(task_version, str) or not task_version
            or any(not isinstance(value, str) or len(value) != 64
                   or any(ch not in "0123456789abcdef" for ch in value)
                   for value in (task_sha256, task_instruction_sha256))):
        raise ValueError("public MCP contract task identity is malformed")
    names: set[str] = set()
    tools = []
    for item in discovered_tools:
        if not isinstance(item, Mapping):
            raise ValueError("public MCP tool descriptor must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("public MCP tool names must be unique nonempty strings")
        names.add(name)
        tools.append(dict(item))
    if not {"submit", "result"} <= names:
        raise ValueError("public MCP contract lacks the submit/result tools")
    server = dict(server_info)
    if (set(server) != {"name", "version"}
            or any(not isinstance(server[key], str) or not server[key] for key in server)):
        raise ValueError("public MCP server information is malformed")
    body = {
        "schema_version": 1, "task_id": task_id, "task_version": task_version,
        "task_sha256": task_sha256,
        "task_instruction_sha256": task_instruction_sha256,
        "public_endpoint": endpoint, "mcp_protocol_version": protocol_version,
        "server": server, "discovered_tools": tools,
        "probe_scope": {
            "operations": ["initialize", "tools/list", "tools/call(status)"],
            "model_called": False, "submit_called": False,
            "result_called": False, "private_verifier_enabled": False,
        },
    }
    return {**body, "contract_sha256": hashlib.sha256(_canonical(body)).hexdigest()}


def validate_public_mcp_contract(contract: Any, *, task_id: str | None = None,
                                 task_version: str | None = None,
                                 task_sha256: str | None = None,
                                 task_instruction_sha256: str | None = None) -> dict[str, Any]:
    if not isinstance(contract, dict):
        raise ValueError("public MCP contract must be an object")
    required = {
        "schema_version", "task_id", "task_version", "task_sha256",
        "task_instruction_sha256", "public_endpoint", "mcp_protocol_version",
        "server", "discovered_tools", "probe_scope", "contract_sha256",
    }
    if set(contract) != required:
        raise ValueError("public MCP contract has missing or unexpected fields")
    expected = make_public_mcp_contract(
        task_id=contract["task_id"], task_version=contract["task_version"],
        task_sha256=contract["task_sha256"],
        task_instruction_sha256=contract["task_instruction_sha256"],
        endpoint=contract["public_endpoint"], protocol_version=contract["mcp_protocol_version"],
        server_info=contract["server"], discovered_tools=contract["discovered_tools"],
    )
    if contract != expected:
        raise ValueError("public MCP contract digest or scope does not match its content")
    for label, actual, wanted in (
        ("task ID", contract["task_id"], task_id),
        ("task version", contract["task_version"], task_version),
        ("task source digest", contract["task_sha256"], task_sha256),
        ("task instruction digest", contract["task_instruction_sha256"], task_instruction_sha256),
    ):
        if wanted is not None and actual != wanted:
            raise ValueError(f"public MCP contract {label} differs from the pinned selection")
    return expected
