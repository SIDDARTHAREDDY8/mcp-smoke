"""Findings, reports, exit codes."""

from __future__ import annotations

import json
from typing import Any

SEVERITY_PASS = "pass"  # noqa: S105 -- not a password, a severity label
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"

EXIT_OK = 0  # no failures
EXIT_FAILURES = 1  # one or more failing checks
EXIT_INFRA = 2  # could not run the suite at all (spawn/connect blew up)
EXIT_INTERRUPTED = 130  # KeyboardInterrupt


class Finding:
    """One check result: pass, warn, or fail with a human-readable title."""

    def __init__(self, check: str, severity: str, title: str, detail: Any = "") -> None:
        """Create a finding; detail is coerced to a string."""
        self.check = str(check)
        self.severity = str(severity)
        self.title = str(title)
        # Coerce: finding details must always render, even for hostile values.
        self.detail = "" if detail is None else str(detail)

    def to_dict(self) -> dict[str, str]:
        """Return the finding as a JSON-serializable dict."""
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
        }


class Report:
    """The full result of a smoke run: target description plus findings."""

    def __init__(self, target: str, findings: list[Finding]) -> None:
        """Create a report for a target with its findings."""
        self.target = str(target)
        self.findings = list(findings)

    def counts(self) -> dict[str, int]:
        """Count findings by severity."""
        c = {SEVERITY_PASS: 0, SEVERITY_WARN: 0, SEVERITY_FAIL: 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c

    def has_failures(self) -> bool:
        """Return True when any check failed."""
        return any(f.severity == SEVERITY_FAIL for f in self.findings)

    def has_internal_error(self) -> bool:
        """Return True when mcp-smoke itself errored (exit 2), not the server."""
        return any(f.check == "internal" for f in self.findings)

    def exit_code(self) -> int:
        """Map the report to a process exit code (0 clean, 1 failures)."""
        return EXIT_FAILURES if self.has_failures() else EXIT_OK

    def to_dict(self) -> dict[str, Any]:
        """Return the report as a JSON-serializable dict."""
        return {
            "target": self.target,
            "summary": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self) -> str:
        """Serialize the report as indented JSON (always valid)."""
        # default=str is belt-and-braces: --json must always be valid JSON,
        # even if a finding somehow carries a non-serializable value.
        return json.dumps(self.to_dict(), indent=2, default=str)

    def to_text(self, *, verbose: bool = False) -> str:
        """Render the report as human-readable text."""
        marks = {SEVERITY_PASS: "PASS", SEVERITY_WARN: "WARN", SEVERITY_FAIL: "FAIL"}
        lines = [f"mcp-smoke: {self.target}"]
        for f in self.findings:
            lines.append(f"  [{marks[f.severity]}] {f.check}: {f.title}")
            if f.detail and (verbose or f.severity != SEVERITY_PASS):
                lines.extend(f"           {dline}" for dline in f.detail.splitlines())
        c = self.counts()
        lines.append(
            f"summary: {c[SEVERITY_PASS]} pass, {c[SEVERITY_WARN]} warn, "
            f"{c[SEVERITY_FAIL]} fail"
        )
        return "\n".join(lines)
