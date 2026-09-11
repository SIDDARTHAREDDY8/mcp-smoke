"""Healthy fixture server: correct handshake, schemas, and tool behavior."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import serve, initialize_result, tool, call_result  # noqa: E402


def handle_call(params):
    name = params.get("name")
    args = params.get("arguments", {})
    if name == "echo":
        return call_result(f"echo: {args.get('message', '')}")
    if name == "add":
        return call_result(str(args.get("a", 0) + args.get("b", 0)))
    return {"content": [{"type": "text", "text": "unknown tool"}],
            "isError": True}


serve({
    "initialize": lambda p: initialize_result(),
    "notifications/initialized": lambda p: None,
    "ping": lambda p: {},
    "tools/list": lambda p: {"tools": [
        tool("echo", "Echoes a message back.",
             {"type": "object",
              "properties": {"message": {"type": "string"}},
              "required": ["message"]}),
        tool("add", "Adds two numbers.",
             {"type": "object",
              "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
              "required": ["a", "b"]}),
    ]},
    "tools/call": handle_call,
})
