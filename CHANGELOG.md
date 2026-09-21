# Changelog

All notable changes to mcp-smoke. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

### Added
- Per-phase timeout flags: `--handshake-timeout`, `--request-timeout`,
  `--call-timeout`, `--write-timeout`, `--shutdown-timeout`. `--timeout`
  is the global default for every phase; explicit per-phase values override
  it. `--call-timeout` now inherits the global when unset.
- CI workflow (`.github/workflows/ci.yml`): Python 3.9-3.13 matrix running
  ruff, ruff format, ty, the test suite with a 90% coverage gate, and a
  self-smoke step against the fixture servers.

### Changed
- `initialize()` validates the `InitializeResult` strictly: `protocolVersion`
  must be a string, and `serverInfo` / `capabilities` must be objects when
  present. A malformed handshake now fails the `initialize` check with a
  clear message instead of corrupting downstream checks.
- Malformed `resources/list` / `prompts/list` shapes (non-object result or
  non-array items) are reported as warnings instead of escaping to the
  last-resort internal-error handler.
- Stdio write and response budgets are separated: `request()` no longer
  spends the response timeout on the write.
- Timeout validation rejects zero, negative, NaN, and infinite values.

## 0.2.0
- Defensive runtime: no tracebacks (internal crashes become `internal`
  findings), timeouts on every phase, guaranteed child-process cleanup
  (stdin close, wait, SIGTERM, SIGKILL, reap).
- Exit codes: 0 clean, 1 server/check failure, 2 internal/infrastructure
  failure, 130 interruption.
- Stdio/HTTP caps: 64 KiB per line, 200 stored pollution/stderr/notification
  entries, 16 MiB HTTP response bodies.
- `python -m mcp_smoke` entry point.

## 0.1.0
- Initial release: headless MCP server smoke tester (stdio + Streamable HTTP),
  handshake/schema/tool-call checks, JSON and text reports, composite GitHub
  Action.
