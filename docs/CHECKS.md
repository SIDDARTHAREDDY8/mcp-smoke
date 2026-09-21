# Check catalog

Every check emits findings with severity `pass` / `warn` / `fail`.

## `startup` (fail)
The server process was spawned and must still be alive when the handshake
begins. An immediate exit (bad import, missing env var, invalid config) fails
here, and the finding includes the stderr tail so you see the real error:
the thing MCP clients hide from you.

## `initialize` (fail / warn)
Runs the MCP `initialize` handshake and the `notifications/initialized`
notification. Fails on transport errors and protocol-level error responses.
The `InitializeResult` is validated strictly: `protocolVersion` must be a
string, and `serverInfo` / `capabilities` must be objects when present.
A malformed handshake fails here with a clear message instead of
corrupting the checks downstream.
Warns when the server speaks a protocol version older than `2024-11-05`.

## `stdout_clean` (fail)
Reads every line the server wrote to stdout during the session. Any line that
is not valid JSON-RPC 2.0 is **protocol corruption**: on the stdio transport,
stdout is the wire, and a single stray log line breaks message framing for
every subsequent message. This is the most common "works in my test, fails in
Claude Desktop" bug. Fix: log to stderr.

## `tools_list` (fail)
`tools/list` must return an array of tools. Each tool needs a unique,
non-empty string `name`. Duplicate or missing names fail.

## `tool_metadata` (warn)
Tools without a `description`. Agents select tools largely by description text;
an undescribed tool is effectively invisible.

## `tool_schema` (fail / warn)
Structural validation of each tool's `inputSchema`:
- **fail:** schema is not an object, unknown JSON Schema types, `required`
  entries not defined in `properties`, `properties` not an object.
- **warn:** empty schema (`{"type": "object"}` with no `properties`). This is
  the silent bug class from modelcontextprotocol/typescript-sdk#2627: the
  server looks fine, but clients strip all arguments and handlers receive
  nothing.

## `tool_calls` (fail / warn)
For every selected tool, mcp-smoke synthesizes minimal arguments from the
schema (required properties only, type-appropriate dummies) and calls the
tool with a per-call timeout:
- **fail:** transport-level failure (timeout, server died mid-call), JSON-RPC
  protocol error, or a result missing the `content` array.
- **warn:** the tool returned `isError: true`. Synthetic arguments may be
  semantically invalid for the tool, so this is a lead, not proof, but tools
  that blow up on plausible inputs deserve a look.
- **pass:** clean result; the finding records latency and the args sent.

Skipped entirely with `--no-call`; scoped with `--include` / `--exclude`.

## `resources`, `prompts` (pass / warn)
Probes `resources/list` and `prompts/list`. "Method not found" is a clean
pass (the server simply doesn't implement them); transport errors warn.
A malformed response shape (not an object, or a non-array items list)
warns instead of crashing the suite.

## `shutdown` (warn)
Warns if the server process exited during the suite (usually a crash inside
a tool handler).
