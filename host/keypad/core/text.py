"""Text helpers: redaction, clipping and one-line tool summaries."""

from __future__ import annotations

import difflib
import os
import re
from typing import Any

_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)(\s*[=:]\s*)\S+")
_TOKEN = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b"
)
_URL = re.compile(r"^https?://")


def redact(s: str) -> str:
    """Hides obvious secrets before text reaches a screen or a log."""
    s = _SECRET.sub(r"\1\2***", s)
    return _TOKEN.sub("***", s)


def clip(s: str, n: int) -> str:
    """Shortens s to n characters, marking the cut with an ellipsis."""
    return s if len(s) <= n else s[: n - 1] + "…"


def first_line(s: str) -> str:
    s = s.strip()
    return s.split("\n", 1)[0].strip()


def base(p: str) -> str:
    p = p.replace("\\", "/").rstrip("/")
    return p.rsplit("/", 1)[-1]


def project_of(cwd: str) -> str:
    return base(os.path.normpath(cwd)) if cwd else ""


def s(m: Any, k: str) -> str:
    """m[k] if it is a string, else ""."""
    v = m.get(k) if isinstance(m, dict) else None
    return v if isinstance(v, str) else ""


def tool_name(name: str) -> str:
    """Shortens MCP tool names: mcp__server__tool -> server:tool."""
    p = name.split("__")
    if len(p) >= 3 and p[0] == "mcp":
        return p[1] + ":" + p[2]
    return name


def tool_verb(name: str) -> str:
    """The status title while a tool runs."""
    return {
        "Edit": "Editing", "Write": "Editing", "NotebookEdit": "Editing", "MultiEdit": "Editing",
        "Read": "Reading", "Glob": "Reading", "Grep": "Reading",
        "Bash": "Running", "PowerShell": "Running",
        "WebFetch": "Fetching", "WebSearch": "Fetching",
        "Agent": "Delegating", "Task": "Delegating",
    }.get(name, "Using " + tool_name(name))


def summarize(name: str, inp: dict[str, Any]) -> str:
    """One short line describing a tool call."""
    if name in ("Bash", "PowerShell"):
        t = first_line(s(inp, "command")) or s(inp, "description")
    elif name in ("Edit", "Write", "Read", "NotebookEdit", "MultiEdit"):
        t = base(s(inp, "file_path") + s(inp, "notebook_path"))
    elif name in ("Glob", "Grep"):
        t = s(inp, "pattern")
    elif name in ("WebFetch", "WebSearch"):
        t = _URL.sub("", s(inp, "url") + s(inp, "query"))
    elif name in ("Agent", "Task"):
        t = s(inp, "description")
    elif name == "ExitPlanMode":
        t = "Plan ready for approval"
    else:
        t = next((v for k in ("description", "command", "file_path", "pattern", "query", "prompt", "title", "url")
                  if (v := s(inp, k))), "")
    return clip(redact(first_line(t)), 80)


def diff_lines(old: str, new: str) -> list[str]:
    """A compact line diff: "- old", "+ new", one line of context, "..." between hunks."""
    out: list[str] = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            if out:
                out.append("...")
            continue
        out.append(line[0] + " " + line[1:] if line[:1] in ("+", "-") else "  " + line[1:])
    return out


def change(name: str, inp: dict[str, Any]) -> list[str]:
    """What an Edit / MultiEdit / Write / NotebookEdit changes, as diff lines."""
    if name == "Edit":
        return diff_lines(s(inp, "old_string"), s(inp, "new_string"))
    if name == "MultiEdit":
        out: list[str] = []
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                out += (["..."] if out else []) + diff_lines(s(e, "old_string"), s(e, "new_string"))
        return out
    new = s(inp, "content") or s(inp, "new_source")
    return ["+ " + line for line in new.splitlines()[:80]]


def details(name: str, inp: dict[str, Any]) -> str:
    """The longer permission-screen text."""
    if name in ("Bash", "PowerShell"):
        t = s(inp, "command")
        if d := s(inp, "description"):
            t = d + "\n" + t
    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        t = "\n".join([s(inp, "file_path") + s(inp, "notebook_path"), *change(name, inp)])
    elif name == "Read":
        t = s(inp, "file_path")
    elif name == "ExitPlanMode":
        t = s(inp, "plan")
    else:
        t = summarize(name, inp)
    return clip(redact(t.replace("\r", "")), 1390)  # the whole command, up to the keypad's dialog body


def first_non_empty(*xs: str) -> str:
    return next((x for x in xs if x.strip()), "")


def permission_title(name: str) -> str:
    """The permission dialog's title, as Claude Code words it."""
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "Edit file"
    if name == "Write":
        return "Create file"
    return {
        "Bash": "Bash command", "PowerShell": "PowerShell command", "Read": "Read file", "Glob": "Search files",
        "Grep": "Search files", "WebFetch": "Fetch", "WebSearch": "Web search", "ExitPlanMode": "Ready to code?",
        "Agent": "Agent", "Task": "Agent",
    }.get(name, "Tool use: " + tool_name(name))


def permission_question(name: str, inp: dict[str, Any]) -> str:
    """The line above the options."""
    f = base(s(inp, "file_path") + s(inp, "notebook_path"))
    if name in ("Edit", "MultiEdit", "NotebookEdit") and f:
        return f"Do you want to make this edit to {f}?"
    if name == "Write" and f:
        return f"Do you want to create {f}?"
    if name == "ExitPlanMode":
        return "Would you like to proceed?"
    return "Do you want to proceed?"


def for_session(suggestions: list[Any]) -> list[Any]:
    """Claude Code's permission_suggestions, each made to last only for the running session."""
    return [{**u, "destination": "session"} if isinstance(u, dict) else u for u in suggestions]


def always_label(suggestions: list[Any], session: bool = False) -> str:
    """Option 2 of the permission dialog: what accepting Claude Code's
    permission_suggestions does, in its words (session: only until the session ends)."""
    when = " this session" if session else ""
    for u in suggestions:
        if not isinstance(u, dict):
            continue
        t = u.get("type")
        if t == "setMode" and u.get("mode") == "acceptEdits":
            return "Yes, allow all edits during this session"
        if t == "addDirectories" and u.get("directories"):
            return f"Yes, and {'allow' if session else 'always allow'} access to {base(str(u['directories'][0]))}/{when}"
        if t == "addRules":
            rules = [str(r.get("ruleContent") or r.get("toolName") or "") for r in u.get("rules") or [] if isinstance(r, dict)]
            if rules := [r for r in rules if r]:
                return "Yes, and don't ask again" + when + " for " + ", ".join(rules)
    return "Yes, and don't ask again" + when
