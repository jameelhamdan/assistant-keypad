"""The real Wi-Fi path: Hub -> WifiLink -> TCP -> a keypad speaking the firmware's handshake."""

import logging
import threading

import pytest
from fakefirmware import FakeFirmware

from keypad import config, proto, secure
from keypad.core.agent import Agent
from keypad.core.ctx import Ctx
from keypad.device.hub import Hub

KEY = secure.new_key()
KEY2 = secure.new_key()


class Rig:
    def __init__(self, fw: FakeFirmware, host_id: str, key: str):
        self.store, _ = config.Store.open()
        self.store._host_id = host_id
        self.store.update(fw.id, lambda d: (setattr(d, "key", key), setattr(d, "last_ip", "127.0.0.1")))
        log = logging.getLogger("test")
        self.agent = Agent(config.Config(), self.store, log)
        self.hub = Hub(self.store, self.agent, host_name=host_id, log=log)
        self.agent.hub = self.hub
        self.stop = threading.Event()
        for fn in (self.agent.run, self.hub.run):
            threading.Thread(target=fn, args=(self.stop,), daemon=True).start()

    def close(self):
        self.stop.set()


@pytest.fixture
def rig(home, monkeypatch):
    made = []

    def make(fw: FakeFirmware, host_id="h-aaaa", key=KEY):
        monkeypatch.setattr(proto, "TCP_PORT", fw.port)
        r = Rig(fw, host_id, key)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


def connected(r: Rig, dev="kp-000001") -> bool:
    return (c := r.hub.get(dev)) is not None and c.live


def test_the_hub_dials_a_paired_keypad_and_syncs_state_over_encrypted_wifi(rig):
    fw = FakeFirmware("kp-000001", {"h-aaaa": KEY})
    r = rig(fw)
    assert fw.wait(lambda: connected(r)), "never connected"
    c = r.hub.get("kp-000001")
    assert c.hello["fw"] == "9.9.9" and c.link.kind == "wifi"
    r.agent.sessions.touch("abc12345", "working", "Working", "pytest")
    r.agent.mark_dirty()
    assert fw.wait(lambda: any(m["t"] == "status" and m["sessions"] for m in fw.received)), "status never reached the keypad"
    assert any(m["t"] == "settings" for m in fw.received) and any(m["t"] == "ping" for m in fw.received)
    fw.close()


def test_a_permission_prompt_is_answered_over_the_real_link(rig):
    fw = FakeFirmware("kp-000001", {"h-aaaa": KEY})
    fw.policy = lambda s: {"key": 1, "act": "pick", "idx": 0}
    r = rig(fw)
    assert fw.wait(lambda: connected(r))
    out = r.agent.hook(Ctx(), "PermissionRequest", {"session_id": "s1", "cwd": "/w/app", "tool_name": "Bash",
                                                    "tool_input": {"command": "ls"}})
    assert out["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert fw.screens[0]["items"] == ["Yes", "No"]
    fw.close()


def test_a_dropped_link_comes_back_by_itself(rig):
    fw = FakeFirmware("kp-000001", {"h-aaaa": KEY})
    r = rig(fw)
    assert fw.wait(lambda: connected(r))
    first = r.hub.get("kp-000001")
    fw.drop()
    assert fw.wait(lambda: r.hub.get("kp-000001") is not first, 12), "the loss was not noticed"
    assert fw.wait(lambda: connected(r), 25), "never reconnected"
    fw.close()


def test_a_screen_open_during_a_drop_is_shown_again_after_reconnecting(rig):
    fw = FakeFirmware("kp-000001", {"h-aaaa": KEY})
    r = rig(fw)
    assert fw.wait(lambda: connected(r))
    result = {}
    threading.Thread(target=lambda: result.update(out=r.agent.hook(Ctx(), "PermissionRequest", {
        "session_id": "s1", "cwd": "/w/app", "tool_name": "Bash", "tool_input": {"command": "ls"}})), daemon=True).start()
    assert fw.wait(lambda: fw.screens)
    fw.drop()
    assert fw.wait(lambda: len(fw.screens) >= 2, 30), "the open screen was not re-sent on reconnect"
    assert fw.screens[1]["id"] == fw.screens[0]["id"]
    fw.send({"t": "press", "id": fw.screens[0]["id"], "key": 1, "act": "pick", "idx": 0})
    assert fw.wait(lambda: "out" in result)
    assert result["out"]["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    fw.close()


def test_a_second_computer_is_told_the_keypad_is_busy_and_can_take_it_over(rig, monkeypatch, tmp_path):
    fw = FakeFirmware("kp-000001", {"h-aaaa": KEY, "h-bbbb": KEY2})
    first = rig(fw, "h-aaaa", KEY)
    assert fw.wait(lambda: connected(first))
    monkeypatch.setenv("KEYPAD_HOME", str(tmp_path / "second"))  # the second computer's own state
    second = rig(fw, "h-bbbb", KEY2)
    assert fw.wait(lambda: "busy" in fw.refused), "the second computer was let in"
    assert fw.wait(lambda: "in use by" in second.hub.problems.get("kp-000001", "")), second.hub.problems
    assert connected(first) and not connected(second)
    second.hub.take("kp-000001")
    assert fw.wait(lambda: connected(second), 10), "take over did not work"
    fw.close()


def test_a_keypad_not_paired_with_this_computer_says_so(rig):
    fw = FakeFirmware("kp-000001", {"h-other": KEY})
    r = rig(fw)
    assert fw.wait(lambda: "kp-000001" in r.hub.problems)
    assert "not paired with this computer" in r.hub.problems["kp-000001"]
    fw.close()
