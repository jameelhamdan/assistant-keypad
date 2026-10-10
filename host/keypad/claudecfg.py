"""Installs and removes the keypad's Claude Code integration: command hooks in
~/.claude/settings.json. Only entries that run our own binary are ever touched,
and every write is preceded by a backup."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MAX_CONTINUES, write_atomic

# Blocking hooks wait for a key press; the agent enforces behavior.timeout (at most 3600 s), so
# Claude Code only cuts a hook off after that plus a grace (a hung agent, not a slow answer).
BLOCKING_TIMEOUT = 3600 + 30
QUICK_TIMEOUT = 15
KEEP_BACKUPS = 5

# event -> (waits for a key press, tool matcher). The hooks only decide; the live feed comes from the transcript.
# PreToolUse is only for AskUserQuestion. PostToolUse runs after every tool call, and only to hand
# Claude a prompt sent from the keypad while it works (it lands with the next tool result).
EVENTS = {
    "SessionStart": (False, ""), "SessionEnd": (False, ""), "UserPromptSubmit": (False, ""),
    "PreToolUse": (True, "AskUserQuestion"), "PostToolUse": (False, ""),
    "PermissionRequest": (True, ""), "Stop": (True, ""),
}


def home() -> Path:
    if d := os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(d)
    return Path.home() / ".claude"


def settings_path() -> Path:
    """The user-scope Claude Code settings file."""
    return home() / "settings.json"


@dataclass
class Status:
    hooks: int = 0  # number of our hook entries
    expected: int = len(EVENTS)  # number we would install
    binary: str = ""  # binary the hooks should point at
    stale: bool = False  # hooks point at another binary path
    settings: str = ""

    @property
    def installed(self) -> bool:
        return self.hooks == self.expected and not self.stale


def _read_settings() -> dict[str, Any]:
    p = settings_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    try:
        m = json.loads(text)
    except ValueError as e:
        raise RuntimeError(f"{p} is not valid JSON: {e}") from e
    if not isinstance(m, dict):
        raise RuntimeError(f"{p} is not a JSON object")
    return m


def _write_settings(m: dict[str, Any]) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(m, indent=2, ensure_ascii=False) + "\n").encode()
    try:
        old = p.read_bytes()
    except FileNotFoundError:
        old = None
    if old is not None:
        if old == data:
            return  # nothing to change: no write, no backup
        bak = p.with_name(p.name + ".keypad-backup-" + time.strftime("%Y%m%d-%H%M%S"))
        write_atomic(bak, old, 0o600)
        backups = sorted(p.parent.glob(p.name + ".keypad-backup-*"))  # the timestamp sorts chronologically
        for b in backups[:-KEEP_BACKUPS]:
            b.unlink(missing_ok=True)
    write_atomic(p, data, 0o600)


def _ours(h: Any) -> str | None:
    """The binary a hook handler runs, if it is one of ours."""
    if not isinstance(h, dict):
        return None
    cmd, args = h.get("command"), h.get("args")
    if not isinstance(cmd, str) or not isinstance(args, list) or not args or args[0] != "hook":
        return None
    name = Path(cmd.replace("\\", "/")).name.lower().removesuffix(".exe")
    return cmd if name == "keypad" else None


def _strip(hooks: dict[str, Any]) -> None:
    """Removes our handlers from every event, dropping groups left empty."""
    for ev in list(hooks):
        keep = []
        for g in hooks[ev] if isinstance(hooks[ev], list) else []:
            if not isinstance(g, dict):
                keep.append(g)
                continue
            lst = g.get("hooks") if isinstance(g.get("hooks"), list) else []
            rest = [h for h in lst if _ours(h) is None]
            if lst and not rest:
                continue
            keep.append({**g, "hooks": rest})
        if keep:
            hooks[ev] = keep
        else:
            del hooks[ev]


def install(binary: str) -> None:
    """Adds hooks for binary, and raises Claude Code's own Stop-hook block cap to MAX_CONTINUES."""
    m = _read_settings()
    hooks = m.get("hooks") if isinstance(m.get("hooks"), dict) else {}
    _strip(hooks)
    for ev, (waits, matcher) in EVENTS.items():
        entry = {"type": "command", "command": binary, "args": ["hook", ev],
                 "timeout": BLOCKING_TIMEOUT if waits else QUICK_TIMEOUT}
        hooks.setdefault(ev, []).append({**({"matcher": matcher} if matcher else {}), "hooks": [entry]})
    m["hooks"] = hooks
    env = m.get("env") if isinstance(m.get("env"), dict) else {}
    env["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] = str(MAX_CONTINUES)
    m["env"] = env
    _write_settings(m)


def uninstall() -> None:
    """Removes everything install added."""
    m = _read_settings()
    if isinstance(m.get("hooks"), dict):
        _strip(m["hooks"])
        if not m["hooks"]:
            del m["hooks"]
    if isinstance(m.get("env"), dict):
        m["env"].pop("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP", None)
        if not m["env"]:
            del m["env"]
    _write_settings(m)


def check(binary: str) -> Status:
    st = Status(binary=binary, settings=str(settings_path()))
    try:
        m = _read_settings()
    except RuntimeError:
        return st
    hooks = m.get("hooks") if isinstance(m.get("hooks"), dict) else {}
    for ev in EVENTS:
        for g in hooks.get(ev) or []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if (cmd := _ours(h)) is not None:
                    st.hooks += 1
                    if cmd != binary:
                        st.stale = True
    return st
