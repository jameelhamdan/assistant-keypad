"""Installs and removes the keypad's Claude Code integration: command hooks in
~/.claude/settings.json and a user-scope MCP server. Only entries that run our
own binary are ever touched, and every write is preceded by a backup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import write_atomic
from .paths import env_without_bundle, hook_path

# Blocking hooks wait for a key press; the agent enforces the real timeout
# (at most 3600 s), so Claude Code must never cut them off first.
BLOCKING_TIMEOUT = 3630
QUICK_TIMEOUT = 15
MCP_NAME = "keypad"
KEEP_BACKUPS = 5

EVENTS = {
    "SessionStart": QUICK_TIMEOUT, "SessionEnd": QUICK_TIMEOUT, "UserPromptSubmit": QUICK_TIMEOUT,
    "PreToolUse": BLOCKING_TIMEOUT, "PostToolUse": QUICK_TIMEOUT, "PostToolUseFailure": QUICK_TIMEOUT,
    "PermissionRequest": BLOCKING_TIMEOUT, "Stop": BLOCKING_TIMEOUT, "StopFailure": QUICK_TIMEOUT,
    "Notification": QUICK_TIMEOUT,
}


def find_claude() -> str:
    """The claude CLI ("" = edit ~/.claude.json directly). Background agents
    get a minimal PATH, so the usual install locations are searched as well."""
    if p := shutil.which("claude"):
        return p
    home_ = Path.home()
    if sys.platform == "win32":
        cands = [home_ / ".local" / "bin" / "claude.exe", Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude-code" / "claude.exe"]
    else:
        cands = [home_ / ".local" / "bin" / "claude", home_ / ".claude" / "local" / "claude",
                 Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    return next((str(c) for c in cands if c.is_file()), "")


def home() -> Path:
    if d := os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(d)
    return Path.home() / ".claude"


def settings_path() -> Path:
    """The user-scope Claude Code settings file."""
    return home() / "settings.json"


def claude_json() -> Path:
    if d := os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(d) / ".claude.json"
    return Path.home() / ".claude.json"


@dataclass
class Status:
    hooks: int = 0  # number of our hook entries
    expected: int = len(EVENTS)  # number we would install
    mcp: bool = False  # MCP server registered
    binary: str = ""  # binary the hooks should point at
    stale: bool = False  # hooks point at another binary path
    settings: str = ""

    @property
    def installed(self) -> bool:
        return self.hooks == self.expected and self.mcp and not self.stale

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "installed": self.installed}


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
    return cmd if name in ("keypad", "keypad-hook") else None


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


def install(binary: str, max_continues: int) -> None:
    """Adds hooks for binary and registers the MCP server. max_continues raises
    Claude Code's own Stop-hook block cap to the same number."""
    hook = hook_path(binary)
    m = _read_settings()
    hooks = m.get("hooks") if isinstance(m.get("hooks"), dict) else {}
    _strip(hooks)
    for ev, timeout in EVENTS.items():
        entry = {"type": "command", "command": hook, "args": ["hook", ev], "timeout": timeout}
        hooks.setdefault(ev, []).append({"hooks": [entry]})
    m["hooks"] = hooks
    env = m.get("env") if isinstance(m.get("env"), dict) else {}
    env["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] = str(max_continues)
    m["env"] = env
    _write_settings(m)
    _register_mcp(binary)


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
    _unregister_mcp()


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
                    if cmd != hook_path(binary):
                        st.stale = True
    st.mcp = _mcp_registered(binary)
    return st


# ---- MCP registration (user scope, in ~/.claude.json) ----


def _run(args: list[str]) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(args, capture_output=True, text=True, creationflags=flags, timeout=60,
                          env=env_without_bundle())


def _register_mcp(binary: str) -> None:
    if _mcp_registered(binary):
        return  # already pointing at this binary
    if c := find_claude():
        _run([c, "mcp", "remove", "--scope", "user", MCP_NAME])
        r = _run([c, "mcp", "add", "--scope", "user", MCP_NAME, "--", binary, "mcp"])
        if r.returncode != 0:
            raise RuntimeError(f"claude mcp add: {(r.stdout + r.stderr).strip()}")
        return
    _edit_claude_json(lambda servers: servers.__setitem__(MCP_NAME, {"type": "stdio", "command": binary, "args": ["mcp"]}))


def _unregister_mcp() -> None:
    if c := find_claude():
        _run([c, "mcp", "remove", "--scope", "user", MCP_NAME])
        return
    _edit_claude_json(lambda servers: servers.pop(MCP_NAME, None))


def _edit_claude_json(fn: Callable[[dict], Any]) -> None:
    p = claude_json()
    m: dict[str, Any] = {}
    if p.exists():
        raw = p.read_bytes()
        try:
            m = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(f"{p} is not valid JSON: {e}") from e
        write_atomic(p.with_name(p.name + ".keypad-backup"), raw, 0o600)
    servers = m.get("mcpServers") if isinstance(m.get("mcpServers"), dict) else {}
    fn(servers)
    m["mcpServers"] = servers
    # Claude Code rewrites this file often: replace it in one step.
    write_atomic(p, json.dumps(m, indent=2, ensure_ascii=False).encode(), 0o600)


def _mcp_registered(binary: str) -> bool:
    try:
        m = json.loads(claude_json().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    s = (m.get("mcpServers") or {}).get(MCP_NAME) if isinstance(m, dict) else None
    return isinstance(s, dict) and s.get("command") == binary
