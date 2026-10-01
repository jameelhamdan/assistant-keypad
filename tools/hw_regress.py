#!/usr/bin/env python3
"""Hardware regression for a real keypad, no hands needed.

Drives real hook calls through the agent to the keypad and presses keys by
injecting them over USB, which the debug firmware accepts. The agent must
reach the keypad over Wi-Fi so the USB port stays free for injection:

    pio run -e keypad-debug -t upload                  # once
    KEYPAD_HOME=/tmp/kp KEYPAD_NO_USB=1 host/.venv/bin/keypad agent &
    KEYPAD_HOME=/tmp/kp python3 tools/hw_regress.py host/.venv/bin/keypad /dev/cu.usbmodem101

Requires pyserial (PlatformIO's Python has it: ~/.platformio/penv/bin/python).
Uses KEYPAD_HOME from the environment; never point it at your real settings.
"""
import json, os, subprocess, sys, time

import serial

if len(sys.argv) != 3 or not os.environ.get("KEYPAD_HOME"):
    sys.exit(__doc__)
KP, PORT = sys.argv[1], sys.argv[2]
ser = serial.Serial(PORT, 115200, timeout=0.1)
ser.dtr = True
def key(*ks, gap=0.6):
    for k in ks:
        time.sleep(gap); ser.write((json.dumps({"t": "key", "key": k}) + "\n").encode())
def hook(event, payload, keys, first_delay=1.5):
    payload.setdefault("session_id", "reg-session-1"); payload.setdefault("cwd", "/tmp/regress/money-mind")
    p = subprocess.Popen([KP, "hook", event], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=os.environ)
    p.stdin.write(json.dumps(payload).encode()); p.stdin.close()
    time.sleep(first_delay); key(*keys)
    out = p.stdout.read().decode(); p.wait(timeout=30)
    return json.loads(out or "{}")
results = []
def check(name, ok, detail=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -> " + str(detail)), flush=True)
def allowed(o): return o.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "allow"
def answers(o): return o.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers", {})
# keys: 1-3 pick, 4 up, 8 down, 7 Enter, 5 Esc (answer on the PC; encoder click too), 6 sessions
CFG = os.path.join(os.environ["KEYPAD_HOME"], "config.yaml")
with open(CFG, "w") as f:   # ask when Claude finishes even though you are at this PC (config is reloaded within seconds)
    f.write("behavior: {stop_when_away: 0}\n")
time.sleep(4)
hook("SessionStart", {}, [], first_delay=0)
bash = {"tool_name": "Bash", "tool_input": {"command": "pytest", "description": "Run tests"}}
always = dict(bash, permission_suggestions=[{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "pytest:*"}],
                                             "behavior": "allow", "destination": "localSettings"}])
o = hook("PermissionRequest", dict(bash), [1]); check("permission: key 1 = Yes", allowed(o), o)
o = hook("PermissionRequest", dict(bash), [7]); check("permission: Enter on the first option = Yes", allowed(o), o)
o = hook("PermissionRequest", dict(bash), [0]); check("permission: encoder click = Esc -> PC", o == {}, o)
o = hook("PermissionRequest", dict(bash), [2]); check("permission: key 2 = No", o.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "deny", o)
o = hook("PermissionRequest", dict(bash), [5]); check("permission: key 5 (Esc) -> PC", o == {}, o)
o = hook("PermissionRequest", dict(always), [8, 7]); check("permission: down, Enter = don't ask again", allowed(o) and o["hookSpecificOutput"]["decision"].get("updatedPermissions"), o)
o = hook("PermissionRequest", dict(bash), [6, 3, 1]); check("permission: 6 and a missing option ignored, then 1", allowed(o), o)
q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
    {"question": "Which database?", "header": "Database", "options": [{"label": "PostgreSQL"}, {"label": "SQLite"}, {"label": "MySQL"}]},
    {"question": "Which checks?", "header": "Checks", "multiSelect": True, "options": [{"label": "Lint"}, {"label": "Unit tests"}, {"label": "Integration tests"}, {"label": "Build"}]}]}}
o = hook("PreToolUse", q, [2, 1, 3, 8, 8, 7], first_delay=1.5)   # SQLite; tick 1 and 3, down to Submit, Enter
check("AskUserQuestion: single + multi", answers(o) == {"Which database?": "SQLite", "Which checks?": "Lint, Integration tests"}, o)
many = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Pick a region", "header": "Region", "options": [{"label": "r%d" % i} for i in range(1, 11)]}]}}
o = hook("PreToolUse", many, [8, 8, 8, 8, 7], first_delay=1.5)
check("AskUserQuestion: 10 options, cursor to the 5th", answers(o) == {"Pick a region": "r5"}, o)
o = hook("Stop", {"last_assistant_message": "All tests pass."}, [1]); check("stop: 1 = continue", o.get("decision") == "block" and "continue" in o.get("reason", "").lower(), o)
o = hook("Stop", {"last_assistant_message": "Done."}, [2, 1]); check("stop: 2 picks nothing (one option), then 1", o.get("decision") == "block", o)
o = hook("Stop", {"last_assistant_message": "Done."}, [5]); check("stop: Esc -> PC", o == {}, o)
# a saved prompt (config.yaml is reloaded within a few seconds)
with open(CFG, "w") as f:
    f.write("behavior: {stop_when_away: 0}\nshortcuts:\n- {label: Write tests, prompt: Add tests for the change.}\n")
time.sleep(4)
o = hook("Stop", {"last_assistant_message": "Done."}, [8, 7]); check("stop: down, Enter = saved prompt", o.get("decision") == "block" and "Add tests" in o.get("reason", ""), o)
# Enter on the status screen while Claude is busy -> delivered on the next tool hook
hook("UserPromptSubmit", {"prompt": "work"}, [], first_delay=0)
key(7, 1, gap=1.0); time.sleep(1.0)
o = hook("PostToolUse", {"tool_name": "Bash"}, [], first_delay=0)
check("status: 7, then 1 sends the saved prompt on the next tool", "Add tests" in o.get("hookSpecificOutput", {}).get("additionalContext", ""), o)
print("%d/%d passed" % (sum(results), len(results)))
