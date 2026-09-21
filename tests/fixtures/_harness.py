"""Minimal raw JSON-RPC stdio MCP server harness for fixtures (stdlib only)."""

import json
import sys


def serve(handlers):
    stdin = sys.stdin
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})
        handler = handlers.get(method)
        if handler is None:
            if req_id is not None:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                )
            continue
        try:
            result = handler(params)
        except Exception as exc:  # noqa: BLE001 - fixture must stay alive
            if req_id is not None:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
            continue
        if req_id is not None:  # notifications get no response
            _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def initialize_result(name="fixture-server", version="0.0.1", protocol="2025-06-18"):
    return {
        "protocolVersion": protocol,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": name, "version": version},
    }


def tool(name, description, schema):
    return {"name": name, "description": description, "inputSchema": schema}


def call_result(text):
    return {"content": [{"type": "text", "text": text}]}
