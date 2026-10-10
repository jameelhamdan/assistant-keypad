"""Workflow scenarios, end to end and with no hardware: Claude Code's hooks go into the real host, the
real firmware UI and key logic (firmware/sim) plays the keypad, and a person's key presses come back out
as hook decisions. One test is one thing a person does on a day with Claude Code.

    python firmware/sim/build.py   (once, and after firmware changes)
    cd host && .venv/Scripts/python -m pytest tests/test_e2e_workflows.py -q
"""

import json
import time

import test_sim_e2e
from conftest import SID
from test_sim_e2e import PERMISSION, SimLink

from keypad.core.ctx import Ctx

rig = test_sim_e2e.rig  # the fixture, registered in this module
CWD = "/work/money-mind"
SID2 = "bbbb-2222-bbbb"
QUESTION = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
    {"question": "Which DB?", "header": "DB", "options": [{"label": "Postgres", "description": "Server"}, {"label": "SQLite"}]}]}}
SUGGESTION = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "pytest:*"}], "behavior": "allow", "destination": "localSettings"}]


def until(cond, what, secs=5.0):
    end = time.time() + secs
    while not cond():
        assert time.time() < end, f"timed out waiting for: {what}"
        time.sleep(0.01)


def start(r, sid=SID, cwd=CWD, **extra):
    r.agent.hook(Ctx(), "SessionStart", {"session_id": sid, "cwd": cwd, **extra})


def prompt(r, text="hi", sid=SID, cwd=CWD):
    return r.agent.hook(Ctx(), "UserPromptSubmit", {"session_id": sid, "cwd": cwd, "prompt": text})


def away(r):
    r.agent.presence = lambda: (600.0, True)


def last_status(r) -> dict:
    return [m for m in r.kp.sent if m["t"] == "status"][-1]


def wait_status(r, pred, what):
    r.agent.push_status()
    until(lambda: any(m["t"] == "status" and pred(m) for m in r.kp.sent), what)


def decision(out) -> str:
    return out.get("hookSpecificOutput", {}).get("decision", {}).get("behavior", "")


# ---- permissions ----


def test_permission_with_a_dont_ask_again_suggestion_offers_three_options(rig):
    r = rig()
    start(r)
    r.hook_async("p", "PermissionRequest", {**PERMISSION, "permission_suggestions": SUGGESTION})
    s = r.wait_screen()
    assert len(s["items"]) == 3 and s["items"][0] == "Yes" and s["items"][2] == "No"
    r.kp.pick(2)  # "Yes, don't ask again"
    d = r.result("p")["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow" and d["updatedPermissions"]


def test_key_3_denies_when_there_are_three_options(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {**PERMISSION, "permission_suggestions": SUGGESTION})
    r.wait_screen()
    r.kp.pick(3)
    assert decision(r.result("p")) == "deny"


def test_a_file_edit_permission_shows_up_and_is_allowed(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {"tool_name": "Edit", "tool_input": {
        "file_path": "/work/money-mind/app.py", "old_string": "a = 1", "new_string": "a = 2"}})
    s = r.wait_screen()
    assert s["diff"] is True
    r.kp.shot("edit-permission")
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_a_key_that_is_not_an_option_decides_nothing(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    for k in (3, 4, 6, 8):  # only two options: nothing here is a decision
        r.kp.key(k)
    time.sleep(0.3)
    assert "p" not in r.results
    r.kp.pick(2)
    assert decision(r.result("p")) == "deny"


def test_a_secret_in_a_command_never_reaches_the_keypad(rig):
    r = rig()
    secret = "sk-abcdefghijklmnop1234567890"
    r.hook_async("p", "PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": f"curl -H 'Authorization: {secret}' x.io"}})
    r.wait_screen()
    assert secret not in json.dumps(r.kp.sent)
    r.kp.pick(1)
    r.result("p")


def test_the_state_of_the_session_follows_a_permission_request(rig):
    r = rig()
    start(r)
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    assert r.agent.sessions.get(SID).state == "asking"
    r.kp.pick(1)
    r.result("p")
    until(lambda: r.agent.sessions.get(SID).state == "working", "the session works again")


def test_an_unanswered_request_falls_back_to_the_pc_after_the_timeout(rig):
    r = rig()
    cfg = r.agent.config()
    cfg.behavior.timeout = 1
    r.agent._cfg = cfg  # below the 10 s floor the settings enforce: a test must not wait that long
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    assert r.result("p") == {}, "nothing is ever auto-approved"
    until(lambda: any(m["t"] == "close" for m in r.kp.sent), "the keypad is told the screen is gone")


# ---- questions ----


def test_two_questions_are_asked_one_after_the_other(rig):
    r = rig()
    q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
        {"question": "Which DB?", "header": "DB", "options": [{"label": "Postgres"}, {"label": "SQLite"}]},
        {"question": "Which ORM?", "header": "ORM", "options": [{"label": "SQLAlchemy"}, {"label": "Peewee"}]}]}}
    r.hook_async("q", "PreToolUse", q)
    s1 = r.wait_screen()
    assert "1/2" in s1["title"]
    r.kp.pick(1)
    s2 = r.wait_screen(2)
    assert "2/2" in s2["title"]
    r.kp.pick(2)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which DB?": "Postgres", "Which ORM?": "Peewee"}


def test_esc_does_not_dismiss_a_question_but_other_hands_it_to_the_pc(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", dict(QUESTION))
    r.wait_screen()
    r.kp.key(5)
    time.sleep(0.3)
    assert "q" not in r.results
    r.kp.pick(3)  # "Other… (type on the PC)"
    assert r.result("q") == {}


def test_a_yes_no_question_without_options_is_not_taken_by_the_keypad(rig):
    r = rig()
    q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Name the module?"}]}}
    assert r.agent.hook(Ctx(), "PreToolUse", {"session_id": SID, "cwd": CWD, **q}) == {}
    assert not r.kp.screens(), "free text is typed on the PC"


def test_a_multi_select_with_nothing_ticked_cannot_be_submitted(rig):
    r = rig()
    q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
        {"question": "Checks?", "multiSelect": True, "options": [{"label": "lint"}, {"label": "test"}]}]}}
    r.hook_async("q", "PreToolUse", q)
    r.wait_screen()
    r.kp.sim.turn(2)  # to Submit
    r.kp.key(7)
    time.sleep(0.3)
    assert "q" not in r.results
    r.kp.sim.turn(-1)  # up to "test"
    r.kp.key(7)
    r.kp.sim.turn(1)  # back to Submit
    r.kp.key(7)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Checks?": "test"}


def test_only_ask_user_question_is_taken_from_pre_tool_use(rig):
    r = rig()
    assert r.agent.hook(Ctx(), "PreToolUse", {"session_id": SID, "cwd": CWD, **PERMISSION}) == {}
    assert not r.kp.screens()


# ---- Claude finishes ----


def test_continue_on_the_finished_screen_blocks_the_stop_and_counts(rig):
    r = rig()
    away(r)
    start(r)
    r.hook_async("s", "Stop", {"last_assistant_message": "Done."})
    r.wait_screen()
    r.kp.pick(1)
    out = r.result("s")
    assert out["decision"] == "block" and "1/" in out["systemMessage"]
    assert r.agent.sessions.get(SID).state == "continuing"
    assert r.agent.sessions.continues(SID) == 1


def test_a_new_prompt_resets_the_continue_count(rig):
    r = rig()
    away(r)
    r.hook_async("s", "Stop", {"last_assistant_message": "Done."})
    r.wait_screen()
    r.kp.pick(1)
    r.result("s")
    assert r.agent.sessions.continues(SID) == 1
    prompt(r, "next thing")
    assert r.agent.sessions.continues(SID) == 0


def test_the_finished_screen_is_not_asked_while_you_are_at_the_pc(rig):
    r = rig()
    r.agent.presence = lambda: (2.0, True)
    assert r.agent.hook(Ctx(), "Stop", {"session_id": SID, "cwd": CWD, "last_assistant_message": "ok"}) == {}
    assert not r.kp.screens()


# ---- several sessions ----


def test_the_keypad_lists_every_session_and_one_can_be_chosen(rig):
    r = rig()
    start(r, SID, "/work/money-mind")
    start(r, SID2, "/work/api")
    wait_status(r, lambda m: len(m["sessions"]) == 2, "both sessions listed")
    assert {x["project"] for x in last_status(r)["sessions"]} == {"money-mind", "api"}
    assert last_status(r)["sel"] == SID2[:8]
    r.kp.wait(2000)
    r.kp.key(6)  # the session list
    r.kp.pick(2)  # the older session
    until(lambda: r.agent.sessions.current().id == SID, "the choice reaches the host")
    wait_status(r, lambda m: m["sel"] == SID[:8], "the keypad shows the chosen session")


def test_a_request_from_another_session_takes_the_screen_over_a_chosen_one(rig):
    r = rig()
    start(r, SID, "/work/money-mind")
    start(r, SID2, "/work/api")
    r.agent.sessions.select(SID[:8])
    r.hook_async("p", "PermissionRequest", {**PERMISSION, "session_id": SID2, "cwd": "/work/api"})
    s = r.wait_screen()
    assert s["project"] == "api"
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_a_session_that_ended_leaves_the_list(rig):
    r = rig()
    start(r, SID, "/work/money-mind")
    start(r, SID2, "/work/api")
    r.agent.hook(Ctx(), "SessionEnd", {"session_id": SID2, "cwd": "/work/api", "reason": "exit"})
    wait_status(r, lambda m: [x["project"] for x in m["sessions"]] == ["money-mind"], "only the live session is listed")


def test_nine_sessions_list_only_the_latest_eight(rig):
    r = rig()
    for i in range(9):
        start(r, f"sess-{i:04d}-xxxx", f"/work/p{i}")
        time.sleep(0.01)
    wait_status(r, lambda m: len(m["sessions"]) == 8, "eight sessions listed")


# ---- the live feed (transcripts) ----


def _line(**kw) -> str:
    return json.dumps(kw) + "\n"


def test_the_feed_mirrors_the_transcript_and_the_state_follows_it(rig, tmp_path):
    r = rig()
    t = tmp_path / "s.jsonl"
    t.write_text(_line(type="user", uuid="u1", cwd=CWD, message={"role": "user", "content": "fix the build"}), encoding="utf-8")
    start(r, transcript_path=str(t))
    with open(t, "a", encoding="utf-8") as f:
        f.write(_line(type="assistant", uuid="a1", message={"role": "assistant", "content": [
            {"type": "text", "text": "Running the tests now."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}]}))
    until(lambda: any(m["t"] == "feed" and "Running the tests now." in json.dumps(m) for m in r.kp.sent), "the feed reaches the keypad")
    assert r.agent.sessions.get(SID).state == "working"


def test_answering_on_the_pc_first_closes_the_keypad_screen(rig, tmp_path):
    r = rig()
    t = tmp_path / "s.jsonl"
    t.write_text(_line(type="user", uuid="u1", cwd=CWD, message={"role": "user", "content": "go"}), encoding="utf-8")
    start(r, transcript_path=str(t))
    r.hook_async("p", "PermissionRequest", {**PERMISSION, "transcript_path": str(t)})
    r.wait_screen()
    with open(t, "a", encoding="utf-8") as f:  # you pressed Yes at the PC: Claude went on
        f.write(_line(type="assistant", uuid="a1", message={"role": "assistant", "content": [{"type": "text", "text": "Tests pass."}]}))
    assert r.result("p") == {}
    until(lambda: any(m["t"] == "close" for m in r.kp.sent), "the keypad is told to drop the screen")


# ---- pause, fall-backs ----


def test_pause_then_resume(rig):
    r = rig()
    r.agent.set_paused(True)
    assert r.agent.hook(Ctx(), "PermissionRequest", {"session_id": SID, "cwd": CWD, **PERMISSION}) == {}
    wait_status(r, lambda m: m["paused"] is True, "the keypad shows paused")
    r.agent.set_paused(False)
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_no_keypad_means_no_decision_and_no_wait(rig):
    r = rig()
    r.kp.close()
    until(lambda: not r.agent.targets(), "the host notices the keypad left", 8)
    t0 = time.time()
    assert r.agent.hook(Ctx(), "PermissionRequest", {"session_id": SID, "cwd": CWD, **PERMISSION}) == {}
    assert time.time() - t0 < 2


def test_a_hook_event_nobody_handles_is_no_decision(rig):
    r = rig()
    for ev in ("Notification", "PostToolUse", "SubagentStop", "PreCompact", "Nonsense"):
        assert r.agent.hook(Ctx(), ev, {"session_id": SID, "cwd": CWD}) == {}


def test_a_garbled_payload_never_raises(rig):
    r = rig()
    cfg = r.agent.config()
    cfg.behavior.timeout = 1  # whatever of these opens a screen nobody answers
    r.agent._cfg = cfg
    for p in (None, {}, {"tool_input": "not a dict"}, {"session_id": 5, "cwd": None}, {"tool_name": "Bash", "tool_input": []}):
        r.agent.hook(Ctx(), "PermissionRequest", p)
        r.agent.hook(Ctx(), "PreToolUse", p)
        r.agent.hook(Ctx(), "UserPromptSubmit", p)


# ---- the keypad connection ----


def test_a_reconnected_keypad_gets_the_open_request_again(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    r.kp.close()  # Wi-Fi drops
    until(lambda: not r.agent.targets(), "the drop is noticed", 8)
    import threading

    kp2 = SimLink()
    threading.Thread(target=r.hub.serve, args=(kp2,), daemon=True).start()
    until(lambda: any(m["t"] == "screen" and m["id"] == s["id"] for m in kp2.sent), "the open request is shown again", 8)
    kp2.wait(600)
    kp2.pick(1)
    assert decision(r.result("p")) == "allow"
    kp2.close_sim()


def test_two_requests_in_a_row_are_each_answered_on_their_own_screen(rig):
    r = rig()
    for n, k, want in ((1, 1, "allow"), (2, 2, "deny"), (3, 1, "allow")):
        r.hook_async(f"p{n}", "PermissionRequest", dict(PERMISSION))
        r.wait_screen(n)
        r.kp.pick(k)
        assert decision(r.result(f"p{n}")) == want
        r.kp.wait(300)


def test_the_status_screen_reports_a_running_request_queue(rig):
    r = rig()
    r.hook_async("a", "PermissionRequest", {**PERMISSION, "session_id": "aaaa-1111"})
    r.wait_screen()
    r.hook_async("b", "PermissionRequest", {**PERMISSION, "session_id": "bbbb-2222"})
    until(lambda: r.agent.dialogs.queued() == 1, "the second request waits")
    assert r.agent.snapshot()["queue"] == 1
    r.kp.pick(1)
    r.result("a")
    until(lambda: len({m["id"] for m in r.kp.screens()}) == 2, "the second screen shows")
    r.kp.wait(400)
    r.kp.pick(1)
    assert decision(r.result("b")) == "allow"


# ---- settings ----


def test_notifications_off_keeps_the_keypad_quiet_when_claude_finishes(rig):
    r = rig()
    cfg = r.agent.config()
    cfg.behavior.notify_when_finished = False
    r.agent.set_config(cfg)
    r.agent.presence = lambda: (1.0, True)
    r.agent.hook(Ctx(), "Stop", {"session_id": SID, "cwd": CWD, "last_assistant_message": "ok"})
    time.sleep(0.4)
    assert not r.kp.toasts()
