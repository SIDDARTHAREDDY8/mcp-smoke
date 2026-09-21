"""Transports: move JSON-RPC messages between mcp-smoke and a server.

Two transports:
- StdioTransport: spawns ``cmd`` as a subprocess and speaks newline-delimited
  JSON-RPC 2.0 over its stdin/stdout. Any stdout line that is not valid
  JSON-RPC is recorded as *pollution* -- the classic silent MCP failure where a
  stray ``print``/``console.log`` corrupts the protocol stream.
- HttpTransport: Streamable HTTP via urllib (POST JSON-RPC, accept SSE or JSON).

Both transports are stdlib-only and defensive: oversized lines are truncated,
stored output is capped, every I/O path has a timeout, and every failure
surfaces as TransportError -- never a traceback, never an unreaped child.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import select
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator


class TransportError(RuntimeError):
    """Raised when the transport itself is broken (crash, timeout, HTTP error).

    ``server_side`` marks failures caused by the server under test (e.g. it
    exited immediately) as opposed to mcp-smoke-side problems (bad command,
    unreachable URL). The CLI maps server-side failures to exit code 1 and
    mcp-smoke-side failures to exit code 2.
    """

    server_side: bool = False


class Transport(Protocol):
    """Structural interface shared by StdioTransport and HttpTransport."""

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> Any:
        """Send a request and wait for the matching response (or timeout)."""

    def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Send a fire-and-forget notification. Never raises."""

    def is_alive(self) -> bool:
        """Return True when the server is still reachable."""

    def exit_code(self) -> int | None:
        """Return the server's exit code, or None if it is still running."""

    @property
    def pollution(self) -> list[str]:
        """Stdout lines that were not valid JSON-RPC (protocol corruption)."""

    @property
    def pollution_dropped(self) -> int:
        """Pollution lines not stored because the buffer cap was reached."""

    def close(self) -> None:
        """Shut down the transport and release all resources."""


# Lifecycle timing for StdioTransport.
_IMMEDIATE_EXIT_GRACE = 0.3  # wait this long to detect instant startup crashes
_SHUTDOWN_GRACE = 3.0  # wait after stdin EOF before SIGTERM
_TERMINATE_GRACE = 3.0  # wait after SIGTERM before SIGKILL
_REAP_GRACE = 1.0  # final wait to reap the child (never leave a zombie)

# Defensive caps: a misbehaving server must not be able to exhaust our memory.
_MAX_LINE_LEN = 64 * 1024  # truncate any single stdout/stderr line beyond this
_MAX_STORED_LINES = 200  # cap pollution / stderr / notification buffers
_MAX_HTTP_BODY = 16 * 1024 * 1024  # refuse HTTP bodies larger than this
_DEFAULT_HTTP_TIMEOUT = 10.0  # fallback when a caller passes no timeout


def _iter_capped_lines(
    stream: Any, limit: int = _MAX_LINE_LEN
) -> Iterator[tuple[str, bool]]:
    """Yield (line, truncated) pairs from a text stream.

    Lines longer than *limit* are truncated and the rest of the line is
    discarded, so a single giant line can never exhaust memory. A final line
    without a trailing newline (EOF) is still yielded.
    """
    while True:
        chunks: list[str] = []
        total = 0
        while True:
            chunk = stream.readline(limit + 1)
            if chunk == "":
                if chunks:
                    yield "".join(chunks), False
                return
            if chunk.endswith("\n"):
                chunks.append(chunk[:-1])
                total += len(chunk) - 1
                text = "".join(chunks)
                yield (text[:limit], True) if total > limit else (text, False)
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                # Discard the rest of this over-long line.
                while True:
                    rest = stream.readline(limit + 1)
                    if rest == "" or rest.endswith("\n"):
                        break
                yield "".join(chunks)[:limit], True
                break


class StdioTransport:
    """Speak newline-delimited JSON-RPC over a subprocess's stdin/stdout."""

    def __init__(
        self,
        cmd: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        write_timeout: float = 10.0,
        shutdown_timeout: float | None = None,
    ) -> None:
        """Configure the server command (spawned by start()).

        write_timeout bounds stdin writes; shutdown_timeout (or the staged
        3s/3s defaults when None) bounds each shutdown wait stage.
        """
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._write_timeout = write_timeout
        self._shutdown_grace = (
            shutdown_timeout if shutdown_timeout is not None else _SHUTDOWN_GRACE
        )
        self._terminate_grace = (
            shutdown_timeout if shutdown_timeout is not None else _TERMINATE_GRACE
        )
        self.proc: subprocess.Popen[str] | None = None
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._responses_lock = threading.Lock()
        self._pollution: list[str] = []
        self._pollution_dropped = 0
        self._notifications: list[dict[str, Any]] = []
        self._notifications_dropped = 0
        self._stderr_lines: list[str] = []
        self._stderr_dropped = 0
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._closed = False
        # Set once we have observed the server's death through a broken
        # stdin pipe or stdout EOF. There is a kernel window where a dying
        # process has closed its fds but waitpid() still reports it alive,
        # so EPIPE can precede poll() noticing; this flag makes is_alive()
        # deterministic instead of racy. Monotonic (False -> True) and only
        # ever set by the reader thread or the writer, read by the main
        # thread: safe under the GIL.
        self._server_gone = False

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Spawn the server process.

        Raises TransportError if it cannot start or dies immediately (with
        the stderr tail for diagnostics).
        """
        args = self._split_cmd()
        try:
            self.proc = subprocess.Popen(  # noqa: S603 -- --cmd is the user's own command; shell=False
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                text=True,
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise TransportError(f"could not spawn {self.cmd!r}: {exc}") from exc
        proc = self.proc
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            raise TransportError(f"could not open stdio pipes for {self.cmd!r}")
        try:
            # Writes must never block forever: a server that stops reading
            # stdin is handled by the select-based write loop in send_raw.
            os.set_blocking(proc.stdin.fileno(), False)
        except OSError as exc:
            raise TransportError(f"could not configure stdin pipe: {exc}") from exc
        # Fast-fail: did it die immediately (bad import, missing env, ...)?
        try:
            rc = proc.wait(timeout=_IMMEDIATE_EXIT_GRACE)
        except subprocess.TimeoutExpired:
            rc = None
        if rc is not None:
            err = self._drain_stderr()
            exc = TransportError(
                f"server exited immediately with code {rc}"
                + (f"; stderr: {err.strip()[:500]}" if err.strip() else "")
            )
            exc.server_side = True  # the server failed, not mcp-smoke
            raise exc
        self._reader = threading.Thread(
            target=self._read_stdout, daemon=True, name="mcp-smoke-stdout"
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, daemon=True, name="mcp-smoke-stderr"
        )
        self._stderr_reader.start()

    def _split_cmd(self) -> list[str]:
        try:
            args = shlex.split(self.cmd)
        except ValueError as exc:
            raise TransportError(f"could not parse --cmd {self.cmd!r}: {exc}") from exc
        if not args:
            raise TransportError("empty --cmd: nothing to launch")
        return args

    def _drain_stderr(self) -> str:
        proc = self.proc
        if proc is None:
            return ""
        try:
            _, err = proc.communicate(timeout=0.5)
        except Exception:  # noqa: BLE001 -- diagnostics must never crash the suite
            return ""
        else:
            return err or ""

    def _record_pollution(self, line: str) -> None:
        if len(self._pollution) < _MAX_STORED_LINES:
            self._pollution.append(line)
        else:
            self._pollution_dropped += 1

    def _record_notification(self, msg: dict[str, Any]) -> None:
        if len(self._notifications) < _MAX_STORED_LINES:
            self._notifications.append(msg)
        else:
            self._notifications_dropped += 1

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw_line, truncated in _iter_capped_lines(proc.stderr):
                line = raw_line
                if truncated:
                    line += f" ...[line truncated at {_MAX_LINE_LEN} chars]"
                if len(self._stderr_lines) < _MAX_STORED_LINES:
                    self._stderr_lines.append(line)
                else:
                    self._stderr_dropped += 1
        except Exception:  # noqa: BLE001, S110 -- reader thread: stream torn down is normal
            pass

    @property
    def stderr_text(self) -> str:
        """Captured server stderr (capped)."""
        return "\n".join(self._stderr_lines)

    @property
    def stderr_dropped(self) -> int:
        """Stderr lines not stored because the buffer cap was reached."""
        return self._stderr_dropped

    # -- reading --------------------------------------------------------
    def _handle_stdout_line(self, line: str, *, truncated: bool) -> None:
        """Classify one stdout line: response, notification, or pollution."""
        if not line.strip():
            return
        if truncated:
            self._record_pollution(
                line + f" ...[line truncated at {_MAX_LINE_LEN} chars]"
            )
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._record_pollution(line)
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._record_pollution(line)
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            with self._responses_lock:
                q = self._responses.get(msg["id"])
            if q is not None:
                q.put(msg)
        else:
            self._record_notification(msg)

    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line, truncated in _iter_capped_lines(proc.stdout):
                self._handle_stdout_line(line, truncated=truncated)
        except Exception:  # noqa: BLE001, S110 -- reader thread: stream torn down is normal
            pass
        else:
            # Clean EOF on stdout: the server is gone, even if waitpid() has
            # not observed it yet (see _server_gone).
            self._server_gone = True

    @property
    def pollution(self) -> list[str]:
        """Stdout lines that were not valid JSON-RPC (protocol corruption)."""
        return list(self._pollution)

    @property
    def pollution_dropped(self) -> int:
        """Pollution lines not stored because the buffer cap was reached."""
        return self._pollution_dropped

    @property
    def notifications(self) -> list[dict[str, Any]]:
        """Server-initiated JSON-RPC notifications seen on stdout."""
        return list(self._notifications)

    def is_alive(self) -> bool:
        """Return True when the child process is still running."""
        return (
            self.proc is not None and not self._server_gone and self.proc.poll() is None
        )

    def exit_code(self) -> int | None:
        """Return the child's exit code, or None if still running."""
        return self.proc.poll() if self.proc else None

    # -- writing --------------------------------------------------------
    def send_raw(self, payload: dict[str, Any], timeout: float | None = None) -> None:
        """Write one JSON-RPC message to the server's stdin.

        Never blocks forever: if the server stops reading stdin, this raises
        TransportError after *timeout* seconds (default: the transport's
        write timeout) instead of hanging the suite.
        """
        if timeout is None:
            timeout = self._write_timeout
        proc = self.proc
        if proc is None or not self.is_alive():
            raise TransportError("server process is not alive")
        stdin = proc.stdin
        if stdin is None:
            raise TransportError("server stdin is closed")
        data = (json.dumps(payload) + "\n").encode()
        fd = stdin.fileno()
        view = memoryview(data)
        deadline = time.monotonic() + timeout
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(
                    "timed out writing to server stdin "
                    "(is the server still reading stdin?)"
                )
            try:
                _, ready, _ = select.select([], [fd], [], remaining)
            except (OSError, ValueError) as exc:
                raise TransportError(f"failed writing to server stdin: {exc}") from exc
            if not ready:
                raise TransportError(
                    "timed out writing to server stdin "
                    "(is the server still reading stdin?)"
                )
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                continue
            except OSError as exc:
                # EPIPE/EBADF: the server is gone (or going), even if
                # waitpid() has not observed it yet (see _server_gone).
                self._server_gone = True
                raise TransportError(f"failed writing to server stdin: {exc}") from exc
            view = view[written:]

    def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Fire-and-forget notification. Never raises.

        If the server is dead or gone, the notification is simply dropped
        (there is nothing useful to report to the caller at this point).
        """
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        # A notification must never crash the suite: broken pipe, dead
        # server, and timeouts are reported via is_alive(), so delivery
        # failures are simply dropped.
        with suppress(Exception):
            self.send_raw(msg, timeout=timeout)

    _next_id: ClassVar[int] = 0
    _id_lock: ClassVar[threading.Lock] = threading.Lock()

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> Any:
        """Send a JSON-RPC request, wait for the matching response."""
        with StdioTransport._id_lock:
            StdioTransport._next_id += 1
            req_id = StdioTransport._next_id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._responses_lock:
            self._responses[req_id] = q
        try:
            # The stdin write has its own budget (write timeout); *timeout*
            # below only bounds waiting for the response.
            self.send_raw(msg)
            # Poll rather than one long blocking wait so a server that dies
            # mid-request is reported promptly instead of costing the full
            # timeout (is_alive() also consults _server_gone, which the
            # reader thread sets on stdout EOF).
            deadline = time.monotonic() + timeout
            while True:
                try:
                    resp = q.get(timeout=0.05)
                    break
                except queue.Empty:
                    pass
                if not self.is_alive():
                    raise TransportError(
                        f"server died while waiting for {method!r} "
                        f"(exit {self.exit_code()})"
                    ) from None
                if time.monotonic() >= deadline:
                    raise TransportError(
                        f"timed out after {timeout}s waiting for {method!r}"
                    ) from None
        finally:
            with self._responses_lock:
                self._responses.pop(req_id, None)
        if "error" in resp:
            err = resp["error"]
            if not isinstance(err, dict):
                # Malformed error shape (e.g. "error": "boom"): still a
                # protocol error, not a reason to crash.
                err = {"code": None, "message": str(err)[:500]}
            return _RpcError(err.get("code"), err.get("message"), err.get("data"))
        return resp.get("result")

    def close(self) -> None:
        """Terminate the server and reap it.

        Never leaves a zombie, even if interrupted: on KeyboardInterrupt the
        child is SIGKILLed and reaped before the interrupt propagates. Safe
        to call twice.
        """
        if self._closed:
            return
        self._closed = True
        proc = self.proc
        if proc is None:
            return
        try:
            self._shutdown_gracefully(proc)
        except KeyboardInterrupt:
            # Interrupted mid-shutdown: SIGKILL and reap before propagating.
            with suppress(Exception):
                if proc.poll() is None:
                    proc.kill()
            raise
        except Exception:  # noqa: BLE001, S110 -- close() must never raise
            pass
        finally:
            # Reap the child so it can never become a zombie.
            with suppress(Exception):
                proc.wait(timeout=_REAP_GRACE)

    def _shutdown_gracefully(self, proc: subprocess.Popen[str]) -> None:
        """Close stdin, then escalate: wait -> SIGTERM -> SIGKILL."""
        if proc.poll() is not None:
            return
        if proc.stdin is not None:
            with suppress(OSError):
                proc.stdin.close()  # EOF: well-behaved servers exit on this
        try:
            proc.wait(timeout=self._shutdown_grace)
        except subprocess.TimeoutExpired:
            pass
        else:
            return
        with suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=self._terminate_grace)
        except subprocess.TimeoutExpired:
            pass
        else:
            return
        with suppress(OSError):
            proc.kill()  # SIGKILL: also stops SIGTERM-ignoring servers


class _RpcError:
    """A JSON-RPC error response (not a transport failure)."""

    def __init__(self, code: Any, message: Any, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data

    def __repr__(self) -> str:
        return f"_RpcError(code={self.code}, message={self.message!r})"


class HttpTransport:
    """Streamable HTTP transport (POST JSON-RPC; handles SSE or plain JSON)."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        """Configure the Streamable HTTP endpoint URL and extra headers."""
        self.url = url
        self.headers = dict(headers or {})
        self._next_id = 0

    def _post(self, payload: dict[str, Any], timeout: float) -> Any:
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.headers)
        try:
            req = urllib.request.Request(  # noqa: S310 -- stdlib-only: this IS the HTTP client
                self.url, data=data, headers=headers
            )
        except ValueError as exc:
            raise TransportError(f"invalid URL {self.url!r}: {exc}") from exc
        try:
            with urllib.request.urlopen(  # noqa: S310 -- stdlib-only: this IS the HTTP client
                req, timeout=timeout
            ) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read(_MAX_HTTP_BODY + 1)
                body = raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise TransportError(
                f"HTTP {exc.code} from {self.url}: {exc.reason}"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            # OSError covers URLError/socket.timeout/connection failures;
            # HTTPException covers malformed HTTP responses (BadStatusLine...).
            raise TransportError(f"HTTP request to {self.url} failed: {exc}") from exc
        if len(raw) > _MAX_HTTP_BODY:
            raise TransportError(
                f"HTTP response exceeded {_MAX_HTTP_BODY} bytes; refusing to buffer it"
            )
        if "text/event-stream" in ctype:
            return self._parse_sse(body)
        body = body.strip()
        if not body:
            return None  # 202 Accepted, no content (notification)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise TransportError(f"non-JSON HTTP response: {body[:200]!r}") from None

    @staticmethod
    def _parse_sse(body: str) -> list[Any]:
        """Extract JSON-RPC messages from an SSE stream; return list."""
        messages: list[Any] = []
        for raw in body.splitlines():
            line = raw.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    # Malformed SSE payloads are skipped, not fatal.
                    with suppress(json.JSONDecodeError):
                        messages.append(json.loads(data))
        return messages

    def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Fire-and-forget notification. Never raises.

        Delivery failures are dropped, like the stdio transport.
        """
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        # A notification must never crash the suite; delivery failures
        # are dropped, like the stdio transport.
        with suppress(Exception):
            self._post(
                msg, timeout=timeout if timeout is not None else _DEFAULT_HTTP_TIMEOUT
            )

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> Any:
        """POST one JSON-RPC request and return its result."""
        self._next_id += 1
        req_id = self._next_id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        resp = self._post(msg, timeout=timeout)
        if resp is None:
            raise TransportError(f"empty response for {method!r}")
        if isinstance(resp, list):  # SSE batch
            for m in resp:
                if isinstance(m, dict) and m.get("id") == req_id:
                    resp = m
                    break
            else:
                raise TransportError(f"no matching SSE response for {method!r}")
        if not isinstance(resp, dict) or resp.get("jsonrpc") != "2.0":
            raise TransportError(f"malformed JSON-RPC response for {method!r}")
        if "error" in resp:
            err = resp["error"]
            if not isinstance(err, dict):
                err = {"code": None, "message": str(err)[:500]}
            return _RpcError(err.get("code"), err.get("message"), err.get("data"))
        return resp.get("result")

    # Uniform surface with StdioTransport for the check runner.
    @property
    def pollution(self) -> list[str]:
        """HTTP has no stdout stream, so there is never pollution."""
        return []

    @property
    def pollution_dropped(self) -> int:
        """HTTP has no stdout stream, so there is never pollution."""
        return 0

    def is_alive(self) -> bool:
        """HTTP is stateless; liveness is assessed per request."""
        return True

    def exit_code(self) -> int | None:
        """HTTP has no server process to report an exit code for."""
        return None

    def close(self) -> None:
        """Release the transport. Nothing to do for stateless HTTP."""
