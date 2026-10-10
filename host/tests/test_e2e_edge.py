"""Edge cases, end to end: odd input from Claude Code, hostile or confused key presses, queues and
sessions at their limits. Same rig as test_e2e_workflows (real host, simulated keypad)."""

import json
import time

import test_e2e_workflows as w
import test_sim_e2e
from conftest import SID
from test_e2e_workflows import CWD, PERMISSION, SID2, decision, prompt, start, until

from keypad import proto
from keypad.core.ctx import Ctx

rig = test_sim_e2e.rig


def quick_timeout(r, secs=1):
    cfg = r.agent.config()
    cfg.behavior.timeout = secs
    r.agent._cfg = cfg  # under the 10 s floor the settings enforce


def q_hook(questions):
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": questions}}


def opts(*labels):
    return [{"label": x} for x in labels]


# ---- text and sizes ----


def test_a_huge_command_is_clipped_and_still_answerable(rig):
    r = rig()
    cmd = "echo " + "x" * 20000
    r.hook_async("p", "PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    s = r.wait_screen()
    assert len(json.dumps(s)) < proto.MAX_DEVICE_MSG * 8
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_unicode_emoji_and_rtl_text_do_not_break_a_screen(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {"tool_name": "Bash", "tool_input": {
        "command": "echo 'héllo wörld 日本語 🚀 مرحبا'", "description": "Grüße ✓"}})
    r.wait_screen()
    r.kp.shot("unicode")
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_an_empty_command_still_shows_a_permission(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {"tool_name": "Bash", "tool_input": {}})
    r.wait_screen()
    r.kp.pick(2)
    assert decision(r.result("p")) == "deny"


def test_an_mcp_tool_with_a_long_name_is_shown(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {"tool_name": "mcp__very_long_server_name__do_something_dangerous_now",
                                           "tool_input": {"arg": "v" * 500, "n": 5, "nested": {"a": 1}}})
    s = r.wait_screen()
    assert s["title"]
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_a_subagent_request_is_labelled(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", {**PERMISSION, "agent_type": "reviewer"})
    s = r.wait_screen()
    assert "reviewer" in s["title"]
    r.kp.pick(1)
    r.result("p")


def test_very_long_option_labels_are_clipped(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Pick?", "options": opts("A" * 400, "B" * 400)}]))
    s = r.wait_screen()
    assert all(len(i) <= proto.SCREEN_ITEM + 2 for i in s["items"])
    r.kp.pick(1)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"]["Pick?"] == "A" * 400  # the answer is the full label


def test_a_very_long_question_goes_in_the_scrollable_body(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Should we? " * 80, "options": opts("Yes", "No")}]))
    s = r.wait_screen()
    assert s["q"] == "" and "Should we?" in s["body"]
    r.kp.pick(2)
    r.result("q")


# ---- questions at their limits ----


def test_four_questions_are_all_asked(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": f"Q{i}?", "options": opts("a", "b")} for i in range(4)]))
    for n in range(1, 5):
        r.wait_screen(n)
        r.kp.pick(1)
    out = r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"]
    assert out == {f"Q{i}?": "a" for i in range(4)}


def test_five_questions_are_left_to_the_pc(rig):
    r = rig()
    out = r.agent.hook(Ctx(), "PreToolUse", {"session_id": SID, "cwd": CWD,
                                             **q_hook([{"question": f"Q{i}?", "options": opts("a")} for i in range(5)])})
    assert out == {} and not r.kp.screens()


def test_a_question_with_32_options_is_left_to_the_pc(rig):
    r = rig()
    out = r.agent.hook(Ctx(), "PreToolUse", {"session_id": SID, "cwd": CWD,
                                             **q_hook([{"question": "Which?", "options": opts(*[f"o{i}" for i in range(proto.MAX_ITEMS)])}])})
    assert out == {}


def test_a_question_with_many_options_scrolls_and_picks_with_enter(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Which?", "options": opts(*[f"opt{i}" for i in range(12)])}]))
    r.wait_screen()
    r.kp.sim.turn(9)
    r.kp.key(7)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which?": "opt9"}


def test_duplicate_labels_answer_by_position(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Which?", "options": opts("same", "same")}]))
    r.wait_screen()
    r.kp.pick(2)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which?": "same"}


def test_options_given_as_plain_strings_work(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Which?", "options": ["red", "blue"]}]))
    r.wait_screen()
    r.kp.pick(2)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which?": "blue"}


def test_an_empty_question_text_is_left_to_the_pc(rig):
    r = rig()
    assert r.agent.hook(Ctx(), "PreToolUse", {"session_id": SID, "cwd": CWD, **q_hook([{"question": "  ", "options": opts("a")}])}) == {}


def test_a_multi_select_returns_every_ticked_option_in_order(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Which?", "multiSelect": True, "options": opts("a", "b", "c")}]))
    r.wait_screen()
    r.kp.key(7)  # a
    r.kp.sim.turn(2)
    r.kp.key(7)  # c
    r.kp.sim.turn(1)  # Submit
    r.kp.key(7)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which?": "a, c"}


def test_a_multi_select_untick_then_submit(rig):
    r = rig()
    r.hook_async("q", "PreToolUse", q_hook([{"question": "Which?", "multiSelect": True, "options": opts("a", "b")}]))
    r.wait_screen()
    r.kp.key(7)  # a
    r.kp.sim.turn(1)
    r.kp.key(7)  # b
    r.kp.sim.turn(-1)
    r.kp.key(7)  # a off again
    r.kp.sim.turn(2)  # Submit
    r.kp.key(7)
    assert r.result("q")["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which?": "b"}


def test_the_question_tool_input_is_echoed_back_whole(rig):
    r = rig()
    inp = {"questions": [{"question": "Which?", "header": "H", "options": opts("a", "b")}], "extra": {"keep": "me"}}
    r.hook_async("q", "PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": inp})
    r.wait_screen()
    r.kp.pick(1)
    upd = r.result("q")["hookSpecificOutput"]["updatedInput"]
    assert upd["extra"] == {"keep": "me"} and upd["questions"] == inp["questions"]


# ---- presses that are not decisions ----


def _raw_press(r, **kw):
    """A press arriving from the keypad's link, as the host's router sees it."""
    conn = r.hub.conns()[0]
    return r.agent.message(conn, {"t": "press", **kw})


def test_a_forged_press_with_a_wrong_key_for_the_option_is_ignored(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    _raw_press(r, id=s["id"], key=2, act="pick", idx=0)  # a number key decides nothing
    time.sleep(0.3)
    assert "p" not in r.results
    assert r.agent.stats.view()
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_a_press_for_an_old_screen_is_sent_back_to_the_status_screen(rig):
    r = rig()
    _raw_press(r, id="gone-1", key=1, act="pick", idx=0)
    until(lambda: any(m["t"] == "close" and m["id"] == "gone-1" for m in r.kp.sent), "the keypad is told the screen is stale")


def test_a_press_with_out_of_range_index_is_ignored(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    _raw_press(r, id=s["id"], key=7, act="pick", idx=99)
    _raw_press(r, id=s["id"], key=7, act="pick", idx=-1)
    time.sleep(0.3)
    assert "p" not in r.results
    r.kp.pick(2)
    assert decision(r.result("p")) == "deny"


def test_a_session_message_with_an_unknown_id_changes_nothing(rig):
    r = rig()
    start(r)
    r.agent.message(r.hub.conns()[0], {"t": "session", "sid": "nope", "act": "select"})
    assert r.agent.sessions.current().id == SID


# ---- queue and FIFO ----


def test_three_requests_from_three_sessions_are_answered_in_arrival_order(rig):
    r = rig()
    for n, sid in enumerate(("aaaa-1", "bbbb-2", "cccc-3")):
        r.hook_async(sid, "PermissionRequest", {**PERMISSION, "session_id": sid, "cwd": f"/w/p{n}"})
        until(lambda n=n: r.agent.dialogs.queued() == max(0, n), "queued in order")
        time.sleep(0.05)
    for n, sid in enumerate(("aaaa-1", "bbbb-2", "cccc-3")):
        s = r.wait_screen(n + 1)
        assert s["project"] == f"p{n}"
        r.kp.pick(1)
        assert decision(r.result(sid)) == "allow"
        r.kp.wait(300)


def test_parallel_requests_from_one_session_are_shown_one_by_one(rig):
    r = rig()
    r.hook_async("a", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.hook_async("b", "PermissionRequest", dict(PERMISSION))
    time.sleep(0.3)
    assert len(r.kp.screens()) == 1
    r.kp.pick(2)
    assert decision(r.result("a")) == "deny"
    r.wait_screen(2)
    r.kp.wait(400)  # a press right after a screen appears is ignored
    r.kp.pick(1)
    assert decision(r.result("b")) == "allow"


def test_a_request_that_waits_in_line_longer_than_its_timeout_goes_to_the_pc(rig):
    r = rig()
    r.hook_async("a", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    quick_timeout(r, 1)
    r.hook_async("b", "PermissionRequest", {**PERMISSION, "session_id": SID2})
    assert r.result("b") == {}
    assert "a" not in r.results, "the one on screen is unaffected"
    r.kp.pick(1)
    r.result("a")


# ---- sessions ----


def test_a_hook_after_session_end_brings_the_session_back(rig):
    r = rig()
    start(r)
    r.agent.hook(Ctx(), "SessionEnd", {"session_id": SID, "cwd": CWD, "reason": "exit"})
    assert r.agent.sessions.get(SID).state == "ended"
    prompt(r, "I'm back")
    assert r.agent.sessions.get(SID).state == "working"


def test_session_start_twice_resets_the_continue_count(rig):
    r = rig()
    w.away(r)
    r.hook_async("s", "Stop", {"last_assistant_message": "Done."})
    r.wait_screen()
    r.kp.pick(1)
    r.result("s")
    assert r.agent.sessions.continues(SID) == 1
    start(r)
    assert r.agent.sessions.continues(SID) == 0


def test_a_secret_in_your_prompt_is_redacted_in_the_status(rig):
    r = rig()
    prompt(r, "use key sk-abcdefghijklmnop1234567890 please")
    w.wait_status(r, lambda m: bool(m["sessions"]), "status sent")
    assert "sk-abcdefghij" not in json.dumps(r.kp.sent)


def test_a_long_session_name_and_project_fit_the_wire(rig):
    r = rig()
    start(r, cwd="/work/" + "p" * 200)
    w.wait_status(r, lambda m: bool(m["sessions"]), "status")
    sess = w.last_status(r)["sessions"][0]
    assert len(sess["project"]) <= proto.SESSION_PROJECT + 3


def test_an_unknown_working_directory_still_makes_a_session(rig):
    r = rig()
    r.agent.hook(Ctx(), "UserPromptSubmit", {"session_id": "zzzz-9", "prompt": "hi"})
    assert r.agent.sessions.get("zzzz-9") is not None


def test_an_interrupted_turn_returns_the_session_to_idle(rig, tmp_path):
    r = rig()
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "user", "uuid": "u1", "cwd": CWD, "message": {"role": "user", "content": "go"}}) + "\n", encoding="utf-8")
    start(r, transcript_path=str(t))
    with open(t, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "uuid": "u2", "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user]"}]}}) + "\n")
    until(lambda: r.agent.sessions.get(SID).state == "idle", "the session is idle after an interrupt")


def test_an_unreadable_transcript_is_flagged_but_the_keypad_keeps_working(rig, tmp_path):
    r = rig()
    t = tmp_path / "s.jsonl"
    t.write_text('{"nonsense": true}\n' * 25, encoding="utf-8")
    start(r, transcript_path=str(t))
    until(lambda: any(not s["feed_ok"] for s in r.agent.snapshot()["sessions"]), "feed flagged unreadable")
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    r.wait_screen()
    r.kp.pick(1)
    assert decision(r.result("p")) == "allow"


def test_a_corrupt_and_a_missing_transcript_never_raise(rig, tmp_path):
    r = rig()
    t = tmp_path / "bad.jsonl"
    t.write_bytes(b"\xff\xfe\x00 not json\n{\n")
    start(r, transcript_path=str(t))
    start(r, SID2, "/w/x", transcript_path=str(tmp_path / "missing.jsonl"))
    r.agent.sync()


def test_agent_start_picks_up_running_sessions(rig, tmp_path):
    r = rig()
    d = tmp_path / "projects" / "-work-app"
    d.mkdir(parents=True)
    f = d / "abcd1234-0000.jsonl"
    f.write_text(json.dumps({"type": "user", "uuid": "u", "cwd": "/work/app", "message": {"role": "user", "content": "hello"}}) + "\n",
                 encoding="utf-8")
    r.agent.discover(tmp_path / "projects")
    assert r.agent.sessions.get("abcd1234-0000") is not None


# ---- the finished screen ----


def test_a_huge_final_message_is_summarised_not_dropped(rig):
    r = rig()
    w.away(r)
    r.hook_async("s", "Stop", {"last_assistant_message": "# Result\n" + ("- point with `code` and **bold**\n" * 800)})
    r.wait_screen()
    r.kp.key(5)
    assert r.result("s") == {}


def test_stop_without_a_message_still_works(rig):
    r = rig()
    w.away(r)
    r.hook_async("s", "Stop", {})
    r.wait_screen()
    r.kp.pick(1)
    assert r.result("s")["decision"] == "block"


def test_the_finished_screen_times_out_to_the_pc(rig):
    r = rig()
    w.away(r)
    quick_timeout(r, 1)
    assert r.agent.hook(Ctx(), "Stop", {"session_id": SID, "cwd": CWD, "last_assistant_message": "x"}) == {}
    assert r.agent.sessions.get(SID).state == "idle"


def test_stop_while_paused_is_left_alone(rig):
    r = rig()
    w.away(r)
    r.agent.set_paused(True)
    assert r.agent.hook(Ctx(), "Stop", {"session_id": SID, "cwd": CWD, "last_assistant_message": "x"}) == {}
    assert not r.kp.screens()


# ---- the connection ----


def test_a_request_while_the_keypad_is_gone_waits_for_it_within_the_grace(rig):
    r = rig()
    r.hook_async("p", "PermissionRequest", dict(PERMISSION))
    s = r.wait_screen()
    r.kp.close()
    until(lambda: not r.agent.targets(), "keypad gone", 8)
    time.sleep(0.3)
    assert "p" not in r.results, "the request is kept for a keypad that comes back"
    import threading

    kp2 = test_sim_e2e.SimLink()
    threading.Thread(target=r.hub.serve, args=(kp2,), daemon=True).start()
    until(lambda: any(m["t"] == "screen" and m["id"] == s["id"] for m in kp2.sent), "request re-shown", 8)
    kp2.wait(600)
    kp2.pick(2)
    assert decision(r.result("p")) == "deny"
    kp2.close_sim()


def test_snapshot_describes_the_keypad_and_sessions(rig):
    r = rig()
    start(r)
    snap = r.agent.snapshot()
    assert snap["keypads"] and snap["sessions"][0]["project"] == "money-mind"
    json.dumps(snap)
