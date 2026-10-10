import logging
import threading
import time

import pytest

from keypad.core.ctx import Ctx
from keypad.core.dialogs import DialogError, Dialogs


class Display:
    def __init__(self):
        self.sent = []

    def targets(self):
        return ["k"]

    def send_to(self, dev, msg):
        self.sent.append(msg)


def press(sid, idx=0):
    return {"t": "press", "id": sid, "key": 7, "act": "pick", "idx": idx}


def wait_for(cond):
    deadline = time.time() + 2
    while not cond():
        assert time.time() < deadline
        time.sleep(0.005)


def screens(disp):
    return [m for m in disp.sent if m.get("t") == "screen"]


def test_a_late_press_for_an_earlier_screen_never_answers_the_next_one():
    disp = Display()
    dialogs = Dialogs(disp, logging.getLogger("t"))
    got = []

    def fn(d):
        got.append(d.show(Ctx().with_timeout(5), {"tpl": "select", "items": ["a", "b"]}))
        got.append(d.show(Ctx().with_timeout(5), {"tpl": "select", "items": ["a", "b"]}))

    t = threading.Thread(target=dialogs.run, args=(Ctx().with_timeout(5), "p", "x", fn), daemon=True)
    t.start()
    wait_for(lambda: len(screens(disp)) == 1)
    first = screens(disp)[0]["id"]
    assert dialogs.press("k", press(first))
    wait_for(lambda: len(screens(disp)) == 2)
    second = screens(disp)[1]["id"]
    dialogs._active.press.put_nowait((press(first, 1), "k"))  # the old press lands after the new screen is up
    time.sleep(0.3)
    assert len(got) == 1, "the stale press answered the second screen"
    assert dialogs.press("k", press(second, 0))
    t.join(2)
    assert [g["id"] for g in got] == [first, second]


def test_a_dialog_with_no_keypad_raises_with_a_reason():
    class Nobody(Display):
        def targets(self):
            return []

    with pytest.raises(DialogError, match="no keypad"):
        Dialogs(Nobody(), logging.getLogger("t")).run(Ctx(), "p", "x", lambda d: None)


def test_screen_ids_differ_between_agent_runs():
    """A keypad repeats its last answer when it sees that screen id again: a restarted agent must not reuse ids."""
    import logging

    from keypad.core.ctx import Ctx
    from keypad.core.dialogs import Dialogs

    class Disp:
        def targets(self):
            return ["kp-1"]

        def send_to(self, dev, msg):
            ids.append(msg.get("id"))

    ids: list = []
    for _ in range(2):
        d = Dialogs(Disp(), logging.getLogger("test"))
        ctx = Ctx().with_timeout(0.3)
        try:
            d.run(ctx, "p", "question", lambda dlg: dlg.show(ctx, {"tpl": "select", "items": ["a"]}))
        except Exception:
            pass
    screens = [i for i in ids if i and not i.endswith("-0")]
    first, second = screens[0], [i for i in screens if i != screens[0]][0]
    assert first.split("-")[0] != second.split("-")[0]
