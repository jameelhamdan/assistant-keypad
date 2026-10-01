"""mDNS discovery of keypads on the local network."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .. import proto

FRESH = 300.0  # seconds a sighting stays valid


class Discovery:
    def __init__(self, log: logging.Logger):
        self.log = log
        self._lock = threading.Lock()
        self._seen: dict[str, dict[str, Any]] = {}
        self._zc = None

    def get(self, dev_id: str) -> dict[str, Any] | None:
        with self._lock:
            s = self._seen.get(dev_id)
            return dict(s) if s and time.time() - s["at"] < FRESH else None

    def list(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [dict(s) for s in self._seen.values() if now - s["at"] < FRESH]

    def start(self) -> None:
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            self._zc = Zeroconf()
            ServiceBrowser(self._zc, proto.MDNS_SERVICE, handlers=[self._on_change])
        except Exception as e:  # no network, blocked multicast...
            self.log.debug("mdns browse: %s", e)

    def stop(self) -> None:
        if self._zc is not None:
            self._zc.close()

    def _on_change(self, zeroconf, service_type: str, name: str, state_change) -> None:
        from zeroconf import ServiceStateChange

        if state_change is ServiceStateChange.Removed:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info is None:
            return
        txt = {k.decode(errors="replace"): (v or b"").decode(errors="replace") for k, v in info.properties.items()}
        dev_id = txt.get("id") or name.split(".", 1)[0]
        addrs = info.parsed_addresses()
        v4 = [a for a in addrs if ":" not in a]
        if not dev_id.startswith("kp-") or not v4:
            return
        with self._lock:
            prev = self._seen.get(dev_id, {})
            self._seen[dev_id] = {
                "id": dev_id, "addr": f"{v4[0]}:{info.port}", "at": time.time(),
                # entries can arrive before their TXT record: keep what we learned earlier
                "fw": txt.get("fw", prev.get("fw", "")), "paired": txt.get("paired", "1" if prev.get("paired") else "0") == "1",
            }
