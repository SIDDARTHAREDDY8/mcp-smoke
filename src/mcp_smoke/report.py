"""Findings, reports, exit codes."""
from __future__ import annotations

import json

SEVERITY_PASS = "pass"
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"

EXIT_OK = 0          # no failures
EXIT_FAILURES = 1    # one or more failing checks
EXIT_INFRA = 2       # could not run the suite at all (spawn/connect blew up)


class Finding:
    def __init__(self, check, severity, title, detail=""):
        self.check = check
        self.severity = severity
        self.title = title
        self.detail = detail

    def to_dict(self):
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
        }


class Report:
    def __init__(self, target, findings):
        self.target = target
        self.findings = list(findings)

    def counts(self):
        c = {SEVERITY_PASS: 0, SEVERITY_WARN: 0, SEVERITY_FAIL: 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c

    def has_failures(self):
        return any(f.severity == SEVERITY_FAIL for f in self.findings)

    def exit_code(self):
        return EXIT_FAILURES if self.has_failures() else EXIT_OK

    def to_dict(self):
        return {
            "target": self.target,
            "summary": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2)

    def to_text(self, verbose=False):
        marks = {SEVERITY_PASS: "PASS", SEVERITY_WARN: "WARN", SEVERITY_FAIL: "FAIL"}
        lines = [f"mcp-smoke: {self.target}"]
        for f in self.findings:
            lines.append(f"  [{marks[f.severity]}] {f.check}: {f.title}")
            if f.detail and (verbose or f.severity != SEVERITY_PASS):
                for dline in f.detail.splitlines():
                    lines.append(f"           {dline}")
        c = self.counts()
        lines.append(
            f"summary: {c[SEVERITY_PASS]} pass, {c[SEVERITY_WARN]} warn, "
            f"{c[SEVERITY_FAIL]} fail"
        )
        return "\n".join(lines)
