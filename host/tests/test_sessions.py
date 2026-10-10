import time

from keypad.core import sessions as S
from keypad.core.text import redact


def test_redact():
    assert redact("export API_KEY=abc123") == "export API_KEY=***"
    assert redact("curl -H 'Authorization: xyz'") == "curl -H 'Authorization: ***"
    assert redact("token sk-abcdefghijklmnop1234") == "token ***"


def test_long_claude_message_kept_whole():
    s = S.Sessions()
    msg = "\n".join(f"Line {i} of a long answer." for i in range(150))  # ~4 KB
    s.add_log("a", {"k": "c", "t": msg})
    assert s.get("a").log[-1]["t"] == msg



def test_new_session_survives_prune():
    s = S.Sessions()
    for i in range(40):
        sid = f"s{i:02d}"
        s.touch(sid, S.WORKING, "Working", "", "/tmp/p")
        assert s.get(sid), f"{sid} pruned as soon as it was created"
    assert s.get("s00") is None


def test_turn_timer():
    s = S.Sessions()
    s.start("a", "/tmp/p")
    s._m["a"].started = time.time() - 3600
    s.touch("a", S.WORKING, "Working", "")
    s.touch("a", S.ASKING, "Permission", "")  # still the same turn
    s.touch("a", S.WORKING, "Allowed", "")
    assert S.wire(s.live())[0]["since"] <= 5


def test_text_fits_keypad_buffers():
    from keypad import proto

    s = proto.fit("نعم" * 50, proto.SESSION_NAME)
    assert len(s.encode()) <= proto.SESSION_NAME and s.endswith("…")
    assert proto.fit("short", 10) == "short"
    scr = proto.fit_screen({"title": "漢" * 100, "q": "?" * 200, "items": ["字" * 40]})
    assert len(scr["title"].encode()) <= proto.SCREEN_TITLE
    assert len(scr["q"].encode()) <= proto.SCREEN_Q
    assert len(scr["items"][0].encode()) <= proto.SCREEN_ITEM




def test_a_session_chosen_by_hand_stays_until_something_needs_you():
    from keypad.core.sessions import ASKING, IDLE, STOPPED, WORKING, Sessions

    s = Sessions()
    s.touch("aaaaaaaa", IDLE, "Ready", "", "/w/one")
    s.touch("bbbbbbbb", IDLE, "Ready", "", "/w/two")
    assert s.current().id == "bbbbbbbb", "the latest activity is shown"
    assert s.select("aaaaaaaa") and s.current().id == "aaaaaaaa"
    s.touch("bbbbbbbb", WORKING, "Working", "", "/w/two")
    assert s.current().id == "aaaaaaaa", "plain activity elsewhere does not take the display away"
    s.touch("bbbbbbbb", ASKING, "Permission", "x")
    assert s.current().id == "bbbbbbbb", "a request does"
    s.select("aaaaaaaa")
    s.touch("bbbbbbbb", STOPPED, "Finished", "")
    assert s.current().id == "bbbbbbbb", "so does Claude finishing"
