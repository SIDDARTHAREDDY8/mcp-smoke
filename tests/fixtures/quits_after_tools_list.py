"""Quits right after answering tools/list (tests the shutdown liveness check).

With --no-call the suite proceeds to the resources/prompts probes against a
dead server, then must warn that the server exited during the suite.
"""

import json
import os
import sys


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
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "quitter", "version": "0"},
                },
            }
        )
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": []}})
        sys.stdout.flush()
        os._exit(0)
    elif req_id is not None:
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
