import json

from keypad import config


def test_save_clamps_in_place(home):
    c = config.Config()
    c.behavior.timeout = 0
    config.save(c)
    assert c.behavior.timeout == 10


def test_round_trip(home):
    c = config.Config()
    c.behavior.timeout = 77
    config.save(c)
    raw = json.loads(config.config_path().read_text())
    assert raw["behavior"]["timeout"] == 77 and "answer_on" not in raw["behavior"]
    back, err = config.load()
    assert err is None and back == c


def test_unknown_keys_are_ignored(home):
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('{"keys": {"allow": 2}, "behavior": {"timeout": 120, "nope": 1}}')
    c, err = config.load()
    assert err is None and c.behavior.timeout == 120 and set(c.to_dict()) == {"behavior"}


def test_damaged_state_moved_aside(home):
    config.state_path().parent.mkdir(parents=True, exist_ok=True)
    config.state_path().write_text("{broken")
    s, err = config.Store.open()
    assert s is not None and err is not None and "damaged" in str(err)
    assert len(list(home.glob("state.json.damaged-*"))) == 1
    assert s.host_id().startswith("h-")


def test_store_view_hides_key(home):
    s, _ = config.Store.open()
    d = s.update("kp-1", lambda d: setattr(d, "key", "secret"))
    assert d.view()["key"] == "" and d.view()["paired"] is True


def test_string_booleans_in_hand_edited_files():
    c = config.Config.from_dict({"behavior": {"notify_when_finished": "no", "always_for_session": "yes", "answer_on": "nonsense"}})
    assert c.behavior.notify_when_finished is False and c.behavior.always_for_session is True
    assert "answer_on" not in c.to_dict()["behavior"], "settings of older versions are dropped"
