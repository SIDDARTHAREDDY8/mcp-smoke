"""Erroring-tool fixture: protocol is fine, but one tool always fails at runtime."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import call_result, initialize_result, serve, tool


def handle_call(params):
    if params.get("name") == "flaky":
        return {
            "content": [{"type": "text", "text": "boom: bad input"}],
            "isError": True,
        }
    return call_result("fine")


serve(
    {
        "initialize": lambda p: initialize_result(name="erroring-server"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {
            "tools": [
                tool("fine", "Always works.", {"type": "object"}),
                tool("flaky", "Always errors.", {"type": "object"}),
            ]
        },
        "tools/call": handle_call,
    }
)
