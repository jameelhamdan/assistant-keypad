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

KEY_SIZE = 32
NONCE_SIZE = 16
MAX_FRAME = 4096
INFO = b"keypad v2"


class SecureError(Exception):
    pass


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
    return json.dumps({"t": "hi", "v": 2, **fields}, separators=(",", ":")).encode()


def client_handshake(sock: socket.socket, host_id: str, want_id: str, psk_hex: str) -> Conn:
    """The host side. want_id is the keypad id we paired with; psk_hex its key."""
    try:
        psk = bytes.fromhex(psk_hex)
    except ValueError as e:
        raise SecureError("invalid pairing key") from e
    if len(psk) != KEY_SIZE:
        raise SecureError("invalid pairing key")
    nh = os.urandom(NONCE_SIZE)
    write_frame(sock, _hi(host=host_id, n=nh.hex()))
    try:
        h = json.loads(read_frame(sock))
    except ValueError as e:
        raise SecureError("bad keypad hello") from e
    if h.get("t") == "no":
        raise SecureError(f"keypad refused: {h.get('why', '')}")
    if h.get("t") != "hi" or h.get("v") != 2 or h.get("id") != want_id:
        raise SecureError(f"unexpected keypad {h.get('id', '')!r}")
    try:
        nd = bytes.fromhex(h.get("n", ""))
    except ValueError as e:
        raise SecureError("bad keypad nonce") from e
    if len(nd) != NONCE_SIZE:
        raise SecureError("bad keypad nonce")
    h2d, d2h = derive_keys(psk, nh, nd)
    return Conn(sock, h2d, d2h, h["id"])


def server_handshake(sock: socket.socket, dev_id: str, paired_host: str, psk_hex: str) -> Conn:
    """The keypad side; used by tests."""
    try:
        h = json.loads(read_frame(sock))
    except ValueError as e:
        raise SecureError("bad hello") from e
    if h.get("t") != "hi" or h.get("v") != 2:
        raise SecureError("bad hello")
    if h.get("host") != paired_host:
        write_frame(sock, json.dumps({"t": "no", "why": "not paired with this host"}).encode())
        raise SecureError("unknown host")
    nh = bytes.fromhex(h.get("n", ""))
    if len(nh) != NONCE_SIZE:
        raise SecureError("bad host nonce")
    nd = os.urandom(NONCE_SIZE)
    write_frame(sock, _hi(id=dev_id, n=nd.hex()))
    h2d, d2h = derive_keys(bytes.fromhex(psk_hex), nh, nd)
    return Conn(sock, d2h, h2d, h["host"])
