import io
import json

from agent.validation_bridge import serve


def test_mcp_narrow_protocol_rejects_model_authored_arguments(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "agent.validation_bridge.request_validation",
        lambda path: called.append(path) or {"status": "not_run"},
    )
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "validate",
                "arguments": {"argv": ["sh", "-c", "anything"]},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "validate", "arguments": {}},
        },
    ]
    output = io.StringIO()
    serve(
        tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), output
    )
    replies = [json.loads(r) for r in output.getvalue().splitlines()]
    assert (
        replies[1]["result"]["tools"][0]["inputSchema"]["additionalProperties"] is False
    )
    assert replies[2]["error"]["code"] == -32602
    assert len(called) == 1
    assert json.loads(replies[3]["result"]["content"][0]["text"])["status"] == "not_run"
