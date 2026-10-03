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


def test_esc_and_encoder_click_leave_it_to_the_pc(kp):
    for key in (5, 0):
        kp.msg({**sc.SCREENS["permission"], "id": f"p-{key}"})
        kp.wait(300)
        kp.key(key)
        assert presses(kp)[-1]["act"] == "pc"


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


def test_mic_button_sends_push_to_talk(kp):
    kp.hold(9, 300)
    mic = [m for m in kp.tx if m["t"] == "mic"]
    assert [m["act"] for m in mic] == ["start", "stop"]


def test_host_silence_shows_waiting_then_recovers(kp):
    kp.wait(8000)
    assert ink(kp.image(), (0, 0, 320, 60)) > 0
    assert any(m["t"] == "hello" for m in kp.tx)   # it keeps announcing itself
    kp.msg(sc.HELLO)                                # the host answers: back to the status screen
    kp.msg(sc.status())
    assert ink(kp.image(), BOTTOM_RIGHT) > 20


def test_arabic_text_is_drawn(kp):
    kp.msg(sc.SCREENS["arabic"])
    assert ink(kp.image(), (10, 10, 310, 60)) > 100
