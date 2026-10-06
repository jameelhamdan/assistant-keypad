from keypad.tray import state_word, tray_look


def test_state_words_match_the_keypad():
    assert state_word("tool") == "working" and state_word("permission") == "needs you"
    assert state_word("failed") == "failed" and state_word("idle") == "idle"


def test_tray_icon_colour_follows_what_needs_you():
    assert tray_look({"keypads": [{"id": "kp-1"}], "sessions": [{"state": "permission"}]})[1] != \
        tray_look({"keypads": [{"id": "kp-1"}], "sessions": [{"state": "idle"}]})[1]
