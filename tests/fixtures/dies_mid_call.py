"""Dies mid-call: healthy handshake, then the process exits during tools/call."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import initialize_result, serve, tool


def handle_call(params):
    os._exit(1)  # die without responding, like a segfaulting handler


serve(
    {
        "initialize": lambda p: initialize_result(name="dies-mid-call"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {
            "tools": [tool("doom", "Kills the server.", {"type": "object"})]
        },
        "tools/call": handle_call,
    }
)
