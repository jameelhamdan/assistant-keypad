"""The private channel between the agent and its local clients (hook shim,
MCP shim, tray, CLI): a Unix socket (mode 0600) or a Windows named pipe only
the current user can open. Nothing listens on a TCP port.

Messages are a 4-byte big-endian length and a JSON object. Requests are
{"m": method, "p": path, "b": body}; replies {"s": status, "b": body}.
Standard library only: the hook shim imports this on every tool call."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from .dirs import data_dir

MAX_MSG = 4 << 20


class AgentNotRunning(ConnectionError):
    pass


class RequestError(Exception):
    """The agent answered with an error."""


class Running(Exception):
    """Another agent already serves this user."""


def _pipe_name() -> str:
    user = os.environ.get("USERNAME", "user").replace("\\", "-").replace(" ", "-")
    if home := os.environ.get("KEYPAD_HOME"):  # an isolated setup (tests, development) gets its own pipe
        import hashlib

        user += "-" + hashlib.sha1(home.encode()).hexdigest()[:8]
    return rf"\\.\pipe\keypad-agent-{user}"


def _sock_path() -> str:
    return os.path.join(data_dir(), "agent.sock")


# ---- framing -------------------------------------------------------------------------


def _pack(obj: Any) -> bytes:
    b = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return struct.pack(">I", len(b)) + b


def _unpack_len(hdr: bytes) -> int:
    (n,) = struct.unpack(">I", hdr)
    if n > MAX_MSG:
        raise ValueError("message too large")
    return n


# ---- client ----------------------------------------------------------------------------


class _Chan:
    """One client connection (socket or pipe file)."""

    def __init__(self, timeout: float | None):
        if sys.platform == "win32":
            deadline = time.monotonic() + 2
            while True:
                try:
                    self._f = open(_pipe_name(), "r+b", buffering=0)  # noqa: SIM115
                    break
                except FileNotFoundError as e:
                    raise AgentNotRunning(str(e)) from e
                except OSError as e:  # ERROR_PIPE_BUSY: all instances in use
                    if time.monotonic() > deadline:
                        raise AgentNotRunning(str(e)) from e
                    time.sleep(0.02)
            self._s = None
        else:
            self._s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._s.settimeout(timeout)
            try:
                self._s.connect(_sock_path())
            except OSError as e:
                self._s.close()
                raise AgentNotRunning(str(e)) from e

    def write(self, b: bytes) -> None:
        if self._s is not None:
            self._s.sendall(b)
        else:
            self._f.write(b)

    def read(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._s.recv(n - len(buf)) if self._s is not None else self._f.read(n - len(buf))
            if not chunk:
                raise ConnectionError("agent closed the connection")
            buf += chunk
        return bytes(buf)

    def close(self) -> None:
        (self._s or self._f).close()


def request(method: str, path: str, body: Any = None, timeout: float | None = 30) -> Any:
    """Sends a request to the agent and returns the reply body. Raises
    AgentNotRunning or RequestError (the agent's error message)."""
    ch = _Chan(timeout)
    try:
        ch.write(_pack({"m": method, "p": path, "b": body}))
        resp = json.loads(ch.read(_unpack_len(ch.read(4))))
    except (OSError, ValueError) as e:
        if isinstance(e, AgentNotRunning):
            raise
        raise RequestError(str(e)) from e
    finally:
        ch.close()
    if resp.get("s", 500) >= 300:
        err = resp.get("b")
        raise RequestError(err.get("error", "request failed") if isinstance(err, dict) else "request failed")
    return resp.get("b")


def alive() -> bool:
    try:
        _Chan(0.5).close()
        return True
    except AgentNotRunning:
        return False


# ---- server ---------------------------------------------------------------------------

Handler = Callable[[str, str, Any, Callable[[], bool]], tuple[int, Any]]
# handler(method, path, body, client_gone) -> (status, body); client_gone() turns
# True when the client hung up (e.g. Claude Code cancelled the hook).


def serve(handler: Handler, stop: threading.Event) -> None:
    """Serves requests until stop is set. Raises Running if another agent serves this user."""
    if alive():
        raise Running()
    if sys.platform == "win32":
        _serve_pipe(handler, stop)
    else:
        _serve_unix(handler, stop)


def _handle(handler: Handler, read: Callable[[int], bytes], write: Callable[[bytes], None],
            gone: Callable[[], bool]) -> None:
    try:
        req = json.loads(read(_unpack_len(read(4))))
        status, body = handler(str(req.get("m", "")), str(req.get("p", "")), req.get("b"), gone)
    except (OSError, ValueError, ConnectionError):
        return
    try:
        write(_pack({"s": status, "b": body}))
    except OSError:
        pass


def _serve_unix(handler: Handler, stop: threading.Event) -> None:
    import select

    p = _sock_path()
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    try:
        os.unlink(p)  # stale socket from a crashed agent
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)  # the socket is created 0600: this user only
    try:
        srv.bind(p)
    finally:
        os.umask(old)
    os.chmod(p, 0o600)
    srv.listen(64)
    srv.settimeout(0.5)

    def conn_thread(c: socket.socket) -> None:
        def read(n: int) -> bytes:
            buf = bytearray()
            while len(buf) < n:
                chunk = c.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("client closed")
                buf += chunk
            return bytes(buf)

        def gone() -> bool:
            try:
                r, _, _ = select.select([c], [], [], 0)
                return bool(r) and c.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

        try:
            c.settimeout(None)
            _handle(handler, read, c.sendall, gone)
        finally:
            c.close()

    try:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except TimeoutError:
                continue
            threading.Thread(target=conn_thread, args=(c,), daemon=True).start()
    finally:
        srv.close()
        try:
            os.unlink(p)
        except OSError:
            pass


def _serve_pipe(handler: Handler, stop: threading.Event) -> None:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]

    k32.CreateNamedPipeW.restype = wintypes.HANDLE
    k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    # Only the pipe's owner (this user) and SYSTEM may open it.
    sd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;GA;;;OW)(A;;GA;;;SY)", 1, ctypes.byref(sd), None):
        raise OSError(ctypes.get_last_error(), "security descriptor")
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)

    PIPE_ACCESS_DUPLEX, FIRST_INSTANCE = 0x3, 0x00080000
    PIPE_REJECT_REMOTE = 0x8
    INVALID = wintypes.HANDLE(-1).value
    ERROR_PIPE_CONNECTED, ERROR_BROKEN_PIPE = 535, 109
    first = True

    def conn_thread(h) -> None:
        def read(n: int) -> bytes:
            buf = ctypes.create_string_buffer(n)
            got = wintypes.DWORD()
            out = bytearray()
            while len(out) < n:
                if not k32.ReadFile(h, buf, n - len(out), ctypes.byref(got), None) or got.value == 0:
                    raise ConnectionError("client closed")
                out += buf.raw[: got.value]
            return bytes(out)

        def write(b: bytes) -> None:
            done = wintypes.DWORD()
            if not k32.WriteFile(h, b, len(b), ctypes.byref(done), None):
                raise OSError(ctypes.get_last_error(), "write")

        def gone() -> bool:
            avail = wintypes.DWORD()
            if k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
                return False
            return ctypes.get_last_error() == ERROR_BROKEN_PIPE

        try:
            _handle(handler, read, write, gone)
        finally:
            k32.DisconnectNamedPipe(h)
            k32.CloseHandle(h)

    while not stop.is_set():
        mode = PIPE_ACCESS_DUPLEX | (FIRST_INSTANCE if first else 0)
        h = k32.CreateNamedPipeW(_pipe_name(), mode, PIPE_REJECT_REMOTE, 255, 65536, 65536, 0, ctypes.byref(sa))
        if h == INVALID:
            if first:
                raise Running()
            time.sleep(0.1)
            continue
        first = False
        if not k32.ConnectNamedPipe(h, None) and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
            k32.CloseHandle(h)
            continue
        threading.Thread(target=conn_thread, args=(h,), daemon=True).start()
