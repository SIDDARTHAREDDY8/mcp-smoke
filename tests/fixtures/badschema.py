"""Bad-schema fixture: duplicate names, empty schema, dangling required."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import serve, initialize_result, tool, call_result  # noqa: E402

serve({
    "initialize": lambda p: initialize_result(name="badschema-server"),
    "notifications/initialized": lambda p: None,
    "tools/list": lambda p: {"tools": [
        # duplicate name + missing description
        {"name": "dup", "inputSchema": {"type": "object",
                                       "properties": {"x": {"type": "string"}}}},
        {"name": "dup", "description": "second",
         "inputSchema": {"type": "object"}},
        # the empty-schema bug class: clients send no arguments, silently
        tool("vague", "Does something.", {"type": "object"}),
        # required references a property that does not exist
        tool("broken", "Broken.", {"type": "object",
                                   "properties": {"a": {"type": "string"}},
                                   "required": ["b"]}),
    ]},
    "tools/call": lambda p: call_result("ok"),
})
