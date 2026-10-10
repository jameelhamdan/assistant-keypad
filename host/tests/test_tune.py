import json

import pytest
from fakekeypad import first_key
from test_agent import wait_status

from keypad.core import tune


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user"))
    p = tmp_path / "proj"
    p.mkdir()
    return p


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_a_project_without_settings_is_at_default(project):
    assert tune.current(str(project)) == ("default", "default")
    v = tune.view(str(project))
    assert (v["m"], v["e"]) == (0, 0) and v["model"][0] == "default" and v["effort"][-1] == "xhigh"


def test_local_settings_win_over_project_over_user(project, tmp_path):
    write(tmp_path / "user" / "settings.json", {"model": "haiku", "effortLevel": "low"})
    write(project / ".claude" / "settings.json", {"model": "sonnet"})
    assert tune.current(str(project)) == ("sonnet", "low")
    write(project / ".claude" / "settings.local.json", {"model": "opus", "effortLevel": "high"})
    assert tune.current(str(project)) == ("opus", "high")


def test_a_model_set_by_hand_gets_its_own_slot_and_survives_a_save(project):
    write(project / ".claude" / "settings.local.json", {"model": "claude-opus-5-5-very-long-id", "theme": "dark"})
    v = tune.view(str(project))
    assert v["model"][-1] == "claude-opus" and v["m"] == len(tune.MODELS)
    model, effort = tune.choose(str(project), v["m"], 3)
    assert (model, effort) == ("claude-opus-5-5-very-long-id", "high")
    tune.save(str(project), model, effort)
    saved = json.loads((project / ".claude" / "settings.local.json").read_text())
    assert saved == {"model": "claude-opus-5-5-very-long-id", "theme": "dark", "effortLevel": "high"}


def test_default_removes_the_keys_and_keeps_the_rest(project):
    f = project / ".claude" / "settings.local.json"
    write(f, {"model": "opus", "effortLevel": "high", "permissions": {"allow": ["Bash(ls)"]}})
    tune.save(str(project), "default", "default")
    assert json.loads(f.read_text()) == {"permissions": {"allow": ["Bash(ls)"]}}


def test_an_unreadable_settings_file_is_never_overwritten(project):
    f = project / ".claude" / "settings.local.json"
    f.parent.mkdir()
    f.write_text("{not json")
    with pytest.raises(ValueError):
        tune.save(str(project), "opus", "high")
    assert f.read_text() == "{not json"


def test_a_position_off_the_slider_is_refused(project):
    for m, e in ((-1, 0), (0, -1), (len(tune.MODELS), 0), (0, len(tune.EFFORTS))):
        with pytest.raises(ValueError):
            tune.choose(str(project), m, e)


def test_the_keypad_gets_the_sliders_and_saves_them(env, project):
    e = env(first_key)
    e.hook("SessionStart", {"cwd": str(project)})
    st = wait_status(e, lambda st: "tune" in st)
    assert st["tune"]["m"] == 0 and st["tune"]["model"][2] == "opus"
    sel = st["sel"]
    e.a.message(e.hub.get("kp-000001"), {"t": "tune", "sel": sel, "m": 2, "e": 4})
    saved = json.loads((project / ".claude" / "settings.local.json").read_text())
    assert saved == {"model": "opus", "effortLevel": "xhigh"}
    st = wait_status(e, lambda st: st.get("tune", {}).get("m") == 2)
    assert st["tune"]["e"] == 4
    assert any("opus" in t["text"] for t in e.fake.toasts)


def test_no_sliders_without_a_project_folder_and_a_forged_tune_changes_nothing(env, project):
    e = env(first_key)
    e.hook("SessionStart", {"cwd": "/no/such/folder"})
    st = wait_status(e, lambda st: st["sessions"])
    assert "tune" not in st
    e.a.message(e.hub.get("kp-000001"), {"t": "tune", "sel": "nobody", "m": 1, "e": 1})
    e.a.message(e.hub.get("kp-000001"), {"t": "tune", "sel": st["sel"], "m": "x", "e": None})
    assert not (project / ".claude").exists()
