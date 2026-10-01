import json

from keypad import hook


def write(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n")
    return str(p)


def text(uuid, t, side=False):
    return {"type": "assistant", "uuid": uuid, "isSidechain": side, "message": {"content": [{"type": "text", "text": t}]}}


def test_transcript_tail(tmp_path):
    p = write(tmp_path, [
        {"type": "ai-title", "aiTitle": "Project review"},
        {"type": "user", "uuid": "u1", "message": {"content": "hello"}},
        {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "thinking", "thinking": "secret"}]}},
        text("a2", "Reading the code."),
        text("a3", "subagent chatter", side=True),
        {"type": "assistant", "uuid": "a4", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "uuid": "u2", "message": {"content": [{"type": "tool_result", "content": '{"type":"text","text":"tool output"}'}]}},
        text("a5", "All fixed."),
    ])
    texts, title = hook.transcript_tail(p)
    assert title == "Project review"
    assert [(t["id"], t["text"]) for t in texts] == [("a2", "Reading the code."), ("a5", "All fixed.")]


def test_transcript_window_widens(tmp_path, monkeypatch):
    """Messages pushed back by huge tool calls are still found."""
    monkeypatch.setattr(hook, "TRANSCRIPT_WINDOW", 1024)
    big = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 3000}]}}
    p = write(tmp_path, [text(f"a{i}", f"message {i}") if i % 2 == 0 else big for i in range(12)])
    texts, _ = hook.transcript_tail(p)
    assert [t["text"] for t in texts] == ["message 4", "message 6", "message 8", "message 10"]


def test_slim_drops_tool_output_and_keeps_ask_input():
    p = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls", "secret_blob": "x"},
         "tool_response": {"stdout": "file contents"}, "prompt": "p" * 3000}
    out = hook.slim(p)
    assert "tool_response" not in out and out["tool_input"] == {"command": "ls"} and len(out["prompt"]) == 2001
    q = {"questions": [{"question": "Q?", "options": [{"label": "A"}]}]}
    assert hook.slim({"tool_name": "AskUserQuestion", "tool_input": q})["tool_input"] == q


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
