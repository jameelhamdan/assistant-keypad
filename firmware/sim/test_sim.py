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


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_knob_and_press_pick_option(kp, idx):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)  # stale-press guard
    kp.pick(idx + 1)
    assert presses(kp)[-1] == {"t": "press", "id": "p-1", "key": 7, "act": "pick", "idx": idx}


@pytest.mark.parametrize("key", [1, 2, 3, 4, 8])
def test_number_keys_pick_nothing(kp, key):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.key(key)
    assert not presses(kp)


def test_enter_selects_the_cursor(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.turn(1)  # down
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
    kp.turn(1)  # move to the second option
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
    kp.key(7)    # tick the first
    kp.turn(2)
    kp.key(7)    # tick the third
    kp.turn(1)   # to Submit
    kp.key(7)
    p = presses(kp)[-1]
    assert p["act"] == "submit" and p["sel"] == [0, 2]


def test_a_press_is_answered_once(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.key(7)
    kp.key(7)
    assert len(presses(kp)) == 1


def test_finished_screen_offers_continue(kp):
    kp.msg(sc.SCREENS["finished"])
    kp.wait(300)
    kp.turn(1)
    kp.key(7)
    assert presses(kp)[-1]["idx"] == 1


def test_session_list_selects_a_session(kp):
    a, b = dict(sc.SESSION, id="aaaa0001"), dict(sc.SESSION, id="bbbb0002", project="api", name="Docs")
    kp.msg(sc.status(sessions=[a, b]))
    kp.key(6)
    kp.turn(1)
    kp.key(7)   # the second session
    s = [m for m in kp.tx if m["t"] == "session"]
    assert s and s[-1] == {"t": "session", "act": "select", "sid": "bbbb0002"}


def test_the_session_list_has_only_sessions_and_the_knob_walks_them(kp):
    a, b, c = (dict(sc.SESSION, id=f"{n}0000", project=n) for n in ("aaaa", "bbbb", "cccc"))
    kp.msg({**sc.status(sessions=[a, b, c])})
    kp.key(6)
    kp.turn(5)   # past the end: stops on the last session
    kp.key(7)
    s = [m for m in kp.tx if m["t"] == "session"]
    assert s[-1]["sid"] == "cccc0000"
    assert not [m for m in kp.tx if m.get("act") == "follow"], "there is no follow row any more"


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
    assert [m["t"] for m in kp.tx] == ["hello"] and kp.tx[0]["v"] == 4
    kp.msg(sc.status())
    assert ink(kp.image(), BOTTOM_RIGHT) > 20


def test_keys_4_and_8_never_move_a_cursor_or_decide_on_a_dialog(kp):
    kp.msg({**sc.SCREENS["question"], "id": "q-nav", "items": [f"option {i}" for i in range(1, 11)]})
    kp.wait(300)
    kp.key(8)
    kp.key(4)
    kp.hold(8, 1500)
    assert not presses(kp), "4 and 8 do nothing: moving is the knob's job"
    kp.key(7)
    assert presses(kp)[-1]["idx"] == 0, "the cursor did not move"


def test_the_knob_moves_one_row_per_detent_in_a_list_however_fast(kp):
    kp.msg({**sc.SCREENS["question"], "id": "q-fast", "items": [f"option {i}" for i in range(1, 11)]})
    kp.wait(300)
    kp.spin(3, 20)   # a fast hand: still one row per detent in a list
    kp.key(7)
    assert presses(kp)[-1]["idx"] == 3


def test_a_fast_knob_scrolls_the_transcript_further_than_a_slow_one(kp):
    kp.msg(sc.status(state="idle", log=[{"k": "c", "t": "\n".join(f"line {i}" for i in range(80))}]))

    def scrolled_back(detents, gap_ms):
        kp.key(5)   # back to the newest line
        kp.wait(1000)
        kp.spin(-detents, gap_ms)
        return kp.image().tobytes()

    fast = scrolled_back(3, 20)      # three detents in a rush: 1 + 4 + 4 rows
    slow9 = scrolled_back(9, 300)    # nine unhurried detents: 9 rows
    slow3 = scrolled_back(3, 300)    # three unhurried detents: 3 rows
    assert fast == slow9 and fast != slow3


def test_a_held_enter_key_does_not_repeat(kp):
    kp.msg(sc.SCREENS["permission"])
    kp.wait(300)
    kp.hold(7, 1500)
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


def test_waiting_says_when_the_computer_was_last_seen(kp):
    kp.msg(sc.status())                 # the host was here
    kp.host(False)
    kp.wait(9000)
    before = kp.image()
    kp.wait(120000)                     # two minutes later the line reads "last seen 2m ago"
    after = kp.image()
    row = (30, 76, 320, 96)
    assert ink(before, row) > 100 and ink(after, row) > 100
    assert before.crop(row).tobytes() != after.crop(row).tobytes(), "the age moves on"


def acts(kp):
    return [m["a"] for m in kp.tx if m["t"] == "act"]


def test_status_keys_send_quick_actions(kp):
    kp.msg(sc.status())
    kp.wait(300)
    kp.tx.clear()
    for key in (1, 3, 4, 8):
        kp.key(key)
    assert acts(kp) == ["pause", "ask", "bright", "alert"]
    assert not presses(kp)


def test_key_2_shows_the_next_session(kp):
    a, b = dict(sc.SESSION, id="aaaa0001"), dict(sc.SESSION, id="bbbb0002", project="api", name="Docs")
    kp.msg(sc.status(sessions=[a, b]))
    kp.wait(300)
    kp.tx.clear()
    kp.key(2)
    assert [m["sid"] for m in kp.tx if m["t"] == "session"] == ["bbbb0002"]


def test_the_quick_action_keys_do_nothing_on_a_dialog_or_the_finished_screen(kp):
    for name in ("permission", "finished"):
        kp.msg(sc.SCREENS[name])
        kp.wait(400)
        kp.tx.clear()
        for key in (1, 2, 3, 4, 8):
            kp.key(key)
        assert not [m for m in kp.tx if m["t"] in ("act", "press", "session")], name
        kp.msg({"t": "close", "id": sc.SCREENS[name]["id"], "why": "pc"})
