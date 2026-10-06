from keypad.tray import tray_look


def look(phase):
    return tray_look({"keypads": [{"id": "kp-1"}], "sessions": [{"phase": phase}]})[1]


def test_tray_icon_colour_follows_what_needs_you():
    assert look("asking") != look("idle")
    assert look("working") not in (look("asking"), look("idle"))
