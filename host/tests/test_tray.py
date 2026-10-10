from keypad.trayicon import tray_look


def look(phase):
    return tray_look({"keypads": [{"id": "kp-1"}], "sessions": [{"state": phase}]})[1]


def test_tray_icon_colour_follows_what_needs_you():
    assert look("asking") != look("idle")
    assert look("working") not in (look("asking"), look("idle"))


def test_setting_up_a_keypad_is_native_dialogs_and_the_agent_api_only(monkeypatch):
    from keypad import dialog, osutil, tray

    calls, said = [], []
    state = {"keypads": [{"id": "kp-000001", "link": "usb"}], "devices": [], "problems": {}}

    def call(method, path, body=None, timeout=30):
        calls.append((method, path, body))
        if path.endswith("/provision"):
            state["keypads"] = [{"id": "kp-000001", "link": "wifi"}]
        return state if path == "/status" else {"ok": True}

    answers = iter(["Desk keypad", "HomeNet", "hunter2"])
    monkeypatch.setattr(tray, "call", call)
    monkeypatch.setattr(dialog, "input", lambda *a, **k: next(answers))
    monkeypatch.setattr(dialog, "alert", lambda title, msg: said.append(msg))
    monkeypatch.setattr(dialog, "notify", lambda *a: None)
    monkeypatch.setattr(osutil, "ssid", lambda: "HomeNet")
    monkeypatch.setattr(tray.time, "sleep", lambda s: None)
    t = object.__new__(tray.Tray)
    t.snap = {}
    t.add_keypad()
    assert ("POST", "/pairing", None) in calls
    assert ("POST", "/devices/kp-000001/provision", {"ssid": "HomeNet", "pass": "hunter2", "name": "Desk keypad"}) in [c[:2] + (c[2],) for c in calls]
    assert said and "connected over Wi-Fi" in said[0]


def test_no_keypad_over_usb_says_so(monkeypatch):
    from keypad import dialog, tray

    said = []
    monkeypatch.setattr(tray, "call", lambda *a, **k: {"keypads": [], "devices": []})
    monkeypatch.setattr(dialog, "alert", lambda title, msg: said.append(msg))
    monkeypatch.setattr(dialog, "notify", lambda *a: None)
    monkeypatch.setattr(tray.time, "sleep", lambda s: None)
    ticks = iter(range(0, 1000, 20))
    monkeypatch.setattr(tray.time, "monotonic", lambda: next(ticks))
    t = object.__new__(tray.Tray)
    t.snap = {}
    t.add_keypad()
    assert said and "No keypad found" in said[0]
