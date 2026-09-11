"""Transports: move JSON-RPC messages between mcp-smoke and a server.

Two transports:
- StdioTransport: spawns ``cmd`` as a subprocess and speaks newline-delimited
  JSON-RPC 2.0 over its stdin/stdout. Any stdout line that is not valid
  JSON-RPC is recorded as *pollution* -- the classic silent MCP failure where a
  stray ``print``/``console.log`` corrupts the protocol stream.
- HttpTransport: Streamable HTTP via urllib (POST JSON-RPC, accept SSE or JSON).
"""
from __future__ import annotations

import json
import queue
import shlex
import subprocess
import threading
import urllib.request
import urllib.error


class TransportError(RuntimeError):
    """Raised when the transport itself is broken (crash, timeout, HTTP error)."""


class StdioTransport:
    def __init__(self, cmd, cwd=None, env=None):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.proc = None
        self._responses = {}          # id -> queue.Queue
        self._responses_lock = threading.Lock()
        self._pollution = []          # stdout lines that are not JSON-RPC
        self._notifications = []
        self._stderr_lines = []
        self._reader = None
        self._stderr_reader = None
        self._closed = False

    # -- lifecycle ------------------------------------------------------
    def start(self, startup_timeout=10.0):
        """Spawn the server process. Raises TransportError if it dies at once."""
        args = shlex.split(self.cmd)
        try:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise TransportError(f"could not spawn {self.cmd!r}: {exc}")
        # Fast-fail: did it die immediately?
        try:
            rc = self.proc.wait(timeout=0.3)
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
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr_thread, daemon=True
        )
        self._stderr_reader.start()

    def _drain_stderr(self):
        try:
            _, err = self.proc.communicate(timeout=0.5)
            return err or ""
        except Exception:
            return ""

    def _read_stderr_thread(self):
        try:
            for line in self.proc.stderr:
                self._stderr_lines.append(line.rstrip("\n"))
        except Exception:
            pass

    @property
    def stderr_text(self):
        return "\n".join(self._stderr_lines)

    # -- reading --------------------------------------------------------
    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._pollution.append(line)
                    continue
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    self._pollution.append(line)
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    with self._responses_lock:
                        q = self._responses.get(msg["id"])
                    if q is not None:
                        q.put(msg)
                else:
                    self._notifications.append(msg)
        except Exception:
            pass

    @property
    def pollution(self):
        """Stdout lines that were not valid JSON-RPC (protocol corruption)."""
        return list(self._pollution)

    @property
    def notifications(self):
        return list(self._notifications)

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.poll() if self.proc else None

    # -- writing --------------------------------------------------------
    def send_raw(self, payload):
        if not self.is_alive():
            raise TransportError("server process is not alive")
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TransportError(f"failed writing to server stdin: {exc}")

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(msg)

    _next_id = 0
    _id_lock = threading.Lock()

    def request(self, method, params=None, timeout=10.0):
        """Send a JSON-RPC request, wait for the matching response."""
        with StdioTransport._id_lock:
            StdioTransport._next_id += 1
            req_id = StdioTransport._next_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        q: queue.Queue = queue.Queue()
        with self._responses_lock:
            self._responses[req_id] = q
        try:
            self.send_raw(msg)
            try:
                resp = q.get(timeout=timeout)
            except queue.Empty:
                if not self.is_alive():
                    raise TransportError(
                        f"server died while waiting for {method!r} "
                        f"(exit {self.exit_code()})"
                    )
                raise TransportError(
                    f"timed out after {timeout}s waiting for {method!r}"
                )
        finally:
            with self._responses_lock:
                self._responses.pop(req_id, None)
        if "error" in resp:
            err = resp["error"]
            return _RpcError(err.get("code"), err.get("message"), err.get("data"))
        return resp.get("result")

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc and self.is_alive():
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
        except Exception:
            pass


class _RpcError:
    """A JSON-RPC error response (not a transport failure)."""

    def __init__(self, code, message, data=None):
        self.code = code
        self.message = message
        self.data = data

    def __repr__(self):
        return f"_RpcError(code={self.code}, message={self.message!r})"


class HttpTransport:
    """Streamable HTTP transport (POST JSON-RPC; handles SSE or plain JSON)."""

    def __init__(self, url, headers=None):
        self.url = url
        self.headers = dict(headers or {})
        self._next_id = 0

    def _post(self, payload, timeout):
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.headers)
        req = urllib.request.Request(self.url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise TransportError(f"HTTP {exc.code} from {self.url}: {exc.reason}")
        except OSError as exc:
            raise TransportError(f"HTTP request failed: {exc}")
        if "text/event-stream" in ctype:
            return self._parse_sse(body)
        body = body.strip()
        if not body:
            return None  # 202 Accepted, no content (notification)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise TransportError(f"non-JSON HTTP response: {body[:200]!r}")

    @staticmethod
    def _parse_sse(body):
        """Extract JSON-RPC messages from an SSE stream; return list."""
        messages = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    try:
                        messages.append(json.loads(data))
                    except json.JSONDecodeError:
                        pass
        return messages

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._post(msg, timeout=10.0)

    def request(self, method, params=None, timeout=10.0):
        self._next_id += 1
        req_id = self._next_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
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
            return _RpcError(err.get("code"), err.get("message"), err.get("data"))
        return resp.get("result")

    # Uniform surface with StdioTransport for the check runner.
    @property
    def pollution(self):
        return []

    def is_alive(self):
        return True

    def close(self):
        pass
