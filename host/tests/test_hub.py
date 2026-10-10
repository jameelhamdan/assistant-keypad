import threading
import time

import fakekeypad as fakemod

from keypad import config
from keypad.device.hub import Hub


def test_usb_is_only_scanned_for_setup(home):
    store = config.Store("h-1", [])
    hub = Hub(store, ev=None)
    assert not hub.usb.wanted()  # no keypad being set up: the cable is only power, another board is left alone
    hub.usb.open()
    assert hub.usb.wanted()  # Add keypad… opens a window


class Sink:
    def __init__(self):
        self.seen, self.messages = [], []

    def connected(self, c):
        self.seen.append(c)

    def disconnected(self, c):
        pass

    def message(self, c, m):
        self.messages.append(m)


def test_hello_handshake_and_a_usb_link_never_replaces_the_working_link(home):
    store, _ = config.Store.open()
    sink = Sink()
    hub = Hub(store, sink, host_name="PC")
    fake = fakemod.Fake("kp-000001")
    threading.Thread(target=hub.serve, args=(fake,), daemon=True).start()
    deadline = time.time() + 2
    while not hub.conns():
        assert time.time() < deadline, "no handshake"
        time.sleep(0.005)
    c = hub.get("kp-000001")
    assert c.live and c.hello["v"] == 3 and c.hello["fw"] == "fake"
    usb = fakemod.Fake("kp-000001")
    usb.kind = "usb"  # a cable plugged in for power while Wi-Fi works
    assert hub.serve(usb) is True and hub.get("kp-000001") is c
    fake.close()


def test_a_usb_setup_link_does_not_stop_the_wifi_dial(home):
    store, _ = config.Store.open()
    store.update("kp-000001", lambda d: setattr(d, "key", "k" * 64))
    hub = Hub(store, Sink(), host_name="PC")
    dialed = []
    hub._dial = lambda d: (dialed.append(d.id), hub.release("dial:" + d.id))
    usb = fakemod.Fake("kp-000001")
    usb.kind = "usb"
    threading.Thread(target=hub.serve, args=(usb,), daemon=True).start()
    deadline = time.time() + 2
    while not hub.conns():
        assert time.time() < deadline
        time.sleep(0.005)
    stop = threading.Event()
    threading.Thread(target=hub.run, args=(stop,), daemon=True).start()
    deadline = time.time() + 4
    while not dialed and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    usb.close()
    assert dialed == ["kp-000001"], "a keypad connected only over USB must still be dialed over Wi-Fi"


def test_refusals_are_explained_in_words():
    from keypad.device.hub import explain_refusal

    assert "flash" in explain_refusal("keypad refused: bad hello")
    assert "not paired with this computer" in explain_refusal("keypad refused: paired with another computer")
    assert "not paired with this computer" in explain_refusal("x", code="other_host")
    busy = explain_refusal("x", code="busy", host="Work PC")
    assert "Work PC" in busy and "Use keypad here" in busy
    assert "Set up Wi-Fi" in explain_refusal("keypad refused: keypad is not paired")
    assert "Set up Wi-Fi" in explain_refusal("frame authentication failed")


def test_a_keypad_that_keeps_dropping_is_retried_less_and_less_often(home):
    store, _ = config.Store.open()
    hub = Hub(store, Sink())
    hub._note_flap("kp-1", 0.5)
    first = hub._dial_after["kp-1"] - time.monotonic()
    hub._note_flap("kp-1", 0.5)
    hub._note_flap("kp-1", 0.5)
    third = hub._dial_after["kp-1"] - time.monotonic()
    assert third > first and third <= 30
    hub._note_flap("kp-1", 60)  # a connection that held: back to normal
    assert "kp-1" not in hub._dial_after and "kp-1" not in hub._flaps


def test_a_cable_on_an_already_connected_keypad_is_not_reopened_in_a_loop(home, monkeypatch):
    store, _ = config.Store.open()
    hub = Hub(store, Sink(), host_name="PC")
    wifi = fakemod.Fake("kp-000001")
    threading.Thread(target=hub.serve, args=(wifi,), daemon=True).start()
    deadline = time.time() + 2
    while not hub.conns():
        assert time.time() < deadline
        time.sleep(0.005)
    from keypad.device import pairing as hubmod

    def open_usb(port):
        usb = fakemod.Fake("kp-000001")
        usb.kind = "usb"
        return usb

    monkeypatch.setattr(hubmod, "UsbLink", open_usb)
    assert hub.claim("usb:COM9")
    hub.usb._serve("COM9")
    wait_until = hub.usb._quiet.get("COM9", 0.0)
    assert wait_until > time.monotonic() + 30, "the port should be left alone for a while"
    wifi.close()


def test_keypad_ids_come_from_the_usb_serial_number():
    from keypad.device.links import keypad_id

    assert keypad_id("68:B6:B3:22:F9:0C") == "kp-22f90c"
    assert keypad_id(None) == "" and keypad_id("A123456") == ""


def test_the_port_of_a_keypad_that_is_connected_over_wifi_is_never_opened(home, monkeypatch):
    from keypad.device import pairing as hubmod

    store, _ = config.Store.open()
    hub = Hub(store, Sink(), host_name="PC")
    wifi = fakemod.Fake("kp-22f90c")
    threading.Thread(target=hub.serve, args=(wifi,), daemon=True).start()
    deadline = time.time() + 2
    while not hub.conns():
        assert time.time() < deadline
        time.sleep(0.005)
    opened = []
    monkeypatch.setattr(hubmod, "usb_devices", lambda: [("COM5", "kp-22f90c"), ("COM6", "kp-aaaaaa")])
    monkeypatch.setattr(hubmod, "UsbLink", lambda port: (opened.append(port), (_ for _ in ()).throw(OSError("busy")))[1])
    hub.usb.open()
    stop = threading.Event()
    threading.Thread(target=hub.run, args=(stop,), daemon=True).start()
    deadline = time.time() + 3
    while "COM6" not in opened and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    wifi.close()
    assert "COM6" in opened and "COM5" not in opened


def test_an_error_after_connecting_is_logged_and_backs_off(home, caplog):
    import logging

    class Boom(Sink):
        def connected(self, c):
            raise RuntimeError("boom")

    store, _ = config.Store.open()
    store.update("kp-000001", lambda d: setattr(d, "key", "k" * 64))
    hub = Hub(store, Boom(), host_name="PC", log=logging.getLogger("keypad-test"))
    fake = fakemod.Fake("kp-000001")
    with caplog.at_level(logging.ERROR, logger="keypad-test"):
        assert hub.serve(fake) is True  # does not raise
    assert "error while serving the connection" in caplog.text and "boom" in caplog.text
    hub._note_flap("kp-000001", 0.0)
    assert hub._dial_after["kp-000001"] > time.monotonic()


def test_a_busy_keypad_is_looked_at_again_later_and_can_be_taken_over(home, monkeypatch):
    from keypad import secure
    from keypad.device import hub as hubmod

    store, _ = config.Store.open()
    store.update("kp-000001", lambda d: setattr(d, "key", "k" * 64))
    hub = Hub(store, Sink(), host_name="PC")
    takes = []

    def refuse(addr, host_id, dev_id, key, take=False):
        takes.append(take)
        raise secure.Refused("in use by another computer", "busy", "Work PC")

    monkeypatch.setattr(hubmod, "WifiLink", refuse)
    d = store.device("kp-000001")
    d.last_ip = "10.0.0.5"
    hub.claim("dial:kp-000001")
    hub._dial(d)
    assert "Work PC" in hub.problems["kp-000001"]
    assert hub._dial_after["kp-000001"] - time.monotonic() > 10, "no dialing every two seconds at a busy keypad"
    assert "kp-000001" not in hub._flaps, "being refused is not a drop-out"
    hub.take("kp-000001")
    assert "kp-000001" not in hub.problems and "kp-000001" not in hub._dial_after
    hub.claim("dial:kp-000001")
    hub._dial(d)
    assert takes == [False, True], "the next dial after Use keypad here asks the keypad to let go"


def test_the_agent_counts_what_it_ignores(home):
    import logging

    from keypad.core.dialogs import Dialogs

    dialogs = Dialogs(None, logging.getLogger("test"))
    assert dialogs.press("kp-1", {"id": "p-1", "key": 7, "act": "pick", "idx": 0}) is False
    assert dialogs.stats.view()["counts"] == {"press_stale": 1}
