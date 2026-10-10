import json

from keypad import claudecfg


def test_install_uninstall_preserves_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    orig = {"model": "opus", "env": {"FOO": "1"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/other"}]}]}}
    (tmp_path / "settings.json").write_text(json.dumps(orig))
    binary = "/Applications/Keypad.app/Contents/MacOS/keypad"
    claudecfg.install(binary)
    claudecfg.install(binary)  # idempotent
    st = claudecfg.check(binary)
    assert st.installed and st.hooks == len(claudecfg.EVENTS)
    assert claudecfg.check("/other/keypad").stale
    m = json.loads((tmp_path / "settings.json").read_text())
    assert len(m["hooks"]["Stop"]) == 2, "foreign Stop hook lost or duplicated"
    assert m["env"] == {"FOO": "1", "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": "20"}

    claudecfg.uninstall()
    m = json.loads((tmp_path / "settings.json").read_text())
    assert m == orig


def test_unchanged_install_writes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text("{}")
    for _ in range(3):
        claudecfg.install("/x/keypad")
    assert len(list(tmp_path.glob("settings.json.keypad-backup-*"))) == 1


def test_only_decision_hooks_are_installed_and_pretooluse_is_matched(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    claudecfg.install("/x/keypad")
    hooks = json.loads((tmp_path / "settings.json").read_text())["hooks"]
    assert sorted(hooks) == ["PermissionRequest", "PreToolUse", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"]
    assert hooks["PreToolUse"][0]["matcher"] == "AskUserQuestion"  # not run on every tool call
    assert "matcher" not in hooks["Stop"][0]


def test_blocking_hooks_outlive_any_configured_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    claudecfg.install("/x/keypad")
    hooks = json.loads((tmp_path / "settings.json").read_text())["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == claudecfg.BLOCKING_TIMEOUT
    assert hooks["SessionStart"][0]["hooks"][0]["timeout"] == claudecfg.QUICK_TIMEOUT
