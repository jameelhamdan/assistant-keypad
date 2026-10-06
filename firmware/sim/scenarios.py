"""Example host messages (what the agent sends) for every screen, used by shots.py and the live viewer."""
SESSION = {"id": "demo0001", "project": "money-mind", "name": "Fix tests", "state": "working", "title": "Running",
           "detail": "pytest", "since": 72, "mode": "default"}
LOG = [{"k": "u", "t": "fix the failing tests in api/"},
       {"k": "c", "t": "\u0001Plan\u0001 I'll run the tests first, then fix \u0002test_login\u0002.\n- check \u0003auth\u0003 first\n- then the rest"},
       {"k": "t", "t": "Bash(pytest -q)"}, {"k": "r", "t": "2 failed, 14 passed"},
       {"k": "t", "t": "Read(api/auth.py)"}]


def status(mode="default", state="working", sessions=None, **kw):
    ss = sessions or [dict(SESSION, mode=mode, state=state)]
    return {"t": "status", "sessions": ss, "sel": ss[0]["id"], "pinned": False, "queue": kw.get("queue", 0),
            "paused": kw.get("paused", False), "menu": kw.get("menu", False), "log": kw.get("log", LOG)}


HELLO = {"t": "hello", "v": 3, "host": "MacBook", "time": 1727712000}
SETTINGS = {"t": "settings", "brightness": 80, "name": "Desk keypad"}

SCREENS = {
    "permission": {"t": "screen", "id": "p-1", "tpl": "select", "tone": "warn", "title": "Bash command",
                   "project": "money-mind", "body": "Run tests, then push\npytest && git push", "q": "Do you want to proceed?",
                   "timeout": 300, "items": ["Yes", "Yes, and don't ask again for pytest:*", "No"]},
    "question": {"t": "screen", "id": "q-1", "tpl": "select", "title": "Database", "q": "Which database?",
                 "project": "api", "items": ["Postgres", "SQLite", "MySQL"]},
    "multi": {"t": "screen", "id": "q-2", "tpl": "multi", "title": "Checks", "q": "Which checks?", "project": "api",
              "items": ["lint", "test", "build"]},
    "diff": {"t": "screen", "id": "p-2", "tpl": "select", "tone": "warn", "title": "Edit file", "diff": True,
             "project": "money-mind", "body": "api/auth.py\n-    if user.pw == pw:\n+    if verify(user.pw, pw):\n     return token",
             "q": "Do you want to make this edit?", "items": ["Yes", "Yes, allow all edits this session", "No"]},
    "finished": {"t": "screen", "id": "s-1", "tpl": "prompt", "title": "Claude finished", "project": "money-mind",
                 "items": ["continue", "Write tests"], "notes": ["keep going", "Add tests for the change."], "esc": "done"},
}
MODES = ["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"]
