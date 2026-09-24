"""Narrow stdio MCP adapter: one fixed validation request, no shell or executor access.

The mounted job mailbox is NOT a container-engine socket. The external launcher owns
commands, images, timeouts and snapshots. This helper cannot grant those authorities.
"""

import json
import os
import sys
import time
from pathlib import Path

from agent.other_execution import read_scoped

TOOL = {
    "name": "validate",
    "description": "Run the maintainer-configured checks against the current patch. No arguments; commands and images cannot be changed. Later edits require validation again.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}


def request_validation(mailbox, seconds=600):
    mailbox = Path(mailbox)
    # One synchronous request per tool process. Exclusive creation prevents overlap.
    nonce = os.urandom(16).hex()
    path = mailbox / "request.json"
    if path.exists():
        return {"status": "unavailable", "reason": "Validation request already pending"}
    staging = mailbox / "request.tmp"
    with staging.open("x") as f:
        json.dump({"version": 1, "nonce": nonce}, f)
    os.replace(staging, path)
    started = time.monotonic()
    try:
        while time.monotonic() - started < seconds:
            if (mailbox / "response.json").exists():
                response = json.loads(read_scoped(mailbox, "response.json", 16384))
                if response.get("nonce") == nonce:
                    (mailbox / "response.json").unlink()
                    return response
            time.sleep(0.05)
        return {
            "status": "unavailable",
            "reason": "Validation launcher did not respond",
        }
    finally:
        path.unlink(missing_ok=True)


def serve(mailbox, input_stream=None, output_stream=None):
    inp = input_stream or sys.stdin
    out = output_stream or sys.stdout
    while True:
        raw = inp.readline(8193)
        if not raw:
            return
        if len(raw) > 8192:
            return
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise ValueError()
            if "id" not in msg:
                continue
            method = msg.get("method")
            params = msg.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "driftplain-validation", "version": "1.0.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif (
                method == "tools/call"
                and params.get("name") == "validate"
                and params.get("arguments", {}) == {}
            ):
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(request_validation(mailbox)),
                        }
                    ]
                }
            else:
                out.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {
                                "code": -32602,
                                "message": "Only validate with no arguments is supported",
                            },
                        }
                    )
                    + "\n"
                )
                out.flush()
                continue
            out.write(
                json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n"
            )
            out.flush()
        except (ValueError, TypeError, KeyError, OSError):
            return


if __name__ == "__main__":
    serve("/bridge")
