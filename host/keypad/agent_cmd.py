"""`keypad agent`: the background agent (started at login)."""

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
from .device import fake as fakemod
from .device.hub import Hub
from .paths import self_path
from .server import Server
from .version import version


def open_log(level: str) -> logging.Logger:
    config.log_dir().mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("keypad")
    log.setLevel(logging.DEBUG if level == "debug" else logging.INFO)
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


def theme() -> str:
    return "dark" if osutil.dark_mode() else "light"


def watch(stop: threading.Event, a: Agent, hub: Hub) -> None:
    """Reloads config.yaml when edited by hand and follows the OS theme."""
    def mtime() -> float:
        try:
            return config.config_path().stat().st_mtime
        except OSError:
            return 0.0

    last_mod, last_theme, n = mtime(), theme(), 0
    while not stop.wait(3):
        if (m := mtime()) != last_mod:
            last_mod = m
            c, err = config.load()
            if err is None:
                a.set_config(c)
                a.log.info("config reloaded")
            else:
                a.log.warning("config.yaml has an error; keeping previous settings: %s", err)
        n += 1
        if n % 5 == 0 and (th := theme()) != last_theme:  # every 15 s
            last_theme = th
            for d in a.store.devices():
                if d.theme not in ("dark", "light"):
                    hub.send_settings(d.id)


def run_agent(args: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="keypad agent")
    ap.add_argument("--fake-device", default="",
                    help="attach a simulated keypad that answers: first|allow|deny|continue|pc|none (testing only)")
    opts = ap.parse_args(args)

    cfg, cfg_err = config.load()
    log = open_log(cfg.log_level)
    if cfg_err:
        log.warning("config problem, using defaults where needed: %s", cfg_err)
    if ipc.alive():
        # Not a failure: exiting 0 keeps launchd / Task Scheduler from
        # restarting this duplicate every few seconds.
        log.info("another agent is already running; exiting")
        return 0
    store, err = config.Store.open()
    if err:
        log.warning("state problem: %s", err)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    agent = Agent(cfg, store, osutil.idle, log, presence=osutil.idle_any)
    hub = Hub(store, agent, host_name=socket.gethostname().split(".")[0], theme=theme, log=log,
              wifi_only=lambda: agent.config().behavior.wifi_only)
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
                log.info("another agent is already running; exiting")
                stop.set()
            except Exception:
                log.exception("ipc server error; restarting")
                time.sleep(1)

    for target, targs in ((serve, ()), (agent.run, (stop,)), (hub.run, (stop,)), (watch, (stop, agent, hub))):
        threading.Thread(target=target, args=targs, daemon=True).start()

    if opts.fake_device:
        policy = fakemod.POLICIES.get(opts.fake_device, fakemod.first_key)
        f = fakemod.Fake("kp-fake01", policy, delay=0.3)
        threading.Thread(target=hub.serve, args=(f,), daemon=True).start()
        log.warning("SIMULATED KEYPAD attached - requests are answered automatically (policy %s)", opts.fake_device)

    log.info("agent started version=%s host_id=%s config=%s pid=%d", version(), store.host_id(),
             config.config_path(), os.getpid())
    while not stop.wait(1):
        pass
    log.info("agent stopping")
    time.sleep(0.2)  # let hub threads close their links
    return 0
