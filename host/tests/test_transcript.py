import json

from keypad.core import transcript as T


def line(**kw) -> str:
    return json.dumps(kw) + "\n"


def asst(*blocks, **kw) -> str:
    return line(type="assistant", uuid=kw.pop("uuid", "a1"), message={"role": "assistant", "content": list(blocks)}, **kw)


def user(content, **kw) -> str:
    return line(type="user", uuid=kw.pop("uuid", "u1"), message={"role": "user", "content": content}, **kw)


def test_parse_keeps_prose_tools_and_errors_only():
    es = T.parse(json.loads(asst(
        {"type": "thinking", "thinking": "secret plan"},
        {"type": "text", "text": "Running the tests."},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q\nsecond line"}})))
    assert [(e.kind, e.text) for e in es] == [("c", "Running the tests."), ("t", "Bash(pytest -q)")]
    assert es[1].tool == "Bash" and es[1].detail == "pytest -q"
    ok = json.loads(user([{"type": "tool_result", "tool_use_id": "t1", "content": "lots of output"}]))
    assert T.parse(ok) == []  # tool output is never mirrored
    bad = json.loads(user([{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom\ntrace"}]))
    assert [(e.kind, e.text) for e in T.parse(bad)] == [("r", "Error: boom")]


def test_parse_skips_sidechains_meta_and_slash_command_echoes():
    assert T.parse(json.loads(asst({"type": "text", "text": "sub"}, isSidechain=True))) == []
    assert T.parse(json.loads(user("meta", isMeta=True))) == []
    assert T.parse(json.loads(user("<command-name>/login</command-name>"))) == []
    assert [e.text for e in T.parse(json.loads(user("fix the build")))] == ["fix the build"]


def test_secrets_are_redacted():
    es = T.parse(json.loads(user("export API_KEY=sk-abcdefghijklmnop1234 now")))
    assert "sk-abcdefghij" not in es[0].text


def test_tail_reads_history_then_only_new_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(user("one") + asst({"type": "text", "text": "two"}) + line(type="ai-title", aiTitle="Fix tests") +
                 line(type="user", cwd="/w/proj", permissionMode="plan", message={"content": "three"}), encoding="utf-8")
    t = T.Tail(str(p), history=2)
    assert [e.text for e in t.poll()] == ["two", "three"]  # the last two entries
    assert (t.title, t.cwd, t.mode) == ("Fix tests", "/w/proj", "plan")
    assert t.poll() == []
    with open(p, "a", encoding="utf-8") as f:
        f.write(asst({"type": "text", "text": "four"}))
    assert [e.text for e in t.poll()] == ["four"]
    assert t.poll() == []


def test_tail_waits_for_a_line_to_be_finished(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("", encoding="utf-8")
    t = T.Tail(str(p))
    assert t.poll() == []
    whole = asst({"type": "text", "text": "hello"})
    with open(p, "a", encoding="utf-8") as f:
        f.write(whole[:20])
    assert t.poll() == []
    with open(p, "a", encoding="utf-8") as f:
        f.write(whole[20:])
    assert [e.text for e in t.poll()] == ["hello"]


def test_tail_survives_missing_and_truncated_files(tmp_path):
    p = tmp_path / "gone.jsonl"
    t = T.Tail(str(p))
    assert t.poll() == []  # no file yet
    p.write_text(user("alpha"), encoding="utf-8")
    assert [e.text for e in t.poll()] == ["alpha"]
    p.write_text(user("b"), encoding="utf-8")  # replaced by a shorter file
    assert [e.text for e in t.poll()] == ["b"]


def test_activity_counts_only_real_conversation_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("", encoding="utf-8")
    t = T.Tail(str(p))
    t.poll()
    with open(p, "a", encoding="utf-8") as f:
        f.write(user("<task-notification>build finished</task-notification>") + line(type="system", content="x") +
                user("<command-name>/clear</command-name>") + user("meta", isMeta=True))
    t.poll()
    assert t.activity == 0, "background notices must not look like you answering"
    with open(p, "a", encoding="utf-8") as f:
        f.write(user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]))
    t.poll()
    assert t.activity == 1  # a tool returned: the request was answered somewhere
