"""Polluting fixture: prints a log line to STDOUT before speaking JSON-RPC.

This is the #1 silent MCP failure: the client sees garbled JSON and tool calls
fail with parse errors, while the server thinks everything is fine.
"""

import os
import sys

print("Starting fixture server v0.0.1...", flush=True)  # the bug under test

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import call_result, initialize_result, serve, tool

serve(
    {
        "initialize": lambda p: initialize_result(name="polluting-server"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {
            "tools": [
                tool(
                    "echo",
                    "Echoes.",
                    {"type": "object", "properties": {"message": {"type": "string"}}},
                ),
            ]
        },
        "tools/call": lambda p: call_result("ok"),
    }
)
