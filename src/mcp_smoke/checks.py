"""The check suite: run every check against a connected client.

Every failure mode -- server crashes, hangs, malformed protocol, runaway
output -- becomes a finding with severity fail/warn. Nothing here ever lets
an exception escape: an unexpected error becomes an ``internal`` finding
(exit code 2) instead of a traceback.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from mcp_smoke.client import McpClient
from mcp_smoke.report import (
    SEVERITY_FAIL,
    SEVERITY_PASS,
    SEVERITY_WARN,
    Finding,
    Report,
)
from mcp_smoke.schemautils import synth_args, validate_schema
from mcp_smoke.transports import Transport, TransportError, _RpcError

if TYPE_CHECKING:
    from mcp_smoke.cli import Options

_DETAIL_LIMIT = 4000  # cap server-controlled text embedded in finding details
_OLD_PROTOCOL = "2024-11-05"  # warn when the server speaks anything older
_METHOD_NOT_FOUND = -32601  # JSON-RPC "Method not found" error code


def _clip(text: Any, limit: int = _DETAIL_LIMIT) -> str:
    """Truncate long text for finding details, noting the truncation."""
    text = str(text)
    if len(text) > limit:
        return text[:limit] + f" ...[truncated, {len(text) - limit} more chars]"
    return text


def _pass(check: str, title: str, detail: str = "") -> Finding:
    """Build a passing finding."""
    return Finding(check, SEVERITY_PASS, title, detail)


def _warn(check: str, title: str, detail: str = "") -> Finding:
    """Build a warning finding."""
    return Finding(check, SEVERITY_WARN, title, detail)


def _fail(check: str, title: str, detail: str = "") -> Finding:
    """Build a failing finding."""
    return Finding(check, SEVERITY_FAIL, title, detail)


def _is_method_not_found(err: Any) -> bool:
    """Return True when a JSON-RPC error means 'method not implemented'."""
    return isinstance(err, _RpcError) and err.code == _METHOD_NOT_FOUND


def run_suite(transport: Transport, opts: Options) -> Report:
    """Connect, run all checks, return a Report. Transport must be started.

    Never raises: an unexpected error becomes an ``internal`` finding so the
    CLI exits 2 with a machine-parseable report instead of a traceback.
    """
    findings: list[Finding] = []
    try:
        _run_all_checks(transport, opts, findings)
    except Exception as exc:  # noqa: BLE001 -- last-resort guard, see docstring
        findings.append(
            _fail(
                "internal",
                "mcp-smoke hit an internal error; the server was not fully tested",
                f"{type(exc).__name__}: {exc}",
            )
        )
    return Report(opts.describe(), findings)


def _run_all_checks(
    transport: Transport, opts: Options, findings: list[Finding]
) -> None:
    client = McpClient(
        transport,
        request_timeout=opts.effective(opts.request_timeout),
        handshake_timeout=opts.effective(opts.handshake_timeout),
    )

    if not _check_startup(transport, findings):
        return
    if not _check_initialize(client, findings):
        findings.append(
            _fail("stdout_clean", "skipped (no session)", "see initialize failure")
        )
        return
    initial_pollution = _check_stdout_clean(transport, findings)
    tools = _check_tools_list(client, findings)
    if tools is None:
        return
    _check_tool_calls(client, transport, opts, tools, findings)
    _check_resources_prompts(client, findings)
    _check_shutdown(transport, findings)
    _check_late_pollution(transport, findings, initial_pollution)


def _check_startup(transport: Transport, findings: list[Finding]) -> bool:
    """Check the server process survived startup. Return False to abort."""
    if not transport.is_alive():
        findings.append(
            _fail(
                "startup",
                "server process died before handshake",
                f"exit code {transport.exit_code()}",
            )
        )
        return False
    findings.append(_pass("startup", "server process started and stayed alive"))
    return True


def _check_initialize(client: McpClient, findings: list[Finding]) -> bool:
    """Run the MCP handshake. Return False to abort the suite."""
    try:
        client.initialize()
    except TransportError as exc:
        findings.append(_fail("initialize", "handshake failed", str(exc)))
        return False
    info = client.server_info
    findings.append(
        _pass(
            "initialize",
            f"handshake ok (server: {info.get('name', '?')} "
            f"{info.get('version', '')}, protocol "
            f"{client.server_protocol_version})",
        )
    )
    if (
        client.server_protocol_version
        and client.server_protocol_version < _OLD_PROTOCOL
    ):
        findings.append(
            _warn(
                "initialize",
                f"old protocol version {client.server_protocol_version}",
                "consider upgrading the server SDK",
            )
        )
    return True


def _check_stdout_clean(transport: Transport, findings: list[Finding]) -> list[str]:
    """Check stdout carried only JSON-RPC. Return the pollution lines seen."""
    pollution = transport.pollution
    total = len(pollution) + transport.pollution_dropped
    if total:
        sample = "\n".join(pollution[:5])
        extra = (
            f" (+{transport.pollution_dropped} more lines not stored)"
            if transport.pollution_dropped
            else ""
        )
        findings.append(
            _fail(
                "stdout_clean",
                f"{total} non-JSON-RPC line(s) on stdout{extra} "
                "-- protocol stream corrupted",
                f"first lines:\n{_clip(sample)}\n"
                "Fix: log to stderr, never stdout "
                "(console.log/print corrupts stdio JSON-RPC).",
            )
        )
    else:
        findings.append(_pass("stdout_clean", "stdout carried only JSON-RPC"))
    return pollution


def _check_tools_list(client: McpClient, findings: list[Finding]) -> list[Any] | None:
    """List tools and validate their schemas. Return None to abort."""
    try:
        tools_result = client.list_tools()
    except TransportError as exc:
        findings.append(_fail("tools_list", f"tools/list failed: {exc}"))
        return None
    if isinstance(tools_result, _RpcError):
        findings.append(
            _fail(
                "tools_list",
                f"tools/list returned error [{tools_result.code}]: "
                f"{tools_result.message}",
            )
        )
        return None
    tools = (tools_result or {}).get("tools", [])
    if not isinstance(tools, list):
        findings.append(_fail("tools_list", "tools/list 'tools' is not an array"))
        return None
    findings.append(_pass("tools_list", f"{len(tools)} tool(s) advertised"))

    seen: set[str] = set()
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not name or not isinstance(name, str):
            findings.append(
                _fail(
                    "tools_list",
                    "tool with missing/non-string name",
                    _clip(repr(tool), 200),
                )
            )
            continue
        if name in seen:
            findings.append(_fail("tools_list", f"duplicate tool name {name!r}"))
        seen.add(name)
        if not tool.get("description"):
            findings.append(
                _warn(
                    "tool_metadata",
                    f"tool {name!r} has no description",
                    "agents choose tools by description; missing ones get ignored",
                )
            )
        for sev, msg in validate_schema(tool.get("inputSchema", {"type": "object"})):
            findings.append(Finding("tool_schema", sev, f"tool {name!r}: {msg}"))
    return tools


def _check_tool_calls(
    client: McpClient,
    transport: Transport,
    opts: Options,
    tools: list[Any],
    findings: list[Finding],
) -> None:
    """Smoke-call each selected tool with synthetic arguments."""
    if opts.no_call:
        findings.append(_pass("tool_calls", "skipped (--no-call)"))
        return
    selected = [
        t
        for t in tools
        if isinstance(t, dict)
        and t.get("name")
        and (not opts.include or t["name"] in opts.include)
        and t["name"] not in opts.exclude
    ]
    if not selected:
        findings.append(_pass("tool_calls", "no tools selected for smoke calls"))
        return
    for tool in selected:
        if not _call_one_tool(client, transport, opts, tool, findings):
            break  # server died; nothing more to call


def _call_one_tool(
    client: McpClient,
    transport: Transport,
    opts: Options,
    tool: dict[str, Any],
    findings: list[Finding],
) -> bool:
    """Smoke-call one tool. Returns False when the server died (abort)."""
    name = tool["name"]
    args = synth_args(tool.get("inputSchema", {}))
    started = time.monotonic()
    try:
        result = client.call_tool(name, args, timeout=opts.effective(opts.call_timeout))
    except TransportError as exc:
        elapsed = time.monotonic() - started
        findings.append(
            _fail(
                "tool_calls",
                f"tool {name!r}: call failed at transport level ({elapsed:.1f}s)",
                f"{exc}\nargs sent: {args}",
            )
        )
        if not transport.is_alive():
            findings.append(
                _fail(
                    "tool_calls",
                    "server died during tool calls -- aborting",
                    f"exit code {transport.exit_code()}",
                )
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 -- one bad tool must not kill the suite
        elapsed = time.monotonic() - started
        findings.append(
            _fail(
                "tool_calls",
                f"tool {name!r}: mcp-smoke error during call ({elapsed:.1f}s)",
                f"{type(exc).__name__}: {exc}\nargs sent: {args}",
            )
        )
        return True
    elapsed = time.monotonic() - started
    if isinstance(result, _RpcError):
        findings.append(
            _fail(
                "tool_calls",
                f"tool {name!r}: protocol error [{result.code}] {result.message}",
                f"args sent: {args}",
            )
        )
        return True
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        findings.append(
            _fail(
                "tool_calls",
                f"tool {name!r}: malformed result (missing 'content' array)",
                f"args sent: {_clip(args, 500)}; got: {_clip(result, 300)}",
            )
        )
        return True
    if result.get("isError"):
        text = _content_text(result)
        findings.append(
            _warn(
                "tool_calls",
                f"tool {name!r}: returned isError ({elapsed:.2f}s)",
                f"args sent: {args}\nnote: synthetic args may be invalid for this "
                f"tool; treat as a lead, not proof.\nerror text: {text}",
            )
        )
    else:
        findings.append(
            _pass(
                "tool_calls",
                f"tool {name!r}: ok ({elapsed:.2f}s, args {args})",
            )
        )
    return True


def _check_resources_prompts(client: McpClient, findings: list[Finding]) -> None:
    """Probe the optional resources/prompts capabilities (informational)."""
    for method, label in (("list_resources", "resources"), ("list_prompts", "prompts")):
        try:
            result = getattr(client, method)()
        except TransportError as exc:
            findings.append(_warn(label, f"{label}/list transport error", str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 -- informational probe, never fatal
            findings.append(
                _warn(label, f"{label}/list failed", f"{type(exc).__name__}: {exc}")
            )
            continue
        if _is_method_not_found(result):
            findings.append(_pass(label, f"server does not implement {label} (fine)"))
        elif isinstance(result, _RpcError):
            findings.append(
                _warn(label, f"{label}/list error [{result.code}]: {result.message}")
            )
        else:
            # A malformed shape here is a server bug, not our bug: report it
            # as a warning instead of letting AttributeError/TypeError escape
            # to the last-resort internal-error handler.
            items = result.get(label, []) if isinstance(result, dict) else None
            if not isinstance(items, list):
                findings.append(
                    _warn(
                        label,
                        f"{label}/list returned a malformed shape",
                        _clip(repr(result), 200),
                    )
                )
            else:
                findings.append(_pass(label, f"{len(items)} {label} advertised"))


def _check_shutdown(transport: Transport, findings: list[Finding]) -> None:
    """Report whether the server survived the whole suite."""
    if transport.is_alive():
        findings.append(_pass("shutdown", "server still alive after suite"))
    else:
        findings.append(
            _warn(
                "shutdown",
                "server exited during the suite",
                f"exit code {transport.exit_code()}",
            )
        )


def _check_late_pollution(
    transport: Transport, findings: list[Finding], initial_pollution: list[str]
) -> None:
    """Catch pollution that only appeared after the first response."""
    # Some servers only start logging after the first response.
    late = [p for p in transport.pollution if p not in initial_pollution]
    if late and not any(
        f.check == "stdout_clean" and f.severity == SEVERITY_FAIL for f in findings
    ):
        findings.append(
            _fail(
                "stdout_clean",
                f"{len(late)} late non-JSON-RPC line(s) on stdout",
                _clip("\n".join(late[:5])),
            )
        )


def _content_text(result: dict[str, Any], limit: int = 300) -> str:
    """Extract text content, truncating as we go so huge outputs stay small."""
    parts: list[str] = []
    total = 0
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text", ""))
            parts.append(text)
            total += len(text)
            if total >= limit:
                break
    return _clip("\n".join(parts), limit)
