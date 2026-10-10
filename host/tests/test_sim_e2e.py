"""The whole product, no hardware: the real host (Agent, Hub, hooks, dialogs) talking to the real firmware
UI and key logic (firmware/sim). Claude Code's hook calls go in, key presses on the simulated keypad
come out as decisions.

    python firmware/sim/build.py   (once, and after firmware changes)
"""

import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

SIM_DIR = Path(__file__).resolve().parents[2] / "firmware" / "sim"
sys.path.insert(0, str(SIM_DIR))
keypad_sim = pytest.importorskip("keypad_sim")
pytestmark = pytest.mark.skipif(not keypad_sim.EXE.exists(), reason="build the simulator: python firmware/sim/build.py")

from conftest import SID  # noqa: E402

from keypad import config  # noqa: E402
from keypad.core.agent import Agent  # noqa: E402
from keypad.core.ctx import Ctx  # noqa: E402
from keypad.device.hub import Hub  # noqa: E402
from keypad.device.links import Link, LinkClosed  # noqa: E402

SHOTS = SIM_DIR / "out" / "e2e"


class SimLink(Link):
    """The simulated keypad as a Wi-Fi link: host messages go in, what the keypad sends comes out."""

    kind = "wifi"
    addr = "sim"

    def __init__(self):
        self.sim = keypad_sim.Sim()
        self.sim.host(False)  # the real Hub pings; the simulator must not play the host as well
        self.sim.tx.clear()  # what the simulator's own stand-in host provoked while booting (a hello and a pong)
        self._lock = threading.Lock()
        self._rx: queue.Queue[bytes | None] = queue.Queue()
        self.sent: list[dict] = []  # host -> keypad, in order
        self._closed = False

    def _pump(self) -> None:
        while self.sim.tx:
            self._rx.put(json.dumps(self.sim.tx.pop(0)).encode())

    def send(self, msg: bytes) -> None:
        if self._closed:
            raise LinkClosed("closed")
        m = json.loads(msg)
        with self._lock:
            self.sent.append(m)
            self.sim.msg(m)
            self._pump()

    def recv(self) -> bytes:
        while True:
            try:
                b = self._rx.get(timeout=0.2)
            except queue.Empty:
                if self._closed:
                    raise LinkClosed("closed") from None
                continue
            if b is None:
                raise LinkClosed("closed")
            return b

    def close(self) -> None:
        self._closed = True
        self._rx.put(None)

    # ---- the person at the keypad ----

    def key(self, k: int) -> None:
        with self._lock:
            self.sim.key(k)
            self._pump()

    def wait(self, ms: int) -> None:
        with self._lock:
            self.sim.wait(ms)
            self._pump()

    def screens(self) -> list[dict]:
        return [m for m in self.sent if m["t"] == "screen"]

    def toasts(self) -> list[dict]:
        return [m for m in self.sent if m["t"] == "toast"]

    def image(self):
        with self._lock:
            return self.sim.image()

    def shot(self, name: str) -> None:
        with self._lock:
            self.sim.shot(SHOTS / f"{name}.png", scale=3)

    def close_sim(self) -> None:
        self.close()
        with self._lock:
            self.sim.close()


class Rig:
    def __init__(self, shortcuts=None):
        store, err = config.Store.open()
        assert err is None
        log = logging.getLogger("test")
        self.agent = Agent(config.Config(shortcuts=list(shortcuts or [])), store, log)
        self.hub = Hub(store, self.agent, log=log)
        self.agent.hub = self.hub
        self.stop = threading.Event()
        threading.Thread(target=self.agent.run, args=(self.stop,), daemon=True).start()
        self.kp = SimLink()
        threading.Thread(target=self.hub.serve, args=(self.kp,), daemon=True).start()
        deadline = time.time() + 5
        while not self.hub.conns():
            assert time.time() < deadline, "the simulated keypad never said hello"
            time.sleep(0.01)
        self.kp.wait(1500)  # past the boot screen
        self.results: dict[str, dict] = {}

    def hook_async(self, name: str, event: str, p: dict) -> None:
        p.setdefault("session_id", SID)
        p.setdefault("cwd", "/work/money-mind")
        threading.Thread(target=lambda: self.results.update({name: self.agent.hook(Ctx(), event, p)}), daemon=True).start()

    def wait_screen(self, n: int = 1) -> dict:
        end = time.time() + 5
        while len(self.kp.screens()) < n:
            assert time.time() < end, "no screen reached the keypad"
            time.sleep(0.01)
        self.kp.wait(400)  # the keypad ignores a press that began before its screen: so does a person
        return self.kp.screens()[n - 1]

    def result(self, name: str) -> dict:
        end = time.time() + 5
        while name not in self.results:
            assert time.time() < end, f"{name}: the hook never returned"
            time.sleep(0.01)
        return self.results[name]

    def close(self) -> None:
        self.stop.set()
        self.kp.close_sim()


@pytest.fixture
def rig(home):
    made = []

    def make(**kw):
        r = Rig(**kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


PERMISSION = {"tool_name": "Bash", "tool_input": {"command": "pytest -q", "description": "Run the tests"}}
SHORTCUTS = config.default_shortcuts()


def test_a_permission_prompt_is_answered_with_a_number_key(rig):
    r = rig()
    r.agent.hook(Ctx(), "SessionStart", {"session_id": SID, "cwd": "/work/money-mind"})
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    r.kp.shot("permission")
    assert s["items"] == ["Yes", "No"]
    r.kp.key(1)
    assert r.result("p")["hookSpecificOutput"]["decision"]["behavior"] == "allow"


def test_no_on_the_keypad_denies(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.kp.key(2)
    assert r.result("p")["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_esc_cannot_dismiss_a_permission_prompt(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.kp.key(5)
    time.sleep(0.3)
    assert "p" not in r.results, "Esc must do nothing on a decision"
    r.kp.key(1)
    assert r.result("p")["hookSpecificOutput"]["decision"]["behavior"] == "allow"


def test_the_knob_press_selects_the_highlighted_option(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.kp.sim.turn(1)  # one detent down: "No"
    r.kp.key(0)  # the knob press
    assert r.result("p")["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_a_question_is_answered_by_number_and_other_goes_to_the_pc(rig):
    r = rig()
    q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
        {"question": "Which DB?", "header": "DB", "options": [{"label": "Postgres", "description": "Server"}, {"label": "SQLite"}]}]}}
    r.hook_async("q", "PreToolUse", dict(q))
    s = r.wait_screen()
    r.kp.shot("question")
    assert s["items"][-1].startswith("Other")
    r.kp.key(2)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which DB?": "SQLite"}
    r.hook_async("q2", "PreToolUse", dict(q))
    r.wait_screen(2)
    r.kp.key(3)  # Other
    assert r.result("q2") == {}, "free text is typed at the PC"


def test_a_multi_select_question_with_the_keys(rig):
    r = rig()
    q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
        {"question": "Checks?", "multiSelect": True, "options": [{"label": "lint"}, {"label": "test"}, {"label": "build"}]}]}}
    r.hook_async("q", "PreToolUse", q)
    r.wait_screen()
    r.kp.key(1)
    r.kp.key(3)
    r.kp.shot("multi")
    r.kp.sim.turn(3)  # the knob: down to Submit
    r.kp.key(7)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Checks?": "lint, build"}


def test_when_claude_finishes_the_keypad_offers_continue_and_the_saved_prompts(rig):
    r = rig(shortcuts=SHORTCUTS)
    r.agent.presence = lambda: (600.0, True)  # away from the PC
    r.agent.hook(Ctx(), "SessionStart", {"session_id": SID, "cwd": "/work/money-mind"})
    r.hook_async("s", "Stop", {"last_assistant_message": "Done: the tests pass."})
    s = r.wait_screen()
    r.kp.shot("finished")
    assert s["items"][:4] == ["continue", "Tests", "Commit", "Review"]
    r.kp.key(2)  # "Tests"
    out = r.result("s")
    assert out["decision"] == "block" and "Run the project's tests" in out["reason"]


def test_esc_on_the_finished_screen_leaves_claude_stopped(rig):
    r = rig(shortcuts=SHORTCUTS)
    r.agent.presence = lambda: (600.0, True)
    r.hook_async("s", "Stop", {"last_assistant_message": "Done."})
    r.wait_screen()
    r.kp.key(5)
    assert r.result("s") == {}


def test_a_saved_prompt_on_a_quick_key_reaches_claude_with_the_next_prompt(rig):
    r = rig(shortcuts=SHORTCUTS)
    r.agent.hook(Ctx(), "SessionStart", {"session_id": SID, "cwd": "/work/money-mind"})
    r.agent.push_status()
    end = time.time() + 3
    while not any(m["t"] == "status" and m.get("quick") for m in r.kp.sent):
        assert time.time() < end, "the keypad was not told its quick prompts"
        time.sleep(0.01)
    r.kp.wait(2000)  # no request just ended
    r.kp.shot("status-quick")
    r.kp.key(2)  # "Commit"
    end = time.time() + 3
    while not r.kp.toasts():
        assert time.time() < end, "no confirmation on the keypad"
        time.sleep(0.01)
    assert "Commit" in r.kp.toasts()[-1]["text"]
    out = r.agent.hook(Ctx(), "UserPromptSubmit", {"session_id": SID, "cwd": "/work/money-mind", "prompt": "hi"})
    assert "Commit the current changes" in out["hookSpecificOutput"]["additionalContext"]


def test_the_keypad_lights_up_when_claude_finishes_at_the_pc(rig):
    r = rig()
    r.agent.presence = lambda: (3.0, True)
    assert r.agent.hook(Ctx(), "Stop", {"session_id": SID, "cwd": "/work/money-mind", "last_assistant_message": "ok"}) == {}
    end = time.time() + 3
    while not r.kp.toasts():
        assert time.time() < end, "no light-up"
        time.sleep(0.01)
    r.kp.shot("finished-toast")
    assert r.kp.toasts()[0]["text"] == "Claude finished: money-mind"


def test_requests_from_two_sessions_are_shown_one_after_the_other(rig):
    r = rig()
    r.hook_async("a", "PermissionRequest", {**PERMISSION, "session_id": "aaaa-1111"})
    r.wait_screen()
    r.hook_async("b", "PermissionRequest", {**PERMISSION, "session_id": "bbbb-2222", "cwd": "/work/api"})
    time.sleep(0.3)
    assert len(r.kp.screens()) == 1, f"the second request waits its turn (and no screen was sent twice): {[(m['t'], m.get('id')) for m in r.kp.sent]}"
    r.kp.key(1)
    assert r.result("a")["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    end = time.time() + 5
    while len({m["id"] for m in r.kp.screens()}) < 2:
        assert time.time() < end
        time.sleep(0.01)
    r.kp.wait(400)
    s = r.kp.screens()[-1]
    assert s["project"] == "api"
    r.kp.shot("second-session")
    r.kp.key(2)
    assert r.result("b")["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_pausing_keeps_every_request_on_the_pc(rig):
    r = rig()
    r.agent.set_paused(True)
    assert r.agent.hook(Ctx(), "PermissionRequest", {"session_id": SID, "cwd": "/w/x", **PERMISSION}) == {}
    assert not r.kp.screens()


def test_a_lost_answer_is_repeated_when_the_screen_comes_again(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    r.kp.sim.tx.clear()
    # the answer is pressed but "lost on the way": the host side never sees it
    with r.kp._lock:
        r.kp.sim.key(1)
        lost = [m for m in r.kp.sim.tx if m["t"] == "press"]
        r.kp.sim.tx.clear()
    assert lost and lost[0]["id"] == s["id"]
    r.hub.conns()[0].send(s)  # the host re-sends the screen (a reconnect does this)
    assert r.result("p")["hookSpecificOutput"]["decision"]["behavior"] == "allow", "the keypad repeats its answer"
