"""End-to-end: run the check suite against deliberately broken fixture servers."""

from typing import Any

import pytest
from conftest import ScriptedTransport, fixture_cmd, make_opts

from mcp_smoke.checks import run_suite
from mcp_smoke.cli import run as cli_run
from mcp_smoke.report import SEVERITY_FAIL, SEVERITY_PASS, SEVERITY_WARN
from mcp_smoke.transports import StdioTransport, TransportError, _RpcError


def run_fixture(name, **kw):
    """Start the fixture server, run the suite, return (report, transport)."""
    cmd = fixture_cmd(name)
    transport = StdioTransport(cmd)
    transport.start()
    try:
        return run_suite(transport, make_opts(cmd=cmd, **kw)), transport
    finally:
        transport.close()


def sev(report, check):
    return {f.title: f.severity for f in report.findings if f.check == check}


def test_healthy_server_passes():
    report, _ = run_fixture("healthy.py")
    assert not report.has_failures(), report.to_text(verbose=True)
    counts = report.counts()
    assert counts[SEVERITY_FAIL] == 0
    # both tools were smoke-called and passed
    calls = [f for f in report.findings if f.check == "tool_calls"]
    assert len(calls) == 2
    assert all(f.severity == SEVERITY_PASS for f in calls)


def test_polluting_server_fails_stdout_clean():
    report, _ = run_fixture("polluting.py")
    assert report.has_failures()
    fails = [
        f
        for f in report.findings
        if f.check == "stdout_clean" and f.severity == SEVERITY_FAIL
    ]
    assert fails, report.to_text(verbose=True)
    assert "Starting fixture server" in fails[0].detail


def test_crashing_server_fails_startup():
    cmd = fixture_cmd("crashing.py")
    report, code = cli_run(make_opts(cmd=cmd))
    assert code == 1  # check failure, not infra
    assert report.has_failures()
    assert any(
        f.check == "startup" and f.severity == SEVERITY_FAIL for f in report.findings
    )


def test_server_exiting_immediately_is_a_check_failure():
    cmd = fixture_cmd("exits_immediately.py")
    report, code = cli_run(make_opts(cmd=cmd))
    assert code == 1
    assert any(
        f.check == "startup" and "failed to start" in f.title for f in report.findings
    )


def test_hanging_server_fails_initialize():
    report, _ = run_fixture("hanging.py", timeout=1.0, call_timeout=1.0)
    assert report.has_failures()
    assert any(
        f.check == "initialize" and f.severity == SEVERITY_FAIL for f in report.findings
    )


def test_server_dying_mid_call_aborts_cleanly():
    # Regression test: this used to raise NameError ('elapsed' referenced
    # before assignment) instead of producing a finding.
    report, transport = run_fixture("dies_mid_call.py", timeout=5.0, call_timeout=5.0)
    assert report.has_failures()
    titles = " ".join(f.title for f in report.findings if f.check == "tool_calls")
    assert "died" in titles
    assert any("aborting" in f.title for f in report.findings)
    # the child was reaped, not left as a zombie
    assert transport.proc is not None and transport.proc.poll() is not None


def test_malformed_error_shape_is_a_finding_not_a_crash():
    report, _ = run_fixture("malformed.py")
    assert report.has_failures()
    fails = [
        f
        for f in report.findings
        if f.check == "tool_calls" and f.severity == SEVERITY_FAIL
    ]
    assert fails, report.to_text(verbose=True)
    assert "protocol error" in fails[0].title


def test_slow_tool_hits_call_timeout():
    report, transport = run_fixture("slow_tool.py", timeout=5.0, call_timeout=1.0)
    assert report.has_failures()
    fails = [
        f
        for f in report.findings
        if f.check == "tool_calls" and f.severity == SEVERITY_FAIL
    ]
    assert fails, report.to_text(verbose=True)
    assert any("timed out" in f.detail for f in fails), report.to_text(verbose=True)
    assert transport.proc is not None and transport.proc.poll() is not None


def test_huge_stdout_is_truncated_not_fatal():
    report, transport = run_fixture("huge_stdout.py", no_call=True)
    assert report.has_failures()  # the giant line is pollution
    fails = [
        f
        for f in report.findings
        if f.check == "stdout_clean" and f.severity == SEVERITY_FAIL
    ]
    assert fails, report.to_text(verbose=True)
    # one truncated entry, not a 5 MB string in memory
    assert len(transport.pollution) == 1
    assert len(transport.pollution[0]) < 70 * 1024
    # stderr storage stayed bounded
    assert len(transport.stderr_text.splitlines()) <= 200
    assert transport.stderr_dropped > 0


def test_server_quitting_mid_suite_warns_on_shutdown():
    report, _ = run_fixture("quits_after_tools_list.py", no_call=True)
    assert not report.has_failures(), report.to_text(verbose=True)
    assert any(
        f.check == "shutdown" and f.severity == SEVERITY_WARN for f in report.findings
    ), report.to_text(verbose=True)


def test_badschema_server_flags_schema_problems():
    report, _ = run_fixture("badschema.py", no_call=True)
    assert report.has_failures()
    titles = " ".join(f.title for f in report.findings)
    assert "duplicate tool name 'dup'" in titles
    assert "required property 'b' not defined" in titles
    warns = [f for f in report.findings if f.severity == SEVERITY_WARN]
    assert any("empty inputSchema" in f.title for f in warns), report.to_text(
        verbose=True
    )
    assert any("no description" in f.title for f in warns)


def test_erroring_tool_is_warn_not_fail():
    report, _ = run_fixture("erroring_tool.py")
    assert not report.has_failures(), report.to_text(verbose=True)
    warns = [
        f
        for f in report.findings
        if f.check == "tool_calls" and f.severity == SEVERITY_WARN
    ]
    assert len(warns) == 1 and "'flaky'" in warns[0].title


def test_no_call_skips_execution():
    report, _ = run_fixture("healthy.py", no_call=True)
    assert not report.has_failures()
    assert any(
        f.check == "tool_calls" and "skipped" in f.title for f in report.findings
    )


def test_include_exclude_filters():
    report, _ = run_fixture("healthy.py", include="echo")
    calls = [f for f in report.findings if f.check == "tool_calls"]
    assert len(calls) == 1 and "'echo'" in calls[0].title
    report, _ = run_fixture("healthy.py", exclude="echo,add")
    assert any(
        f.check == "tool_calls" and "no tools selected" in f.title
        for f in report.findings
    )


def _healthy_script():
    return {
        "initialize": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "scripted", "version": "0"},
        },
        "tools/list": {"tools": []},
        "resources/list": {"resources": []},
        "prompts/list": {"prompts": []},
    }


def test_unexpected_transport_error_becomes_internal_finding():
    # A non-TransportError from the transport layer must not escape as a
    # traceback; it becomes an 'internal' finding (exit 2 via cli.run).
    script = _healthy_script()
    script["tools/list"] = RuntimeError("simulated client bug")
    report = run_suite(ScriptedTransport(script), make_opts(cmd="dummy"))
    assert any(f.check == "internal" for f in report.findings)
    assert report.has_internal_error()


def test_broken_resources_probe_is_warn_not_fatal():
    script = _healthy_script()
    script["resources/list"] = RuntimeError("simulated probe bug")
    report = run_suite(ScriptedTransport(script), make_opts(cmd="dummy"))
    assert not report.has_internal_error()
    assert any(
        f.check == "resources" and f.severity == SEVERITY_WARN for f in report.findings
    ), report.to_text(verbose=True)


def test_internal_finding_maps_to_exit_2(monkeypatch):
    import mcp_smoke.cli as cli_module

    script = _healthy_script()
    script["initialize"] = RuntimeError("simulated bug")

    class FakeStdio(ScriptedTransport):
        def __init__(self, cmd: str, **kwargs: Any) -> None:
            super().__init__(script)

        def start(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "StdioTransport", FakeStdio)
    report, code = cli_run(make_opts(cmd="dummy"))
    assert code == 2
    assert report.has_internal_error()


def test_client_uses_handshake_timeout_for_session_setup():
    from mcp_smoke.client import McpClient

    script = {
        "initialize": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "t", "version": "1"},
            "capabilities": {},
        },
        "tools/list": {"tools": []},
        "ping": {},
    }
    t = ScriptedTransport(script)
    client = McpClient(t, request_timeout=9.0, handshake_timeout=3.0)
    client.initialize()
    client.list_tools()
    client.ping()
    assert t.timeouts["initialize"] == 3.0
    assert t.timeouts["tools/list"] == 3.0
    assert t.timeouts["ping"] == 9.0


def test_client_handshake_timeout_defaults_to_request_timeout():
    from mcp_smoke.client import McpClient

    script = {
        "initialize": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "t", "version": "1"},
            "capabilities": {},
        },
    }
    t = ScriptedTransport(script)
    client = McpClient(t, request_timeout=9.0)
    client.initialize()
    assert t.timeouts["initialize"] == 9.0


def _init_result(**overrides):
    result = {
        "protocolVersion": "2025-06-18",
        "serverInfo": {"name": "t", "version": "1"},
        "capabilities": {},
    }
    result.update(overrides)
    return result


def test_initialize_rejects_non_string_protocol_version():
    from mcp_smoke.client import McpClient
    from mcp_smoke.transports import TransportError

    for bad in (None, 123, ["2025-06-18"], {"v": 1}):
        script = {"initialize": _init_result(protocolVersion=bad)}
        with pytest.raises(TransportError, match="protocolVersion"):
            McpClient(ScriptedTransport(script)).initialize()


def test_initialize_rejects_malformed_server_info_and_capabilities():
    from mcp_smoke.client import McpClient
    from mcp_smoke.transports import TransportError

    with pytest.raises(TransportError, match="serverInfo"):
        McpClient(
            ScriptedTransport({"initialize": _init_result(serverInfo="nope")})
        ).initialize()
    with pytest.raises(TransportError, match="capabilities"):
        McpClient(
            ScriptedTransport({"initialize": _init_result(capabilities=["tools"])})
        ).initialize()


def test_initialize_tolerates_missing_server_info_and_capabilities():
    from mcp_smoke.client import McpClient

    result = {"protocolVersion": "2025-06-18"}
    client = McpClient(ScriptedTransport({"initialize": result}))
    assert client.initialize() == result
    assert client.server_info == {}
    assert client.capabilities == {}


def test_malformed_handshake_is_initialize_failure_not_internal():
    report = run_suite(
        ScriptedTransport({"initialize": _init_result(protocolVersion=42)}),
        make_opts(cmd="dummy"),
    )
    fails = [
        f for f in report.findings if f.check == "initialize" and f.severity == "fail"
    ]
    assert fails, report.to_text(verbose=True)
    assert "protocolVersion" in fails[0].detail
    assert not report.has_internal_error()


def _ok_handshake():
    return {
        "initialize": _init_result(),
        "tools/list": {"tools": []},
        "ping": {},
    }


def test_resources_list_malformed_shapes_are_warnings_not_internal():
    cases = [
        ({"resources/list": ["not", "a", "dict"]}, "resources"),
        ({"resources/list": {"resources": "nope"}}, "resources"),
        ({"prompts/list": 42}, "prompts"),
        ({"prompts/list": {"prompts": {"a": 1}}}, "prompts"),
    ]
    for extra, check in cases:
        script = dict(_ok_handshake(), **extra)
        report = run_suite(ScriptedTransport(script), make_opts(cmd="dummy"))
        warns = [
            f for f in report.findings if f.check == check and f.severity == "warn"
        ]
        assert len(warns) == 1, report.to_text(verbose=True)
        assert "malformed shape" in warns[0].title
        assert not report.has_internal_error(), report.to_text(verbose=True)


def test_resources_list_well_formed_reports_counts():
    script = dict(
        _ok_handshake(),
        **{
            "resources/list": {"resources": [{"uri": "x"}, {"uri": "y"}]},
            "prompts/list": {"prompts": []},
        },
    )
    report = run_suite(ScriptedTransport(script), make_opts(cmd="dummy"))
    by_check = {
        f.check: f for f in report.findings if f.check in ("resources", "prompts")
    }
    assert "2 resources advertised" in by_check["resources"].title
    assert "0 prompts advertised" in by_check["prompts"].title


def test_short_repr_truncates_giant_values():
    from mcp_smoke.client import _short_repr

    assert "[truncated" in _short_repr("x" * 500)
    assert _short_repr("short") == "'short'"


def test_initialize_rpc_error_and_non_dict():
    from mcp_smoke.client import McpClient
    from mcp_smoke.transports import TransportError, _RpcError

    with pytest.raises(TransportError, match="initialize failed"):
        McpClient(
            ScriptedTransport({"initialize": _RpcError(-32600, "bad")})
        ).initialize()
    with pytest.raises(TransportError, match="non-object"):
        McpClient(ScriptedTransport({"initialize": ["nope"]})).initialize()


# --- Failure-mode branch coverage -------------------------------------------


def _suite_with(extra: dict, **opts_kw):
    script = _healthy_script()
    script.update(extra)
    return run_suite(ScriptedTransport(script), make_opts(cmd="dummy", **opts_kw))


def _tool_script(**call_overrides):
    script = _healthy_script()
    script["tools/list"] = {
        "tools": [
            {
                "name": "t",
                "description": "test tool",
                "inputSchema": {"type": "object"},
            }
        ]
    }
    script.update(call_overrides)
    return script


def test_startup_dead_transport_aborts_suite():
    report = run_suite(
        ScriptedTransport(_healthy_script(), alive=False), make_opts(cmd="dummy")
    )
    fails = [f for f in report.findings if f.check == "startup"]
    assert len(fails) == 1 and fails[0].severity == "fail"
    assert "died before handshake" in fails[0].title
    assert not any(f.check == "initialize" for f in report.findings)


def test_old_protocol_version_warns():
    script = _healthy_script()
    script["initialize"] = _init_result(protocolVersion="2024-10-07")
    report = run_suite(ScriptedTransport(script), make_opts(cmd="dummy"))
    warns = [
        f for f in report.findings if f.check == "initialize" and f.severity == "warn"
    ]
    assert len(warns) == 1 and "old protocol version" in warns[0].title


def test_tools_list_transport_error_aborts():
    report = _suite_with({"tools/list": TransportError("boom")})
    assert any(
        f.check == "tools_list" and f.severity == "fail" for f in report.findings
    )
    assert not any(f.check == "tool_calls" for f in report.findings)


def test_tools_list_rpc_error_aborts():
    report = _suite_with({"tools/list": _RpcError(-32601, "Method not found")})
    fails = [
        f for f in report.findings if f.check == "tools_list" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "Method not found" in fails[0].title
    assert not any(f.check == "tool_calls" for f in report.findings)


def test_tools_list_non_array_aborts():
    report = _suite_with({"tools/list": {"tools": "nope"}})
    fails = [
        f for f in report.findings if f.check == "tools_list" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "not an array" in fails[0].title


def test_tools_list_bad_and_duplicate_names():
    report = _suite_with(
        {
            "tools/list": {
                "tools": [
                    {"description": "no name"},
                    "not-a-dict",
                    {"name": "dup", "description": "d"},
                    {"name": "dup", "description": "d"},
                ]
            }
        }
    )
    titles = [
        f.title
        for f in report.findings
        if f.check == "tools_list" and f.severity == "fail"
    ]
    assert sum("missing/non-string name" in t for t in titles) == 2
    assert any("duplicate tool name" in t for t in titles)


def test_tool_call_unexpected_exception_is_fail_not_internal():
    report = run_suite(
        ScriptedTransport(
            _tool_script(**{"tools/call": RuntimeError("weird")}),
        ),
        make_opts(cmd="dummy"),
    )
    fails = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "mcp-smoke error during call" in fails[0].title
    assert not report.has_internal_error()


def test_tool_call_rpc_error():
    report = run_suite(
        ScriptedTransport(
            _tool_script(**{"tools/call": _RpcError(-32602, "bad params")})
        ),
        make_opts(cmd="dummy"),
    )
    fails = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "protocol error" in fails[0].title


def test_tool_call_malformed_result():
    report = run_suite(
        ScriptedTransport(_tool_script(**{"tools/call": {"nope": True}})),
        make_opts(cmd="dummy"),
    )
    fails = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "malformed result" in fails[0].title


def test_tool_call_is_error_warns():
    report = run_suite(
        ScriptedTransport(
            _tool_script(
                **{
                    "tools/call": {
                        "content": [{"type": "text", "text": "bad"}],
                        "isError": True,
                    }
                }
            )
        ),
        make_opts(cmd="dummy"),
    )
    warns = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "warn"
    ]
    assert len(warns) == 1 and "returned isError" in warns[0].title


def test_resources_list_rpc_error_warns():
    report = _suite_with({"resources/list": _RpcError(-32000, "boom")})
    warns = [
        f for f in report.findings if f.check == "resources" and f.severity == "warn"
    ]
    assert len(warns) == 1 and "[-32000]" in warns[0].title


def test_late_pollution_fails_stdout_clean():
    class LatePolluted(ScriptedTransport):
        def __init__(self, script):
            super().__init__(script)
            self._requests = 0

        def request(self, method, params=None, timeout=None):
            self._requests += 1
            return super().request(method, params, timeout)

        @property
        def pollution(self):
            return ["late log line"] if self._requests >= 2 else []

    report = run_suite(LatePolluted(_healthy_script()), make_opts(cmd="dummy"))
    fails = [
        f for f in report.findings if f.check == "stdout_clean" and f.severity == "fail"
    ]
    assert len(fails) == 1 and "late non-JSON-RPC" in fails[0].title
