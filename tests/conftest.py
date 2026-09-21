"""Shared test helpers: src on sys.path, fixture servers, Options builder."""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_smoke.cli import Options, build_parser

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture_cmd(name: str) -> str:
    return f"{sys.executable} {os.path.join(FIXTURES, name)}"


def make_opts(**kw: Any) -> Options:
    """Build real CLI Options from kwargs (goes through argparse).

    Accepts the same logical options as the CLI: cmd, url, timeout,
    handshake_timeout, request_timeout, call_timeout, write_timeout,
    shutdown_timeout, no_call, include/exclude (comma strings), fail_on_warn,
    json, verbose, output, cwd.
    """
    argv: list[str] = []
    cmd = kw.pop("cmd", None)
    url = kw.pop("url", None)
    if cmd is not None:
        argv += ["--cmd", cmd]
    if url is not None:
        argv += ["--url", url]
    for key in (
        "timeout",
        "handshake_timeout",
        "request_timeout",
        "call_timeout",
        "write_timeout",
        "shutdown_timeout",
    ):
        if key in kw:
            argv += ["--" + key.replace("_", "-"), str(kw.pop(key))]
    for key in ("include", "exclude", "output", "cwd"):
        if key in kw:
            argv += ["--" + key, str(kw.pop(key))]
    for flag in ("no_call", "fail_on_warn", "json", "verbose"):
        if kw.pop(flag, False):
            argv += ["--" + flag.replace("_", "-")]
    assert not kw, f"unknown options: {kw}"
    return Options(build_parser().parse_args(argv))


class ScriptedTransport:
    """In-memory transport scripted per method (no subprocess).

    script maps method name -> response value, response factory, or exception
    to raise. Conforms to the Transport protocol structurally.
    """

    def __init__(self, script: dict[str, Any], alive: bool = True) -> None:
        self.script = script
        self._alive = alive
        self.closed = False
        self.timeouts: dict[str, float | None] = {}

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        self.timeouts[method] = timeout
        action = self.script[method]
        if isinstance(action, Exception):
            raise action
        return action() if callable(action) else action

    def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def exit_code(self) -> int | None:
        return None if self._alive else 1

    @property
    def pollution(self) -> list[str]:
        return []

    @property
    def pollution_dropped(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True
