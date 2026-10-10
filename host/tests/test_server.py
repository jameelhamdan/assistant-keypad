import logging

from keypad import config
from keypad.core.agent import Agent
from keypad.server import Server


def server(home):
    store, _ = config.Store.open()
    log = logging.getLogger("test")
    return Server(Agent(config.Config(), store, log), "/x/keypad", log, lambda: None)


def call(srv, method, path, body=None):
    return srv.handle(method, path, body, lambda: False)


def test_config_round_trip_over_the_api(home):
    srv = server(home)
    _, c = call(srv, "GET", "/config")
    c["behavior"]["timeout"] = 120
    assert call(srv, "PUT", "/config", c)[0] == 200
    assert srv.a.config().behavior.timeout == 120


def test_status_tells_whether_you_are_at_the_pc(home):
    srv = server(home)
    assert call(srv, "GET", "/status")[1]["presence"] == {"known": False}


def test_the_hook_never_outlasts_the_shim_that_asked(home):
    srv = server(home)
    seen = []
    srv.a.hook = lambda ctx, event, payload: seen.append(ctx.remaining()) or {}
    call(srv, "POST", "/hook", {"event": "Stop", "payload": {}, "wait": 320})
    call(srv, "POST", "/hook", {"event": "Stop", "payload": {}})
    assert 295 < seen[0] <= 300, "the shim waits 320 s, 20 s of it grace"
    assert seen[1] is None, "no deadline from an old shim"


def test_status_carries_health_counters(home):
    srv = server(home)
    srv.a.hook(__import__("keypad.core.ctx", fromlist=["Ctx"]).Ctx(), "SessionStart", {"session_id": "s", "cwd": "/x"})
    st = call(srv, "GET", "/status")[1]["stats"]
    assert st["hooks"]["SessionStart"]["n"] == 1 and "uptime" in st


def test_an_update_is_only_done_when_the_keypad_comes_back_with_the_new_firmware(home, monkeypatch):
    from keypad import firmware
    from keypad import server as servermod

    srv = server(home)
    monkeypatch.setattr(firmware, "version", lambda: "1.2.0")
    monkeypatch.setattr(servermod, "UPDATE_RECONNECT", 0.2)

    class C:
        hello = {"fw": "1.1.0"}

    srv.a.hub = type("H", (), {"wait_reconnect": lambda s, i, o, t: C()})()
    assert "rolled back" in srv.check_updated("kp-1", None)
    C.hello = {"fw": "1.2.0"}
    assert srv.check_updated("kp-1", None) is None
    srv.a.hub = type("H", (), {"wait_reconnect": lambda s, i, o, t: None})()
    assert "did not reconnect" in srv.check_updated("kp-1", None)
