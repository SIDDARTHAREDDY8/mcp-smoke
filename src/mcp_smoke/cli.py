"""mcp-smoke CLI: point at a server, get a verdict."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

from mcp_smoke import __version__
from mcp_smoke.checks import run_suite
from mcp_smoke.report import (
    EXIT_FAILURES,
    EXIT_INFRA,
    EXIT_INTERRUPTED,
    EXIT_OK,
    SEVERITY_FAIL,
    Finding,
    Report,
)
from mcp_smoke.transports import HttpTransport, StdioTransport, TransportError


class Options:
    """Validated CLI options. Invalid values exit 2 with a usage error."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Validate parsed CLI arguments; raise UsageError on bad values."""
        self.cmd: str | None = args.cmd
        self.url: str | None = args.url
        self.cwd: str | None = args.cwd
        self.env: dict[str, str] | None = _parse_env(args.env)
        self.headers: dict[str, str] = _parse_headers(args.header)
        self.timeout: float = _positive(args.timeout, "--timeout")
        self.handshake_timeout: float | None = _positive_or_none(
            args.handshake_timeout, "--handshake-timeout"
        )
        self.request_timeout: float | None = _positive_or_none(
            args.request_timeout, "--request-timeout"
        )
        self.call_timeout: float | None = _positive_or_none(
            args.call_timeout, "--call-timeout"
        )
        self.write_timeout: float | None = _positive_or_none(
            args.write_timeout, "--write-timeout"
        )
        self.shutdown_timeout: float | None = _positive_or_none(
            args.shutdown_timeout, "--shutdown-timeout"
        )
        self.no_call: bool = args.no_call
        self.include: set[str] = _parse_csv(args.include)
        self.exclude: set[str] = _parse_csv(args.exclude)
        self.fail_on_warn: bool = args.fail_on_warn
        self.json: bool = args.json
        self.output: str | None = args.output
        self.verbose: bool = args.verbose

    def effective(self, specific: float | None) -> float:
        """Resolve a per-phase timeout.

        The explicit per-phase value wins; otherwise the global --timeout
        applies.
        """
        return specific if specific is not None else self.timeout

    def describe(self) -> str:
        """Describe the target for report headers."""
        if self.cmd:
            return f"stdio: {self.cmd}"
        return f"http: {self.url}"


def _usage_error(message: str) -> NoReturn:
    print(f"mcp-smoke: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _positive(value: float, flag: str) -> float:
    if not math.isfinite(value) or value <= 0:
        _usage_error(f"{flag} must be a positive number of seconds, got {value}")
    return value


def _positive_or_none(value: float | None, flag: str) -> float | None:
    """Validate an optional per-phase timeout; None means 'use --timeout'."""
    if value is None:
        return None
    return _positive(value, flag)


def _parse_csv(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _parse_env(items: list[str]) -> dict[str, str] | None:
    env = None
    if items:
        env = dict(os.environ)
        for item in items:
            if "=" not in item:
                _usage_error(f"--env expects KEY=VALUE, got {item!r}")
            k, v = item.split("=", 1)
            env[k] = v
    return env


def _parse_headers(items: list[str]) -> dict[str, str]:
    headers = {}
    for item in items or []:
        if ":" not in item:
            _usage_error(f"--header expects 'Name: value', got {item!r}")
        k, v = item.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def build_parser() -> argparse.ArgumentParser:
    """Build the mcp-smoke command-line parser."""
    p = argparse.ArgumentParser(
        prog="mcp-smoke",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Headless runtime smoke-tester for MCP servers. Launches the server, "
            "runs the MCP handshake, validates tool schemas, and actually calls "
            "each tool -- catching the silent failures (stdout pollution, "
            "startup crashes, empty schemas, hanging tools) that break agents "
            "in production."
        ),
        epilog=(
            "examples:\n"
            '  mcp-smoke --cmd "python server.py"\n'
            '  mcp-smoke --cmd "node dist/server.js" --no-call\n'
            "  mcp-smoke --url http://localhost:8000/mcp "
            '--header "Authorization: Bearer $TOKEN"\n'
            '  mcp-smoke --cmd "python server.py" --json --output smoke.json '
            "--fail-on-warn\n"
            "\nexit codes: 0 clean, 1 failing checks, 2 mcp-smoke error, "
            "130 interrupted."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--cmd", help='launch command, e.g. --cmd "python server.py"')
    src.add_argument(
        "--url", help="Streamable HTTP endpoint, e.g. --url http://localhost:8000/mcp"
    )
    p.add_argument("--cwd", help="working directory for --cmd")
    p.add_argument(
        "--env",
        action="append",
        default=[],
        help="extra env vars for --cmd (KEY=VALUE; repeatable)",
    )
    p.add_argument(
        "--header",
        action="append",
        default=[],
        help="extra HTTP headers for --url ('Name: value'; repeatable)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="global default timeout in seconds for every phase below; each "
        "phase can be overridden individually (default 10)",
    )
    p.add_argument(
        "--handshake-timeout",
        type=float,
        default=None,
        help="seconds to wait for initialize and tools/list (default: --timeout)",
    )
    p.add_argument(
        "--request-timeout",
        type=float,
        default=None,
        help="seconds to wait for other requests: ping, resources/list, "
        "prompts/list (default: --timeout)",
    )
    p.add_argument(
        "--call-timeout",
        type=float,
        default=None,
        help="seconds to wait per tools/call (default: --timeout)",
    )
    p.add_argument(
        "--write-timeout",
        type=float,
        default=None,
        help="seconds to wait when writing to the server's stdin "
        "(stdio only; default: --timeout)",
    )
    p.add_argument(
        "--shutdown-timeout",
        type=float,
        default=None,
        help="seconds to wait at each shutdown stage (stdin EOF, then SIGTERM) "
        "before SIGKILL (default: 3)",
    )
    p.add_argument(
        "--no-call",
        action="store_true",
        help="skip actually calling tools (protocol + schema checks only)",
    )
    p.add_argument(
        "--include",
        default="",
        help="comma-separated tool names to smoke-call (default: all)",
    )
    p.add_argument(
        "--exclude", default="", help="comma-separated tool names to skip smoke-calling"
    )
    p.add_argument("--json", action="store_true", help="machine-readable JSON report")
    p.add_argument("--output", help="write report to file (in addition to stdout)")
    p.add_argument(
        "--fail-on-warn", action="store_true", help="exit non-zero on warnings too"
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show details for passing checks as well",
    )
    return p


def run(opts: Options) -> tuple[Report, int]:
    """Run the suite against opts; return (Report, exit_code).

    A server that crashes on startup is a *check failure* (exit 1); only
    mcp-smoke-side problems (bad command, unreachable URL) are infra (exit 2).
    An unexpected mcp-smoke bug becomes an ``internal`` finding (exit 2),
    never a traceback.
    """
    if opts.cmd:
        transport = StdioTransport(
            opts.cmd,
            cwd=opts.cwd,
            env=opts.env,
            write_timeout=opts.effective(opts.write_timeout),
            shutdown_timeout=opts.shutdown_timeout,
        )
    else:
        transport = HttpTransport(opts.url or "", headers=opts.headers)

    try:
        if isinstance(transport, StdioTransport):
            transport.start()
        report = run_suite(transport, opts)
    except TransportError as exc:
        if exc.server_side:
            report = Report(
                opts.describe(),
                [Finding("startup", SEVERITY_FAIL, "server failed to start", str(exc))],
            )
            return report, EXIT_FAILURES
        report = Report(
            opts.describe(),
            [Finding("startup", SEVERITY_FAIL, "suite could not run", str(exc))],
        )
        return report, EXIT_INFRA
    finally:
        # close() never raises, but suppress is honest belt-and-braces.
        with suppress(Exception):
            transport.close()

    if report.has_internal_error():
        return report, EXIT_INFRA

    code = report.exit_code()
    if (
        opts.fail_on_warn
        and code == EXIT_OK
        and any(f.severity == "warn" for f in report.findings)
    ):
        code = EXIT_FAILURES
    return report, code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, run the suite, print the report."""
    opts = Options(build_parser().parse_args(argv))

    if opts.cmd and not opts.no_call:
        print(
            "note: smoke-calling real tools on a dev server is expected; "
            "use --no-call to skip execution.",
            file=sys.stderr,
        )
    try:
        report, code = run(opts)
        text = report.to_json() if opts.json else report.to_text(verbose=opts.verbose)
        if opts.output:
            try:
                with Path(opts.output).open("w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            except OSError as exc:
                print(
                    f"mcp-smoke: error: cannot write --output {opts.output!r}: {exc}",
                    file=sys.stderr,
                )
                return EXIT_INFRA
        else:
            try:
                print(text)
            except BrokenPipeError:
                # stdout went away (e.g. piped to `head`); the report was
                # produced fine, but it could not be delivered.
                return EXIT_FAILURES
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 -- last resort: never traceback
        detail = f"{type(exc).__name__}: {exc}"
        if opts.json:
            # Still machine-parseable: a minimal valid report.
            print(
                json.dumps(
                    {
                        "target": opts.describe(),
                        "summary": {"pass": 0, "warn": 0, "fail": 1},
                        "findings": [
                            {
                                "check": "internal",
                                "severity": "fail",
                                "title": "mcp-smoke internal error",
                                "detail": detail,
                            }
                        ],
                    }
                )
            )
        else:
            print(f"mcp-smoke: internal error: {detail}", file=sys.stderr)
        return EXIT_INFRA
    return code


if __name__ == "__main__":
    sys.exit(main())
