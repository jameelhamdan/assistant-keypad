import json
import os
import socket
import threading

import pytest
from cryptography.exceptions import InvalidTag

from keypad import secure


def server_handshake(sock, dev_id, paired_host, psk_hex):
    """The keypad's side of the handshake (the firmware does this in link.cpp)."""
    try:
        h = json.loads(secure.read_frame(sock))
    except ValueError as e:
        raise secure.SecureError("bad hello") from e
    if h.get("t") != "hi" or h.get("v") != secure.VERSION:
        raise secure.SecureError("bad hello")
    if h.get("host") != paired_host:
        secure.write_frame(sock, json.dumps({"t": "no", "why": "paired with another computer", "code": "other_host"}).encode())
        raise secure.SecureError("unknown host")
    nh = bytes.fromhex(h.get("n", ""))
    if len(nh) != secure.NONCE_SIZE:
        raise secure.SecureError("bad host nonce")
    nd = os.urandom(secure.NONCE_SIZE)
    secure.write_frame(sock, secure._hi(id=dev_id, n=nd.hex()))
    h2d, d2h = secure.derive_keys(bytes.fromhex(psk_hex), nh, nd)
    return secure.Conn(sock, d2h, h2d, h["host"])


def pair(host_key, dev_key, host_id, paired_host):
    a, b = socket.socketpair()
    res = {}

    def server():
        try:
            res["d"] = server_handshake(b, "kp-000001", paired_host, dev_key)
        except Exception as e:
            res["derr"] = e
            b.close()

    t = threading.Thread(target=server)
    t.start()
    try:
        h = secure.client_handshake(a, host_id, "kp-000001", host_key)
    except Exception as e:
        h, res["herr"] = None, e
    t.join()
    return h, res.get("d"), res.get("herr"), res.get("derr")


def test_round_trip():
    k = secure.new_key()
    h, d, e1, e2 = pair(k, k, "h-1", "h-1")
    assert not e1 and not e2
    for msg in (b'{"t":"ping"}', b'{"t":"status"}'):
        h.send(msg)
        assert d.recv() == msg
        d.send(msg)
        assert h.recv() == msg


def test_wrong_key_fails_first_frame():
    h, d, e1, e2 = pair(secure.new_key(), secure.new_key(), "h-1", "h-1")
    h.send(b'{"t":"ping"}')
    with pytest.raises(secure.SecureError):
        d.recv()


def test_unknown_host_refused():
    k = secure.new_key()
    _, _, e1, e2 = pair(k, k, "h-stranger", "h-1")
    assert e1 is not None and e2 is not None


def test_replay_rejected():
    k = bytes.fromhex(secure.new_key())
    s, r = secure.Sealer(k), secure.Sealer(k)
    f = s.seal(b"x")
    r.open(f)
    with pytest.raises(InvalidTag):
        r.open(f)


def test_known_vector():
    """Fixed vector shared with the firmware's boot self-test (firmware/src/crypto.cpp)."""
    psk = bytes(range(32))
    nh = bytes(range(0xA0, 0xB0))
    nd = bytes(range(0xB0, 0xC0))
    h2d, d2h = secure.derive_keys(psk, nh, nd)
    ct = secure.Sealer(h2d).seal(b'{"t":"ping"}')
    assert h2d.hex() == "4e45580c0a8caf97b0b55a10355c1da2a5ef693d196e2f8e8b85c2dc459bc1c2"
    assert d2h.hex() == "70ff6e53ceaa8b83334365430a74e8bcc32320ba6a64e55cb53700e9a1e61420"
    assert ct.hex() == "bca3f535a7905941f515bb6e77b78ff5795f493d28067dc1dde1018b"


def test_a_refusal_carries_the_keypads_reason():
    k = secure.new_key()
    _, _, e1, _ = pair(k, k, "h-stranger", "h-1")
    assert isinstance(e1, secure.Refused) and e1.code == "other_host"
    assert "another computer" in e1.why
