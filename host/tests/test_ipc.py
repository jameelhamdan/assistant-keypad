import os
import socket
import stat
import sys
import threading
import time

import pytest

from keypad import ipc

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix socket transport")


@pytest.fixture
def home(monkeypatch):
    """A short directory: Unix socket paths are limited to ~104 bytes, and
    pytest's own temp folders on macOS are longer."""
    import shutil
    import tempfile
    from pathlib import Path

    d = tempfile.mkdtemp(prefix="kp", dir="/tmp")
    monkeypatch.setenv("KEYPAD_HOME", d)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(home):
    calls = []

    def handler(method, path, body, gone):
        calls.append((method, path, body))
        if path == "/wait":  # blocks until the client hangs up
            deadline = time.time() + 5
            while not gone():
                assert time.time() < deadline
                time.sleep(0.02)
            calls.append("client gone")
            return 200, {}
        if path == "/fail":
            return 400, {"error": "nope"}
        return 200, {"echo": body}

    stop = threading.Event()
    t = threading.Thread(target=ipc.serve, args=(handler, stop), daemon=True)
    t.start()
    for _ in range(100):
        if ipc.alive():
            break
        time.sleep(0.01)
    yield calls
    stop.set()
    t.join(2)


def test_round_trip_and_errors(server):
    assert ipc.request("POST", "/x", {"a": "é"}) == {"echo": {"a": "é"}}
    with pytest.raises(ipc.RequestError, match="nope"):
        ipc.request("GET", "/fail")


def test_socket_is_owner_only(server, home):
    mode = stat.S_IMODE(os.stat(home / "agent.sock").st_mode)
    assert mode == 0o600


def test_second_server_refused(server):
    with pytest.raises(ipc.Running):
        ipc.serve(lambda *a: (200, {}), threading.Event())


def test_handler_sees_client_hang_up(server):
    """A hook cancelled by Claude Code must not keep a keypad dialog open."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(ipc._sock_path())
    s.sendall(ipc._pack({"m": "POST", "p": "/wait", "b": None}))
    time.sleep(0.2)
    s.close()
    for _ in range(100):
        if "client gone" in server:
            return
        time.sleep(0.02)
    raise AssertionError("handler never noticed the client left")


def test_not_running(home):
    assert not ipc.alive()
    with pytest.raises(ipc.AgentNotRunning):
        ipc.request("GET", "/status")
