"""Saved prompts: the list on the keypad, and the one queued for a session's next hook."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

from .. import config, proto
from .ctx import Ctx
from .dialogs import Dialog, DialogError
from .sessions import IDLE, LOG_RESULT, WORKING, short
from .text import clip

if TYPE_CHECKING:
    from .agent import Agent

TTL = 900  # s a queued saved prompt waits for the session's next hook


class Shortcuts:
    def __init__(self, agent: Agent):
        self.a = agent
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[config.Shortcut, float]] = {}
        self._running: set[str] = set()  # sessions a saved prompt is being run in (headless resume)

    def labels(self) -> list[str]:
        return [clip(s.label, 28) for s in self.a.config().shortcuts]

    def quick(self) -> list[str]:
        """The first saved prompts, short, for keys 1-3 on the keypad's status screen."""
        return [clip(s.label, 8) for s in self.a.config().shortcuts[:proto.MAX_QUICK]]

    def screen(self, title: str, items: list[str], esc: str) -> dict[str, Any]:
        """The prompt box with saved prompts as suggestions (prompt template)."""
        return {"tpl": "prompt", "title": title, "items": items + self.labels(), "esc": esc}

    def menu(self) -> None:
        """Enter on the status screen: send a saved prompt to the shown session."""
        cur = self.a.sessions.get(self.a.shown_id())
        if not cur:
            return
        ctx = Ctx().with_timeout(30)

        def fn(d: Dialog) -> None:
            p = d.show(ctx, {**self.screen("Send to Claude", [], "back"), "project": cur.project})
            if p.get("act") == "pick" and isinstance(p.get("idx"), int):
                threading.Thread(target=self.queue, args=(p["idx"], cur.id), daemon=True).start()

        try:
            self.a.dialogs.run(ctx, cur.project, "menu", fn, sid=cur.id)
        except DialogError:
            pass

    def queue(self, idx: int, sid: str) -> None:
        """Queues saved prompt idx for a session. It is delivered on the
        session's next hook: the next tool result while Claude works (it queues
        the prompt itself), its stop, or your next prompt."""
        cfg = self.a.config()
        if not 0 <= idx < len(cfg.shortcuts):
            return
        sc = cfg.shortcuts[idx]
        s = self.a.sessions.get(sid)
        if not s:
            return
        if cfg.behavior.run_when_idle and not self.a.sessions.is_busy(sid) and self._run(sid, s.cwd, sc, s.project):
            return
        with self._lock:
            self._pending[sid] = (sc, time.monotonic())
        self.a.log.info("shortcut queued session=%s project=%s label=%s", short(sid), s.project, sc.label)
        where = "" if self.a.sessions.is_busy(sid) else " (with your next prompt)"
        self.a.toast(f"Queued for {s.project}: {sc.label}{where}", "info", 2500)
        self.a.mark_dirty()

    def _run(self, sid: str, cwd: str, sc: config.Shortcut, project: str) -> bool:
        """An idle session cannot be reached by a hook, so the prompt is run on its own: the
        session is resumed headless (`claude -p --resume`) and the prompt goes in on stdin. False
        if that is not possible (no claude on the PATH, already running): it is queued instead."""
        with self._lock:
            if sid in self._running:
                return False
        proc = self._spawn(sid, cwd, sc.prompt)
        if proc is None:
            return False
        with self._lock:
            self._running.add(sid)
        self.a.log.info("shortcut run session=%s project=%s label=%s", short(sid), project, sc.label)
        self.a.sessions.touch(sid, WORKING, "Running", clip(sc.label, 40), cwd)
        self.a.sessions.add_log(sid, {"k": LOG_RESULT, "t": f"{sc.label} sent from the keypad"})
        self.a.toast(f"Sent to {project}: {sc.label}", "info", 2500)
        threading.Thread(target=self._watch, args=(sid, proc, project, sc.label), daemon=True).start()
        return True

    def _spawn(self, sid: str, cwd: str, prompt: str) -> subprocess.Popen | None:
        exe = shutil.which("claude")
        if not exe:
            return None
        try:
            proc = subprocess.Popen(
                [exe, "-p", "--resume", sid], cwd=cwd if cwd and os.path.isdir(cwd) else None, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
            return proc
        except (OSError, ValueError):
            self.a.log.exception("could not start claude for a saved prompt")
            return None

    def _watch(self, sid: str, proc: subprocess.Popen, project: str, label: str) -> None:
        try:
            out = proc.communicate(timeout=3600)[0] or ""
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            out, ok = "timed out", False
        except Exception:
            out, ok = "", False
        with self._lock:
            self._running.discard(sid)
        s = self.a.sessions.get(sid)
        if s and s.state == WORKING and s.title == "Running":
            self.a.sessions.touch(sid, IDLE, "Done" if ok else "Failed", "" if ok else clip(out.strip().splitlines()[-1] if out.strip() else "", 120))
        if ok:
            self.a.notify(f"Done in {project}: {label}")
        else:
            self.a.log.warning("saved prompt failed session=%s label=%s output=%s", short(sid), label, clip(out, 300))
            self.a.toast(f"Failed in {project}: {label}", "warn", 4000)

    def queued(self, sid: str) -> str:
        """The label of the saved prompt waiting for a session's next hook ("" if none)."""
        with self._lock:
            p = self._pending.get(sid)
        return clip(p[0].label, 20) if p and time.monotonic() - p[1] <= TTL else ""

    def cancel(self, sid: str) -> bool:
        """Takes back a queued saved prompt that Claude has not received yet."""
        label = self.queued(sid)
        with self._lock:
            self._pending.pop(sid, None)
        if label:
            self.a.log.info("shortcut cancelled session=%s label=%s", short(sid), label)
            self.a.toast(f"Cancelled: {label}", "info", 2000)
            self.a.mark_dirty()
        return bool(label)

    def take(self, sid: str) -> config.Shortcut | None:
        with self._lock:
            p = self._pending.pop(sid, None)
        if p:
            self.a.mark_dirty()  # the keypad stops showing it as waiting
        if not p or time.monotonic() - p[1] > TTL:
            return None
        return p[0]
