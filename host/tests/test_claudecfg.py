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


def test_hooks_run_the_keypad_binary_and_replace_older_keypad_hook_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/old/keypad-hook", "args": ["hook", "Stop"]}]}]}}
    (tmp_path / "settings.json").write_text(json.dumps(old))
    claudecfg.install("/app/keypad", 20)
    m = json.loads((tmp_path / "settings.json").read_text())
    assert [g["hooks"][0]["command"] for g in m["hooks"]["Stop"]] == ["/app/keypad"]  # the separate hook binary is gone
    assert claudecfg.check("/app/keypad").installed


def test_install_drops_the_mcp_server_older_versions_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"keypad": {"command": "/x/keypad"}, "other": {"command": "o"}}}))
    claudecfg.install("/x/keypad", 20)
    assert json.loads((tmp_path / ".claude.json").read_text())["mcpServers"] == {"other": {"command": "o"}}


def test_only_decision_hooks_are_installed_and_pretooluse_is_matched(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(claudecfg, "find_claude", lambda: "")
    claudecfg.install("/x/keypad", 20)
    hooks = json.loads((tmp_path / "settings.json").read_text())["hooks"]
    assert sorted(hooks) == ["PermissionRequest", "PreToolUse", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"]
    assert hooks["PreToolUse"][0]["matcher"] == "AskUserQuestion"  # not run on every tool call
    assert "matcher" not in hooks["Stop"][0]
