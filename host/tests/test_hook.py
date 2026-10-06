from keypad import hook


def test_slim_drops_tool_output_and_keeps_ask_input():
    p = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls", "secret_blob": "x"},
         "tool_response": {"stdout": "file contents"}, "prompt": "p" * 3000}
    out = hook.slim(p)
    assert "tool_response" not in out and out["tool_input"] == {"command": "ls"} and len(out["prompt"]) == 2001
    q = {"questions": [{"question": "Q?", "options": [{"label": "A"}]}]}
    assert hook.slim({"tool_name": "AskUserQuestion", "tool_input": q})["tool_input"] == q


def test_slim_passes_the_transcript_path_not_its_text():
    out = hook.slim({"session_id": "s", "transcript_path": "/home/u/.claude/projects/p/s.jsonl"})
    assert out["transcript_path"].endswith("s.jsonl") and "transcript_texts" not in out


def test_slim_keeps_permission_suggestions():
    sugg = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "npm test:*"}], "behavior": "allow",
             "destination": "localSettings"}]
    p = {"tool_name": "Bash", "tool_input": {"command": "npm test"}, "permission_suggestions": sugg}
    assert hook.slim(p)["permission_suggestions"] == sugg  # echoed back whole


def test_hook_without_agent_prints_empty_object(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KEYPAD_HOME", str(tmp_path))
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"session_id":"s"}')))
    assert hook.run_hook(["Stop"]) == 0
    assert capsys.readouterr().out == "{}"


def test_a_blocking_hook_waits_for_the_configured_timeout_plus_a_margin(home):
    assert hook.blocking_timeout() == 300 + hook.GRACE  # no config yet: the default
    (home / "config.json").write_text('{"behavior": {"timeout": 60}}')
    assert hook.blocking_timeout() == 60 + hook.GRACE
    (home / "config.json").write_text("{broken")
    assert hook.blocking_timeout() == 300 + hook.GRACE
