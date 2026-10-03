import json
import time

from conftest import SID, behavior

from keypad import config
from keypad.core import sessions as S
from keypad.core.text import redact
from keypad.device.fake import first_key, press_label

SUGGEST = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "go test:*"}], "behavior": "allow",
            "destination": "localSettings"}]


def test_permission_allow(env):
    e = env(press_label("Yes"))
    e.hook("SessionStart", {})
    out = e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."}})
    assert behavior(out) == "allow" and "updatedPermissions" not in out["hookSpecificOutput"]["decision"]
    s = e.fake.last_screen()
    assert (s["tpl"], s["title"], s["items"], s["esc"]) == ("select", "Bash command", ["Yes", "No"], "pc")
    assert s["body"] == "go test ./..." and s["q"] == "Do you want to proceed?"


def test_ask_user_question_needs_no_permission_screen(env):
    e = env(press_label("Yes"))
    e.hook("SessionStart", {})
    out = e.hook("PermissionRequest", {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}})
    assert out == {} and e.fake.last_screen() is None


def test_permission_dont_ask_again(env):
    e = env(lambda s: {"key": 2, "act": "pick", "idx": 1})  # number key 2
    out = e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."},
                                       "permission_suggestions": SUGGEST})
    assert e.fake.last_screen()["items"] == ["Yes", "Yes, and don't ask again for go test:*", "No"]
    d = out["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow" and d["updatedPermissions"] == SUGGEST


def test_permission_deny(env):
    e = env(press_label("No"))
    out = e.hook("PermissionRequest", {"tool_name": "Write", "tool_input": {"file_path": "/x/a.go"},
                                       "permission_suggestions": SUGGEST})
    assert behavior(out) == "deny"
    s = e.fake.last_screen()
    assert s["title"] == "Create file" and s["q"] == "Do you want to create a.go?"


def test_permission_to_pc_falls_back(env):
    e = env(press_label("pc"))
    assert e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}) == {}


def test_no_keypad_is_instant_noop(env):
    e = env(connect=False)
    start = time.time()
    assert e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "ls"}}) == {}
    assert time.time() - start < 0.1


def test_paused_is_noop(env):
    e = env(press_label("Yes"))
    e.a.set_paused(True)
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}


def test_timeout_falls_back(env):
    e = env(None)
    cfg = config.Config()
    cfg.behavior.timeout = 1
    e.a.set_config(cfg)
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}


def test_handback_on_pc_input(env):
    e = env(None)
    cfg = config.Config()
    cfg.behavior.pc_handback.enabled = True  # off by default
    e.a.set_config(cfg)
    e.idle = 0  # the user keeps typing at the PC
    start = time.time()
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}
    assert time.time() - start < 4


def test_no_handback_when_pc_is_idle(env):
    e = env(None)
    cfg = config.Config()
    cfg.behavior.timeout = 3
    e.a.set_config(cfg)
    start = time.time()
    e.hook("PermissionRequest", {"tool_name": "Bash"})
    assert time.time() - start >= 2.9, "handed back although nobody used the PC"


def with_shortcuts(e, n=3):
    cfg = config.Config()
    cfg.shortcuts = [config.Shortcut(f"Prompt {i}", f"Do thing number {i}.") for i in range(n)]
    e.a.set_config(cfg)
    return cfg


def test_no_default_shortcuts():
    assert config.Config().shortcuts == []


def test_stop_continue(env):
    e = env(press_label("continue"))
    out = e.hook("Stop", {"last_assistant_message": "All done."})
    assert out["decision"] == "block"
    assert e.a.sessions.continues(SID) == 1
    s = e.fake.last_screen()
    assert (s["tpl"], s["title"], s["items"], s["esc"]) == ("prompt", "Claude finished", ["continue"], "pc")


def test_stop_esc_leaves_it_to_the_pc(env):
    e = env(press_label("pc"))
    assert e.hook("Stop", {}) == {}


def test_stop_with_shortcut(env):
    e = env(lambda s: {"key": 7, "act": "pick", "idx": 3})  # cursor moved to the 3rd saved prompt, Enter
    cfg = with_shortcuts(e)
    out = e.hook("Stop", {})
    assert e.fake.last_screen()["items"] == ["continue", "Prompt 0", "Prompt 1", "Prompt 2"]
    assert out["decision"] == "block" and cfg.shortcuts[2].prompt in out["reason"]


def test_continue_limit(env):
    e = env(press_label("No"))
    cfg = config.Config()
    cfg.behavior.max_continues = 1
    e.a.set_config(cfg)
    e.a.sessions.add_continue(SID)
    assert e.hook("Stop", {}) == {}
    assert e.fake.last_screen()["title"] == "Continue limit reached"


def test_queued_shortcut_kept_at_continue_limit(env):
    e = env(press_label("Yes"))
    cfg = with_shortcuts(e)
    cfg.behavior.max_continues = 1
    e.a.set_config(cfg)
    e.hook("UserPromptSubmit", {"prompt": "hi"})
    e.a.sessions.add_continue(SID)
    e.a.queue_shortcut(1, SID)
    out = e.hook("Stop", {})
    assert out["decision"] == "block" and cfg.shortcuts[1].prompt in out["reason"]


def test_ask_user_question(env):
    def policy(s):
        if s["tpl"] == "multi":
            return {"key": 7, "act": "submit", "sel": [0, 2]}
        return {"key": 2, "act": "pick", "idx": 1}

    e = env(policy)
    inp = {"questions": [
        {"question": "Which DB?", "header": "DB", "options": [{"label": "Postgres"}, {"label": "SQLite"}]},
        {"question": "Checks?", "multiSelect": True, "options": [{"label": "lint"}, {"label": "test"}, {"label": "build"}]},
    ]}
    out = e.hook("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": inp})
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "allow"
    assert h["updatedInput"]["answers"] == {"Which DB?": "SQLite", "Checks?": "lint, build"}
    assert h["updatedInput"]["questions"] == inp["questions"]


def test_shortcut_delivered_on_next_tool(env):
    e = env(first_key)
    cfg = with_shortcuts(e)
    e.hook("SessionStart", {})
    e.hook("UserPromptSubmit", {"prompt": "hi"})
    e.a.queue_shortcut(1, SID)  # busy session -> queued
    out = e.hook("PostToolUse", {"tool_name": "Bash"})
    assert cfg.shortcuts[1].prompt in out["hookSpecificOutput"]["additionalContext"]


def test_shortcut_for_idle_session_waits_for_next_prompt(env):
    e = env(first_key)
    cfg = with_shortcuts(e)
    e.hook("SessionStart", {})  # idle
    e.a.queue_shortcut(0, SID)
    out = e.hook("UserPromptSubmit", {"prompt": "hi"})
    assert cfg.shortcuts[0].prompt in out["hookSpecificOutput"]["additionalContext"]


def test_status_menu_only_with_saved_prompts(env):
    e = env(first_key)
    e.hook("SessionStart", {})
    wait_status(e, lambda st: st["menu"] is False and "keys" not in st)
    with_shortcuts(e)
    wait_status(e, lambda st: st["menu"] is True)


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

    threading.Thread(target=e.hook, args=("PermissionRequest", {"session_id": "aaaaaaaa-1", "cwd": "/w/one",
                                                                "tool_name": "Bash"}), daemon=True).start()
    wait_status(e, lambda st: st["sel"] == "aaaaaaaa")
    wait_status(e, lambda st: st["sel"] == "bbbbbbbb", timeout=4)  # back to the pinned one afterwards


def test_permission_mode_mirrored(env):
    e = env(first_key)
    e.hook("UserPromptSubmit", {"prompt": "hi", "permission_mode": "acceptEdits"})
    wait_status(e, lambda st: st["sessions"] and st["sessions"][0].get("mode") == "acceptEdits")
    e.hook("PreToolUse", {"tool_name": "Read", "permission_mode": "default"})
    wait_status(e, lambda st: st["sessions"] and st["sessions"][0].get("mode") == "default")


def test_snapshot_marks_sessions_on_the_keypad(env):
    e = env(first_key)
    e.hook("SessionStart", {"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
    e.hook("SessionStart", {"session_id": "bbbbbbbb-2", "cwd": "/w/two"})
    e.a.store.update("kp-000001", lambda d: setattr(d, "projects", ["two"]))
    snap = e.a.snapshot()
    assert {x["project"]: x["on_keypad"] for x in snap["sessions"]} == {"one": False, "two": True}
    assert snap["current"] == "bbbbbbbb-2"


def test_project_filter(env):
    e = env(press_label("Yes"))
    e.a.store.update("kp-000001", lambda d: setattr(d, "projects", ["other"]))
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}


def test_sessions_and_pinning(env):
    e = env(first_key)
    e.hook("SessionStart", {"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
    e.hook("SessionStart", {"session_id": "bbbbbbbb-2", "cwd": "/w/two"})
    assert e.a.sessions.current().project == "two"
    e.a.sessions.select("aaaaaaaa")
    e.hook("PreToolUse", {"session_id": "bbbbbbbb-2", "cwd": "/w/two", "tool_name": "Read"})
    assert e.a.sessions.current().project == "one", "pin not honoured"
    assert e.a.sessions.by_pid(42) is not None


def test_redact():
    assert redact("export API_KEY=abc123") == "export API_KEY=***"
    assert redact("curl -H 'Authorization: xyz'") == "curl -H 'Authorization: ***"
    assert redact("token sk-abcdefghijklmnop1234") == "token ***"


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


def test_status_mirrors_transcript(env):
    e = env(first_key)
    e.hook("SessionStart", {})
    e.hook("UserPromptSubmit", {"prompt": "fix the tests", "session_title": "Fix tests"})
    e.hook("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."},
                          "transcript_texts": [{"id": "a1", "text": "Running the tests first."}]})
    e.hook("PostToolUseFailure", {"tool_name": "Bash", "error": "exit status 1\nlots of output"})
    # the same message again (it stays in the transcript tail) must not repeat
    e.hook("PostToolUse", {"tool_name": "Bash", "transcript_texts": [{"id": "a1", "text": "Running the tests first."}]})
    # a message reaching the transcript only after its tool's PreToolUse still lands before that tool
    e.hook("PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "/w/main_test.go"}})
    e.hook("PostToolUse", {"tool_name": "Read", "transcript_texts": [
        {"id": "a1", "text": "Running the tests first."}, {"id": "a2", "text": "Checking the test."}]})
    want = [{"k": "u", "t": "fix the tests"}, {"k": "c", "t": "Running the tests first."},
            {"k": "t", "t": "Bash(go test ./...)"}, {"k": "r", "t": "Error: exit status 1"},
            {"k": "c", "t": "Checking the test."}, {"k": "t", "t": "Read(main_test.go)"}]
    wait_status(e, lambda st: st.get("log") == want and st["sessions"][0].get("name") == "Fix tests")


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


def test_long_claude_message_kept_whole():
    s = S.Sessions()
    msg = "\n".join(f"Line {i} of a long answer." for i in range(150))  # ~4 KB
    s.add_claude_texts("a", [{"id": "m1", "text": msg}], before_tool=False)
    assert s.get("a").log[-1]["t"] == msg


def test_markdown_for_the_keypad():
    from keypad.core.text import BOLD as B
    from keypad.core.text import CODE as C
    from keypad.core.text import markdown

    md = ("## Summary\n\nAll **12 tests** pass in `pytest`; see [the docs](https://x.y/z).\n\n"
          "* first\n  + nested\n1. one\n\n```python\nprint('hi')\n\nx = 1\n```\n\n"
          "| a | b |\n|---|:-:|\n| 1 | 2 |\n\n---\n\n*emphasis* and snake_case_name and 2 * 3 * 4")
    assert markdown(md).split("\n") == [
        f"{B}Summary{B}", "", f"All {B}12 tests{B} pass in {C}pytest{C}; see the docs.", "",
        "- first", "  - nested", "1. one", "", f"{C}print('hi'){C}", "", f"{C}x = 1{C}", "",
        "| a | b |", "| 1 | 2 |", "", "emphasis and snake_case_name and 2 * 3 * 4"]
    assert markdown("### **Bold** heading `x`") == f"{B}Bold heading {C}x{C}{B}"
    assert markdown("a `b") == "a `b" and markdown("\x01raw\x02") == "raw"


def test_new_session_survives_prune():
    s = S.Sessions()
    for i in range(40):
        sid = f"s{i:02d}"
        s.touch(sid, S.WORKING, "Working", "", "/tmp/p")
        assert s.get(sid), f"{sid} pruned as soon as it was created"
    assert s.get("s00") is None


def test_turn_timer():
    s = S.Sessions()
    s.start("a", "/tmp/p", None)
    s._m["a"].started = time.time() - 3600
    s.touch("a", S.THINKING, "Working", "")
    s.touch("a", S.PERMISSION, "Permission", "")  # still the same turn
    s.touch("a", S.WORKING, "Allowed", "")
    assert S.wire(s.live())[0]["since"] <= 5


def test_text_fits_keypad_buffers():
    from keypad import proto

    s = proto.fit("نعم" * 50, proto.SESSION_NAME)
    assert len(s.encode()) <= proto.SESSION_NAME and s.endswith("…")
    assert proto.fit("short", 10) == "short"
    scr = proto.fit_screen({"title": "漢" * 100, "q": "?" * 200, "items": ["字" * 40], "notes": ["Ä" * 30]})
    assert len(scr["title"].encode()) <= proto.SCREEN_TITLE
    assert len(scr["q"].encode()) <= proto.SCREEN_Q
    assert len(scr["items"][0].encode()) <= proto.SCREEN_ITEM
    assert len(scr["notes"][0].encode()) <= proto.SCREEN_NOTE


def test_press_must_match_the_screen():
    from keypad.core.dialogs import press_matches

    sel = {"tpl": "select", "esc": "pc", "items": [f"o{i}" for i in range(10)]}
    assert press_matches(sel, {"key": 1, "act": "pick", "idx": 0}), "number key on its option"
    assert press_matches(sel, {"key": 3, "act": "pick", "idx": 2})
    assert press_matches(sel, {"key": 7, "act": "pick", "idx": 9}), "Enter on any option"
    assert not press_matches(sel, {"key": 0, "act": "pick", "idx": 0}), "the encoder click never decides"
    assert press_matches(sel, {"key": 5, "act": "pc"})
    assert press_matches(sel, {"key": 0, "act": "pc"}), "the encoder click is Esc"
    assert not press_matches(sel, {"key": 2, "act": "pick", "idx": 0}), "number key on another option"
    assert not press_matches(sel, {"key": 4, "act": "pick", "idx": 3}), "4 is up, not a pick"
    assert not press_matches(sel, {"key": 7, "act": "pick", "idx": 10}), "no such option"
    assert not press_matches(sel, {"key": 7, "act": "pc"}), "Esc is key 5"
    assert not press_matches(sel, {"key": 7, "act": "allow"}), "not an action the screen offers"
    multi = {"tpl": "multi", "esc": "pc", "items": ["a", "b", "c"]}
    assert press_matches(multi, {"key": 7, "act": "submit", "sel": [0, 2]})
    assert not press_matches(multi, {"key": 7, "act": "submit", "sel": [3]})
    assert not press_matches(multi, {"key": 7, "act": "submit", "sel": []})
    assert not press_matches(multi, {"key": 7, "act": "pick", "idx": 0})


def test_forged_press_is_not_a_decision(env):
    e = env(lambda s: {"key": 3, "act": "pick", "idx": 0})  # claims option 1 (Yes) from key 3
    cfg = config.Config()
    cfg.behavior.timeout = 1
    e.a.set_config(cfg)
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}


def test_permission_shows_the_whole_command(env):
    from keypad import hook

    e = env(press_label("pc"))
    cmd = "cat <<'EOF' > deploy.sh\n" + "\n".join(f"step {i} --flag value" for i in range(40)) + "\nEOF\nrm -rf /tmp/x"
    p = hook.slim({"session_id": SID, "cwd": "/w/p", "tool_name": "Bash", "tool_input": {"command": cmd}})
    e.hook("PermissionRequest", p)
    assert e.fake.last_screen()["body"].endswith("rm -rf /tmp/x"), "the end of the command was cut"


def test_question_descriptions_and_long_text(env):
    e = env(lambda s: {"key": 1, "act": "pick", "idx": 0})
    long_q = "Which of these deployment strategies should I use for the staging environment, given the latency and cost constraints we discussed above?"
    inp = {"questions": [
        {"question": "Which DB?", "options": [{"label": "Postgres", "description": "Relational, battle-tested"},
                                              {"label": "SQLite"}]},
        {"question": long_q, "options": [{"label": "Blue/green"}, {"label": "Rolling"}]},
    ]}
    e.hook("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": inp})
    first, second = e.fake.screens[-2], e.fake.screens[-1]
    assert first["q"] == "Which DB?" and first["body"] == "1. Postgres: Relational, battle-tested"
    assert second["q"] == "" and second["body"] == long_q, "a long question goes in the scrollable body, whole"


def test_stop_not_asked_while_at_the_pc(env):
    e = env(press_label("continue"))
    e.a.presence = lambda: (5.0, True)  # input 5 s ago: at the PC
    start = time.time()
    assert e.hook("Stop", {}) == {} and time.time() - start < 0.5
    assert e.fake.last_screen() is None, "nothing shown on the keypad"
    e.a.presence = lambda: (120.0, True)  # away: asked
    assert e.hook("Stop", {})["decision"] == "block"
    cfg = config.Config()
    cfg.behavior.stop_when_away = 0  # "Always"
    e.a.set_config(cfg)
    e.a.presence = lambda: (1.0, True)
    assert e.hook("Stop", {})["decision"] == "block"


def test_edit_approval_shows_the_diff(env):
    from keypad import hook

    e = env(press_label("pc"))
    inp = {"file_path": "/w/app.py", "old_string": "x = 1\ny = 2\n", "new_string": "x = 1\ny = 3\n"}
    e.hook("PermissionRequest", hook.slim({"session_id": SID, "cwd": "/w/p", "tool_name": "Edit",
                                                                 "tool_input": inp}))
    s = e.fake.last_screen()
    assert s["diff"] is True and s["body"].split("\n") == ["/w/app.py", "  x = 1", "- y = 2", "+ y = 3"]


def test_sessions_survive_an_agent_restart(env):
    e = env(first_key)
    e.hook("SessionStart", {"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
    e.hook("UserPromptSubmit", {"session_id": "aaaaaaaa-1", "cwd": "/w/one", "prompt": "fix it",
                                "transcript_texts": [{"id": "m1", "text": "On it."}]})
    e.a._save_sessions()
    e2 = env(first_key)  # a new agent on the same data folder
    s = e2.a.sessions.get("aaaaaaaa-1")
    assert s and s.project == "one" and [x["t"] for x in s.log] == ["On it.", "fix it"]
    assert e2.a.sessions.current().id == "aaaaaaaa-1"
    # a message already shown is not repeated by the next hook
    e2.hook("PostToolUse", {"session_id": "aaaaaaaa-1", "cwd": "/w/one", "transcript_texts": [{"id": "m1", "text": "On it."}]})
    assert sum(x["t"] == "On it." for x in e2.a.sessions.get("aaaaaaaa-1").log) == 1
