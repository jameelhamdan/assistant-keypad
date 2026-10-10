import time

from conftest import SID, behavior, with_shortcuts
from fakekeypad import first_key, press_label

from keypad import config

SUGGEST = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "go test:*"}], "behavior": "allow",
            "destination": "localSettings"}]


def test_permission_allow(env):
    e = env(press_label("Yes"))
    e.hook("SessionStart", {})
    out = e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."}})
    assert behavior(out) == "allow" and "updatedPermissions" not in out["hookSpecificOutput"]["decision"]
    s = e.fake.last_screen()
    assert (s["tpl"], s["title"], s["items"]) == ("select", "Bash command", ["Yes", "No"])
    assert "esc" not in s, "a permission dialog is answered on the keypad: no way out to the PC"
    assert s["body"] == "go test ./..." and s["q"] == "Do you want to proceed?"


def test_ask_user_question_needs_no_permission_screen(env):
    e = env(press_label("Yes"))
    e.hook("SessionStart", {})
    out = e.hook("PermissionRequest", {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}})
    assert out == {} and e.fake.last_screen() is None


def test_permission_dont_ask_again(env):
    e = env(lambda s: {"key": 2, "act": "pick", "idx": 1})  # number key 2
    out = e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."}, "permission_suggestions": SUGGEST})
    assert e.fake.last_screen()["items"] == ["Yes", "Yes, and don't ask again for go test:*", "No"]
    d = out["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow" and d["updatedPermissions"] == SUGGEST


def test_permission_deny(env):
    e = env(press_label("No"))
    out = e.hook("PermissionRequest", {"tool_name": "Write", "tool_input": {"file_path": "/x/a.go"}, "permission_suggestions": SUGGEST})
    assert behavior(out) == "deny"
    s = e.fake.last_screen()
    assert s["title"] == "Create file" and s["q"] == "Do you want to create a.go?"


def test_esc_does_nothing_on_a_permission_dialog(env):
    e = env(press_label("pc"))  # the keypad sends Esc: there is no such way out, so it is ignored
    cfg = config.Config()
    cfg.behavior.timeout = 2
    e.a.set_config(cfg)
    start = time.time()
    assert e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}) == {}
    assert time.time() - start >= 1.9, "Esc must not end the request: only an answer, a timeout or a lost keypad does"


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


def test_typing_at_the_pc_does_not_take_a_request_off_the_keypad(env):
    e = env(None)
    cfg = config.Config()
    cfg.behavior.timeout = 3
    e.a.set_config(cfg)
    e.a.presence = lambda: (0.0, True)  # the user is typing right now
    start = time.time()
    e.hook("PermissionRequest", {"tool_name": "Bash"})
    assert time.time() - start >= 2.9, "the keypad's request was handed back although nobody answered"


def test_a_first_run_starts_with_useful_saved_prompts(home):
    assert config.Config().shortcuts == [], "the dataclass itself has none: tests and hand-written files stay as written"
    c, err = config.load()
    assert err is None and [s.label for s in c.shortcuts][:3] == ["Tests", "Commit", "Review"]
    assert all(len(s.label) <= 12 and s.prompt for s in c.shortcuts), "labels fit a quick key"
    c.shortcuts = []
    config.save(c)  # the user removed them all: that stays
    assert config.load()[0].shortcuts == []


def test_stop_continue(env):
    e = env(press_label("continue"))
    out = e.hook("Stop", {"last_assistant_message": "All done."})
    assert out["decision"] == "block"
    assert e.a.sessions.continues(SID) == 1
    s = e.fake.last_screen()
    assert (s["tpl"], s["title"], s["items"], s["esc"]) == ("prompt", "Claude finished", ["continue"], "done")


def test_stop_done_ends_it_there(env):
    e = env(lambda s: {"key": 5, "act": "done"})  # 5 = done: no continue, no saved prompt
    assert e.hook("Stop", {}) == {}


def test_stop_with_shortcut(env):
    e = env(lambda s: {"key": 7, "act": "pick", "idx": 3})  # cursor moved to the 3rd saved prompt, Enter
    cfg = with_shortcuts(e)
    out = e.hook("Stop", {})
    assert e.fake.last_screen()["items"] == ["continue", "Prompt 0", "Prompt 1", "Prompt 2"]
    assert out["decision"] == "block" and cfg.shortcuts[2].prompt in out["reason"]


def test_continuing_has_no_confirmation_screen_of_its_own(env, monkeypatch):
    e = env(press_label("continue"))
    e.a.presence = lambda: (600.0, True)
    for _ in range(25):
        e.a.sessions.add_continue(SID)
    out = e.hook("Stop", {})
    assert out["decision"] == "block", "Claude Code's own cap ends the loop, not a dialog"
    assert e.fake.last_screen()["title"] == "Claude finished"


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


def test_shortcut_for_idle_session_waits_for_next_prompt(env):
    e = env(first_key)
    cfg = with_shortcuts(e)
    e.hook("SessionStart", {})  # idle
    e.a.shortcuts.queue(0, SID)
    out = e.hook("UserPromptSubmit", {"prompt": "hi"})
    assert cfg.shortcuts[0].prompt in out["hookSpecificOutput"]["additionalContext"]


def test_forged_press_is_not_a_decision(env):
    e = env(lambda s: {"key": 3, "act": "pick", "idx": 0})  # claims option 1 (Yes) from key 3
    cfg = config.Config()
    cfg.behavior.timeout = 1
    e.a.set_config(cfg)
    assert e.hook("PermissionRequest", {"tool_name": "Bash"}) == {}


def test_permission_shows_the_whole_command(env):
    from keypad import hook

    e = env(first_key)
    cmd = "cat <<'EOF' > deploy.sh\n" + "\n".join(f"step {i} --flag value" for i in range(40)) + "\nEOF\nrm -rf /tmp/x"
    p = hook.slim({"session_id": SID, "cwd": "/w/p", "tool_name": "Bash", "tool_input": {"command": cmd}})
    e.hook("PermissionRequest", p)
    assert e.fake.last_screen()["body"].endswith("rm -rf /tmp/x"), "the end of the command was cut"


def test_question_descriptions_and_long_text(env):
    e = env(lambda s: {"key": 1, "act": "pick", "idx": 0})
    long_q = "Which of these deployment strategies should I use for the staging environment, given the latency and cost constraints we discussed above?"
    inp = {"questions": [
        {"question": "Which DB?", "options": [{"label": "Postgres", "description": "Relational, battle-tested"},{"label": "SQLite"}]},
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
    cfg.behavior.ask_when_finished = 0  # "Always"
    e.a.set_config(cfg)
    e.a.presence = lambda: (1.0, True)
    assert e.hook("Stop", {})["decision"] == "block"


def test_edit_approval_shows_the_diff(env):
    from keypad import hook

    e = env(first_key)
    inp = {"file_path": "/w/app.py", "old_string": "x = 1\ny = 2\n", "new_string": "x = 1\ny = 3\n"}
    e.hook("PermissionRequest", hook.slim({"session_id": SID, "cwd": "/w/p", "tool_name": "Edit","tool_input": inp}))
    s = e.fake.last_screen()
    assert s["diff"] is True and s["body"].split("\n") == ["/w/app.py", "  x = 1", "- y = 2", "+ y = 3"]


def test_dont_ask_again_can_last_for_the_session_only(env):
    e = env(lambda s: {"key": 2, "act": "pick", "idx": 1})
    cfg = config.Config()
    cfg.behavior.always_for_session = True
    e.a.set_config(cfg)
    out = e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "go test ./..."}, "permission_suggestions": SUGGEST})
    assert e.fake.last_screen()["items"][1] == "Yes, and don't ask again this session for go test:*"
    rules = out["hookSpecificOutput"]["decision"]["updatedPermissions"]
    assert [r["destination"] for r in rules] == ["session"] and rules[0]["rules"] == SUGGEST[0]["rules"]
    assert SUGGEST[0]["destination"] == "localSettings", "the suggestion Claude Code sent is not changed"


def _toasts(e, n=1):
    deadline = time.time() + 2
    while len(e.fake.toasts) < n and time.time() < deadline:
        time.sleep(0.01)
    return e.fake.toasts


def test_the_keypad_lights_up_when_claude_finishes_and_nothing_is_asked(env):
    e = env(press_label("continue"))
    e.a.presence = lambda: (5.0, True)  # at the PC: no dialog on the keypad
    assert e.hook("Stop", {}) == {}
    t = _toasts(e)
    assert len(t) == 1 and t[0]["text"] == "Claude finished: money-mind" and t[0]["level"] == "ok"
    cfg = config.Config()
    cfg.behavior.notify_when_finished = False
    e.a.set_config(cfg)
    e.hook("Stop", {})
    time.sleep(0.2)
    assert len(e.fake.toasts) == 1, "switched off"


def test_no_light_up_when_the_keypad_asks_or_is_paused(env):
    e = env(press_label("continue"))
    e.a.presence = lambda: (120.0, True)
    assert e.hook("Stop", {})["decision"] == "block"  # asked on the keypad: the dialog is the notice
    e.a.presence = lambda: (5.0, True)
    e.a.set_paused(True)
    e.hook("Stop", {})
    time.sleep(0.2)
    assert e.fake.toasts == []


def test_a_keypad_that_stays_gone_hands_the_request_back_to_the_pc(env, monkeypatch):
    from keypad.core import dialogs

    monkeypatch.setattr(dialogs, "RECONNECT_GRACE", 0.5)
    e = env(None)  # shows the screen, never presses
    result = {}
    import threading

    threading.Thread(target=lambda: result.update(out=e.hook("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "ls"}})),
                     daemon=True).start()
    deadline = time.time() + 2
    while e.fake.last_screen() is None and time.time() < deadline:
        time.sleep(0.01)
    e.fake.close()  # unplugged for good
    deadline = time.time() + 5
    while "out" not in result and time.time() < deadline:
        time.sleep(0.05)
    assert result.get("out") == {}, "after the grace, Claude Code asks in the terminal"


def test_a_question_offers_other_and_choosing_it_hands_the_question_to_the_pc(env):
    e = env(lambda s: {"key": 7, "act": "pick", "idx": len(s["items"]) - 1})  # the last row
    inp = {"questions": [{"question": "Which DB?", "header": "DB", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}
    out = e.hook("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": inp})
    assert e.fake.last_screen()["items"] == ["Postgres", "SQLite", "Other… (type on the PC)"]
    assert out == {}, "Claude Code asks its own question, with its own free-text field"


def test_a_multi_select_question_has_no_other_row(env):
    e = env(lambda s: {"key": 7, "act": "submit", "sel": [1]})
    inp = {"questions": [{"question": "Checks?", "multiSelect": True, "options": [{"label": "lint"}, {"label": "test"}]}]}
    out = e.hook("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": inp})
    assert e.fake.last_screen()["items"] == ["lint", "test"]
    assert out["hookSpecificOutput"]["updatedInput"]["answers"] == {"Checks?": "test"}


def test_quick_keys_send_the_first_saved_prompts(env):
    e = env(first_key)
    cfg = with_shortcuts(e)
    e.hook("SessionStart", {})
    e.a.push_status()
    deadline = time.time() + 2
    while not e.fake.status and time.time() < deadline:
        time.sleep(0.01)
    assert e.fake.status[-1]["quick"] == [s.label[:8] for s in cfg.shortcuts[:3]]
    c = e.hub.get("kp-000001")
    e.a.message(c, {"t": "press", "id": "status", "key": 1, "act": "quick", "idx": 0})
    deadline = time.time() + 2
    while time.time() < deadline and not (sc := e.a.shortcuts.take(SID)):
        time.sleep(0.01)
    assert sc.label == cfg.shortcuts[0].label


def test_a_queued_prompt_is_shown_on_the_keypad_and_can_be_cancelled_from_it(env):
    e = env(first_key)
    with_shortcuts(e)
    e.hook("SessionStart", {})
    e.a.shortcuts.queue(1, SID)
    deadline = time.time() + 2
    while time.time() < deadline and e.fake.status[-1].get("queued") != "Prompt 1":
        time.sleep(0.01)
    assert e.fake.status[-1]["queued"] == "Prompt 1"
    e.a.message(e.hub.get("kp-000001"), {"t": "press", "id": "status", "key": 5, "act": "unqueue"})
    deadline = time.time() + 2
    while time.time() < deadline and e.fake.status[-1].get("queued"):
        time.sleep(0.01)
    assert e.fake.status[-1]["queued"] == "" and e.a.shortcuts.take(SID) is None
    assert any("Cancelled" in t["text"] for t in e.fake.toasts)
