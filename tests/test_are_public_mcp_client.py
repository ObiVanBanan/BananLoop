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


def test_mcp_client_restricts_tool_names(tmp_path, monkeypatch, capsys):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"tool": "status", "arguments": {}}))
    monkeypatch.setattr(client.sys, "argv", ["are_public_mcp_client.py", str(request_file)])
    assert client.main() == 0
    assert capsys.readouterr().out == '{"transport_error":true}\n'
