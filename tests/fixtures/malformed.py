"""Malformed protocol fixture: speaks JSON-RPC but with wrong shapes.

- initialize: healthy
- tools/list: one tool
- tools/call: responds with a *string* error instead of an error object
  (must surface as a protocol error, not an AttributeError)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import initialize_result, tool


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    req = json.loads(line)
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": initialize_result(name="malformed"),
            }
        )
    elif method == "tools/list":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [tool("weird", "Weird tool.", {"type": "object"})]},
            }
        )
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": req_id, "error": "boom (not an object)"})
    elif req_id is not None:
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
