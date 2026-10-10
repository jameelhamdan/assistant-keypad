"""A keypad's Wi-Fi side over a real TCP socket: the same handshake rules as firmware/src/link.cpp
(paired hosts, one at a time, busy/take), then encrypted frames."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any

from keypad import secure
from keypad.secure import read_frame, write_frame

HOST_TIMEOUT = 6.0


class Session:
    def __init__(self, sock: socket.socket, conn: secure.Conn, host: str):
        self.sock, self.conn, self.host = sock, conn, host
        self.last_rx = time.monotonic()
        self.peer = ""

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class FakeFirmware:
    def __init__(self, dev_id: str, hosts: dict[str, str], fw: str = "9.9.9"):
        self.id, self.hosts, self.fw = dev_id, dict(hosts), fw
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.lock = threading.Lock()
        self.active: Session | None = None
        self.received: list[dict[str, Any]] = []
        self.screens: list[dict[str, Any]] = []
        self.policy = None  # screen -> press fields
        self.refused: list[str] = []
        self.closing = False
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self.closing = True
        self.srv.close()
        self.drop()

    def drop(self) -> None:
        """The link dies (Wi-Fi lost, keypad rebooted)."""
        with self.lock:
            a, self.active = self.active, None
        if a:
            a.close()

    def send(self, msg: dict[str, Any]) -> None:
        with self.lock:
            a = self.active
        if a:
            a.conn.send(json.dumps(msg).encode())

    def _accept(self) -> None:
        while not self.closing:
            try:
                sock, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _refuse(self, sock: socket.socket, code: str, why: str, host: str = "") -> None:
        self.refused.append(code)
        write_frame(sock, json.dumps({"t": "no", "code": code, "why": why, **({"host": host} if host else {})}).encode())
        sock.close()

    def _serve(self, sock: socket.socket) -> None:
        try:
            sock.settimeout(5)
            h = json.loads(read_frame(sock))
            if h.get("t") != "hi" or h.get("v") != secure.VERSION:
                return self._refuse(sock, "bad_hello", "bad hello")
            if not self.hosts:
                return self._refuse(sock, "not_paired", "keypad is not paired")
            key = self.hosts.get(h.get("host", ""))
            if key is None:
                return self._refuse(sock, "other_host", "not paired with this computer")
            with self.lock:
                a = self.active
            if a and a.host != h["host"] and time.monotonic() - a.last_rx < HOST_TIMEOUT and not h.get("take"):
                return self._refuse(sock, "busy", "in use by another computer", a.peer)
            nh, nd = bytes.fromhex(h["n"]), os.urandom(secure.NONCE_SIZE)
            write_frame(sock, secure._hi(id=self.id, n=nd.hex()))
            h2d, d2h = secure.derive_keys(bytes.fromhex(key), nh, nd)
            s = Session(sock, secure.Conn(sock, d2h, h2d, h["host"]), h["host"])
            sock.settimeout(None)
            first = True
            while True:
                m = json.loads(s.conn.recv())
                s.last_rx = time.monotonic()
                if first:  # authenticated: this host now holds the keypad
                    first = False
                    with self.lock:
                        old, self.active = self.active, s
                    if old and old is not s:
                        old.close()
                self._handle(s, m)
        except (OSError, ValueError, secure.SecureError, ConnectionError):
            pass
        finally:
            sock.close()

    def _handle(self, s: Session, m: dict[str, Any]) -> None:
        self.received.append(m)
        t = m.get("t")
        if t == "hello":
            s.peer = m.get("host", "")
            s.conn.send(json.dumps({"t": "hello", "v": secure.VERSION, "id": self.id, "fw": self.fw, "name": "Fake",
                                    "paired": True, "wifi": {"state": "up", "ip": "127.0.0.1"}}).encode())
        elif t == "ping":
            s.conn.send(json.dumps({"t": "pong", "bat": 80}).encode())
        elif t == "screen":
            self.screens.append(m)
            if self.policy:
                s.conn.send(json.dumps({"t": "press", "id": m["id"], **self.policy(m)}).encode())

    def wait(self, pred, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False
