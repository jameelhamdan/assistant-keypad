import json

from fakekeypad import first_key

from keypad import config


def act(e, name):
    e.a.message(e.hub.get("kp-000001"), {"t": "act", "a": name})


def toast_text(e):
    return [t["text"] for t in e.fake.toasts]


def test_pause_toggles_and_says_so(env):
    e = env(first_key)
    act(e, "pause")
    assert e.a.paused() and any("paused" in t for t in toast_text(e))
    act(e, "pause")
    assert not e.a.paused() and any("resumed" in t for t in toast_text(e))


def test_ask_when_finished_steps_through_always_away_never_and_is_saved(env, home):
    e = env(first_key)
    e.a.set_config(config.Config())  # 60: only when away
    seen = []
    for _ in range(3):
        act(e, "ask")
        seen.append(e.a.config().behavior.ask_when_finished)
    assert seen == [-1, 0, 60]
    assert json.loads(config.config_path().read_text())["behavior"]["ask_when_finished"] == 60


def test_the_finished_alert_toggles(env, home):
    e = env(first_key)
    assert e.a.config().behavior.notify_when_finished is True
    act(e, "alert")
    assert e.a.config().behavior.notify_when_finished is False
    act(e, "alert")
    assert e.a.config().behavior.notify_when_finished is True


def test_brightness_steps_up_and_wraps(env):
    e = env(first_key)
    e.a.store.update("kp-000001", lambda d: setattr(d, "brightness", 50))
    seen = []
    for _ in range(4):
        act(e, "bright")
        seen.append(e.a.store.device("kp-000001").brightness)
    assert seen == [80, 100, 20, 50]


def test_an_unknown_action_changes_nothing(env):
    e = env(first_key)
    before = e.a.config().to_dict()
    act(e, "format-disk")
    assert e.a.config().to_dict() == before and not e.a.paused()
