"""CLI-level tests: exit codes, output formats, validation, file writing."""

import json
import re
import time

import pytest
from conftest import fixture_cmd, make_opts

from mcp_smoke.cli import main, run


def test_healthy_server_exit_zero_text():
    report, code = run(make_opts(cmd=fixture_cmd("healthy.py")))
    assert code == 0
    assert report.counts()["pass"] > 0
    text = report.to_text()
    assert "mcp-smoke:" in text and "summary:" in text


def test_broken_server_exit_one():
    _, code = run(make_opts(cmd=fixture_cmd("polluting.py"), no_call=True))
    assert code == 1


def test_bad_command_exit_two():
    report, code = run(make_opts(cmd="definitely-not-a-real-command-xyz"))
    assert code == 2
    assert any(
        f.check == "startup" and "suite could not run" in f.title
        for f in report.findings
    )


def test_fail_on_warn_flips_exit():
    _, code = run(make_opts(cmd=fixture_cmd("erroring_tool.py"), fail_on_warn=True))
    assert code == 1  # erroring_tool warns (isError) but has no failures
    _, code = run(make_opts(cmd=fixture_cmd("erroring_tool.py")))
    assert code == 0


def test_json_output_is_valid_and_stable():
    report, _ = run(make_opts(cmd=fixture_cmd("healthy.py")))
    data = json.loads(report.to_json())
    assert set(data) == {"target", "summary", "findings"}
    assert data["summary"]["fail"] == 0
    # deterministic: same findings every run
    report2, _ = run(make_opts(cmd=fixture_cmd("healthy.py"), no_call=True))
    report3, _ = run(make_opts(cmd=fixture_cmd("healthy.py"), no_call=True))
    assert report2.to_json() == report3.to_json()


def test_json_valid_even_on_infra_error():
    report, code = run(make_opts(cmd="definitely-not-a-real-command-xyz"))
    assert code == 2
    data = json.loads(report.to_json())
    assert data["summary"]["fail"] >= 1


def test_main_rejects_negative_timeout(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--timeout", "-1"])
    assert exc.value.code == 2
    assert "error" in capsys.readouterr().err


def test_main_rejects_zero_call_timeout(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--call-timeout", "0"])
    assert exc.value.code == 2
    assert "error" in capsys.readouterr().err


def test_main_rejects_bad_env(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--env", "NOEQUALS"])
    assert exc.value.code == 2
    assert "error" in capsys.readouterr().err


def test_main_rejects_bad_header(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--url", "http://x", "--header", "no-colon"])
    assert exc.value.code == 2
    assert "error" in capsys.readouterr().err


def test_main_requires_cmd_or_url():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2  # argparse errors exit 2


def test_main_rejects_cmd_and_url_together(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--url", "http://x"])
    assert exc.value.code == 2


def test_output_file_written(tmp_path, capsys):
    out = str(tmp_path / "report.json")
    code = main(
        ["--cmd", fixture_cmd("healthy.py"), "--no-call", "--json", "--output", out]
    )
    assert code == 0
    with open(out, encoding="utf-8") as fh:
        data = json.loads(fh.read())
    assert data["summary"]["fail"] == 0
    # nothing printed to stdout when --output is used with valid content
    assert capsys.readouterr().out == ""


def test_unwritable_output_exits_two(capsys):
    code = main(
        [
            "--cmd",
            fixture_cmd("healthy.py"),
            "--no-call",
            "--output",
            "/proc/does-not-exist/report.json",
        ]
    )
    assert code == 2
    assert "cannot write" in capsys.readouterr().err


def test_output_to_existing_directory_exits_two(tmp_path, capsys):
    code = main(
        [
            "--cmd",
            fixture_cmd("healthy.py"),
            "--no-call",
            "--output",
            str(tmp_path),
        ]
    )
    assert code == 2


def test_main_healthy_end_to_end(capsys):
    code = main(["--cmd", fixture_cmd("healthy.py"), "--no-call"])
    assert code == 0
    out = capsys.readouterr().out
    assert "summary:" in out


def test_main_json_crash_path_is_valid_json(monkeypatch, capsys):
    import mcp_smoke.cli as cli_module

    def boom(opts):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(cli_module, "run", boom)
    code = main(["--cmd", "x", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["findings"][0]["check"] == "internal"


def test_keyboard_interrupt_exit_130(monkeypatch, capsys):
    import mcp_smoke.cli as cli_module

    def boom(opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "run", boom)
    assert main(["--cmd", "x"]) == 130


def test_parser_help_has_no_em_dashes(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "\u2014" not in out
    assert "exit codes" in out


def test_timeout_precedence_global_is_default():
    opts = make_opts(cmd="x")
    assert opts.effective(opts.handshake_timeout) == 10.0
    assert opts.effective(opts.request_timeout) == 10.0
    assert opts.effective(opts.call_timeout) == 10.0
    assert opts.effective(opts.write_timeout) == 10.0


def test_timeout_precedence_global_overrides_all_phases():
    opts = make_opts(cmd="x", timeout=7)
    assert opts.effective(opts.handshake_timeout) == 7
    assert opts.effective(opts.request_timeout) == 7
    assert opts.effective(opts.call_timeout) == 7
    assert opts.effective(opts.write_timeout) == 7


def test_timeout_precedence_per_phase_wins_over_global():
    opts = make_opts(cmd="x", timeout=30, call_timeout=2, handshake_timeout=5)
    assert opts.effective(opts.call_timeout) == 2
    assert opts.effective(opts.handshake_timeout) == 5
    assert opts.effective(opts.request_timeout) == 30  # untouched phases use global


def test_call_timeout_flag_preserved():
    # --call-timeout keeps working as a standalone flag (old default 15 is
    # gone: unset phases now inherit --timeout).
    opts = make_opts(cmd="x", call_timeout=20)
    assert opts.effective(opts.call_timeout) == 20


def test_main_rejects_nan_timeout(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--timeout", "nan"])
    assert exc.value.code == 2
    assert "positive" in capsys.readouterr().err


def test_main_rejects_inf_call_timeout(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--cmd", "x", "--call-timeout", "inf"])
    assert exc.value.code == 2
    assert "positive" in capsys.readouterr().err


def test_global_timeout_bounds_tool_call():
    # slow_tool sleeps 30s per call; with only --timeout 1 the call must fail
    # fast, proving the global governs the call phase when unset.
    start = time.monotonic()
    report, _ = run(make_opts(cmd=fixture_cmd("slow_tool.py"), timeout=1))
    elapsed = time.monotonic() - start
    fails = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "fail"
    ]
    assert fails, report.to_text(verbose=True)
    assert any("timed out" in f.detail for f in fails)
    assert elapsed < 25


def test_per_phase_call_timeout_beats_global():
    # --timeout 30 but --call-timeout 1: the 30s tool call must fail after
    # ~1s (the finding title records how long the call took), proving the
    # per-phase value wins over the global. request/handshake timeouts are
    # kept small so later checks do not dominate the run.
    report, _ = run(
        make_opts(
            cmd=fixture_cmd("slow_tool.py"),
            timeout=30,
            call_timeout=1,
            request_timeout=1,
            handshake_timeout=2,
        )
    )
    fails = [
        f for f in report.findings if f.check == "tool_calls" and f.severity == "fail"
    ]
    assert fails, report.to_text(verbose=True)
    match = re.search(r"\((\d+\.\d+)s\)", fails[0].title)
    assert match, fails[0].title
    assert float(match.group(1)) < 10, fails[0].title  # ~1s, not 30s


def test_write_and_shutdown_timeouts_flow_through_run():
    report, code = run(
        make_opts(cmd=fixture_cmd("healthy.py"), write_timeout=2, shutdown_timeout=1)
    )
    assert code == 0, report.to_text(verbose=True)


def test_usage_error_bad_env_and_header():
    with pytest.raises(SystemExit) as ei:
        main(["--env", "NOVALUE", "echo"])
    assert ei.value.code == 2
    with pytest.raises(SystemExit) as ei:
        main(["--header", "NOVALUE", "--url", "http://localhost:9"])
    assert ei.value.code == 2


def test_start_transport_error_server_side_is_exit_1(monkeypatch):
    import mcp_smoke.cli as cli_module
    from mcp_smoke.transports import TransportError

    class BoomStdio:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            exc = TransportError("server exited immediately")
            exc.server_side = True
            raise exc

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "StdioTransport", BoomStdio)
    report, code = run(make_opts(cmd="dummy"))
    assert code == 1
    assert any(f.check == "startup" for f in report.findings)


def test_start_transport_error_infra_is_exit_2(monkeypatch):
    import mcp_smoke.cli as cli_module
    from mcp_smoke.transports import TransportError

    class BoomStdio:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise TransportError("could not spawn")

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "StdioTransport", BoomStdio)
    _report, code = run(make_opts(cmd="dummy"))
    assert code == 2


def test_main_keyboard_interrupt_is_130(monkeypatch, capsys):
    import mcp_smoke.cli as cli_module

    def _boom(opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "run", _boom)
    assert main(["--no-call", "--cmd", "echo"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_main_unexpected_error_json_and_text(monkeypatch, capsys):
    import json as jsonlib

    import mcp_smoke.cli as cli_module

    def _boom(opts):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli_module, "run", _boom)
    assert main(["--no-call", "--json", "--cmd", "echo"]) == 2
    out = capsys.readouterr().out
    assert jsonlib.loads(out)["findings"][0]["check"] == "internal"
    assert main(["--no-call", "--cmd", "echo"]) == 2
    assert "internal error" in capsys.readouterr().err
