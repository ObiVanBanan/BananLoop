"""Minimal task-container MCP client used by the Harbor bridge agent.

Only `submit` and `result` tools are allowed. The JSON written to stdout is
sanitized before it leaves the task container; raw grader output is discarded.
"""
from __future__ import annotations

import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "http://grader:8000/mcp"
PROTOCOL_VERSION = "2024-11-05"
ALLOWED_TOOLS = {"submit", "result"}


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
    except (HTTPError, URLError, TimeoutError, OSError):
        # Never echo server text or grader output into Harbor's agent log.
        raise RuntimeError("public MCP transport failed") from None
    if "text/event-stream" in content_type:
        data_lines = [line[5:].strip() for line in raw.decode("utf-8").splitlines()
                      if line.startswith("data:")]
        if not data_lines:
            return None, session_id
        raw = data_lines[-1].encode("utf-8")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("public MCP response was not valid JSON") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("public MCP response was not an object")
    return parsed, session_id


def _tool_payload(response: dict[str, Any], tool: str) -> dict[str, Any]:
    if "error" in response:
        return {"transport_error": True}
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return {"transport_error": True}
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
        return {"transport_error": True}
    safe: dict[str, Any] = {}
    for key in ("id", "status", "validation_score", "grading_seconds",
                "time_remaining_seconds", "total_budget_seconds"):
        item = value.get(key)
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            safe[key] = item
    if "error" in value:
        safe["error"] = bool(value["error"])
    return safe


def main() -> int:
    try:
        request = json.loads(open(sys.argv[1], encoding="utf-8").read())
        if not isinstance(request, dict) or request.get("tool") not in ALLOWED_TOOLS:
            raise ValueError
        tool = request["tool"]
        arguments = request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError
        session_id = request.get("session_id")
        request_id = request.get("request_id", 1)
        if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
            raise ValueError
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
                raise RuntimeError("public MCP initialization failed")
            _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
            request_id += 1
        response, session_id = _post({
            "jsonrpc": "2.0", "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }, session_id)
        if response is None:
            raise RuntimeError("public MCP tool response is empty")
        result = _tool_payload(response, tool)
        output = {"session_id": session_id, "request_id": request_id + 1, "result": result}
        sys.stdout.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except Exception:
        sys.stdout.write('{"transport_error":true}\n')
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
