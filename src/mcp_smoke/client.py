"""Thin MCP client over a transport: handshake + capability probes."""
from __future__ import annotations

from mcp_smoke.transports import TransportError, _RpcError

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcp-smoke", "version": "0.1.0"}


class McpClient:
    def __init__(self, transport, request_timeout=10.0):
        self.t = transport
        self.request_timeout = request_timeout
        self.server_info = {}
        self.server_protocol_version = None
        self.capabilities = {}

    def initialize(self):
        result = self.t.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.request_timeout,
        )
        if isinstance(result, _RpcError):
            raise TransportError(
                f"initialize failed: [{result.code}] {result.message}"
            )
        if not isinstance(result, dict):
            raise TransportError(f"initialize returned non-object: {result!r}")
        self.server_protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.t.notify("notifications/initialized")
        return result

    def ping(self):
        return self.t.request("ping", timeout=self.request_timeout)

    def list_tools(self):
        return self.t.request("tools/list", timeout=self.request_timeout)

    def call_tool(self, name, arguments=None, timeout=15.0):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.t.request("tools/call", params, timeout=timeout)

    def list_resources(self):
        return self.t.request("resources/list", timeout=self.request_timeout)

    def list_prompts(self):
        return self.t.request("prompts/list", timeout=self.request_timeout)
