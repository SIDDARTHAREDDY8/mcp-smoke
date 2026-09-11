"""mcp-smoke CLI: point at a server, get a verdict."""
from __future__ import annotations

import argparse
import os
import sys

from mcp_smoke import __version__
from mcp_smoke.checks import run_suite
from mcp_smoke.report import EXIT_INFRA, EXIT_OK
from mcp_smoke.transports import HttpTransport, StdioTransport, TransportError


class Options:
    def __init__(self, args):
        self.cmd = args.cmd
        self.url = args.url
        self.cwd = args.cwd
        self.env = _parse_env(args.env)
        self.headers = _parse_headers(args.header)
        self.timeout = args.timeout
        self.call_timeout = args.call_timeout
        self.no_call = args.no_call
        self.include = set(args.include.split(",")) if args.include else set()
        self.exclude = set(args.exclude.split(",")) if args.exclude else set()
        self.fail_on_warn = args.fail_on_warn
        self.json = args.json
        self.output = args.output
        self.verbose = args.verbose

    def describe(self):
        if self.cmd:
            return f"stdio: {self.cmd}"
        return f"http: {self.url}"


def _parse_env(items):
    env = None
    if items:
        env = dict(os.environ)
        for item in items:
            if "=" not in item:
                raise SystemExit(f"--env expects KEY=VALUE, got {item!r}")
            k, v = item.split("=", 1)
            env[k] = v
    return env


def _parse_headers(items):
    headers = {}
    for item in items or []:
        if ":" not in item:
            raise SystemExit(f"--header expects 'Name: value', got {item!r}")
        k, v = item.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def build_parser():
    p = argparse.ArgumentParser(
        prog="mcp-smoke",
        description=(
            "Headless runtime smoke-tester for MCP servers. Launches the server, "
            "runs the MCP handshake, validates tool schemas, and actually calls "
            "each tool -- catching the silent failures (stdout pollution, "
            "startup crashes, empty schemas, hanging tools) that break agents "
            "in production. Exits non-zero on failures: CI-ready."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--cmd", help='launch command, e.g. --cmd "python server.py"')
    src.add_argument("--url", help="Streamable HTTP endpoint, e.g. --url http://localhost:8000/mcp")
    p.add_argument("--cwd", help="working directory for --cmd")
    p.add_argument("--env", action="append", default=[],
                   help="extra env vars for --cmd (KEY=VALUE; repeatable)")
    p.add_argument("--header", action="append", default=[],
                   help="extra HTTP headers for --url ('Name: value'; repeatable)")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="seconds to wait for protocol responses (default 10)")
    p.add_argument("--call-timeout", type=float, default=15.0,
                   help="seconds to wait per tool call (default 15)")
    p.add_argument("--no-call", action="store_true",
                   help="skip actually calling tools (protocol + schema checks only)")
    p.add_argument("--include", default="",
                   help="comma-separated tool names to smoke-call (default: all)")
    p.add_argument("--exclude", default="",
                   help="comma-separated tool names to skip smoke-calling")
    p.add_argument("--json", action="store_true", help="machine-readable JSON report")
    p.add_argument("--output", help="write report to file (in addition to stdout)")
    p.add_argument("--fail-on-warn", action="store_true",
                   help="exit non-zero on warnings too")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show details for passing checks as well")
    return p


def run(opts):
    """Run the suite against opts; return (Report, exit_code).

    A server that crashes on startup is a *check failure* (exit 1); only
    mcp-smoke-side problems (bad command, unreachable URL) are infra (exit 2).
    """
    from mcp_smoke.report import EXIT_FAILURES, Finding, Report, SEVERITY_FAIL

    if opts.cmd:
        transport = StdioTransport(opts.cmd, cwd=opts.cwd, env=opts.env)
    else:
        transport = HttpTransport(opts.url, headers=opts.headers)

    try:
        if isinstance(transport, StdioTransport):
            transport.start()
        report = run_suite(transport, opts)
    except TransportError as exc:
        if getattr(exc, "server_side", False):
            report = Report(opts.describe(), [Finding(
                "startup", SEVERITY_FAIL, "server failed to start", str(exc))])
            return report, EXIT_FAILURES
        report = Report(opts.describe(), [Finding(
            "startup", SEVERITY_FAIL, "suite could not run", str(exc))])
        return report, EXIT_INFRA
    finally:
        try:
            transport.close()
        except Exception:
            pass

    code = report.exit_code()
    if opts.fail_on_warn and code == EXIT_OK and any(
        f.severity == "warn" for f in report.findings
    ):
        code = EXIT_FAILURES
    return report, code


def main(argv=None):
    args = build_parser().parse_args(argv)
    opts = Options(args)

    if opts.cmd and not opts.no_call:
        print("note: smoke-calling real tools on a dev server is expected; "
              "use --no-call to skip execution.", file=sys.stderr)
    try:
        report, code = run(opts)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    text = report.to_json() if opts.json else report.to_text(verbose=opts.verbose)
    if opts.output:
        with open(opts.output, "w") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
