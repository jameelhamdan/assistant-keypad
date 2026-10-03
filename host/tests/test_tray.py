from keypad.tray import latest_release, newer


def test_newer_compares_dotted_versions():
    assert newer("v2.1.0", "2.0.3")
    assert newer("2.0.10", "2.0.9")
    assert not newer("2.0.3", "2.0.3")
    assert not newer("2.0.2", "2.0.3")


def test_newer_ignores_trailing_non_numeric_parts():
    assert newer("2.1.0-beta", "2.0.3")  # "2.1.0" still compares higher before the suffix breaks parsing
    assert not newer("dev", "2.0.3")  # no numeric parts at all


def test_latest_release_skips_dev_builds(monkeypatch):
    monkeypatch.setattr("keypad.tray.version", lambda: "dev")
    assert latest_release() == ""


def test_icon_looks_for_the_three_states():
    from keypad.tray import COL_IDLE, COL_OFF, COL_WORKING, tray_look

    kp = [{"id": "kp-1"}]
    assert tray_look({"keypads": []}) == ("off", COL_OFF)  # disconnected
    assert tray_look({"keypads": kp, "paused": True, "sessions": [{"state": "working"}]}) == ("off", COL_OFF)
    assert tray_look({"keypads": kp, "sessions": []}) == ("ready", COL_IDLE)  # available, no sessions
    assert tray_look({"keypads": kp, "sessions": [{"state": "working"}]}) == ("sessions", COL_WORKING)
    assert tray_look({"keypads": kp, "sessions": [{"state": "idle"}]}) == ("sessions", COL_IDLE)
