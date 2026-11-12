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
