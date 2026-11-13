"""Minimal task-container MCP client used by the Harbor bridge agent.

Only the public `submit`, `result`, and read-only `status` tools are allowed;
`discover_contract` is a local action that performs MCP `tools/list` only.
The JSON written to stdout is sanitized before it leaves the task container;
raw grader output is discarded.
"""
from __future__ import annotations

import json
import math
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "http://grader:8000/mcp"
PROTOCOL_VERSION = "2024-11-05"
ALLOWED_TOOLS = {"submit", "result", "status"}
_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items", "enum", "const",
    "anyOf", "oneOf", "allOf", "not", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength", "maxLength", "pattern", "minItems", "maxItems",
    "uniqueItems", "format", "minProperties", "maxProperties", "$schema", "$ref",
}


def _sanitize_schema(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Bound and structurally allowlist JSON Schema, dropping arbitrary prose/metadata."""
    if budget is None:
        budget = [256]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        raise ValueError("schema exceeds structural bounds")
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("schema object exceeds field bound")
        out = {}
        for key, item in value.items():
            if key not in _SCHEMA_KEYS:
                continue
            if key in {"properties", "$defs", "definitions"}:
                if not isinstance(item, dict) or len(item) > 64:
                    raise ValueError("schema properties exceed bounds")
                out[key] = {str(name)[:120]: _sanitize_schema(child, depth=depth + 1, budget=budget)
                            for name, child in sorted(item.items())}
            elif key in {"required", "enum", "anyOf", "oneOf", "allOf"}:
                if not isinstance(item, list) or len(item) > 64:
                    raise ValueError("schema array exceeds bounds")
                if key in {"anyOf", "oneOf", "allOf"}:
                    out[key] = [_sanitize_schema(child, depth=depth + 1, budget=budget) for child in item]
                elif key == "required" and any(not isinstance(child, str) or len(child) > 120 for child in item):
                    raise ValueError("schema required list is malformed")
                elif key == "enum" and any(
                    not (child is None or isinstance(child, (str, int, bool))
                         or (isinstance(child, float) and math.isfinite(child)))
                    or (isinstance(child, str) and len(child) > 512) for child in item
                ):
                    raise ValueError("schema enum is malformed")
                else:
                    out[key] = item
            elif key in {"items", "not", "additionalProperties"} and isinstance(item, (dict, bool)):
                out[key] = _sanitize_schema(item, depth=depth + 1, budget=budget) if isinstance(item, dict) else item
            elif isinstance(item, (str, int, float, bool)) or item is None:
                if isinstance(item, str) and len(item) > 512:
                    raise ValueError("schema scalar exceeds bound")
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("schema scalar is not finite")
                out[key] = item
        return out
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError("schema array exceeds bound")
        return [_sanitize_schema(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError("schema contains unsupported value")


class _MCPFailure(RuntimeError):
    """A safe, non-content-bearing MCP failure classification."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _post(payload: dict[str, Any], session_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            session_id = response.headers.get("Mcp-Session-Id", session_id)
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("MCP response exceeds the local parse bound")
            if not raw:
                return None, session_id
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        # Status code is safe diagnostic metadata; the response body may
        # contain grader output and is intentionally discarded.
        status = int(exc.code)
        category = "http_client_error" if 400 <= status < 500 else "http_server_error"
        raise _MCPFailure(category) from None
    except TimeoutError:
        raise _MCPFailure("http_timeout") from None
    except URLError:
        raise _MCPFailure("network_error") from None
    except OSError:
        raise _MCPFailure("local_io_error") from None
    except ValueError:
        raise _MCPFailure("oversized_response") from None
    if "text/event-stream" in content_type:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _MCPFailure("malformed_response") from None
        data_lines = [line[5:].strip() for line in decoded.splitlines()
                      if line.startswith("data:")]
        if not data_lines:
            return None, session_id
        raw = data_lines[-1].encode("utf-8")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _MCPFailure("malformed_response") from None
    if not isinstance(parsed, dict):
        raise _MCPFailure("malformed_response")
    return parsed, session_id


def _tool_payload(response: dict[str, Any], tool: str) -> dict[str, Any]:
    if "error" in response:
        return {"transport_error": True, "transport_category": "jsonrpc_error"}
    result = response.get("result")
    if not isinstance(result, dict):
        return {"transport_error": True, "transport_category": "malformed_response"}
    if result.get("isError") is True:
        return {"transport_error": True, "transport_category": "tool_error"}
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    decoded = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    value = decoded
                    break
    if not isinstance(value, dict):
        return {"transport_error": True, "transport_category": "missing_structured_result"}
    safe: dict[str, Any] = {}
    for key in ("id", "status", "validation_score", "grading_seconds",
                "time_remaining_seconds", "total_budget_seconds"):
        item = value.get(key)
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            safe[key] = item
    if tool == "status":
        for key in ("submissions", "queued_or_grading"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                safe[key] = item
    if "error" in value:
        safe["error"] = bool(value["error"])
    return safe


def _tool_list_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Keep public MCP schemas while dropping arbitrary top-level metadata."""
    if "error" in response:
        return {"transport_error": True, "transport_category": "jsonrpc_error"}
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return {"transport_error": True, "transport_category": "malformed_response"}
    tools = result.get("tools")
    if not isinstance(tools, list) or len(tools) > 64:
        return {"transport_error": True, "transport_category": "malformed_response"}
    sanitized = []
    names: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            return {"transport_error": True, "transport_category": "malformed_response"}
        name, description = item.get("name"), item.get("description", "")
        input_schema, output_schema = item.get("inputSchema", {}), item.get("outputSchema", {})
        if (not isinstance(name, str) or not name or len(name) > 120 or name in names
                or not isinstance(description, str) or len(description) > 512
                or not isinstance(input_schema, dict) or not isinstance(output_schema, dict)):
            return {"transport_error": True, "transport_category": "malformed_response"}
        names.add(name)
        try:
            sanitized.append({"name": name, "description": description,
                              "inputSchema": _sanitize_schema(input_schema),
                              "outputSchema": _sanitize_schema(output_schema)})
        except (TypeError, ValueError):
            return {"transport_error": True, "transport_category": "malformed_response"}
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 128_000:
        return {"transport_error": True, "transport_category": "oversized_response"}
    return {"tools": sanitized}


def main() -> int:
    try:
        with open(sys.argv[1], encoding="utf-8") as request_stream:
            request = json.load(request_stream)
    except FileNotFoundError:
        category = "request_file_missing"
    except PermissionError:
        category = "request_file_permission"
    except UnicodeDecodeError:
        category = "request_file_encoding_error"
    except json.JSONDecodeError:
        category = "request_json_invalid"
    except OSError:
        category = "request_file_error"
    else:
        category = None
    if category is not None:
        sys.stdout.write(json.dumps({"transport_error": True, "transport_category": category},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    try:
        if not isinstance(request, dict):
            raise _MCPFailure("request_shape_error")
        tool = request["tool"]
        if not isinstance(tool, str) or tool not in (*ALLOWED_TOOLS, "discover_contract"):
            raise _MCPFailure("tool_not_allowed")
        arguments = request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise _MCPFailure("arguments_shape_error")
        session_id = request.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise _MCPFailure("session_shape_error")
        request_id = request.get("request_id", 1)
        if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
            raise _MCPFailure("request_id_error")
        server_info = None
        protocol_version = None
        if session_id is None:
            initialized, session_id = _post({
                "jsonrpc": "2.0", "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "bananloop-are-bridge", "version": "1"},
                },
            }, None)
            if not initialized or "error" in initialized:
                raise _MCPFailure("initialize_error")
            initialize_result = initialized.get("result") if isinstance(initialized, dict) else None
            if isinstance(initialize_result, dict):
                candidate_protocol = initialize_result.get("protocolVersion")
                if isinstance(candidate_protocol, str) and len(candidate_protocol) <= 120:
                    protocol_version = candidate_protocol
                candidate_info = initialize_result.get("serverInfo")
                if isinstance(candidate_info, dict):
                    name, version = candidate_info.get("name"), candidate_info.get("version")
                    if (isinstance(name, str) and len(name) <= 120
                            and isinstance(version, str) and len(version) <= 120):
                        server_info = {"name": name, "version": version}
            _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
            request_id += 1
        method = "tools/list" if tool == "discover_contract" else "tools/call"
        params = {} if tool == "discover_contract" else {"name": tool, "arguments": arguments}
        if tool == "discover_contract" and arguments:
            raise _MCPFailure("arguments_shape_error")
        response, session_id = _post({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params,
        }, session_id)
        if response is None:
            raise _MCPFailure("empty_tool_response")
        result = _tool_list_payload(response) if tool == "discover_contract" else _tool_payload(response, tool)
        output = {"session_id": session_id, "request_id": request_id + 1, "result": result}
        if protocol_version is not None:
            output["protocol_version"] = protocol_version
        if server_info is not None:
            output["server_info"] = server_info
        sys.stdout.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except _MCPFailure as exc:
        sys.stdout.write(json.dumps({"transport_error": True, "transport_category": exc.category},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except Exception:
        sys.stdout.write(json.dumps({"transport_error": True, "transport_category": "client_internal_error"},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
