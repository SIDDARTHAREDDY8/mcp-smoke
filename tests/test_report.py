"""Report semantics: exit codes, JSON validity, weird detail values."""

import json

from mcp_smoke.report import (
    EXIT_FAILURES,
    EXIT_INFRA,
    EXIT_INTERRUPTED,
    EXIT_OK,
    SEVERITY_FAIL,
    SEVERITY_PASS,
    SEVERITY_WARN,
    Finding,
    Report,
)


def test_clean_report_exits_zero():
    r = Report("x", [Finding("a", SEVERITY_PASS, "ok")])
    assert r.exit_code() == EXIT_OK == 0


def test_failure_exits_one():
    r = Report("x", [Finding("a", SEVERITY_FAIL, "bad")])
    assert r.exit_code() == EXIT_FAILURES == 1


def test_warn_only_exits_zero():
    r = Report("x", [Finding("a", SEVERITY_WARN, "hmm")])
    assert r.exit_code() == 0


def test_internal_finding_detected():
    r = Report("x", [Finding("internal", SEVERITY_FAIL, "boom")])
    assert r.has_internal_error()
    assert not Report("x", []).has_internal_error()


def test_finding_detail_coerces_weird_values():
    assert Finding("c", SEVERITY_FAIL, "t", None).detail == ""
    assert Finding("c", SEVERITY_FAIL, "t", 42).detail == "42"
    f = Finding("c", SEVERITY_FAIL, "t", object())
    assert isinstance(f.detail, str)
    # JSON never breaks on hostile values
    json.dumps(Report("x", [f]).to_dict())


def test_json_is_stable_and_valid():
    r = Report(
        "stdio: x",
        [
            Finding("a", SEVERITY_PASS, "ok"),
            Finding("b", SEVERITY_FAIL, "bad", "detail"),
        ],
    )
    assert json.loads(r.to_json()) == json.loads(r.to_json())


def test_text_summary_line():
    r = Report("x", [Finding("a", SEVERITY_PASS, "ok")])
    text = r.to_text()
    assert "summary: 1 pass, 0 warn, 0 fail" in text


def test_exit_constants():
    assert (EXIT_OK, EXIT_FAILURES, EXIT_INFRA, EXIT_INTERRUPTED) == (0, 1, 2, 130)


def test_to_text_verbose_shows_pass_details():
    report = Report("stdio: dummy", [Finding("x", SEVERITY_PASS, "ok", "why it is ok")])
    quiet = report.to_text()
    assert "why it is ok" not in quiet
    assert "why it is ok" in report.to_text(verbose=True)
