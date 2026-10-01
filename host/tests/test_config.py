import yaml

from keypad import config


def test_save_clamps_in_place(home):
    c = config.Config()
    c.behavior.timeout = 0
    config.save(c)
    assert c.behavior.timeout == 10


def test_round_trip(home):
    c = config.Config()
    c.behavior.max_continues = 7
    c.shortcuts = [config.Shortcut("Ship it", "Commit and push.")]
    config.save(c)
    raw = yaml.safe_load(config.config_path().read_text())
    assert raw["behavior"]["max_continues"] == 7 and raw["behavior"]["pc_handback"]["enabled"] is True
    back, err = config.load()
    assert err is None and back == c


def test_old_key_map_is_ignored(home):
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text("keys: {allow: 2, deny: 2}\nbehavior: {max_continues: 7}\n")
    c, err = config.load()
    assert err is None and c.behavior.max_continues == 7 and "keys" not in c.to_dict()


def test_old_default_shortcuts_dropped(home):
    old = [{"label": label, "prompt": "x"} for label in config.OLD_DEFAULT_LABELS]
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text(yaml.safe_dump({"shortcuts": old}))
    assert config.load()[0].shortcuts == []
    config.config_path().write_text(yaml.safe_dump({"shortcuts": old[:7]}))  # edited: kept
    assert len(config.load()[0].shortcuts) == 7


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
    c = config.Config.from_dict({"behavior": {"ask_on_stop": "no", "announce_in_context": "yes", "pc_handback": {"enabled": "off"}}})
    assert c.behavior.ask_on_stop is False and c.behavior.announce_in_context is True and c.behavior.pc_handback.enabled is False


def test_removed_settings_are_ignored(home):
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text("workers: {enabled: true}\nbehavior: {timeouts: {permission: 60}, timeout: 120}\n")
    c, err = config.load()
    assert err is None and c.behavior.timeout == 120 and "workers" not in c.to_dict()
