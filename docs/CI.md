# CI integration

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No failing checks |
| `1` | One or more failing checks |
| `2` | mcp-smoke itself couldn't run (bad launch command, unreachable URL) |

Add `--fail-on-warn` to also fail on warnings.

## GitHub Actions (pipx)

```yaml
jobs:
  mcp-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install .        # your server's deps, if Python
      - run: pipx run mcp-smoke --cmd "python server.py" --json --output smoke.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: mcp-smoke-report, path: smoke.json }
```

## GitHub Action (bundled)

`action.yml` in this repo is a composite action:

```yaml
- uses: SIDDARTHAREDDY8/mcp-smoke@v0.1.0
  with:
    cmd: "node dist/server.js"
    fail-on-warn: "true"
```

Inputs: `cmd` or `url`, `args` (extra CLI flags), `fail-on-warn`.

## Failing the build on schema regressions

Snapshot the passing report and diff it in CI, or simply gate on the exit
code. A common pattern: run with `--no-call` on every PR (fast, side-effect
free) and the full smoke suite nightly against a staging server.
