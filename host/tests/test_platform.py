"""The OS-facing pieces that can be checked on any machine: generated files, encodings, return shapes."""

import plistlib
import xml.etree.ElementTree as ET

from keypad import dialog, osutil, service
from keypad.tray import session_label, synced
from keypad.trayicon import icon_image, tray_look


def test_the_windows_task_is_valid_xml_that_restarts_on_failure_and_has_no_repetition():
    root = ET.fromstring(service._task_xml(r"C:\Keypad\keypadw.exe", r"PC\me & you"))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:LogonTrigger", ns) is not None and root.find(".//t:TimeTrigger", ns) is None
    assert root.find(".//t:RestartOnFailure", ns) is not None
    assert root.find(".//t:Exec/t:Arguments", ns).text == "tray --quiet"


def test_the_launchd_plist_parses_and_restarts_only_after_a_failure():
    d = plistlib.loads(service._plist("com.example.keypad", "/Applications/Keypad.app/Contents/MacOS/keypad", "tray --quiet").encode())
    assert d["ProgramArguments"] == ["/Applications/Keypad.app/Contents/MacOS/keypad", "tray", "--quiet"]
    assert d["KeepAlive"] == {"SuccessfulExit": False}


def test_powershell_scripts_run_from_a_file_and_values_travel_in_the_environment():
    cmd = dialog._powershell("C:/x/keypad-dialog.ps1")
    assert cmd[-2:] == ["-File", "C:/x/keypad-dialog.ps1"] and "-EncodedCommand" not in cmd
    assert "KP_MSG" in dialog._FORM and "$env:KP_TITLE" in dialog._FORM, "titles and messages are read from the environment"


def test_osutil_shapes():
    idle, known = osutil.idle_any()
    assert isinstance(idle, float) and isinstance(known, bool)
    assert osutil.run([__import__("sys").executable, "-c", "print('hi')"]).stdout.strip() == "hi"


def test_tray_icon_and_labels():
    for mode in ("sessions", "ready", "off"):
        assert icon_image((1, 2, 3), mode).size == (64, 64)
    assert session_label({"id": "abcdef123456", "project": "api", "name": "Fix tests"}) == "Fix tests (api)"
    assert session_label({"id": "abcdef123456", "project": "", "name": ""}) == "abcdef12"
    snap = {"sessions": [{"id": "a", "on_keypad": True}, {"id": "b", "on_keypad": False}]}
    assert [x["id"] for x in synced(snap)] == ["a"]


def test_the_tray_is_off_when_paused_or_without_a_keypad():
    s = {"keypads": [{"id": "kp-1"}], "sessions": [{"state": "working"}]}
    assert tray_look(s)[0] == "sessions" and tray_look({**s, "paused": True})[0] == "off"
    assert tray_look({**s, "keypads": []})[0] == "off" and tray_look({**s, "sessions": []})[0] == "ready"


def test_a_powershell_launch_that_prints_nothing_is_tried_again(monkeypatch):
    calls = []

    class R:
        def __init__(self, out):
            self.stdout = out

    outs = iter([b"", b"", b"OK\nhello"])
    monkeypatch.setattr(dialog.subprocess, "run", lambda *a, **k: calls.append(1) or R(next(outs)))
    assert dialog._run_powershell("x", {}) == "OK\nhello" and len(calls) == 3
    outs = iter([b"", b"", b""])
    assert dialog._run_powershell("x", {}) is None, "three silent launches: give up (the caller treats it as Cancel)"


def test_a_silent_uninstall_never_deletes_the_users_settings_and_pairing_keys():
    """/SUPPRESSMSGBOXES makes Inno answer Yes to the 'also delete settings?' box: found by uninstalling for real."""
    from pathlib import Path

    iss = (Path(__file__).resolve().parents[2] / "packaging" / "windows" / "keypad.iss").read_text(encoding="utf-8")
    step = iss[iss.index("CurUninstallStepChanged"):]
    assert "not UninstallSilent" in step and step.index("not UninstallSilent") < step.index("MsgBox")
