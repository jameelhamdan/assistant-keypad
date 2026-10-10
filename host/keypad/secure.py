"""The paired Wi-Fi channel: a nonce handshake, HKDF-SHA256 session keys and
AES-256-GCM frames with implicit counters (see proto/PROTOCOL.md)."""

from __future__ import annotations

import json
import os
import socket
import struct
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .proto import VERSION

KEY_SIZE = 32
NONCE_SIZE = 16
MAX_FRAME = 16384
INFO = b"keypad v3"


class SecureError(Exception):
    pass


class Refused(SecureError):
    """The keypad said no. code is a proto.Refusal ("" from older firmware), host the computer holding it (busy)."""

    def __init__(self, why: str, code: str = "", host: str = ""):
        super().__init__(f"keypad refused: {why}")
        self.why, self.code, self.host = why, code, host


def new_key() -> str:
    """A fresh random pairing key, as hex."""
    return os.urandom(KEY_SIZE).hex()


def derive_keys(psk: bytes, nonce_host: bytes, nonce_device: bytes) -> tuple[bytes, bytes]:
    """Returns (host->device, device->host) session keys."""
    okm = HKDF(algorithm=hashes.SHA256(), length=2 * KEY_SIZE, salt=nonce_host + nonce_device, info=INFO).derive(psk)
    return okm[:KEY_SIZE], okm[KEY_SIZE:]


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket) -> bytes:
    (n,) = struct.unpack(">H", _read_exact(sock, 2))
    if n == 0 or n > MAX_FRAME:
        raise SecureError(f"bad frame length {n}")
    return _read_exact(sock, n)


def write_frame(sock: socket.socket, b: bytes) -> None:
    if not b or len(b) > MAX_FRAME:
        raise SecureError(f"bad frame length {len(b)}")
    sock.sendall(struct.pack(">H", len(b)) + b)


class Sealer:
    """AES-256-GCM with nonce = 4 zero bytes || u64 BE counter."""

    def __init__(self, key: bytes):
        self.aead = AESGCM(key)
        self.ctr = 0

    def nonce(self) -> bytes:
        n = b"\0\0\0\0" + struct.pack(">Q", self.ctr)
        self.ctr += 1
        return n

    def seal(self, plain: bytes) -> bytes:
        return self.aead.encrypt(self.nonce(), plain, None)

    def open(self, frame: bytes) -> bytes:
        return self.aead.decrypt(self.nonce(), frame, None)


class Conn:
    """An authenticated, encrypted message stream."""

    def __init__(self, sock: socket.socket, tx_key: bytes, rx_key: bytes, peer: str):
        self.sock = sock
        self.tx, self.rx = Sealer(tx_key), Sealer(rx_key)
        self.peer = peer
        self._wlock = threading.Lock()

    def send(self, plain: bytes) -> None:
        with self._wlock:
            write_frame(self.sock, self.tx.seal(plain))

    def recv(self) -> bytes:
        """Any failure is fatal for the session."""
        f = read_frame(self.sock)
        try:
            return self.rx.open(f)
        except InvalidTag as e:
            raise SecureError("frame authentication failed") from e


def _hi(**fields) -> bytes:
    return json.dumps({"t": "hi", "v": VERSION, **fields}, separators=(",", ":")).encode()


def client_handshake(sock: socket.socket, host_id: str, want_id: str, psk_hex: str, take: bool = False) -> Conn:
    """The host side. want_id is the keypad id we paired with; psk_hex its key. take asks to
    replace another computer that holds the keypad now."""
    try:
        psk = bytes.fromhex(psk_hex)
    except ValueError as e:
        raise SecureError("invalid pairing key") from e
    if len(psk) != KEY_SIZE:
        raise SecureError("invalid pairing key")
    nh = os.urandom(NONCE_SIZE)
    write_frame(sock, _hi(host=host_id, n=nh.hex(), **({"take": 1} if take else {})))
    try:
        h = json.loads(read_frame(sock))
    except ValueError as e:
        raise SecureError("bad keypad hello") from e
    if h.get("t") == "no":
        raise Refused(str(h.get("why", "")), str(h.get("code", "")), str(h.get("host", "")))
    if h.get("t") != "hi" or h.get("v") != VERSION or h.get("id") != want_id:
        raise SecureError(f"unexpected keypad {h.get('id', '')!r}")
    try:
        nd = bytes.fromhex(h.get("n", ""))
    except ValueError as e:
        raise SecureError("bad keypad nonce") from e
    if len(nd) != NONCE_SIZE:
        raise SecureError("bad keypad nonce")
    h2d, d2h = derive_keys(psk, nh, nd)
    return Conn(sock, h2d, d2h, h["id"])
