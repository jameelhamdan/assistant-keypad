"""The agent's API over the private IPC channel (see ipc.py), used by the hook
shim, the tray and the CLI."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any

from . import claudecfg, config, firmware, proto
from .core.agent import Agent
from .core.ctx import Ctx


class Fail(Exception):
    """A request error shown to the user."""


class Server:
    def __init__(self, agent: Agent, binary: str, log: logging.Logger, quit_: Callable[[], None]):
        self.a, self.bin, self.log, self.quit = agent, binary, log, quit_
        self._ota_lock = threading.Lock()
        self._ota: dict[str, int] = {}
        self._pair_page = None
        self._routes: list[tuple[str, re.Pattern, Callable]] = []
        r = self._route
        r("POST", "/hook", self.hook)
        r("GET", "/status", lambda b, **_: self.a.snapshot())
        r("POST", "/pause", self.pause)
        r("POST", "/quit", self.do_quit)
        r("POST", "/shortcut", self.shortcut)
        r("POST", "/session", self.session)
        r("GET", "/config", lambda b, **_: {**self.a.config().to_dict(), "rev": self.a.config_rev()})
        r("PUT", "/config", self.put_config)
        r("PATCH", "/devices/(?P<id>[^/]+)", self.patch_device)
        r("GET", "/pair-url", self.pair_url)
        r("POST", "/devices/(?P<id>[^/]+)/provision", self.provision)
        r("POST", "/devices/(?P<id>[^/]+)/unpair", self.unpair)
        r("POST", "/devices/(?P<id>[^/]+)/update", self.update)
        r("GET", "/devices/(?P<id>[^/]+)/update", self.update_status)
        r("GET", "/firmware", lambda b, **_: {"bundled": firmware.image() is not None, "version": firmware.version()})

    @property
    def hub(self):
        return self.a.hub

    def _route(self, method: str, pattern: str, fn: Callable) -> None:
        self._routes.append((method, re.compile(pattern + "$"), fn))

    def handle(self, method: str, path: str, body: Any, client_gone: Callable[[], bool]) -> tuple[int, Any]:
        for m, rx, fn in self._routes:
            if m == method and (mt := rx.match(path)):
                try:
                    return 200, fn(body if isinstance(body, dict) else {}, gone=client_gone, **mt.groupdict())
                except (Fail, RuntimeError, ValueError, OSError, TimeoutError, ConnectionError) as e:
                    return 400, {"error": str(e)}
                except Exception as e:  # a bug: report it, keep serving
                    self.log.exception("request %s %s failed", method, path)
                    return 500, {"error": f"internal error: {e}"}
        return 404, {"error": f"no route {method} {path}"}

    # ---- Claude Code shims ----

    def hook(self, b: dict, gone: Callable[[], bool], **_) -> dict:
        ctx = Ctx(cancelled=gone)  # the hook shim hung up: Claude Code moved on
        payload = b.get("payload") if isinstance(b.get("payload"), dict) else {}
        return self.a.hook(ctx, str(b.get("event", "")), payload)

    # ---- control ----

    def do_quit(self, b: dict, **_) -> dict:
        threading.Timer(0.1, self.quit).start()
        return ok()

    def pause(self, b: dict, **_) -> dict:
        self.a.set_paused(bool(b.get("paused")))
        return ok()

    def shortcut(self, b: dict, **_) -> dict:
        sid = str(b.get("session") or "")
        if not sid:
            cur = self.a.sessions.current()
            sid = cur.id if cur else ""
        threading.Thread(target=self.a.queue_shortcut, args=(int(b.get("index", -1)), sid), daemon=True).start()
        return ok()

    def session(self, b: dict, **_) -> dict:
        if b.get("follow"):
            self.a.sessions.follow()
        elif not self.a.sessions.select(str(b.get("id", ""))):
            raise Fail("no such session")
        self.a.mark_dirty()
        return ok()

    def put_config(self, b: dict, **_) -> dict:
        if not b:
            raise Fail("empty settings")
        prev = self.a.config()
        c = config.Config.from_dict(b)
        c.validate()  # clamps in place
        if not self.a.set_config(c, b.get("rev") if isinstance(b.get("rev"), int) else None):
            raise Fail("the settings changed elsewhere meanwhile: try again")
        config.save(c)
        # keep Claude Code's own continue cap and hook timeout in step (only when they changed:
        # every install backs up settings.json)
        if ((c.behavior.max_continues, c.behavior.timeout) != (prev.behavior.max_continues, prev.behavior.timeout)
                and claudecfg.check(self.bin).hooks > 0):
            try:
                claudecfg.install(self.bin, c.behavior)
            except (OSError, RuntimeError) as e:
                self.log.warning("updating Claude Code's hook settings failed: %s", e)
        return {**c.to_dict(), "rev": self.a.config_rev()}

    # ---- keypads ----

    def patch_device(self, b: dict, id: str, **_) -> dict:
        if self.a.store.device(id) is None:
            raise Fail("unknown keypad")

        def upd(d: config.Device) -> None:
            if isinstance(b.get("name"), str) and b["name"].strip():
                d.name = proto.fit(b["name"].strip(), proto.DEVICE_NAME)

        self.a.store.update(id, upd)
        self.hub.send_settings(id)
        self.a.mark_dirty()  # the project filter may have changed
        return ok()

    def unpair(self, b: dict, id: str, **_) -> dict:
        self.hub.unpair(id)
        return ok()

    def pair_url(self, b: dict, **_) -> dict:
        """The address of the local pairing page (also opens the USB port for a while)."""
        from .pairpage import PairPage

        self.hub.open_pairing()
        if self._pair_page is None:
            self._pair_page = PairPage(self.a, self.hub)
        return {"url": self._pair_page.url()}

    def provision(self, b: dict, id: str, **_) -> dict:
        ssid = str(b.get("ssid", "")).strip()
        if not ssid:
            raise Fail("enter the Wi-Fi network name")
        self.hub.provision(id, ssid, str(b.get("pass", "")), proto.fit(str(b.get("name", "")), proto.DEVICE_NAME))
        return ok()

    def update(self, b: dict, id: str, **_) -> dict:
        image = firmware.image()
        if image is None:
            raise Fail("this build has no bundled firmware")
        with self._ota_lock:
            if 0 <= self._ota.get(id, -1) < 100:
                raise Fail("an update is already running on this keypad")
            self._ota[id] = 0

        def progress(p: int) -> None:
            with self._ota_lock:
                self._ota[id] = p

        def run() -> None:
            try:
                self.hub.update_firmware(id, image, progress)
                progress(100)
            except Exception as e:
                self.log.warning("firmware update failed id=%s: %s", id, e)
                progress(-1)

        threading.Thread(target=run, daemon=True).start()
        return ok()

    def update_status(self, b: dict, id: str, **_) -> dict:
        with self._ota_lock:
            p = self._ota.get(id)
        return {"progress": p if p is not None else 0, "running": p is not None and 0 <= p < 100}


def ok() -> dict:
    return {"ok": True}
