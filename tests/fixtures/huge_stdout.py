"""Huge output: one 5 MB stdout line plus 5000 stderr lines, then healthy.

Exercises the defensive caps: the giant line must become one truncated
pollution entry (not a MemoryError), and stderr storage must stay bounded.
"""

import os
import sys

sys.stdout.write("X" * (5 * 1024 * 1024) + "\n")
sys.stdout.flush()
for i in range(5000):
    print(f"stderr log line {i}", file=sys.stderr)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import call_result, initialize_result, serve, tool

serve(
    {
        "initialize": lambda p: initialize_result(name="huge-stdout"),
        "notifications/initialized": lambda p: None,
        "tools/list": lambda p: {"tools": [tool("ok", "Fine.", {"type": "object"})]},
        "tools/call": lambda p: call_result("ok"),
    }
)
