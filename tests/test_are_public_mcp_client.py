from __future__ import annotations

import json

from benchmarks.autoresearchexam import are_public_mcp_client as client


class _Response:
    def __init__(self, body: bytes, *, content_type="application/json", session_id=None):
        self.body = body
        self.headers = {"Content-Type": content_type}
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.body


def test_mcp_client_initializes_and_strips_grader_tail(tmp_path, monkeypatch, capsys):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({
        "tool": "result", "arguments": {"id": 12},
        "session_id": None, "request_id": 5,
    }))
    responses = [
        _Response(b'{"jsonrpc":"2.0","id":5,"result":{"protocolVersion":"2024-11-05"}}',
                  session_id="session-1"),
        _Response(b"", content_type="application/json", session_id="session-1"),
        _Response(json.dumps({
            "jsonrpc": "2.0", "id": 6,
            "result": {"structuredContent": {
                "id": 12, "status": "graded", "validation_score": 0.4,
                "grading_seconds": 3.0, "error": None,
                "grader_output_tail": "must never leave task container",
            }},
        }).encode(), session_id="session-1"),
    ]
    sent = []

    def fake_urlopen(request, timeout):
        sent.append((request, timeout))
        return responses.pop(0)

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    assert client.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["result"] == {
        "id": 12, "status": "graded", "validation_score": 0.4,
        "grading_seconds": 3.0, "error": False,
    }
    assert "grader_output_tail" not in output
    assert len(sent) == 3
    assert sent[0][0].full_url == "http://grader:8000/mcp"
    assert sent[1][0].get_header("Mcp-session-id") == "session-1"
    assert sent[2][0].get_header("Mcp-session-id") == "session-1"
    assert sent[2][1] == 120


def test_mcp_client_allows_read_only_status_and_sanitizes_counts(
    tmp_path, monkeypatch, capsys,
):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"tool": "status", "arguments": {}}))
    responses = [
        _Response(b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}',
                  session_id="session-2"),
        _Response(b"", content_type="application/json", session_id="session-2"),
        _Response(json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "result": {"structuredContent": {
                "submissions": 2, "queued_or_grading": 1,
                "time_remaining_seconds": 45.0, "total_budget_seconds": 3600.0,
                "best": {"validation_score": 0.9, "grader_output_tail": "private"},
            }},
        }).encode(), session_id="session-2"),
    ]

    def fake_urlopen(_request, timeout):
        return responses.pop(0)

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    assert client.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["result"] == {
        "submissions": 2, "queued_or_grading": 1,
        "time_remaining_seconds": 45.0, "total_budget_seconds": 3600.0,
    }
    assert "grader_output_tail" not in output
    assert "best" not in payload["result"]


def test_mcp_client_discovers_public_contract_without_calling_a_tool(
    tmp_path, monkeypatch, capsys,
):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"tool": "discover_contract", "arguments": {}}))
    responses = [
        _Response(json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"protocolVersion": "2024-11-05",
                       "serverInfo": {"name": "are", "version": "2.14.7"}},
        }).encode(), session_id="session-contract"),
        _Response(b"", session_id="session-contract"),
        _Response(json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [
                {"name": "submit", "description": "public submit", "inputSchema": {"type": "object"},
                 "outputSchema": {"type": "object"}, "private": "drop"},
                {"name": "result", "description": "public result", "inputSchema": {},
                 "outputSchema": {}},
            ]},
        }).encode(), session_id="session-contract"),
    ]
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return responses.pop(0)

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    assert client.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["server_info"] == {"name": "are", "version": "2.14.7"}
    assert payload["protocol_version"] == "2024-11-05"
    assert [item["name"] for item in payload["result"]["tools"]] == ["submit", "result"]
    assert "private" not in output
    assert [json.loads(item.data)["method"] for item in sent] == [
        "initialize", "notifications/initialized", "tools/list",
    ]


def test_mcp_contract_digest_rejects_changed_public_schema():
    from benchmarks.autoresearchexam.public_mcp_contract import (
        make_public_mcp_contract, validate_public_mcp_contract,
    )

    contract = make_public_mcp_contract(
        task_id="task", task_version="1.1.0", task_sha256="a" * 64,
        task_instruction_sha256="b" * 64, endpoint=client.ENDPOINT,
        protocol_version=client.PROTOCOL_VERSION,
        server_info={"name": "are", "version": "2.14.7"},
        discovered_tools=[
            {"name": "submit", "description": "submit", "inputSchema": {}, "outputSchema": {}},
            {"name": "result", "description": "result", "inputSchema": {}, "outputSchema": {}},
        ],
    )
    assert validate_public_mcp_contract(contract)["contract_sha256"] == contract["contract_sha256"]
    contract["discovered_tools"][0]["description"] = "changed"
    import pytest
    with pytest.raises(ValueError, match="digest or scope"):
        validate_public_mcp_contract(contract)


def test_mcp_discovery_recursively_drops_unbounded_schema_metadata():
    payload = client._tool_list_payload({"result": {"tools": [{
        "name": "submit", "description": "safe", "documentation": "x" * 9000,
        "inputSchema": {"type": "object", "description": "untrusted prose",
                        "properties": {"id": {"type": "integer", "default": 3,
                                                  "description": "drop me"}}},
        "outputSchema": {},
    }]}})
    assert payload["tools"][0]["inputSchema"] == {
        "type": "object", "properties": {"id": {"type": "integer"}},
    }
    encoded = json.dumps(payload)
    assert "untrusted prose" not in encoded and "drop me" not in encoded


def test_mcp_discovery_rejects_overdeep_schema():
    schema = {}
    current = schema
    for _ in range(11):
        current["items"] = {}
        current = current["items"]
    result = client._tool_list_payload({"result": {"tools": [{
        "name": "submit", "description": "", "inputSchema": schema, "outputSchema": {},
    }]}})
    assert result == {"transport_error": True, "transport_category": "malformed_response"}


def test_mcp_client_reports_safe_rpc_error_category(tmp_path, monkeypatch, capsys):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"tool": "result", "arguments": {"id": 1}}))
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    responses = [
        _Response(b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}',
                  session_id="session-3"),
        _Response(b"", session_id="session-3"),
        _Response(b'{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"private details"}}',
                  session_id="session-3"),
    ]
    monkeypatch.setattr(client, "urlopen", lambda *_args, **_kwargs: responses.pop(0))
    assert client.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["result"] == {
        "transport_error": True, "transport_category": "jsonrpc_error",
    }
    assert "private details" not in output


def test_mcp_client_classifies_http_error_without_body(tmp_path, monkeypatch, capsys):
    from urllib.error import HTTPError

    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"tool": "status", "arguments": {}}))
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    monkeypatch.setattr(client, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        HTTPError(client.ENDPOINT, 503, "grader private text", {}, None),
    ))
    assert client.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "transport_error": True, "transport_category": "http_server_error",
    }
    assert "grader private text" not in output
