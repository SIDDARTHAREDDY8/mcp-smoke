"""Ignores SIGTERM: close() must escalate to SIGKILL and still reap."""

import os
import signal
import sys

signal.signal(signal.SIGTERM, signal.SIG_IGN)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import call_result, initialize_result, serve, tool

serve(
    {
        "initialize": lambda p: initialize_result(name="ignores-sigterm"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {"tools": [tool("ok", "Fine.", {"type": "object"})]},
        "tools/call": lambda p: call_result("ok"),
    }
)
