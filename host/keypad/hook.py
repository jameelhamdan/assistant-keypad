"""The Claude Code command hook. It must never fail: whatever happens, it
prints a JSON object and exits 0. `{}` means "no decision".

Standard library only (plus keypad.ipc and keypad.dirs): it starts on every
prompt, permission, question and stop, so it must start quickly."""

from __future__ import annotations

import json
import sys

# Events whose hook may wait for a key press (PreToolUse only for AskUserQuestion).
BLOCKING = {"PermissionRequest", "Stop"}
QUICK_TIMEOUT = 10  # everything else answers at once; never let a stuck agent stall Claude Code


def _clip(s: str, n: int) -> str:
    """Not core.text.clip (different truncation math, off by one at the
    boundary) -- this file is stdlib-only and can't import it."""
    return s[:n] + "…" if len(s) > n else s


def slim(p: dict) -> dict:
    """Keeps only what the agent displays or needs to answer. Tool output,
    file contents and transcripts never leave the hook process."""
    out = {k: p[k] for k in ("session_id", "cwd", "hook_event_name", "permission_mode", "agent_id", "agent_type",
                             "source", "reason", "notification_type", "error_type", "stop_hook_active") if k in p}
    for k, n in (("prompt", 2000), ("last_assistant_message", 8000), ("message", 300), ("error", 400)):
        if isinstance(p.get(k), str):
            out[k] = _clip(p[k], n)
    if isinstance(tp := p.get("transcript_path"), str) and tp:
        out["transcript_path"] = tp  # the agent follows the file itself
    tool = p.get("tool_name")
    if not isinstance(tool, str) or not tool:
        return out
    out["tool_name"] = tool
    inp = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
    if tool == "AskUserQuestion":
        out["tool_input"] = inp  # echoed back in updatedInput: must be complete
        return out
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):  # the change itself, shown as a diff to approve
        for k in ("old_string", "new_string", "content", "new_source"):
            if isinstance(v := inp.get(k), str):
                out.setdefault("change", {})[k] = _clip(v, 4000)
        if isinstance(edits := inp.get("edits"), list):
            out.setdefault("change", {})["edits"] = [
                {k: _clip(e[k], 2000) for k in ("old_string", "new_string") if isinstance(e.get(k), str)}
                for e in edits[:10] if isinstance(e, dict)]
    if isinstance(sugg := p.get("permission_suggestions"), list):
        out["permission_suggestions"] = sugg[:8]  # echoed back as updatedPermissions ("don't ask again")
    keep = {k: _clip(v, 1400 if k in ("command", "plan") else 600)  # commands and plans whole: they are approved from them
            for k in ("command", "description", "file_path", "notebook_path", "pattern", "url", "query", "prompt", "title",
                      "path", "plan") if isinstance(v := inp.get(k), str)}
    if not keep:
        for k, v in inp.items():
            if isinstance(v, str) and len(keep) < 4:
                keep[k] = _clip(v, 200)
    out["tool_input"] = {**keep, **out.pop("change", {})}
    return out


def run_hook(args: list[str]) -> int:
    out: object = {}
    try:
        if len(args) == 1:
            event = args[0]
            payload = json.loads(sys.stdin.buffer.read(5 << 20) or b"{}")
            if isinstance(payload, dict):
                from . import ipc

                req = {"event": event, "payload": slim(payload)}
                waits = event in BLOCKING or (event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion")
                timeout = 3600 + 20 if waits else QUICK_TIMEOUT
                reply = ipc.request("POST", "/hook", req, timeout=timeout)
                if isinstance(reply, dict):
                    out = reply
    except BaseException:  # agent not running, bad input, anything: behave as if no hook existed
        out = {}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0
