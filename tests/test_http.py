"""Verify the Streamable HTTP transport against a tiny fixture HTTP server."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_smoke.client import McpClient  # noqa: E402
from mcp_smoke.transports import HttpTransport, TransportError  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        method = req.get("method")
        req_id = req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "http-fixture", "version": "0.0.1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "ping_tool",
                                 "description": "Replies pong.",
                                 "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "pong"}]}
        elif method == "ping":
            result = {}
        else:
            result = None
        if req_id is None:
            self.send_response(202)
            self.end_headers()
            return
        body = {"jsonrpc": "2.0", "id": req_id}
        if result is None:
            body["error"] = {"code": -32601, "message": "Method not found"}
        else:
            body["result"] = result
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()


def test_http_initialize_and_tools(http_server):
    client = McpClient(HttpTransport(http_server), request_timeout=5.0)
    init = client.initialize()
    assert init["serverInfo"]["name"] == "http-fixture"
    tools = client.list_tools()
    assert tools["tools"][0]["name"] == "ping_tool"
    result = client.call_tool("ping_tool", {})
    assert result["content"][0]["text"] == "pong"


def test_http_method_not_found_surfaces():
    client = McpClient(HttpTransport("http://127.0.0.1:1/mcp"),
                       request_timeout=2.0)
    with pytest.raises(TransportError):
        client.ping()
