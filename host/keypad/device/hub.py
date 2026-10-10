"""The hub finds, connects and supervises keypads."""

from __future__ import annotations

import base64
import hashlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .. import config, proto, secure
from ..proto import Refusal
from .discovery import Discovery
from .links import Link, LinkClosed, WifiLink
from .pairing import UsbSetup

DIAL_EVERY = 2.0  # s between attempts to reach a paired keypad that is not connected
FLAP_SECONDS = 8.0  # a connection shorter than this counts as a drop-out
BUSY_RETRY = 15.0  # s between looks at a keypad another computer is using
PING_EVERY = 2.0
DEAD_AFTER = 8.0
HELLO_WITHIN = 5.0


class Events(Protocol):
    def connected(self, c: Conn) -> None: ...
    def disconnected(self, c: Conn) -> None: ...
    def message(self, c: Conn, m: dict[str, Any]) -> None: ...


def battery_of(m: dict[str, Any]) -> int:
    b = m.get("bat")
    return b if isinstance(b, int) and 0 <= b <= 100 else -1


class Conn:
    """One live, handshaken keypad."""

    def __init__(self, hub: Hub, link: Link, hello: dict[str, Any]):
        self.hub, self.link, self.hello = hub, link, hello
        self.id: str = hello["id"]
        self.since = time.time()
        self.wifi: dict[str, Any] = hello.get("wifi") if isinstance(hello.get("wifi"), dict) else {}
        self.battery = battery_of(hello)
        self._lock = threading.Lock()
        self._waiters: list[tuple[str, Callable[[dict[str, Any]], bool] | None, queue.Queue]] = []
        self.done = threading.Event()
        self.feed: tuple[str, list[dict[str, str]]] | None = None  # the transcript last sent: session, entries

    def send(self, msg: dict[str, Any]) -> bool:
        """Sends a host message; False (and logged) if it could not be sent."""
        try:
            self.link.send(proto.encode(msg))
            return True
        except (proto.InvalidMessage, LinkClosed, OSError) as e:
            self.hub.log.debug("send to %s failed: %s", self.id, e)
            return False

    def push_feed(self, sel: str, log: list[dict[str, str]]) -> None:
        """Gives the keypad the transcript of the selected session, when it changed."""
        # The oldest entries go until the text fits the keypad's buffer and the message the protocol limit.
        while log and sum(len(x["t"].encode()) + 1 for x in log) > proto.LOG_POOL:
            log = log[1:]
        if self.feed == (sel, log):
            return
        while True:
            msg = {"t": "feed", "sid": sel, "full": log}
            try:
                proto.encode(msg)
                break
            except proto.InvalidMessage:
                if not log:
                    return
                log = log[1:]
        if self.send(msg):
            self.feed = (sel, log)

    def close(self) -> None:
        if not self.done.is_set():
            self.done.set()
            self.link.close()

    @property
    def live(self) -> bool:
        """Whether this is the keypad's working link (Wi-Fi). USB is only for setup: pairing and unpairing."""
        return self.link.kind != "usb"

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {"id": self.id, "name": self.hello.get("name", ""), "link": self.link.kind, "addr": self.link.addr,
                    "fw": self.hello.get("fw", ""), "paired": bool(self.hello.get("paired")), "battery": self.battery,
                    "wifi": dict(self.wifi), "since": self.since}

    def request(self, msg: dict[str, Any], t: str, timeout: float,
                match: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
        """Sends msg and waits for the reply of type t. Registers before
        sending: a fast keypad can answer before a later registration.
        match, if given, further filters replies (e.g. by chunk offset) so a
        stale reply to an earlier, already-timed-out request of the same type
        can't be delivered to this one."""
        w: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._waiters.append((t, match, w))
        try:
            if not self.send(msg):
                raise ConnectionError("could not reach the keypad")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    return w.get(timeout=0.2)
                except queue.Empty:
                    if self.done.is_set():
                        raise ConnectionError("keypad disconnected") from None
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"keypad did not answer {t!r}") from None
        finally:
            with self._lock:
                self._waiters = [x for x in self._waiters if x[2] is not w]

    def _deliver(self, m: dict[str, Any]) -> None:
        with self._lock:
            for t, match, w in self._waiters:
                if t == m["t"] and (match is None or match(m)):
                    try:
                        w.put_nowait(m)
                    except queue.Full:
                        pass


def explain_refusal(why: str, code: str = "", host: str = "") -> str:
    """A keypad's refusal of a Wi-Fi connection, in words for the tray. code is the keypad's own
    reason (proto.Refusal); firmware that predates it is recognised by the words."""
    if code == Refusal.BUSY:
        return f"it is in use by {host or 'another computer'}: choose Use keypad here in the tray to take it over"
    if code == Refusal.BAD_HELLO or (not code and ("bad hello" in why or "version" in why)):
        return "its firmware is out of date for this app: flash it over USB (make flash)"
    if code == Refusal.OTHER_HOST or (not code and "another computer" in why):
        return "it is not paired with this computer: use Set up Wi-Fi… with the USB cable to add this computer"
    if code == Refusal.NOT_PAIRED or (not code and "not paired" in why):
        return "it is not paired: use Set up Wi-Fi… with the USB cable"
    return f"it refused the connection ({why}): use Set up Wi-Fi… over USB"


class Hub:
    def __init__(self, store: config.Store, ev: Events, host_name: str = "", log: logging.Logger | None = None):
        self.store, self.ev = store, ev
        self.host_name = host_name
        self.log = log or logging.getLogger("keypad")
        self.disc = Discovery(self.log)
        self._lock = threading.Lock()
        self._conns: dict[str, Conn] = {}
        self._take: set[str] = set()  # keypads to take over from another computer on the next dial
        self._flaps: dict[str, int] = {}  # keypad id -> connections in a row that dropped within seconds
        self._dial_after: dict[str, float] = {}  # keypad id -> earliest next attempt (backoff while it keeps dropping)
        self.problems: dict[str, str] = {}  # keypad id -> why it cannot connect (shown in the tray)
        self._busy: set[str] = set()  # USB ports, dial and update jobs in progress
        self.usb = UsbSetup(self)  # USB is only for setup: pairing and unpairing

    # ---- supervision ----

    def run(self, stop: threading.Event) -> None:
        """Scans USB, browses mDNS and dials paired keypads until stop is set."""
        self.disc.start()
        last_dial: dict[str, float] = {}
        try:
            while not stop.is_set():
                self.usb.scan()
                for d in self.store.devices():
                    if not d.key or ((c := self.get(d.id)) and c.live) or time.monotonic() - last_dial.get(d.id, -1e9) < DIAL_EVERY or time.monotonic() < self._dial_after.get(d.id, 0):
                        continue
                    if self.claim("dial:" + d.id):
                        last_dial[d.id] = time.monotonic()
                        threading.Thread(target=self._dial, args=(d,), daemon=True).start()
                stop.wait(2)
        finally:
            for c in self.conns():
                c.close()
            self.disc.stop()

    def take(self, dev_id: str) -> None:
        """Takes a keypad over from the other computer using it: the next dial asks the keypad to let go."""
        self._take.add(dev_id)
        self._dial_after.pop(dev_id, None)
        self._flaps.pop(dev_id, None)
        self.problems.pop(dev_id, None)

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._busy.discard(key)

    def _dial(self, d: config.Device) -> None:
        try:
            addrs = []
            if s := self.disc.get(d.id):
                addrs.append(s["addr"])
            if d.last_ip:
                addrs.append(f"{d.last_ip}:{proto.TCP_PORT}")
            for a in dict.fromkeys(addrs):
                try:
                    link = WifiLink(a, self.store.host_id(), d.id, d.key, take=d.id in self._take)
                except secure.Refused as e:
                    self.log.info("keypad %s refused the connection: %s", d.id, e)
                    self.problems[d.id] = explain_refusal(e.why, e.code, e.host)
                    if e.code == Refusal.BUSY:  # someone else has it: look again now and then, not every 2 s
                        self._dial_after[d.id] = time.monotonic() + BUSY_RETRY
                    return
                except secure.SecureError as e:
                    self.log.info("keypad %s refused the connection: %s", d.id, e)
                    self.problems[d.id] = explain_refusal(str(e))
                    return
                except OSError as e:
                    self.log.debug("wifi dial failed id=%s addr=%s: %s", d.id, a, e)
                    continue
                self._take.discard(d.id)
                started = time.monotonic()
                try:
                    self.serve(link)
                finally:
                    self._note_flap(d.id, time.monotonic() - started)
                return
        finally:
            self.release("dial:" + d.id)

    def _note_flap(self, dev_id: str, lived: float) -> None:
        """A keypad that drops within seconds of every connection (another copy of Keypad,
        or another computer, is taking it over) is retried less and less often, not in a loop."""
        if lived >= FLAP_SECONDS:
            self._flaps.pop(dev_id, None)
            self._dial_after.pop(dev_id, None)
            return
        n = self._flaps[dev_id] = self._flaps.get(dev_id, 0) + 1
        self._dial_after[dev_id] = time.monotonic() + min(30.0, DIAL_EVERY * 2 ** n)
        if n == 3:
            self.log.warning("keypad %s keeps dropping right after it connects: is another copy of Keypad, or another "
                             "computer, using it? Retrying less often", dev_id)

    def serve(self, link: Link) -> bool:
        """Runs the protocol on an open link until it dies (also used for the
        fake keypad). False when no keypad answered on it."""
        quit_ = threading.Event()
        inbox = self._read_in_background(link, quit_)
        try:
            hello = self._handshake(link, inbox)
            if hello is None:
                return False
            c = Conn(self, link, hello)
            if not self._register(c):
                return True  # a keypad, already connected over Wi-Fi
            try:
                self._greet(c)
                self.ev.connected(c)
                self._loop(c, inbox)
            except Exception:  # a bug in what follows a connection must be seen, not die in a thread nobody reads
                self.log.exception("keypad %s: error while serving the connection", c.id)
            finally:
                self._unregister(c)
            return True
        finally:
            quit_.set()
            link.close()

    def _read_in_background(self, link: Link, quit_: threading.Event) -> queue.Queue[bytes | None]:
        """What the link receives, in order; None when it is lost."""
        inbox: queue.Queue[bytes | None] = queue.Queue(maxsize=16)

        def reader() -> None:
            while True:
                try:
                    b = link.recv()
                except Exception as e:
                    self.log.info("link lost kind=%s addr=%s: %s", link.kind, link.addr, e)
                    b = None
                while not quit_.is_set():  # never block forever once serve() is gone
                    try:
                        inbox.put(b, timeout=0.5)
                        break
                    except queue.Full:
                        continue
                if b is None or quit_.is_set():
                    return

        threading.Thread(target=reader, daemon=True).start()
        return inbox

    def _handshake(self, link: Link, inbox: queue.Queue[bytes | None]) -> dict[str, Any] | None:
        """Says hello and waits for the keypad's own; None if none came or it is not one we can serve."""
        try:
            link.send(proto.encode(self._hello()))
        except Exception:
            return None
        hello = None
        deadline = time.monotonic() + HELLO_WITHIN
        while hello is None:
            try:
                b = inbox.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                self.log.debug("no hello from %s", link.addr)
                return None
            if b is None:
                return None
            try:
                m = proto.decode(b)
            except proto.InvalidMessage:
                continue
            if m["t"] == "hello":
                hello = m
        if not str(hello.get("id", "")).startswith("kp-") or hello.get("v") != proto.VERSION:
            self.log.warning("incompatible keypad id=%s protocol=%s fw=%s", hello.get("id"), hello.get("v"), hello.get("fw"))
            if str(hello.get("id", "")).startswith("kp-"):
                self.problems[hello["id"]] = (f"its firmware ({hello.get('fw', '?')}) speaks protocol v{hello.get('v')}, "
                                              f"this app v{proto.VERSION}: flash it over USB (make flash)")
            return None
        return hello

    def _loop(self, c: Conn, inbox: queue.Queue) -> None:
        last_rx = next_ping = time.monotonic()
        while not c.done.is_set():
            try:
                b = inbox.get(timeout=max(0.01, next_ping - time.monotonic()))
            except queue.Empty:
                b = ...
            now = time.monotonic()
            if b is None:
                return
            if b is not ...:
                last_rx = now
                try:
                    m = proto.decode(b)
                except proto.InvalidMessage:
                    self.log.debug("dropped device message id=%s len=%d", c.id, len(b))
                    continue
                if m["t"] != "pong":
                    self.log.debug("from keypad %s: %s", c.id, b.decode(errors="replace"))
                self._handle(c, m)
            if now >= next_ping:
                if now - last_rx > DEAD_AFTER:
                    self.log.info("keypad timed out id=%s link=%s (no traffic for %.0fs)", c.id, c.link.kind, DEAD_AFTER)
                    return
                c.send({"t": "ping"})
                next_ping = now + PING_EVERY

    def _register(self, c: Conn) -> bool:
        with self._lock:
            old = self._conns.get(c.id)
            if old and old.live and not c.live:
                return False  # a cable adds power, not a second link
            self._conns[c.id] = c
        if old:
            old.close()

        def upd(d: config.Device) -> None:
            if d.name == d.id and c.hello.get("name"):
                d.name = c.hello["name"]
            if ip := c.wifi.get("ip"):
                d.last_ip = ip

        d = self.store.update(c.id, upd)
        self.problems.pop(c.id, None)
        self.log.info("keypad connected id=%s name=%s link=%s addr=%s fw=%s", c.id, d.name, c.link.kind,
                      c.link.addr, c.hello.get("fw"))
        return True

    def _unregister(self, c: Conn) -> None:
        c.close()
        with self._lock:
            mine = self._conns.get(c.id) is c
            if mine:
                del self._conns[c.id]
        if mine:
            self.log.info("keypad disconnected id=%s link=%s", c.id, c.link.kind)
            self.ev.disconnected(c)

    def _hello(self) -> dict[str, Any]:
        return {"t": "hello", "v": proto.VERSION, "host": proto.fit(self.host_name, proto.HOST_NAME), "time": int(time.time())}

    def _greet(self, c: Conn) -> None:
        if c.live:
            self.send_settings(c.id)

    def send_settings(self, dev_id: str) -> None:
        """Pushes brightness and name to one keypad."""
        c, d = self.get(dev_id), self.store.device(dev_id)
        if not c or not d:
            return
        c.send({"t": "settings", "brightness": d.brightness, "name": proto.fit(d.name, proto.DEVICE_NAME)})

    def _handle(self, c: Conn, m: dict[str, Any]) -> None:
        c._deliver(m)
        t = m["t"]
        if t == "pong":  # carries the battery level and the Wi-Fi state, so they stay current
            with c._lock:
                c.battery = battery_of(m)
                c.wifi = {**c.wifi, **{k: m[k] for k in ("rssi",) if k in m}}
                if isinstance(m.get("wifi"), str):
                    c.wifi["state"] = m["wifi"]
        elif t == "hello":  # the keypad answers a hello we sent again (it rebooted without the link dropping)
            with c._lock:
                c.hello, c.battery = m, battery_of(m)
            self._greet(c)
            self.ev.connected(c)
        else:
            self.ev.message(c, m)

    # ---- queries ----

    def get(self, dev_id: str) -> Conn | None:
        with self._lock:
            return self._conns.get(dev_id)

    def conns(self) -> list[Conn]:
        with self._lock:
            return sorted(self._conns.values(), key=lambda c: c.id)

    # ---- actions ----

    def unpair(self, dev_id: str) -> None:
        """Tells a connected keypad to wipe its Wi-Fi pairing, then forgets it.
        A keypad that stays connected over USB stays listed, as USB only."""
        c = self.get(dev_id)
        if c:
            c.send({"t": "unpair"})
            if c.link.kind == "wifi":
                threading.Timer(0.5, c.close).start()
        if c and c.link.kind != "wifi":
            def upd(d: config.Device) -> None:
                d.key = d.ssid = d.last_ip = ""
            self.store.update(dev_id, upd)
        else:
            self.store.remove(dev_id)

    def wait_reconnect(self, dev_id: str, old: Conn | None, timeout: float) -> Conn | None:
        """The keypad's new connection after it restarted (not `old`), or None if it did not come back."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if (c := self.get(dev_id)) is not None and c is not old:
                return c
            time.sleep(0.5)
        return None

    def update_firmware(self, dev_id: str, image: bytes, progress: Callable[[int], None] | None = None) -> None:
        """Streams a firmware image over the live link (USB or Wi-Fi)."""
        c = self.get(dev_id)
        if not c:
            raise RuntimeError("keypad not connected")
        if not self.claim("ota:" + dev_id):
            raise RuntimeError("an update is already running on this keypad")
        try:
            m = c.request({"t": "ota_begin", "size": len(image), "md5": hashlib.md5(image).hexdigest()}, "ota", 10)
            if m.get("ok") is False:
                raise RuntimeError(f"keypad can't start the update: {m.get('err', '')}")
            chunk = 2048
            for off in range(0, len(image), chunk):
                end = min(off + chunk, len(image))
                m = c.request({"t": "ota_data", "off": off, "d": base64.b64encode(image[off:end]).decode()}, "ota", 10,
                             match=lambda m, off=off: m.get("off") == off)
                if m.get("ok") is False:
                    raise RuntimeError(f"keypad rejected the update: {m.get('err', '')}")
                if m.get("off", 0) != off:
                    raise RuntimeError(f"keypad acknowledged offset {m.get('off')}, expected {off}")
                if progress:
                    progress(end * 100 // len(image))
            m = c.request({"t": "ota_end"}, "ota", 20)
            if not m.get("ok"):
                raise RuntimeError(f"update failed: {m.get('err', '')}")
        finally:
            self.release("ota:" + dev_id)
