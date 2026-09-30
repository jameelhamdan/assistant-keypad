#!/usr/bin/env python3
"""Hardware regression for a real keypad, no hands needed.

Drives real hook calls through the agent to the keypad and presses keys by
injecting them over USB, which the debug firmware accepts. The agent must
reach the keypad over Wi-Fi so the USB port stays free for injection:

    pio run -e keypad-debug -t upload                  # once
    KEYPAD_HOME=/tmp/kp KEYPAD_NO_USB=1 dist/keypad agent &
    KEYPAD_HOME=/tmp/kp python3 tools/hw_regress.py dist/keypad /dev/cu.usbmodem101

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
hook("SessionStart", {}, [], first_delay=0)
bash = {"tool_name": "Bash", "tool_input": {"command": "go test ./...", "description": "Run tests"}}
o = hook("PermissionRequest", dict(bash), [1]); check("permission: key 1 allows", o.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "allow", o)
o = hook("PermissionRequest", dict(bash), [4]); check("permission: key 4 denies", o.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "deny", o)
o = hook("PermissionRequest", dict(bash), [0]); check("permission: encoder click -> PC", o == {}, o)
o = hook("PermissionRequest", dict(bash), [2, 3, 1]); check("permission: unbound keys ignored, then 1 allows", o.get("hookSpecificOutput", {}).get("decision", {}).get("behavior") == "allow", o)
q = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
    {"question": "Which database?", "header": "Database", "options": [{"label": "PostgreSQL"}, {"label": "SQLite"}, {"label": "MySQL"}]},
    {"question": "Which checks?", "header": "Checks", "multiSelect": True, "options": [{"label": "Lint"}, {"label": "Unit tests"}, {"label": "Integration tests"}, {"label": "Build"}]}]}}
o = hook("PreToolUse", q, [2, 1, 3, 8], first_delay=1.5)
ans = o.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers", {})
check("AskUserQuestion: single + multi", ans == {"Which database?": "SQLite", "Which checks?": "Lint, Integration tests"}, o)
many = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Pick a region", "header": "Region", "options": [{"label": "r%d" % i} for i in range(1, 11)]}]}}
o = hook("PreToolUse", many, [2], first_delay=1.5)
check("AskUserQuestion: 10 options, page 1 key 2", o.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers", {}) == {"Pick a region": "r2"}, o)
o = hook("Stop", {"last_assistant_message": "All tests pass."}, [1]); check("stop: continue", o.get("decision") == "block" and "continue" in o.get("reason", "").lower(), o)
o = hook("Stop", {"last_assistant_message": "Done."}, [4]); check("stop: done", o == {}, o)
o = hook("Stop", {"last_assistant_message": "Done."}, [2, 3]); check("stop: shortcut 3 (Fix bugs)", o.get("decision") == "block" and "Look for bugs" in o.get("reason", ""), o)
o = hook("Stop", {"last_assistant_message": "Done."}, [2, 0, 4]); check("stop: shortcut menu, back, done", o == {}, o)
# status-screen shortcut menu while Claude is busy -> delivered on the next tool hook
hook("UserPromptSubmit", {"prompt": "work"}, [], first_delay=0)
key(1, 1, gap=1.0); time.sleep(1.0)
o = hook("PostToolUse", {"tool_name": "Bash"}, [], first_delay=0)
check("status menu: shortcut 1 delivered on next tool", "Continue with the current task" in o.get("hookSpecificOutput", {}).get("additionalContext", ""), o)
print("%d/%d passed" % (sum(results), len(results)))
