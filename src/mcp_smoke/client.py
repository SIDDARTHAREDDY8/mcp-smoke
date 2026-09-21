"""Thin MCP client over a transport: handshake + capability probes."""

from __future__ import annotations

from typing import Any

from mcp_smoke import __version__
from mcp_smoke.transports import Transport, TransportError, _RpcError

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcp-smoke", "version": __version__}


def _short_repr(value: Any, limit: int = 200) -> str:
    """repr() clipped, so a malicious giant value cannot flood the report."""
    text = repr(value)
    if len(text) > limit:
        text = text[:limit] + f" ...[truncated, {len(text) - limit} more chars]"
    return text


class McpClient:
    """Thin MCP client: handshake plus capability probes over a transport."""

    def __init__(
        self,
        transport: Transport,
        request_timeout: float = 10.0,
        handshake_timeout: float | None = None,
    ) -> None:
        """Attach the client to a transport.

        handshake_timeout bounds initialize/tools-list (session setup);
        request_timeout bounds every other probe. When handshake_timeout is
        None it falls back to request_timeout.
        """
        self.t = transport
        self.request_timeout = request_timeout
        self.handshake_timeout = (
            handshake_timeout if handshake_timeout is not None else request_timeout
        )
        self.server_info: dict[str, Any] = {}
        self.server_protocol_version: str | None = None
        self.capabilities: dict[str, Any] = {}

    def initialize(self) -> dict[str, Any]:
        """Run the MCP handshake and record the server\u2019s advertised info.

        Raises TransportError when the InitializeResult is malformed:
        protocolVersion must be a string, and serverInfo / capabilities must
        be objects when present.
        """
        result = self.t.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.handshake_timeout,
        )
        if isinstance(result, _RpcError):
            raise TransportError(f"initialize failed: [{result.code}] {result.message}")
        if not isinstance(result, dict):
            raise TransportError(f"initialize returned non-object: {result!r}")
        version = result.get("protocolVersion")
        if not isinstance(version, str):
            raise TransportError(
                f"initialize returned invalid protocolVersion: {_short_repr(version)}"
            )
        server_info = result.get("serverInfo")
        if server_info is None:
            server_info = {}
        elif not isinstance(server_info, dict):
            raise TransportError(
                f"initialize returned invalid serverInfo: {_short_repr(server_info)}"
            )
        capabilities = result.get("capabilities")
        if capabilities is None:
            capabilities = {}
        elif not isinstance(capabilities, dict):
            raise TransportError(
                f"initialize returned invalid capabilities: {_short_repr(capabilities)}"
            )
        self.server_protocol_version = version
        self.server_info = server_info
        self.capabilities = capabilities
        self.t.notify("notifications/initialized", timeout=self.request_timeout)
        return result

    def ping(self) -> Any:
        """Send a ping probe."""
        return self.t.request("ping", timeout=self.request_timeout)

    def list_tools(self) -> Any:
        """Request the server\u2019s tool list."""
        return self.t.request("tools/list", timeout=self.handshake_timeout)

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 15.0
    ) -> Any:
        """Call one tool with the given arguments."""
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.t.request("tools/call", params, timeout=timeout)

    def list_resources(self) -> Any:
        """Request the server\u2019s resource list."""
        return self.t.request("resources/list", timeout=self.request_timeout)

    def list_prompts(self) -> Any:
        """Request the server\u2019s prompt list."""
        return self.t.request("prompts/list", timeout=self.request_timeout)
