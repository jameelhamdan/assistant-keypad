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


def test_a_settings_edit_that_started_from_an_old_revision_is_refused(home):
    srv = server(home)
    _, first = call(srv, "GET", "/config")
    mine = {**first, "behavior": {**first["behavior"], "max_continues": 5}}
    theirs = {**first, "behavior": {**first["behavior"], "max_continues": 9}}
    assert call(srv, "PUT", "/config", theirs)[0] == 200
    status, err = call(srv, "PUT", "/config", mine)  # started from the revision before theirs
    assert status == 400 and "changed elsewhere" in err["error"]
    assert srv.a.config().behavior.max_continues == 9
    _, now = call(srv, "GET", "/config")
    assert call(srv, "PUT", "/config", {**mine, "rev": now["rev"]})[0] == 200
    assert srv.a.config().behavior.max_continues == 5


def test_status_tells_whether_you_are_at_the_pc(home):
    srv = server(home)
    assert call(srv, "GET", "/status")[1]["presence"] == {"known": False}
