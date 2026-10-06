"""Link transports: each carries whole messages (bytes) in both directions."""

from __future__ import annotations

import socket
import struct
import threading

from .. import proto, secure

MAX_LINE = 4096


class LinkClosed(ConnectionError):
    pass


class Link:
    kind = ""
    addr = ""

    def send(self, msg: bytes) -> None:
        raise NotImplementedError

    def recv(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


# ---- USB ------------------------------------------------------------------------


def keypad_id(serial_number: str | None) -> str:
    """The keypad id (kp- and the last 3 bytes of its MAC) from the USB serial number, which
    is the MAC address ("68:B6:B3:22:F9:0C" -> "kp-22f90c"); "" if it is not one."""
    parts = (serial_number or "").split(":")
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        return ""
    return "kp-" + "".join(parts[3:]).lower()


def usb_devices() -> list[tuple[str, str]]:
    """(port, keypad id) of every Espressif native-USB serial device. The id is known before the
    port is opened, so a keypad that is already connected can be left alone: opening its port
    toggles the reset lines and reboots it."""
    from serial.tools import list_ports

    try:
        return [(p.device, keypad_id(p.serial_number)) for p in list_ports.comports() if p.vid == proto.USB_VENDOR_ID]
    except Exception:
        return []


class UsbLink(Link):
    kind = "usb"

    def __init__(self, port: str):
        import serial

        self.addr = port
        self._s = serial.Serial(port, 115200, timeout=0.5, write_timeout=5)
        self._s.dtr = True  # native USB CDC only transmits once DTR is set
        self._wlock = threading.Lock()
        self._closed = threading.Event()
        self._buf = bytearray()

    def send(self, msg: bytes) -> None:
        with self._wlock:
            try:
                self._s.write(msg + b"\n")
            except Exception as e:  # serial raises its own exception types
                raise LinkClosed(str(e)) from e

    def recv(self) -> bytes:
        skipping = False  # inside an oversized line: drop it up to its newline
        while True:
            if self._closed.is_set():
                raise LinkClosed("closed")
            i = self._buf.find(b"\n")
            if i < 0:
                if len(self._buf) > MAX_LINE:
                    self._buf.clear()
                    skipping = True
                try:
                    chunk = self._s.read(max(1, self._s.in_waiting or 1))
                except Exception as e:
                    raise LinkClosed(str(e)) from e
                self._buf += chunk
                continue
            line = bytes(self._buf[:i]).strip()
            del self._buf[: i + 1]
            if skipping:
                skipping = False
                continue
            if line.startswith(b"{"):  # skip boot ROM chatter and blank lines
                return line

    def close(self) -> None:
        self._closed.set()
        try:
            self._s.close()
        except Exception:
            pass


# ---- Wi-Fi ------------------------------------------------------------------------

SEND_TIMEOUT = 5.0  # bounds a write to a keypad that vanished without closing the connection


class WifiLink(Link):
    kind = "wifi"

    def __init__(self, addr: str, host_id: str, device_id: str, key: str):
        host, port = addr.rsplit(":", 1)
        self.addr = addr
        self._sock = socket.create_connection((host, int(port)), timeout=3)
        try:
            self._sock.settimeout(5)
            self._conn = secure.client_handshake(self._sock, host_id, device_id, key)
            self._sock.settimeout(SEND_TIMEOUT)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            self._sock.close()
            raise
        self._closed = threading.Event()
        self._buf = bytearray()

    def send(self, msg: bytes) -> None:
        try:
            self._conn.send(msg)
        except OSError as e:
            self.close()  # a partial frame desyncs the stream: end the session
            raise LinkClosed(str(e)) from e

    def recv(self) -> bytes:
        while True:
            if len(self._buf) >= 2:
                (n,) = struct.unpack(">H", self._buf[:2])
                if n == 0 or n > secure.MAX_FRAME:
                    raise LinkClosed(f"bad frame length {n}")
                if len(self._buf) >= 2 + n:
                    frame = bytes(self._buf[2 : 2 + n])
                    del self._buf[: 2 + n]
                    try:
                        return self._conn.rx.open(frame)
                    except Exception as e:
                        raise LinkClosed("frame authentication failed") from e
            if self._closed.is_set():
                raise LinkClosed("closed")
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                continue  # idle; the hub's liveness check decides when it's dead
            except OSError as e:
                raise LinkClosed(str(e)) from e
            if not chunk:
                raise LinkClosed("connection closed")
            self._buf += chunk

    def close(self) -> None:
        self._closed.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
