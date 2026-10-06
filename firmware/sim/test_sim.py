"""Firmware UI and key-logic tests against the simulator (the real app.cpp / ui.cpp). No hardware.

    python firmware/sim/build.py
    host/.venv/Scripts/python -m pytest firmware/sim -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import scenarios as sc  # noqa: E402
from keypad_sim import EXE, Sim  # noqa: E402

pytestmark = pytest.mark.skipif(not EXE.exists(), reason="build the simulator first: python firmware/sim/build.py")


@pytest.fixture
def kp():
    with Sim() as k:
        k.msg(sc.HELLO)
        k.msg(sc.SETTINGS)
        k.msg(sc.status())
        yield k


def presses(kp):
    return [m for m in kp.tx if m["t"] == "press"]


def ink(im, box):
    """Number of non-background pixels in a box (left, top, right, bottom) of the 320x170 frame."""
    bg = im.getpixel((2, 100))
    return sum(1 for x in range(box[0], box[2]) for y in range(box[1], box[3]) if im.getpixel((x, y)) != bg)


BOTTOM_RIGHT = (150, 154, 320, 170)


@pytest.mark.parametrize("mode", sc.MODES)
def test_mode_label_is_drawn(kp, mode):
    kp.msg(sc.status(mode="acceptEdits"))
    before = ink(kp.image(), BOTTOM_RIGHT)
    kp.msg(sc.status(mode=mode))
    assert ink(kp.image(), BOTTOM_RIGHT) > 20, f"no label for {mode}"
    assert before > 20


def test_unknown_mode_is_shown_as_sent(kp):
    kp.msg(sc.status(mode="newMode"))
    assert ink(kp.image(), BOTTOM_RIGHT) > 20


@pytest.mark.parametrize("key,idx", [(1, 0), (2, 1), (3, 2)])
def test_number_keys_pick(kp, key, idx):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)  # stale-press guard
    kp.key(key)
    assert presses(kp)[-1] | {} == {"t": "press", "id": "p-1", "key": key, "act": "pick", "idx": idx}


def test_enter_selects_the_cursor(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.key(8)   # down
    kp.key(7)   # enter
    assert presses(kp)[-1]["idx"] == 1


def test_a_decision_has_no_way_out_but_an_answer(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    before = len(presses(kp))
    kp.key(5)  # Esc: nothing to leave to the PC
    assert len(presses(kp)) == before, "Esc must not answer a permission dialog"
    assert ink(kp.image(), (10, 100, 310, 150)) > 100, "the dialog is still on screen"


def test_esc_ends_the_finished_screen(kp):
    kp.msg(sc.SCREENS["finished"])
    kp.wait(300)
    kp.key(5)
    assert presses(kp)[-1]["act"] == "done"


def test_pressing_the_knob_is_enter(kp):
    kp.msg({**sc.SCREENS["question"], "id": "q-knob"})
    kp.wait(300)
    kp.key(8)  # move to the second option
    kp.key(0)  # the knob press
    p = presses(kp)[-1]
    assert p["act"] == "pick" and p["idx"] == 1 and p["key"] == 7, "the knob press answers like Enter"
    kp.msg({**sc.SCREENS["finished"], "id": "s-knob"})
    kp.wait(300)
    kp.key(0)  # Enter on the first option (continue)
    assert presses(kp)[-1]["idx"] == 0


def test_the_screen_never_dims_on_a_wire(kp):
    kp.power("usb")
    kp.wait(11000)  # the power source is read every 10 s
    kp.wait(90000)  # well past the 60 s idle time
    assert kp.backlight() == 80


def test_on_battery_the_screen_dims_and_wakes_on_a_key_or_a_request(kp):
    kp.power("battery")
    kp.wait(11000)
    assert kp.backlight() == 80, "bright right after start"
    kp.wait(65000)
    assert kp.backlight() == 8, "dim after a minute without a key or a request"
    kp.key(4)  # a key press wakes it (and does nothing else)
    assert kp.backlight() == 80
    kp.wait(65000)
    assert kp.backlight() == 8
    kp.msg(sc.SCREENS["permission"])  # a request lights it up at once
    assert kp.backlight() == 80
    kp.msg({"t": "close", "id": sc.SCREENS["permission"]["id"], "why": "done"})
    kp.wait(30000)
    assert kp.backlight() == 80, "stays bright for a while after the last interaction"


def test_plugging_in_while_dim_lights_the_screen(kp):
    kp.power("battery")
    kp.wait(80000)
    assert kp.backlight() == 8
    kp.power("usb")
    kp.wait(11000)
    assert kp.backlight() == 80


def test_encoder_turn_moves_the_cursor(kp):
    kp.msg(sc.SCREENS["question"])
    kp.wait(300)
    kp.turn(1)
    kp.key(7)
    assert presses(kp)[-1]["idx"] == 1


def test_multi_select_submits_ticked_options(kp):
    kp.msg(sc.SCREENS["multi"])
    kp.wait(300)
    kp.key(1)
    kp.key(3)
    kp.key(8); kp.key(8); kp.key(8)   # to Submit
    kp.key(7)
    p = presses(kp)[-1]
    assert p["act"] == "submit" and p["sel"] == [0, 2]


def test_a_press_is_answered_once(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.key(1)
    kp.key(2)
    assert len(presses(kp)) == 1


def test_finished_screen_offers_continue(kp):
    kp.msg(sc.SCREENS["finished"])
    kp.wait(300)
    kp.key(2)
    assert presses(kp)[-1]["idx"] == 1


def test_session_list_selects_a_session(kp):
    a, b = dict(sc.SESSION, id="aaaa0001"), dict(sc.SESSION, id="bbbb0002", project="api", name="Docs")
    kp.msg(sc.status(sessions=[a, b]))
    kp.key(6)
    kp.key(2)   # the second session row (row 0 is "follow latest")
    s = [m for m in kp.tx if m["t"] == "session"]
    assert s and s[-1]["act"] == "select" and s[-1]["sid"] in ("aaaa0001", "bbbb0002")


def test_feed_replaces_the_transcript_and_keeps_the_newest_entries(kp):
    kp.msg(sc.status(log=[]))
    sid = sc.SESSION["id"]
    kp.msg({"t": "feed", "sid": sid, "full": [{"k": "c", "t": f"entry number {i}"} for i in range(45)]})  # more than the keypad keeps
    assert ink(kp.image(), (0, 60, 320, 150)) > 100
    kp.msg({"t": "feed", "sid": "someone-else", "full": [{"k": "c", "t": "not this session"}]})  # ignored
    kp.msg({"t": "feed", "sid": sid, "full": []})
    assert ink(kp.image(), (0, 60, 320, 120)) < 400  # an empty transcript: just the welcome box


def test_host_silence_shows_waiting_then_recovers(kp):
    kp.host(False)
    kp.wait(8000)
    assert ink(kp.image(), (0, 0, 320, 60)) > 0
    kp.tx.clear()
    kp.msg(sc.HELLO)                                # the host says hello again: the keypad introduces itself, back to status
    assert [m["t"] for m in kp.tx] == ["hello"] and kp.tx[0]["v"] == 3
    kp.msg(sc.status())
    assert ink(kp.image(), BOTTOM_RIGHT) > 20


def test_holding_down_repeats_the_cursor_but_never_decides(kp):
    kp.msg({**sc.SCREENS["question"], "id": "q-rep", "items": [f"option {i}" for i in range(1, 11)]})
    kp.wait(300)
    kp.hold(8, 1500)
    assert not presses(kp), "holding a navigation key must not answer"
    kp.key(7)
    assert presses(kp)[-1]["idx"] >= 5


def test_a_held_number_key_does_not_repeat(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.hold(1, 1500)
    assert len(presses(kp)) == 1


def test_paused_is_visible_while_working(kp):
    def warning_pixels():
        im = kp.image()
        return sum(1 for x in range(0, 150) for y in range(154, 170)
                   if (lambda p: p[0] > 200 and 150 < p[1] < 220 and p[2] < 60)(im.getpixel((x, y))))

    kp.msg(sc.status(state="working"))
    assert warning_pixels() == 0
    kp.msg(sc.status(state="working", paused=True))
    assert warning_pixels() > 20
