import logging
import threading
import time

import pytest

from keypad import config
from keypad.core.agent import Agent
from keypad.core.ctx import Ctx
from keypad.device import fake as fakemod
from keypad.device.hub import Hub

SID = "sess-0001-aaaa"


class Env:
    def __init__(self, policy, connect: bool):
        self.idle = 3600.0
        store, err = config.Store.open()
        assert err is None
        log = logging.getLogger("test")
        self.a = Agent(config.Config(), store, lambda: (self.idle, True), log)
        self.hub = Hub(store, self.a, log=log)
        self.a.hub = self.hub
        self.stop = threading.Event()
        threading.Thread(target=self.a.run, args=(self.stop,), daemon=True).start()
        self.fake = None
        if connect:
            self.fake = fakemod.Fake("kp-000001", policy)
            threading.Thread(target=self.hub.serve, args=(self.fake,), daemon=True).start()
            deadline = time.time() + 2
            while not self.hub.conns():
                assert time.time() < deadline, "fake keypad never connected"
                time.sleep(0.005)

    def hook(self, event, p):
        p.setdefault("session_id", SID)
        p.setdefault("cwd", "/work/money-mind")
        return self.a.hook(Ctx.background(), event, p, [42, 1])

    def close(self):
        self.stop.set()
        if self.fake:
            self.fake.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYPAD_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def env(home):
    made = []

    def make(policy=fakemod.first_key, connect=True):
        e = Env(policy, connect)
        made.append(e)
        return e

    yield make
    for e in made:
        e.close()


def behavior(out):
    return out.get("hookSpecificOutput", {}).get("decision", {}).get("behavior", "")
