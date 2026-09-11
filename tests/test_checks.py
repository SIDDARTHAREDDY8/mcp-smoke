"""End-to-end: run the check suite against deliberately broken fixture servers."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_smoke.checks import run_suite  # noqa: E402
from mcp_smoke.cli import run as cli_run  # noqa: E402
from mcp_smoke.report import SEVERITY_FAIL, SEVERITY_WARN, SEVERITY_PASS  # noqa: E402
from mcp_smoke.transports import StdioTransport  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class Opts:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.url = kw.get("url")
        self.cwd = kw.get("cwd")
        self.env = kw.get("env")
        self.headers = kw.get("headers", {})
        self.timeout = kw.get("timeout", 5.0)
        self.call_timeout = kw.get("call_timeout", 5.0)
        self.no_call = kw.get("no_call", False)
        self.include = set(kw.get("include", ()))
        self.exclude = set(kw.get("exclude", ()))
        self.fail_on_warn = kw.get("fail_on_warn", False)

    def describe(self):
        return f"stdio: {self.cmd}"


def run_fixture(name, **kw):
    cmd = f"{sys.executable} {os.path.join(FIXTURES, name)}"
    transport = StdioTransport(cmd)
    transport.start()
    try:
        return run_suite(transport, Opts(cmd, **kw))
    finally:
        transport.close()


def sev(report, check):
    return {f.title: f.severity for f in report.findings if f.check == check}


def test_healthy_server_passes():
    report = run_fixture("healthy.py")
    assert not report.has_failures(), report.to_text(verbose=True)
    counts = report.counts()
    assert counts[SEVERITY_FAIL] == 0
    # both tools were smoke-called and passed
    calls = [f for f in report.findings if f.check == "tool_calls"]
    assert len(calls) == 2
    assert all(f.severity == SEVERITY_PASS for f in calls)


def test_polluting_server_fails_stdout_clean():
    report = run_fixture("polluting.py")
    assert report.has_failures()
    fails = [f for f in report.findings
             if f.check == "stdout_clean" and f.severity == SEVERITY_FAIL]
    assert fails, report.to_text(verbose=True)
    assert "Starting fixture server" in fails[0].detail


def test_crashing_server_fails_startup():
    cmd = f"{sys.executable} {os.path.join(FIXTURES, 'crashing.py')}"
    report, code = cli_run(Opts(cmd))
    assert code == 1  # check failure, not infra
    assert report.has_failures()
    assert any(f.check == "startup" and f.severity == SEVERITY_FAIL
               for f in report.findings)


def test_hanging_server_fails_initialize():
    report = run_fixture("hanging.py", timeout=1.0, call_timeout=1.0)
    assert report.has_failures()
    assert any(f.check == "initialize" and f.severity == SEVERITY_FAIL
               for f in report.findings)


def test_badschema_server_flags_schema_problems():
    report = run_fixture("badschema.py", no_call=True)
    assert report.has_failures()
    titles = " ".join(f.title for f in report.findings)
    assert "duplicate tool name 'dup'" in titles
    assert "required property 'b' not defined" in titles
    warns = [f for f in report.findings if f.severity == SEVERITY_WARN]
    assert any("empty inputSchema" in f.title for f in warns), \
        report.to_text(verbose=True)
    assert any("no description" in f.title for f in warns)


def test_erroring_tool_is_warn_not_fail():
    report = run_fixture("erroring_tool.py")
    assert not report.has_failures(), report.to_text(verbose=True)
    warns = [f for f in report.findings
             if f.check == "tool_calls" and f.severity == SEVERITY_WARN]
    assert len(warns) == 1 and "'flaky'" in warns[0].title


def test_no_call_skips_execution():
    report = run_fixture("healthy.py", no_call=True)
    assert not report.has_failures()
    assert any(f.check == "tool_calls" and "skipped" in f.title
               for f in report.findings)


def test_include_exclude_filters():
    report = run_fixture("healthy.py", include=["echo"])
    calls = [f for f in report.findings if f.check == "tool_calls"]
    assert len(calls) == 1 and "'echo'" in calls[0].title
    report = run_fixture("healthy.py", exclude=["echo", "add"])
    assert any(f.check == "tool_calls" and "no tools selected" in f.title
               for f in report.findings)
