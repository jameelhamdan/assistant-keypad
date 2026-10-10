"""USB is only for setup: pairing a keypad over the cable. The hub opens a port
only while a keypad is being set up (Add keypad… in the tray), so a flasher can have it and another ESP32
board is not reset by a probe."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .. import config, secure
from .links import UsbLink, usb_devices

if TYPE_CHECKING:
    from .hub import Hub


class UsbSetup:
    def __init__(self, hub: Hub):
        self.hub = hub
        self._until = 0.0  # monotonic time until which USB is scanned for pairing
        # USB ports to leave alone until a time: a board that isn't a keypad (another ESP32 board
        # shares the vendor id) or a keypad already on Wi-Fi. Opening a port toggles DTR, which resets it.
        self._quiet: dict[str, float] = {}

    def open(self, seconds: float = 300) -> None:
        """Scans USB for a while (Add keypad… in the tray)."""
        self._until = time.monotonic() + seconds

    def wanted(self) -> bool:
        """Whether to open USB ports now: only while a keypad is being set up."""
        return time.monotonic() < self._until

    def scan(self) -> None:
        """One pass over the USB ports: serves each keypad found."""
        for port, dev_id in usb_devices() if self.wanted() else []:
            if time.monotonic() < self._quiet.get(port, 0.0):
                continue
            if dev_id and (c := self.hub.get(dev_id)) and c.live:
                continue  # already connected over Wi-Fi: opening its port would reboot it
            if self.hub.claim("usb:" + port):
                threading.Thread(target=self._serve, args=(port,), daemon=True).start()

    def _serve(self, port: str) -> None:
        hub = self.hub
        try:
            link = UsbLink(port)
        except Exception as e:
            hub.log.debug("usb open failed port=%s: %s", port, e)
            time.sleep(3)  # busy (flasher, serial monitor): back off
            hub.release("usb:" + port)
            return
        try:
            started = time.monotonic()
            answered = hub.serve(link)
            if time.monotonic() - started < 3:  # not a keypad, or one that is already connected over Wi-Fi
                if not answered:
                    hub.log.info("USB port %s doesn't answer as a keypad", port)
                self._quiet[port] = time.monotonic() + (60 if answered else 30)
        finally:
            hub.release("usb:" + port)

    def provision(self, dev_id: str, ssid: str, password: str, name: str) -> None:
        """Pairs a USB-connected keypad and gives it Wi-Fi credentials."""
        c = self.hub.get(dev_id)
        if not c or c.link.kind != "usb":
            raise RuntimeError("connect the keypad with a USB cable to pair it")
        name = name or dev_id
        key = secure.new_key()
        m = c.request({"t": "provision", "ssid": ssid, "pass": password, "host": self.hub.store.host_id(),
                       "key": key, "name": name}, "provisioned", 10)
        if not m.get("ok"):
            raise RuntimeError(f"keypad rejected the settings: {m.get('err', '')}")

        def upd(d: config.Device) -> None:
            d.key, d.name, d.ssid = key, name, ssid

        self.hub.store.update(dev_id, upd)
        self.hub.send_settings(dev_id)
