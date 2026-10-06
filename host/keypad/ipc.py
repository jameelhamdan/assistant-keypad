"""The private channel between the agent and its local clients (hook shim, tray,
CLI): TCP on 127.0.0.1 with a random port and a secret token. The agent writes
`agent.json` ({"port", "token"}) into the user's private data folder (mode 0600);
a client reads it and sends the token with every request, so only a process that
can read that file can talk to the agent. The same code serves macOS and Windows.

Messages are a 4-byte big-endian length and a JSON object. Requests are
{"m": method, "p": path, "b": body, "k": token}; replies {"s": status, "b": body}.
Standard library only: the hook shim imports this on every prompt and decision."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import select
import socket
import struct
import threading
from collections.abc import Callable
from typing import Any

from .dirs import data_dir

MAX_MSG = 4 << 20
HOST = "127.0.0.1"


class AgentNotRunning(ConnectionError):
    pass


class RequestError(Exception):
    """The agent answered with an error."""


class Running(Exception):
    """Another agent already serves this user."""


def _info_path() -> str:
    return os.path.join(data_dir(), "agent.json")


def _read_info() -> dict[str, Any]:
    try:
        with open(_info_path(), encoding="utf-8") as f:
            info = json.load(f)
        if isinstance(info.get("port"), int) and isinstance(info.get("token"), str):
            return info
    except (OSError, ValueError, AttributeError):
        pass
    raise AgentNotRunning("no agent.json: the agent is not running")


# ---- framing -------------------------------------------------------------------------


def _pack(obj: Any) -> bytes:
    b = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return struct.pack(">I", len(b)) + b


def _read(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def _read_msg(sock: socket.socket) -> Any:
    (n,) = struct.unpack(">I", _read(sock, 4))
    if n > MAX_MSG:
        raise ValueError("message too large")
    return json.loads(_read(sock, n))


# ---- client ----------------------------------------------------------------------------


def _connect(timeout: float | None) -> tuple[socket.socket, str]:
    info = _read_info()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((HOST, info["port"]))
    except OSError as e:  # a stale agent.json from a crashed agent
        s.close()
        raise AgentNotRunning(str(e)) from e
    return s, info["token"]


def request(method: str, path: str, body: Any = None, timeout: float | None = 30) -> Any:
    """Sends a request to the agent and returns the reply body. Raises
    AgentNotRunning or RequestError (the agent's error message)."""
    s, token = _connect(2 if timeout is None else min(timeout, 2))  # connecting is quick; waiting is not
    try:
        s.settimeout(timeout)
        s.sendall(_pack({"m": method, "p": path, "b": body, "k": token}))
        resp = _read_msg(s)
    except (OSError, ValueError) as e:
        raise RequestError(str(e)) from e
    finally:
        s.close()
    if resp.get("s", 500) >= 300:
        err = resp.get("b")
        raise RequestError(err.get("error", "request failed") if isinstance(err, dict) else "request failed")
    return resp.get("b")


def stop_legacy() -> bool:
    """Asks an agent of an older version (Keypad 2.x listened on a Windows named pipe or a
    Unix socket instead of agent.json) to quit, so two generations never fight over the
    keypad. True if one answered."""
    try:
        msg = _pack({"m": "POST", "p": "/quit", "b": None})
        if os.name == "nt":
            user = os.environ.get("USERNAME", "user").replace("\\", "-").replace(" ", "-")
            if home := os.environ.get("KEYPAD_HOME"):
                import hashlib

                user += "-" + hashlib.sha1(home.encode()).hexdigest()[:8]
            with open(rf"\\.\pipe\keypad-agent-{user}", "r+b", buffering=0) as f:
                f.write(msg)
                f.read(4)
            return True
        sock = os.path.join(data_dir(), "agent.sock")
        if not os.path.exists(sock):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock)
        s.sendall(msg)
        s.close()
        return True
    except OSError:
        return False


def alive() -> bool:
    try:
        _connect(0.5)[0].close()
        return True
    except AgentNotRunning:
        return False


# ---- server ---------------------------------------------------------------------------

Handler = Callable[[str, str, Any, Callable[[], bool]], tuple[int, Any]]
# handler(method, path, body, client_gone) -> (status, body); client_gone() turns
# True when the client hung up (e.g. Claude Code cancelled the hook).


def _write_info(port: int, token: str) -> None:
    p = _info_path()
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps({"port": port, "token": token}).encode())
    finally:
        os.close(fd)
    os.replace(tmp, p)


def _serve_conn(c: socket.socket, token: str, handler: Handler) -> None:
    def gone() -> bool:
        try:
            r, _, _ = select.select([c], [], [], 0)
            return bool(r) and c.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    try:
        c.settimeout(10)  # a client has this long to send its request
        req = _read_msg(c)
        c.settimeout(None)
        if not isinstance(req, dict) or not hmac.compare_digest(str(req.get("k", "")), token):
            status, body = 403, {"error": "forbidden"}
        else:
            status, body = handler(str(req.get("m", "")), str(req.get("p", "")), req.get("b"), gone)
        c.sendall(_pack({"s": status, "b": body}))
    except (OSError, ValueError, ConnectionError):
        pass
    finally:
        c.close()


def serve(handler: Handler, stop: threading.Event) -> None:
    """Serves requests until stop is set. Raises Running if another agent serves this user."""
    if alive():
        raise Running()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((HOST, 0))
    srv.listen(64)
    srv.settimeout(0.5)
    token = secrets.token_hex(32)
    _write_info(srv.getsockname()[1], token)
    try:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except TimeoutError:
                continue
            threading.Thread(target=_serve_conn, args=(c, token, handler), daemon=True).start()
    finally:
        srv.close()
        try:
            with open(_info_path(), encoding="utf-8") as f:
                mine = json.load(f).get("token") == token
            if mine:
                os.unlink(_info_path())
        except (OSError, ValueError):
            pass
