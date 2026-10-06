"""The agent: owns the keypads, answers Claude Code's hooks. It runs inside the tray
process (start_agent), or alone with `keypad agent`."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time

from . import config, ipc, osutil
from .core.agent import Agent
from .device.hub import Hub
from .paths import self_path
from .server import Server
from .version import version


def open_log(level: str) -> logging.Logger:
    config.log_dir().mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("keypad")
    log.setLevel(logging.DEBUG if level == "debug" else logging.INFO)
    if log.handlers:  # already open in this process
        return log
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(config.log_dir() / "agent.log", maxBytes=5 << 20, backupCount=1,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return log


def watch(stop: threading.Event, a: Agent) -> None:
    """Reloads config.json when edited by hand."""
    def mtime() -> float:
        try:
            return config.config_path().stat().st_mtime
        except OSError:
            return 0.0

    last_mod = mtime()
    while not stop.wait(3):
        if (m := mtime()) != last_mod:
            last_mod = m
            c, err = config.load()
            if err is None:
                a.set_config(c)
                a.log.info("config reloaded")
            else:
                a.log.warning("config.json has an error; keeping previous settings: %s", err)


_started: threading.Event | None = None  # the stop event of the agent running in this process


def start_agent(fake_device: str = "") -> tuple[threading.Event, logging.Logger] | None:
    """Starts the agent's threads in this process (the tray hosts the agent, or
    `keypad agent` does). Returns the stop event and logger, or None when
    another agent already serves this user."""
    global _started
    # Development only: the simulated keypad answers requests by itself, so it is not part of the
    # shipped app (packaging/keypad.spec leaves it out) and needs KEYPAD_DEV=1 from source.
    fakemod = None
    if fake_device:
        if os.environ.get("KEYPAD_DEV") != "1":
            raise SystemExit("--fake-device is for development: set KEYPAD_DEV=1 (it is not in the packaged app)")
        try:
            from .device import fake as fakemod
        except ImportError:
            raise SystemExit("--fake-device is for development and is not in the packaged app") from None
    if _started is not None and not _started.is_set():
        return None  # this process already runs one
    cfg, cfg_err = config.load()
    log = open_log(cfg.log_level)
    if cfg_err:
        log.warning("config problem, using defaults where needed: %s", cfg_err)
    if ipc.alive():
        log.info("another agent is already running")
        return None
    if ipc.stop_legacy():  # an older version's agent still holds the keypad
        log.warning("stopped an agent of an older Keypad version")
        time.sleep(1.5)
    store, err = config.Store.open()
    if err:
        log.warning("state problem: %s", err)

    stop = _started = threading.Event()
    agent = Agent(cfg, store, log, presence=osutil.idle_any)
    hub = Hub(store, agent, host_name=socket.gethostname().split(".")[0], log=log)
    agent.hub = hub
    srv = Server(agent, self_path(), log, stop.set)

    def serve() -> None:
        # A transient error here (e.g. a momentary OSError from the accept
        # loop) must not take the whole agent down with it: restart the IPC
        # server instead of escalating to a full shutdown. Only a genuine
        # second agent (ipc.Running) or a real stop request ends this loop.
        while not stop.is_set():
            try:
                ipc.serve(srv.handle, stop)
                return
            except ipc.Running:
                log.info("another agent is already running; stopping this one")
                stop.set()
            except Exception:
                log.exception("ipc server error; restarting")
                time.sleep(1)

    for target, targs in ((serve, ()), (agent.run, (stop,)), (hub.run, (stop,)), (watch, (stop, agent))):
        threading.Thread(target=target, args=targs, daemon=True).start()

    if fakemod is not None:
        policy = fakemod.POLICIES.get(fake_device, fakemod.first_key)
        f = fakemod.Fake("kp-fake01", policy, delay=0.3)
        threading.Thread(target=hub.serve, args=(f,), daemon=True).start()
        log.warning("SIMULATED KEYPAD attached - requests are answered automatically (policy %s)", fake_device)

    log.info("agent started version=%s host_id=%s config=%s pid=%d", version(), store.host_id(),
             config.config_path(), os.getpid())
    return stop, log


def run_agent(args: list[str]) -> int:
    """`keypad agent`: the agent alone, without a tray (headless)."""
    ap = argparse.ArgumentParser(prog="keypad agent")
    ap.add_argument("--fake-device", default="",
                    help="attach a simulated keypad that answers: first|allow|deny|continue|pc|none (testing only)")
    opts = ap.parse_args(args)
    started = start_agent(opts.fake_device)
    if started is None:
        return 0  # not a failure: exiting 0 keeps launchd / Task Scheduler from restarting a duplicate
    stop, log = started
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    log.info("agent stopping")
    time.sleep(0.2)  # let hub threads close their links
    return 0
