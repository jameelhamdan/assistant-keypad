import json

from keypad import claudecfg


def test_install_uninstall_preserves_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")  # edit .claude.json directly
    orig = {"model": "opus", "env": {"FOO": "1"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/other"}]}]}}
    (tmp_path / "settings.json").write_text(json.dumps(orig))
    binary = "/Applications/Keypad.app/Contents/MacOS/keypad"
    claudecfg.install(binary, 20)
    claudecfg.install(binary, 20)  # idempotent
    st = claudecfg.check(binary)
    assert st.installed and st.hooks == len(claudecfg.EVENTS)
    assert claudecfg.check("/other/keypad").stale
    m = json.loads((tmp_path / "settings.json").read_text())
    assert len(m["hooks"]["Stop"]) == 2, "foreign Stop hook lost or duplicated"
    assert m["env"] == {"FOO": "1", "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": "20"}

    claudecfg.uninstall()
    m = json.loads((tmp_path / "settings.json").read_text())
    assert m == orig
    assert not claudecfg.check(binary).mcp


def test_unchanged_install_writes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    (tmp_path / "settings.json").write_text("{}")
    for _ in range(3):
        claudecfg.install("/x/keypad", 20)
    assert len(list(tmp_path.glob("settings.json.keypad-backup-*"))) == 1


def test_recognises_go_era_hooks(tmp_path, monkeypatch):
    """Entries written by the earlier Go build look the same and are ours."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "C:\\Keypad\\keypad.exe", "args": ["hook", "Stop"]}]}]}}
    (tmp_path / "settings.json").write_text(json.dumps(old))
    claudecfg.uninstall()
    assert "hooks" not in json.loads((tmp_path / "settings.json").read_text())


def test_hooks_use_keypad_hook_when_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    app = tmp_path / "Keypad.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "keypad").write_text("")
    (app / "keypad-hook").write_text("")
    claudecfg.install(str(app / "keypad"), 20)
    m = json.loads((tmp_path / "claude" / "settings.json").read_text())
    assert m["hooks"]["Stop"][0]["hooks"][0]["command"] == str(app / "keypad-hook")
    assert claudecfg.check(str(app / "keypad")).installed
    assert json.loads((tmp_path / "claude" / ".claude.json").read_text())["mcpServers"]["keypad"]["command"] == str(app / "keypad")
