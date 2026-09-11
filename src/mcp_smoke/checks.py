"""The check suite: run every check against a connected client."""
from __future__ import annotations

import time

from mcp_smoke.client import McpClient
from mcp_smoke.report import Finding, Report, SEVERITY_PASS, SEVERITY_WARN, SEVERITY_FAIL
from mcp_smoke.schemautils import validate_schema, synth_args
from mcp_smoke.transports import TransportError, _RpcError


def _pass(check, title, detail=""):
    return Finding(check, SEVERITY_PASS, title, detail)


def _warn(check, title, detail=""):
    return Finding(check, SEVERITY_WARN, title, detail)


def _fail(check, title, detail=""):
    return Finding(check, SEVERITY_FAIL, title, detail)


def _is_method_not_found(err):
    return isinstance(err, _RpcError) and err.code == -32601


def run_suite(transport, opts):
    """Connect, run all checks, return a Report. Transport must be started."""
    findings = []
    client = McpClient(transport, request_timeout=opts.timeout)
    target = opts.describe()

    # 1. startup ---------------------------------------------------------
    if not transport.is_alive():
        findings.append(_fail("startup", "server process died before handshake",
                              f"exit code {transport.exit_code()}"))
        return Report(target, findings)
    findings.append(_pass("startup", "server process started and stayed alive"))

    # 2. initialize -------------------------------------------------------
    try:
        init = client.initialize()
    except TransportError as exc:
        findings.append(_fail("initialize", "handshake failed", str(exc)))
        findings.append(_fail("stdout_clean", "skipped (no session)",
                              "see initialize failure"))
        return Report(target, findings)
    findings.append(_pass(
        "initialize",
        f"handshake ok (server: {client.server_info.get('name', '?')} "
        f"{client.server_info.get('version', '')}, protocol "
        f"{client.server_protocol_version})",
    ))
    if client.server_protocol_version and client.server_protocol_version < "2024-11-05":
        findings.append(_warn("initialize",
                              f"old protocol version {client.server_protocol_version}",
                              "consider upgrading the server SDK"))

    # 3. stdout pollution --------------------------------------------------
    pollution = transport.pollution
    if pollution:
        sample = "\n".join(pollution[:5])
        findings.append(_fail(
            "stdout_clean",
            f"{len(pollution)} non-JSON-RPC line(s) on stdout -- protocol stream corrupted",
            f"first lines:\n{sample}\n"
            "Fix: log to stderr, never stdout (console.log/print corrupts stdio JSON-RPC).",
        ))
    else:
        findings.append(_pass("stdout_clean", "stdout carried only JSON-RPC"))

    # 4. tools/list --------------------------------------------------------
    try:
        tools_result = client.list_tools()
    except TransportError as exc:
        findings.append(_fail("tools_list", f"tools/list failed: {exc}"))
        return Report(target, findings)
    if isinstance(tools_result, _RpcError):
        findings.append(_fail("tools_list",
                              f"tools/list returned error [{tools_result.code}]: "
                              f"{tools_result.message}"))
        return Report(target, findings)
    tools = (tools_result or {}).get("tools", [])
    if not isinstance(tools, list):
        findings.append(_fail("tools_list", "tools/list 'tools' is not an array"))
        return Report(target, findings)
    findings.append(_pass("tools_list", f"{len(tools)} tool(s) advertised"))

    seen = set()
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not name or not isinstance(name, str):
            findings.append(_fail("tools_list", "tool with missing/non-string name",
                                  repr(tool)[:200]))
            continue
        if name in seen:
            findings.append(_fail("tools_list", f"duplicate tool name {name!r}"))
        seen.add(name)
        if not tool.get("description"):
            findings.append(_warn("tool_metadata",
                                  f"tool {name!r} has no description",
                                  "agents choose tools by description; missing ones get ignored"))
        for sev, msg in validate_schema(tool.get("inputSchema", {"type": "object"})):
            findings.append(Finding("tool_schema", sev, f"tool {name!r}: {msg}"))

    # 5. smoke-call every tool ----------------------------------------------
    if opts.no_call:
        findings.append(_pass("tool_calls", "skipped (--no-call)"))
    else:
        selected = [
            t for t in tools
            if isinstance(t, dict) and t.get("name")
            and (not opts.include or t["name"] in opts.include)
            and t["name"] not in opts.exclude
        ]
        if not selected:
            findings.append(_pass("tool_calls", "no tools selected for smoke calls"))
        for tool in selected:
            name = tool["name"]
            args = synth_args(tool.get("inputSchema", {}))
            started = time.monotonic()
            try:
                result = client.call_tool(name, args, timeout=opts.call_timeout)
                elapsed = time.monotonic() - started
            except TransportError as exc:
                findings.append(_fail(
                    "tool_calls",
                    f"tool {name!r}: call failed at transport level ({elapsed:.1f}s)",
                    f"{exc}\nargs sent: {args}",
                ))
                if not transport.is_alive():
                    findings.append(_fail("tool_calls",
                                          "server died during tool calls -- aborting",
                                          f"exit code {transport.exit_code()}"))
                    break
                continue
            elapsed = time.monotonic() - started
            if isinstance(result, _RpcError):
                findings.append(_fail(
                    "tool_calls",
                    f"tool {name!r}: protocol error [{result.code}] {result.message}",
                    f"args sent: {args}",
                ))
                continue
            if not isinstance(result, dict) or not isinstance(result.get("content"), list):
                findings.append(_fail(
                    "tool_calls",
                    f"tool {name!r}: malformed result (missing 'content' array)",
                    f"args sent: {args}; got: {str(result)[:300]}",
                ))
                continue
            if result.get("isError"):
                text = _content_text(result)[:300]
                findings.append(_warn(
                    "tool_calls",
                    f"tool {name!r}: returned isError ({elapsed:.2f}s)",
                    f"args sent: {args}\nnote: synthetic args may be invalid for this "
                    f"tool; treat as a lead, not proof.\nerror text: {text}",
                ))
            else:
                findings.append(_pass(
                    "tool_calls",
                    f"tool {name!r}: ok ({elapsed:.2f}s, args {args})",
                ))

    # 6. resources / prompts (informational) ---------------------------------
    for method, label in (("list_resources", "resources"), ("list_prompts", "prompts")):
        try:
            result = getattr(client, method)()
        except TransportError as exc:
            findings.append(_warn(label, f"{label}/list transport error", str(exc)))
            continue
        if _is_method_not_found(result):
            findings.append(_pass(label, f"server does not implement {label} (fine)"))
        elif isinstance(result, _RpcError):
            findings.append(_warn(label,
                                  f"{label}/list error [{result.code}]: {result.message}"))
        else:
            items = (result or {}).get(label, [])
            findings.append(_pass(label, f"{len(items)} {label} advertised"))

    # 7. liveness at end ------------------------------------------------------
    if transport.is_alive():
        findings.append(_pass("shutdown", "server still alive after suite"))
    else:
        findings.append(_warn("shutdown",
                              "server exited during the suite",
                              f"exit code {transport.exit_code()}"))

    # 8. late pollution check (some servers log after first response) ----------
    late = [p for p in transport.pollution if p not in pollution]
    if late and not any(f.check == "stdout_clean" and f.severity == SEVERITY_FAIL
                        for f in findings):
        findings.append(_fail("stdout_clean",
                              f"{len(late)} late non-JSON-RPC line(s) on stdout",
                              "\n".join(late[:5])))

    return Report(target, findings)


def _content_text(result):
    parts = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)
