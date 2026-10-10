import json
import threading
import time

from conftest import SID
from fakekeypad import first_key

from keypad import config
from keypad.core import sessions as S
from keypad.core.markdown import BOLD as B


def test_status_shows_the_asking_session(env):
    """A request from another session switches the keypad to it while it is on screen."""
    e = env(None)
    e.hook("SessionStart", {"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
    e.hook("SessionStart", {"session_id": "bbbbbbbb-2", "cwd": "/w/two"})
    e.a.sessions.select("bbbbbbbb")
    cfg = config.Config()
    cfg.behavior.timeout = 2
    e.a.set_config(cfg)
    import threading

    threading.Thread(target=e.hook, args=("PermissionRequest", {"session_id": "aaaaaaaa-1", "cwd": "/w/one", "tool_name": "Bash"}), daemon=True).start()
    wait_status(e, lambda st: st["sel"] == "aaaaaaaa")
    time.sleep(2.5)  # the request timed out and went to the PC: that session still needs you, so the view stays on it
    assert e.fake.status_snapshot()[-1]["sel"] == "aaaaaaaa"


def test_permission_mode_mirrored(env):
    e = env(first_key)
    e.hook("UserPromptSubmit", {"prompt": "hi", "permission_mode": "acceptEdits"})
    wait_status(e, lambda st: st["sessions"] and st["sessions"][0].get("mode") == "acceptEdits")
    e.hook("PermissionRequest", {"tool_name": "Read", "permission_mode": "default"})
    wait_status(e, lambda st: st["sessions"] and st["sessions"][0].get("mode") == "default")


def test_snapshot_marks_sessions_on_the_keypad(env):
    e = env(first_key)
    for i in range(10):  # a keypad lists the latest 8
        e.hook("SessionStart", {"session_id": f"aaaaaaa{i}-1", "cwd": f"/w/p{i}"})
        time.sleep(0.01)
    snap = e.a.snapshot()
    assert sum(x["on_keypad"] for x in snap["sessions"]) == 8
    assert snap["current"] == "aaaaaaa9-1"


def test_sessions_and_pinning(env):
    e = env(first_key)
    e.hook("SessionStart", {"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
    e.hook("SessionStart", {"session_id": "bbbbbbbb-2", "cwd": "/w/two"})
    assert e.a.sessions.current().project == "two"
    e.a.sessions.select("aaaaaaaa")
    e.hook("UserPromptSubmit", {"session_id": "bbbbbbbb-2", "cwd": "/w/two", "prompt": "go"})
    assert e.a.sessions.current().project == "one", "pin not honoured"


def test_snapshot_never_exposes_pairing_keys(env):
    e = env(connect=False)
    e.a.store.update("kp-000002", lambda d: setattr(d, "key", "deadbeef"))
    b = json.dumps(e.a.snapshot())
    assert "deadbeef" not in b and '"paired": true' in b and "null" not in b


def wait_status(e, pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = e.fake.status_snapshot()
        if st and pred(st[-1]):
            return st[-1]
        time.sleep(0.02)
    raise AssertionError(f"status never matched; last: {e.fake.status_snapshot()[-1:]}")


def jl(**kw):
    return json.dumps(kw) + "\n"


def asst(*blocks):
    return jl(type="assistant", message={"content": list(blocks)})


def test_status_follows_the_transcript_live(env, tmp_path):
    e = env(first_key)
    tp = tmp_path / "t.jsonl"
    tp.write_text("", encoding="utf-8")
    e.hook("SessionStart", {"transcript_path": str(tp)})
    e.hook("UserPromptSubmit", {"prompt": "fix the tests"})

    def add(text):
        with open(tp, "a", encoding="utf-8") as f:
            f.write(text)

    add(jl(type="ai-title", aiTitle="Fix tests") + jl(type="user", message={"content": "fix the tests"}))
    add(asst({"type": "text", "text": "Running the tests **first**."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "go test ./..."}}))
    add(jl(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "exit status 1\nlots of output"}]}))
    add(asst({"type": "text", "text": "Checking the test."}))
    want = [{"k": "u", "t": "fix the tests"}, {"k": "c", "t": f"Running the tests {B}first{B}."},
            {"k": "t", "t": "Bash(go test ./...)"}, {"k": "r", "t": "Error: exit status 1"},
            {"k": "c", "t": "Checking the test."}]
    wait_status(e, lambda st: st.get("log") == want and st["sessions"][0].get("name") == "Fix tests")


def one(st):
    return (st["sessions"] or [{}])[0]


def test_transcript_state_follows_tools_but_not_open_requests(env, tmp_path):
    e = env(None)
    tp = tmp_path / "t.jsonl"
    tp.write_text("", encoding="utf-8")
    e.hook("SessionStart", {"transcript_path": str(tp)})
    with open(tp, "a", encoding="utf-8") as f:
        f.write(asst({"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/w/main.go"}}))
    wait_status(e, lambda st: one(st).get("state") == "working" and one(st).get("detail") == "main.go")
    with open(tp, "a", encoding="utf-8") as f:
        f.write(jl(type="user", message={"content": "[Request interrupted by user]"}))
    wait_status(e, lambda st: one(st).get("state") == "idle")
    e.a.sessions.touch(SID, S.ASKING, "Permission", "Bash: ls")  # a request is waiting on someone
    with open(tp, "a", encoding="utf-8") as f:
        f.write(asst({"type": "tool_use", "id": "t2", "name": "Glob", "input": {"pattern": "*.go"}}))
    wait_status(e, lambda st: one(st).get("state") == "asking" and (st.get("log") or [{}])[-1].get("t") == "Glob(*.go)")


def test_pc_answering_first_closes_the_keypad_dialog(env, tmp_path):
    e = env(None)  # the keypad never presses
    tp = tmp_path / "t.jsonl"
    tp.write_text(jl(type="user", message={"content": "go"}), encoding="utf-8")
    e.hook("SessionStart", {"transcript_path": str(tp)})
    result = []
    t = threading.Thread(target=lambda: result.append(e.hook("PermissionRequest", {"tool_name": "Bash", "transcript_path": str(tp)})))
    t.start()
    deadline = time.time() + 3
    while e.fake.last_screen() is None and time.time() < deadline:
        time.sleep(0.01)
    assert e.fake.last_screen() is not None and t.is_alive()
    with open(tp, "a", encoding="utf-8") as f:  # you allowed it at the PC: Claude carries on
        f.write(jl(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}))
    t.join(5)
    assert not t.is_alive() and result == [{}], "the hook should end with no decision once the PC answered"


def test_sessions_already_running_are_discovered(env, home):
    e = env(first_key)
    d = home / "claude" / "projects" / "-w-one"
    d.mkdir(parents=True)
    (d / "aaaaaaaa-1.jsonl").write_text(
        jl(type="user", cwd="/w/one", message={"content": "fix it"}) + asst({"type": "text", "text": "On it."}), encoding="utf-8")
    e.a.discover()
    s = e.a.sessions.get("aaaaaaaa-1")
    assert s and s.project == "one" and [x["t"] for x in s.log] == ["fix it", "On it."]
    assert e.a.sessions.current().id == "aaaaaaaa-1"


def test_status_trims_long_transcript(env):
    from keypad import proto

    e = env(first_key)
    e.hook("SessionStart", {})
    for i in range(4):  # whole Claude messages, fitted to the keypad's limits
        e.a.sessions.add_log(SID, {"k": "c", "t": f"{i} " + "<&>" * 1000 + "é" * 2000})
    # past the keypad's transcript buffer the oldest entries go first
    e.a.mark_dirty()
    st = wait_status(e, lambda st: 0 < len(st.get("log", [])) < 4)
    assert all(len(x["t"].encode()) <= proto.LOG_CLAUDE_TEXT for x in st["log"])
    assert sum(len(x["t"].encode()) + 1 for x in st["log"]) <= proto.LOG_POOL
    assert len(proto.encode(st)) <= proto.MAX_HOST_MSG
    assert st["log"][-1]["t"].startswith("3 ")
    # short lines all fit
    e.a.sessions._m[SID].log.clear()
    for i in range(S.LOG_MAX):
        e.a.sessions.add_log(SID, {"k": "c", "t": f"line {i}"})
    e.a.mark_dirty()
    wait_status(e, lambda st: len(st.get("log", [])) == S.LOG_MAX)


def test_keypad_gets_the_transcript_when_it_changes(env):
    e = env(first_key)
    e.hook("SessionStart", {})
    e.a.sessions.add_log(SID, {"k": "c", "t": "one"})
    e.a.mark_dirty()
    wait_status(e, lambda st: st.get("log") == [{"k": "c", "t": "one"}])
    e.a.sessions.add_log(SID, {"k": "c", "t": "two"})
    e.a.mark_dirty()
    wait_status(e, lambda st: len(st.get("log") or []) == 2)
    assert e.fake.feeds[-1]["full"] == [{"k": "c", "t": "one"}, {"k": "c", "t": "two"}]
    n = len(e.fake.feeds)
    e.a.mark_dirty()
    time.sleep(0.4)
    assert len(e.fake.feeds) == n, "an unchanged transcript is not sent again"
