"""Slow tool: tools/call sleeps 30s (tests per-call timeout enforcement)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import call_result, initialize_result, serve, tool


def handle_call(params):
    time.sleep(30)
    return call_result("too late")


serve(
    {
        "initialize": lambda p: initialize_result(name="slow-tool"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {
            "tools": [tool("slow", "Takes forever.", {"type": "object"})]
        },
        "tools/call": handle_call,
    }
)
