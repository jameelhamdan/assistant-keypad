"""The Claude Code command hook. It must never fail: whatever happens, it
prints a JSON object and exits 0. `{}` means "no decision".

Standard library only (plus keypad.ipc and keypad.dirs): this runs on every
tool call, and the bundled keypad-hook executable contains nothing else."""

from __future__ import annotations

import json
import os
import sys

# Events whose hook may wait for a key press (PreToolUse only for AskUserQuestion).
BLOCKING = {"PermissionRequest", "Stop"}
QUICK_TIMEOUT = 10  # everything else answers at once; never let a stuck agent stall Claude Code
TRANSCRIPT_WINDOW = 256 << 10
TRANSCRIPT_MAX = 4 << 20


def clip(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


def _scan(b: bytes) -> tuple[list[dict[str, str]], str]:
    texts: list[dict[str, str]] = []
    title = ""
    for line in b.split(b"\n"):
        if b'"type":"ai-title"' in line:
            try:
                title = clip(json.loads(line).get("aiTitle", ""), 60) or title
            except ValueError:
                pass
            continue
        if b'"type":"text"' not in line or b'"type":"assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                texts.append({"id": str(e.get("uuid", "")), "text": clip(c["text"], 8000)})
    return texts, title


def transcript_tail(path: str, want: int = 4) -> tuple[list[dict[str, str]], str]:
    """Claude's latest text messages (oldest first) and the session's title,
    from the end of the transcript, so the keypad can mirror the terminal.
    Only Claude's own prose is taken: tool calls, their output, thinking and
    file contents are skipped. The window widens (256 KB, 1 MB, 4 MB) when
    large tool calls push the messages further back."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            window = TRANSCRIPT_WINDOW
            while True:
                f.seek(max(0, size - window))
                b = f.read(window)
                if size > window and (i := b.find(b"\n")) >= 0:
                    b = b[i + 1 :]  # drop the partial first line
                texts, title = _scan(b)
                if len(texts) >= want or window >= size or window >= TRANSCRIPT_MAX:
                    return texts[-want:], title
                window *= 4
    except OSError:
        return [], ""


def slim(p: dict) -> dict:
    """Keeps only what the agent displays or needs to answer. Tool output,
    file contents and transcripts never leave the hook process."""
    out = {k: p[k] for k in ("session_id", "cwd", "hook_event_name", "permission_mode", "agent_id", "agent_type",
                             "source", "reason", "notification_type", "error_type", "stop_hook_active") if k in p}
    for k, n in (("prompt", 2000), ("last_assistant_message", 8000), ("message", 300), ("error", 400)):
        if isinstance(p.get(k), str):
            out[k] = clip(p[k], n)
    if isinstance(tp := p.get("transcript_path"), str) and tp:
        texts, title = transcript_tail(tp)
        if texts:
            out["transcript_texts"] = texts
        if title:
            out["session_title"] = title
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
                out.setdefault("change", {})[k] = clip(v, 4000)
        if isinstance(edits := inp.get("edits"), list):
            out.setdefault("change", {})["edits"] = [
                {k: clip(e[k], 2000) for k in ("old_string", "new_string") if isinstance(e.get(k), str)}
                for e in edits[:10] if isinstance(e, dict)]
    if isinstance(sugg := p.get("permission_suggestions"), list):
        out["permission_suggestions"] = sugg[:8]  # echoed back as updatedPermissions ("don't ask again")
    keep = {k: clip(v, 1400 if k in ("command", "plan") else 600)  # commands and plans whole: they are approved from them
            for k in ("command", "description", "file_path", "notebook_path", "pattern", "url", "query", "prompt", "title",
                      "path", "plan") if isinstance(v := inp.get(k), str)}
    if not keep:
        for k, v in inp.items():
            if isinstance(v, str) and len(keep) < 4:
                keep[k] = clip(v, 200)
    out["tool_input"] = {**keep, **out.pop("change", {})}
    return out


# Events where the agent learns which claude process a session is (the MCP
# server is matched to its session through it).
PID_EVENTS = {"SessionStart", "UserPromptSubmit"}


def parent_of(pid: int) -> int:
    """The parent of pid, standard library only (0 when unknown)."""
    try:
        import ctypes

        if sys.platform == "darwin":
            # proc_pidinfo(PROC_PIDTBSDINFO): struct proc_bsdinfo, pbi_ppid at offset 16
            buf = ctypes.create_string_buffer(136)
            lib = ctypes.CDLL("/usr/lib/libproc.dylib")
            if lib.proc_pidinfo(pid, 3, ctypes.c_uint64(0), buf, 136) < 20:
                return 0
            return int.from_bytes(buf.raw[16:20], "little")
        if sys.platform == "win32":
            from ctypes import wintypes

            class PE(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

            k32 = ctypes.windll.kernel32
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            snap = k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
            try:
                e = PE(ctypes.sizeof(PE))
                ok = k32.Process32First(wintypes.HANDLE(snap), ctypes.byref(e))
                while ok:
                    if e.th32ProcessID == pid:
                        return int(e.th32ParentProcessID)
                    ok = k32.Process32Next(wintypes.HANDLE(snap), ctypes.byref(e))
            finally:
                k32.CloseHandle(wintypes.HANDLE(snap))
            return 0
        with open(f"/proc/{pid}/stat", "rb") as f:
            return int(f.read().rsplit(b")", 1)[1].split()[1])
    except Exception:
        return 0


def run_hook(args: list[str]) -> int:
    out: object = {}
    try:
        if len(args) == 1:
            event = args[0]
            payload = json.loads(sys.stdin.buffer.read(5 << 20) or b"{}")
            if isinstance(payload, dict):
                from . import ipc

                ppid = os.getppid()
                # the hook's parent, and its parent (claude, when a shell runs the hook)
                pids = [ppid, parent_of(ppid)] if event in PID_EVENTS else []
                req = {"event": event, "pids": pids,
                       "payload": slim(payload)}
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
