"""Follows a Claude Code session's transcript (the JSONL file it appends to) and
turns new lines into what the keypad mirrors: your prompts, Claude's text, tool
calls and errors. Thinking, tool output and file contents are never taken.

This is the single source of truth for the live feed: hooks only decide
(permissions, questions, stop); they do not carry text."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .text import first_line, redact, s, summarize, tool_name

WINDOW = 256 << 10  # first read: the end of the file, widened (x4) until enough entries are found
MAX_WINDOW = 4 << 20
MAX_CHUNK = 4 << 20  # at most this much new data per poll

USER, CLAUDE, TOOL, RESULT = "u", "c", "t", "r"  # the keypad's transcript kinds
INTERRUPT = "i"  # you stopped Claude: not shown, but the session is no longer working


@dataclass
class Entry:
    kind: str
    text: str
    uuid: str = ""
    tool: str = ""  # TOOL entries: the tool's name
    detail: str = ""  # TOOL entries: its one-line summary


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _is_synthetic(text: str) -> bool:
    """Slash-command echoes and other text Claude Code writes as a "user" message."""
    t = text.lstrip()
    return t.startswith(("<command-", "<local-command", "<system-reminder", "<task-notification", "Caveat:"))


def parse(e: dict[str, Any]) -> list[Entry]:
    """The keypad entries of one transcript line."""
    if e.get("isSidechain") or e.get("isMeta"):
        return []
    msg = e.get("message") if isinstance(e.get("message"), dict) else {}
    uid = s(e, "uuid")
    out: list[Entry] = []
    if e.get("type") == "user":
        for b in _blocks(msg.get("content")):
            if b.get("type") == "text" and (t := s(b, "text").strip()):
                if t.startswith("[Request interrupted"):
                    out.append(Entry(INTERRUPT, t, uid))
                elif not _is_synthetic(t):
                    out.append(Entry(USER, redact(t), uid))
            elif b.get("type") == "tool_result" and b.get("is_error"):
                body = b.get("content")
                text = " ".join(s(x, "text") for x in _blocks(body)) if not isinstance(body, str) else body
                out.append(Entry(RESULT, "Error: " + first_line(redact(text)), uid))
    elif e.get("type") == "assistant":
        for b in _blocks(msg.get("content")):
            if b.get("type") == "text" and (t := s(b, "text").strip()):
                out.append(Entry(CLAUDE, redact(t), uid))
            elif b.get("type") == "tool_use" and s(b, "name"):
                name = s(b, "name")
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                detail = summarize(name, inp)
                out.append(Entry(TOOL, f"{tool_name(name)}({detail})", uid, name, detail))
    return out


def _moves(e: dict[str, Any]) -> bool:
    """Whether a line means the conversation moved on: Claude wrote something, a tool
    returned, or you typed a prompt. Echoes of system events (background task
    notices, slash commands) do not."""
    if e.get("isMeta"):
        return False
    msg = e.get("message") if isinstance(e.get("message"), dict) else {}
    if e.get("type") == "assistant":
        return True
    if e.get("type") != "user":
        return False
    return bool(parse(e)) or any(b.get("type") == "tool_result" for b in _blocks(msg.get("content")))


class Tail:
    """One transcript file. poll() returns the entries appended since the last call."""

    def __init__(self, path: str, history: int = 12):
        self.path, self.history = path, history
        self.pos: int | None = None
        self.buf = b""
        self.title = ""  # the session's title, as on its terminal tab
        self.cwd = ""
        self.mode = ""  # permission mode
        self.seen = False  # whether anything was ever read
        self.activity = 0  # lines of the main conversation read so far (anything new means Claude moved on)

    def _lines(self, b: bytes) -> list[Entry]:
        out: list[Entry] = []
        for line in b.split(b"\n"):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get("type") == "ai-title" and s(e, "aiTitle"):
                self.title = s(e, "aiTitle")[:60]
            if not e.get("isSidechain"):
                if _moves(e):
                    self.activity += 1
                self.cwd = s(e, "cwd") or self.cwd
                self.mode = s(e, "permissionMode") or self.mode
            out.extend(parse(e))
        return out

    def poll(self) -> list[Entry]:
        try:
            size = os.stat(self.path).st_size
            if self.pos is None:
                return self._first(size)
            if size < self.pos:  # replaced or truncated: start again
                self.pos, self.buf = 0, b""
            if size == self.pos:
                return []
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(min(size - self.pos, MAX_CHUNK))
        except OSError:
            return []
        self.pos += len(data)
        data = self.buf + data
        cut = data.rfind(b"\n") + 1  # a line still being written stays in the buffer
        self.buf = data[cut:]
        return self._lines(data[:cut])

    def _first(self, size: int) -> list[Entry]:
        window = WINDOW
        with open(self.path, "rb") as f:
            while True:
                f.seek(max(0, size - window))
                b = f.read(min(window, size))
                if size > window and (i := b.find(b"\n")) >= 0:
                    b = b[i + 1:]  # drop the partial first line
                cut = b.rfind(b"\n") + 1
                self.title = self.cwd = self.mode = ""
                entries = self._lines(b[:cut])
                if len(entries) >= self.history or window >= size or window >= MAX_WINDOW:
                    break
                window *= 4
        self.pos, self.buf, self.seen = size, b[cut:], True  # a line still being written finishes in the next poll
        return entries[-self.history:]
