"""Minimal MCP stdio client: newline-delimited JSON-RPC over a subprocess."""

from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Deque, Dict, List, Optional

PROTOCOL_VERSION = "2025-11-25"


class ServerExited(Exception):
    def __init__(self, code: Optional[int]):
        super().__init__(f"server exited (code {code})")
        self.code = code


class CallTimeout(Exception):
    pass


class RpcError(Exception):
    def __init__(self, error: Any):
        self.error = error if isinstance(error, dict) else {"code": None, "message": str(error)}
        super().__init__(str(self.error.get("message")))

    @property
    def code(self):
        return self.error.get("code")


class StdioServer:
    def __init__(self, cmd: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.cmd = cmd
        self.env = env
        self.cwd = cwd
        self.proc: Optional[subprocess.Popen] = None
        self.inbox: "queue.Queue[Any]" = queue.Queue()
        self.noise: List[str] = []  # non-JSON-RPC lines seen on stdout
        self.stderr_tail: Deque[str] = collections.deque(maxlen=40)
        self._next_id = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ process
    def start(self) -> None:
        env = dict(os.environ)
        env.update(self.env or {})
        self.inbox = queue.Queue()
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self.cwd,
        )
        threading.Thread(target=self._read_stdout, args=(self.proc, self.inbox), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True).start()

    def _read_stdout(self, proc: subprocess.Popen, inbox: "queue.Queue[Any]") -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.noise.append(line)
                continue
            if not isinstance(msg, (dict, list)):
                self.noise.append(line)
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                inbox.put(m)
        inbox.put(None)  # EOF marker

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip("\r\n"))

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        p = self.proc
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None

    def kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
            except Exception:
                pass
            self.proc = None

    # ------------------------------------------------------------ JSON-RPC
    def _send(self, msg: Dict[str, Any]) -> None:
        if not self.alive():
            raise ServerExited(self.proc.returncode if self.proc else None)
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            assert self.proc is not None and self.proc.stdin is not None
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ServerExited(self._wait_code())

    def _wait_code(self) -> Optional[int]:
        if self.proc is None:
            return None
        try:
            return self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return None

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Any:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CallTimeout(f"no response to {method} within {timeout:g}s")
            try:
                m = self.inbox.get(timeout=min(left, 0.25))
            except queue.Empty:
                if not self.alive():
                    # drain anything written right before exit
                    try:
                        m = self.inbox.get(timeout=0.2)
                    except queue.Empty:
                        raise ServerExited(self._wait_code())
                else:
                    continue
            if m is None:
                raise ServerExited(self._wait_code())
            if not isinstance(m, dict):
                continue
            if "method" in m and "id" in m:  # server -> client request
                self._answer_server_request(m)
                continue
            if "method" in m:  # notification (logging, progress, list_changed)
                continue
            if m.get("id") != rid:
                continue
            if "error" in m:
                raise RpcError(m["error"])
            return m.get("result")

    def _answer_server_request(self, m: Dict[str, Any]) -> None:
        if m.get("method") == "ping":
            reply: Dict[str, Any] = {"jsonrpc": "2.0", "id": m["id"], "result": {}}
        elif m.get("method") == "roots/list":
            reply = {"jsonrpc": "2.0", "id": m["id"], "result": {"roots": []}}
        else:
            reply = {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported by mcpshake"}}
        try:
            self._send(reply)
        except ServerExited:
            pass

    # ------------------------------------------------------------ MCP
    def initialize(self, timeout: float = 15.0) -> Dict[str, Any]:
        res = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcpshake", "version": _version()},
            },
            timeout=timeout,
        )
        self.notify("notifications/initialized")
        return res if isinstance(res, dict) else {}

    def list_tools(self, timeout: float = 15.0) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(100):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=timeout)
            if not isinstance(res, dict):
                break
            tools += [t for t in res.get("tools") or [] if isinstance(t, dict)]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools


def _version() -> str:
    from . import __version__

    return __version__
