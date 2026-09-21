# mcp-smoke

**Headless runtime smoke-tester for MCP servers.** Point it at your server's launch command and it tells you — in CI, in seconds — whether the server your agents will actually talk to is healthy.

MCP servers fail *silently*: a stray `print`/`console.log` on stdout corrupts the JSON-RPC stream, servers crash on startup with no diagnostics, schemas ship empty so clients send no arguments, tools hang or error only when actually called. Static linters check the menu; **mcp-smoke checks the kitchen** — it launches the real server, runs the real protocol, and actually calls the tools.

Zero dependencies. Python 3.9+.

## Quickstart

```bash
pip install mcp-smoke
mcp-smoke --cmd "python server.py"
```

```
mcp-smoke: stdio: python server.py
  [PASS] startup: server process started and stayed alive
  [PASS] initialize: handshake ok (server: my-server 1.2.0, protocol 2025-06-18)
  [FAIL] stdout_clean: 1 non-JSON-RPC line(s) on stdout -- protocol stream corrupted
           first lines:
           Starting server...
           Fix: log to stderr, never stdout (console.log/print corrupts stdio JSON-RPC).
  [PASS] tools_list: 3 tool(s) advertised
  [PASS] tool_calls: tool 'search': ok (0.42s, args {'query': 'test'})
  ...
summary: 8 pass, 0 warn, 1 fail
```

Exit code `0` = clean, `1` = failures, `2` = mcp-smoke itself couldn't run (bad command, unreachable URL).

## What it checks

| Check | What it catches |
|---|---|
| `startup` | Server crashes or exits immediately (broken import, bad config) — with the stderr tail |
| `initialize` | Handshake failures, ancient protocol versions |
| `stdout_clean` | **The #1 silent failure:** non-JSON-RPC output on stdout corrupting the protocol stream |
| `tools_list` | Missing/duplicate tool names |
| `tool_metadata` | Tools with no description (agents pick tools by description) |
| `tool_schema` | Dangling `required` properties, unknown types, and the empty-`{"type":"object"}` schema class that makes clients silently send no arguments |
| `tool_calls` | Actually calls every tool with schema-generated arguments: timeouts, protocol errors, malformed results, and `isError` responses |
| `resources` / `prompts` | Informational capability probe |
| `shutdown` | Server dying mid-suite |

Full check catalog: [docs/CHECKS.md](docs/CHECKS.md).

## Usage

```bash
# stdio server (most MCP servers)
mcp-smoke --cmd "node dist/server.js"
mcp-smoke --cmd "uvx my-mcp-server --api-key $KEY" --env API_KEY=secret

# Streamable HTTP server
mcp-smoke --url http://localhost:8000/mcp --header "Authorization: Bearer $TOKEN"

# protocol + schema checks only, don't execute tools
mcp-smoke --cmd "python server.py" --no-call

# only smoke-call specific tools / skip dangerous ones
mcp-smoke --cmd "python server.py" --include search,fetch
mcp-smoke --cmd "python server.py" --exclude delete_everything

# CI: machine-readable report, fail on warnings too
mcp-smoke --cmd "node dist/server.js" --json --output smoke.json --fail-on-warn
```

> **Safety:** smoke calls *execute your tools* with synthetic arguments. Point
> mcp-smoke at dev servers, and use `--no-call` / `--exclude` for anything with
> side effects.

## CI

```yaml
- name: Smoke-test MCP server
  run: |
    pipx run mcp-smoke --cmd "python server.py" --json --output smoke.json
```

Or use the bundled action (see [docs/CI.md](docs/CI.md)):

```yaml
- uses: SIDDARTHAREDDY8/mcp-smoke@v1
  with:
    cmd: "python server.py"
```

## How it differs

- **vs MCP Inspector** — the Inspector is an interactive UI for humans; mcp-smoke is headless and CI-gated.
- **vs mcp-conform** — mcp-conform lints the *static* manifest (names, descriptions, schema shape). mcp-smoke runs the *live server* and executes the tools. Complementary: run both.
- **vs `mcpc` / hand-rolled clients** — those are for calling tools manually; mcp-smoke is a verdict (pass/fail + exit code).

## Development

```bash
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

18 tests, including six deliberately-broken fixture servers (stdout polluter, instant crasher, hanging server, bad schemas, erroring tool) and a fixture HTTP server.

## License

MIT — see [LICENSE](LICENSE).
