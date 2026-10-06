import json
import os
import socket
import stat
import sys
import threading
import time

import pytest

from keypad import ipc


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


def test_only_loopback_with_the_token(server, home):
    info = json.loads((home / "agent.json").read_text())
    s = socket.socket()
    s.connect(("127.0.0.1", info["port"]))
    s.sendall(ipc._pack({"m": "GET", "p": "/x", "b": None, "k": "wrong"}))
    assert ipc._read_msg(s)["s"] == 403, "a request without the token must be refused"
    s.close()
    assert "/x" not in [c[1] for c in server if isinstance(c, tuple)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_info_file_is_owner_only(server, home):
    assert stat.S_IMODE(os.stat(home / "agent.json").st_mode) == 0o600


def test_second_server_refused(server):
    with pytest.raises(ipc.Running):
        ipc.serve(lambda *a: (200, {}), threading.Event())


def test_handler_sees_client_hang_up(server, home):
    """A hook cancelled by Claude Code must not keep a keypad dialog open."""
    info = json.loads((home / "agent.json").read_text())
    s = socket.socket()
    s.connect(("127.0.0.1", info["port"]))
    s.sendall(ipc._pack({"m": "POST", "p": "/wait", "b": None, "k": info["token"]}))
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


def test_stale_info_file_means_not_running(home):
    (home / "agent.json").write_text(json.dumps({"port": 1, "token": "x"}))  # nothing listens on port 1
    assert not ipc.alive()


def test_info_file_removed_on_stop(home):
    stop = threading.Event()
    t = threading.Thread(target=ipc.serve, args=(lambda *a: (200, {}), stop), daemon=True)
    t.start()
    for _ in range(100):
        if ipc.alive():
            break
        time.sleep(0.01)
    assert (home / "agent.json").exists()
    stop.set()
    t.join(2)
    assert not (home / "agent.json").exists()


def test_no_legacy_agent_means_nothing_to_stop(home):
    assert ipc.stop_legacy() is False
