import socket
import threading

import pytest
from cryptography.exceptions import InvalidTag

from keypad import secure


def pair(host_key, dev_key, host_id, paired_host):
    a, b = socket.socketpair()
    res = {}

    def server():
        try:
            res["d"] = secure.server_handshake(b, "kp-000001", paired_host, dev_key)
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
