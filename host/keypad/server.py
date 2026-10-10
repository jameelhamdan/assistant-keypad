"""The agent's API over the private IPC channel (see ipc.py), used by the hook
shim, the tray and the CLI."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any

from . import config, firmware, proto
from .core.agent import Agent
from .core.ctx import Ctx
from .hook import GRACE


class Fail(Exception):
    """A request error shown to the user."""


class Server:
    def __init__(self, agent: Agent, binary: str, log: logging.Logger, quit_: Callable[[], None]):
        self.a, self.bin, self.log, self.quit = agent, binary, log, quit_
        self._ota_lock = threading.Lock()
        self._ota: dict[str, int] = {}
        self._routes: list[tuple[str, re.Pattern, Callable]] = []
        r = self._route
        r("POST", "/hook", self.hook)
        r("GET", "/status", self.status)
        r("POST", "/pause", self.pause)
        r("POST", "/quit", self.do_quit)
        r("POST", "/shortcut", self.shortcut)
        r("POST", "/session", self.session)
        r("GET", "/config", lambda b, **_: self.a.config().to_dict())
        r("PUT", "/config", self.put_config)
        r("PATCH", "/devices/(?P<id>[^/]+)", self.patch_device)
        r("POST", "/pairing", self.pairing)
        r("POST", "/devices/(?P<id>[^/]+)/provision", self.provision)
        r("POST", "/devices/(?P<id>[^/]+)/unpair", self.unpair)
        r("POST", "/devices/(?P<id>[^/]+)/take", self.take)
        r("POST", "/devices/(?P<id>[^/]+)/update", self.update)
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
        wait = b.get("wait")
        if isinstance(wait, int | float) and wait > GRACE:  # the shim's own deadline, less its grace
            ctx = ctx.with_timeout(wait - GRACE)
        payload = b.get("payload") if isinstance(b.get("payload"), dict) else {}
        return self.a.hook(ctx, str(b.get("event", "")), payload)

    # ---- control ----

    def status(self, b: dict, **_) -> dict:
        with self._ota_lock:
            updates = dict(self._ota)  # firmware updates by keypad id: 0-99 running, 100 done, -1 failed
        return {**self.a.snapshot(), "updates": updates}

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
        threading.Thread(target=self.a.shortcuts.queue, args=(int(b.get("index", -1)), sid), daemon=True).start()
        return ok()

    def session(self, b: dict, **_) -> dict:
        if not self.a.sessions.select(str(b.get("id", ""))):
            raise Fail("no such session")
        self.a.mark_dirty()
        return ok()

    def put_config(self, b: dict, **_) -> dict:
        if not b:
            raise Fail("empty settings")
        c = config.Config.from_dict(b)
        c.validate()  # clamps in place
        self.a.set_config(c)
        config.save(c)
        return c.to_dict()

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

    def take(self, b: dict, id: str, **_) -> dict:
        if self.a.store.device(id) is None:
            raise Fail("unknown keypad")
        self.hub.take(id)
        return ok()

    def pairing(self, b: dict, **_) -> dict:
        """Opens the USB ports for a few minutes: a keypad plugged in is found and can be set up."""
        self.hub.usb.open()
        return ok()

    def provision(self, b: dict, id: str, **_) -> dict:
        ssid = str(b.get("ssid", "")).strip()
        if not ssid:
            raise Fail("enter the Wi-Fi network name")
        self.hub.usb.provision(id, ssid, str(b.get("pass", "")), proto.fit(str(b.get("name", "")), proto.DEVICE_NAME))
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
                old = self.hub.get(id)
                self.hub.update_firmware(id, image, progress)
                if (why := self.check_updated(id, old)) is not None:
                    raise RuntimeError(why)
                progress(100)
            except Exception as e:
                self.log.warning("firmware update failed id=%s: %s", id, e)
                progress(-1)

        threading.Thread(target=run, daemon=True).start()
        return ok()


    def check_updated(self, id: str, old) -> str | None:
        """After an update the keypad restarts: why it did not come back with the new firmware, or None."""
        c = self.hub.wait_reconnect(id, old, UPDATE_RECONNECT)
        if c is None:
            return f"the keypad did not reconnect within {UPDATE_RECONNECT:.0f} s of the update"
        want, got = firmware.version(), c.hello.get("fw", "")
        if want != "dev" and got != want:
            return f"the keypad came back with firmware {got!r}, not {want!r}: the update was rolled back"
        return None


UPDATE_RECONNECT = 60.0  # s a keypad gets to restart and reconnect after an update


def ok() -> dict:
    return {"ok": True}
