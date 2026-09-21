"""Transport-level tests: stdio process control + HTTP edge cases."""

import json
import threading
import time
from contextlib import suppress
from typing import ClassVar

import pytest
from conftest import fixture_cmd

from mcp_smoke.transports import (
    HttpTransport,
    StdioTransport,
    TransportError,
    _RpcError,
)


def start(cmd):
    t = StdioTransport(cmd)
    t.start()
    return t


def test_pollution_is_reported_but_response_parses():
    t = start(fixture_cmd("polluting.py"))
    try:
        result = t.request("initialize", {}, timeout=5)
        assert result["serverInfo"]["name"] == "polluting-server"
        assert len(t.pollution) == 1
        assert "Starting fixture server" in t.pollution[0]
    finally:
        t.close()


def test_rpc_error_is_returned_not_raised():
    t = start(fixture_cmd("healthy.py"))
    try:
        result = t.request("nope/method", {}, timeout=5)
        assert isinstance(result, _RpcError)
        assert result.code == -32601
    finally:
        t.close()


def test_close_is_idempotent_and_reaps():
    t = start(fixture_cmd("healthy.py"))
    proc = t.proc
    t.close()
    t.close()
    assert proc.poll() is not None


def test_ignores_sigterm_still_gets_killed_and_reaped():
    t = start(fixture_cmd("ignores_sigterm.py"))
    t.request("initialize", {}, timeout=5)
    t.close()
    assert t.proc is not None and t.proc.poll() is not None


def test_bad_command_is_clean_transport_error_not_traceback():
    with pytest.raises(TransportError, match="could not spawn"):
        start("definitely-not-a-real-command-xyz")


def test_empty_command_is_clean_error():
    with pytest.raises(TransportError, match="empty"):
        start("")


def test_unreachable_host_becomes_transport_error():
    t = HttpTransport("http://127.0.0.1:1/mcp")
    with pytest.raises(TransportError):
        t.request("ping", timeout=2)


def test_invalid_url_is_clean_error():
    t = HttpTransport("not-a-url")
    with pytest.raises(TransportError, match="invalid URL"):
        t.request("ping", timeout=2)


def test_http_error_status_surfaces_cleanly():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"server blew up")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_port}/mcp")
        with pytest.raises(TransportError, match="HTTP 500"):
            t.request("ping", timeout=5)
    finally:
        srv.shutdown()


def test_http_sse_stream_returns_matching_response():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            req_id = json.loads(body)["id"]
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: ping\ndata: ignored\n\n")
            self.wfile.write(b"data: " + payload.encode() + b"\n\n")
            self.wfile.write(b"data: " + payload.encode() + b"\n\n")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_port}/mcp")
        result = t.request("ping", timeout=5)
        assert result == {"ok": True}
    finally:
        srv.shutdown()


def test_http_sse_stream_without_match_is_clean_error():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: hello\n\n")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_port}/mcp")
        with pytest.raises(TransportError, match="no matching SSE response"):
            t.request("ping", timeout=5)
    finally:
        srv.shutdown()


def test_http_malformed_json_is_clean_error():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"this is not json")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_port}/mcp")
        with pytest.raises(TransportError, match="non-JSON HTTP response"):
            t.request("ping", timeout=5)
    finally:
        srv.shutdown()


def test_http_empty_body_is_clean_error():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_port}/mcp")
        with pytest.raises(TransportError, match="empty"):
            t.request("ping", timeout=5)
    finally:
        srv.shutdown()


def test_notify_to_dead_server_is_silent():
    t = StdioTransport(fixture_cmd("exits_immediately.py"))
    # expected: it died before the handshake
    with suppress(TransportError):
        t.start()
    t.notify("notifications/initialized")  # must not raise
    t.close()


def test_dead_server_request_is_clean_transport_error():
    t = StdioTransport(fixture_cmd("exits_immediately.py"))
    # expected: it died before the handshake
    with suppress(TransportError):
        t.start()
    with pytest.raises(TransportError, match="not alive"):
        t.request("ping", timeout=2)
    t.close()


def test_notification_buffer_is_bounded():
    # The property returns a defensive copy; the internal buffer (fed by the
    # reader thread) caps at _MAX_STORED_LINES.
    t = StdioTransport(fixture_cmd("healthy.py"))
    for _ in range(300):
        t._record_notification({"method": "x"})
    assert len(t.notifications) == 200
    assert t._notifications_dropped == 100
    t.notifications.clear()  # mutating the copy must not touch the buffer
    assert len(t.notifications) == 200
    t.close()


def test_blocked_stdin_write_times_out():
    # A reader that stops reading stdin: select-based writes must not block
    # forever. 1.5 MB at a 64 KiB pipe buffer => write would block for an
    # eternity; the timeout must fire instead.
    cmd = fixture_cmd("slow_tool.py")
    t = StdioTransport(cmd)
    t.start()
    try:
        t.request("initialize", {}, timeout=5)
        with pytest.raises(TransportError, match="timed out"):
            t.request(
                "tools/call",
                {"name": "slow", "arguments": {"x": "y" * (1024 * 512)}},
                timeout=1.0,
            )
    finally:
        t.close()


def test_shutdown_timeout_is_configurable():
    # ignores_sigterm.py needs SIGKILL: default staging is 3s + 3s, but with
    # shutdown_timeout=0.5 the whole close must finish well under that.
    t = StdioTransport(fixture_cmd("ignores_sigterm.py"), shutdown_timeout=0.5)
    t.start()
    t.request("initialize", {}, timeout=5)
    start = time.monotonic()
    t.close()
    elapsed = time.monotonic() - start
    assert t.proc is not None and t.proc.poll() is not None
    assert elapsed < 4.0


def test_write_timeout_defaults_and_overrides():
    t = StdioTransport(fixture_cmd("healthy.py"))
    assert t._write_timeout == 10.0
    t2 = StdioTransport(fixture_cmd("healthy.py"), write_timeout=2.5)
    assert t2._write_timeout == 2.5


def test_send_raw_uses_write_timeout_when_none_given():
    t = StdioTransport(fixture_cmd("healthy.py"), write_timeout=5.0)
    t.start()
    try:
        # No explicit timeout: the write must succeed under the configured
        # write timeout.
        t.send_raw({"jsonrpc": "2.0", "id": 999, "method": "ping"})
    finally:
        t.close()


# --- Defensive branch coverage -----------------------------------------------


def test_start_bad_command_raises_transport_error():
    from mcp_smoke.transports import StdioTransport, TransportError

    with pytest.raises(TransportError, match="could not spawn"):
        StdioTransport("definitely-not-a-real-command-xyz").start()


def test_split_cmd_rejects_empty_and_unparseable():
    from mcp_smoke.transports import StdioTransport, TransportError

    with pytest.raises(TransportError, match="empty --cmd"):
        StdioTransport("")._split_cmd()
    with pytest.raises(TransportError, match="could not parse"):
        StdioTransport("'unclosed-quote")._split_cmd()


def test_drain_stderr_without_proc_is_empty():
    from mcp_smoke.transports import StdioTransport

    assert StdioTransport("echo hi")._drain_stderr() == ""


def test_pollution_and_notification_caps_drop_with_counter():
    from mcp_smoke.transports import StdioTransport

    t = StdioTransport("echo hi")
    for i in range(201):
        t._record_pollution(f"line {i}")
        t._record_notification({"n": i})
    assert len(t.pollution) == 200
    assert t.pollution_dropped == 1
    assert t._notifications_dropped == 1


def test_handle_stdout_line_branches():
    from mcp_smoke.transports import StdioTransport

    t = StdioTransport("echo hi")
    t._handle_stdout_line("   ", truncated=False)
    assert t.pollution == []
    t._handle_stdout_line("x" * 10, truncated=True)
    assert "line truncated" in t.pollution[-1]
    t._handle_stdout_line("not json", truncated=False)
    assert t.pollution[-1] == "not json"
    t._handle_stdout_line('{"jsonrpc": "1.0", "id": 1}', truncated=False)
    assert t.pollution[-1] == '{"jsonrpc": "1.0", "id": 1}'
    # a notification (no id) is recorded, not treated as pollution
    t._handle_stdout_line('{"jsonrpc": "2.0", "method": "ping"}', truncated=False)
    assert t._notifications and t._notifications[-1]["method"] == "ping"


def test_read_stdout_without_proc_returns():
    from mcp_smoke.transports import StdioTransport

    StdioTransport("echo hi")._read_stdout()  # must not raise


def test_send_raw_on_dead_transport_raises():
    from mcp_smoke.transports import StdioTransport, TransportError

    with pytest.raises(TransportError, match="not alive"):
        StdioTransport("echo hi").send_raw({"jsonrpc": "2.0", "method": "x"})


def test_notify_with_params_on_dead_transport_never_raises():
    from mcp_smoke.transports import StdioTransport

    StdioTransport("echo hi").notify("m", params={"a": 1})  # suppressed, no raise


def test_close_without_proc_returns():
    from mcp_smoke.transports import StdioTransport

    StdioTransport("echo hi").close()  # must not raise


def test_rpc_error_repr():
    from mcp_smoke.transports import _RpcError

    assert "code=-1" in repr(_RpcError(-1, "bad"))


def test_parse_sse_skips_malformed_payloads():
    from mcp_smoke.transports import HttpTransport

    msgs = HttpTransport._parse_sse(
        'data: {"jsonrpc": "2.0", "id": 1}\ndata: not-json{\ndata: [DONE]\n: comment\n'
    )
    assert msgs == [{"jsonrpc": "2.0", "id": 1}]


def _http_with_post(monkeypatch, payload):
    from mcp_smoke.transports import HttpTransport

    t = HttpTransport("http://localhost:9/mcp")
    monkeypatch.setattr(t, "_post", lambda msg, timeout: payload)
    return t


def test_http_request_empty_response_raises(monkeypatch):
    from mcp_smoke.transports import TransportError

    t = _http_with_post(monkeypatch, None)
    with pytest.raises(TransportError, match="empty response"):
        t.request("ping")


def test_http_request_sse_without_match_raises(monkeypatch):
    from mcp_smoke.transports import TransportError

    t = _http_with_post(monkeypatch, [{"jsonrpc": "2.0", "id": 12345}])
    with pytest.raises(TransportError, match="no matching SSE response"):
        t.request("ping")


def test_http_request_malformed_jsonrpc_raises(monkeypatch):
    from mcp_smoke.transports import TransportError

    t = _http_with_post(monkeypatch, {"nope": True})
    with pytest.raises(TransportError, match="malformed JSON-RPC"):
        t.request("ping")


def test_http_request_non_dict_error_becomes_rpc_error(monkeypatch):
    from mcp_smoke.transports import HttpTransport, _RpcError

    t = HttpTransport("http://localhost:9/mcp")
    monkeypatch.setattr(
        t, "_post", lambda msg, timeout: {"jsonrpc": "2.0", "id": 1, "error": "boom"}
    )
    err = t.request("ping")
    assert isinstance(err, _RpcError) and err.code is None and "boom" in err.message


def test_http_post_connection_failure(monkeypatch):
    import urllib.request

    from mcp_smoke.transports import HttpTransport, TransportError

    def _raise(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(TransportError, match=r"HTTP request to .* failed"):
        HttpTransport("http://localhost:9/mcp")._post({"x": 1}, timeout=1.0)


def test_http_post_non_json_response(monkeypatch):
    import urllib.request

    from mcp_smoke.transports import HttpTransport, TransportError

    class FakeResp:
        headers: ClassVar[dict] = {"Content-Type": "text/html"}

        def read(self, n=-1):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp())
    with pytest.raises(TransportError, match="non-JSON HTTP response"):
        HttpTransport("http://localhost:9/mcp")._post({"x": 1}, timeout=1.0)


def test_http_post_oversized_body_refused(monkeypatch):
    import urllib.request

    from mcp_smoke.transports import _MAX_HTTP_BODY, HttpTransport, TransportError

    class FakeResp:
        headers: ClassVar[dict] = {"Content-Type": "application/json"}

        def read(self, n=-1):
            return b"x" * (_MAX_HTTP_BODY + 1)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp())
    with pytest.raises(TransportError, match="exceeded"):
        HttpTransport("http://localhost:9/mcp")._post({"x": 1}, timeout=1.0)
